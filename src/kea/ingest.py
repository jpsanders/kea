"""
Read evidence into the index: AI agent sessions, chat exports, git history, documents, and the brain itself.

Every adapter is read only and incremental. A source whose fingerprint (size and modification time, or the git refs,
together with the brain's deny and identity settings) has not changed is skipped, so a second run takes a moment.
Paths that name a credential store are never opened, symbolic links are judged by where they point, a forgotten source
is never read back, and :class:`kea.store.Store` redacts every text before it is stored. A malformed record is skipped
and counted; a malformed file is reported, and the rest of the run carries on.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404: runs git log and git config with fixed arguments only
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from kea.brain import INDEX_DIR, Brain
from kea.redact import is_denied
from kea.store import Item, Store

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org"}
MAX_DOC_BYTES = 2_000_000
MAX_SECTION = 6_000
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".kea",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "site-packages",
    "dist",
    "build",
}
# kea's own boilerplate in a brain: searchable in the manual, never mistaken for what the owner wrote.
BRAIN_BOILERPLATE = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "council/PROTOCOL.md",
    "frameworks/_TEMPLATE.md",
    "journal/README.md",
}
# Text Claude Code and Codex inject into the user turn; it is not what the person typed.
WRAPPER = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr|command-name|command-message|"
    r"command-args|task-notification|user-prompt-submit-hook|bash-input|bash-stdout|bash-stderr)>.*?</\1>",
    re.S,
)
NOISE = {"[Request interrupted by user]", "[Request interrupted by user for tool use]"}
KINDS = ("claude-code", "codex", "opencode", "chatgpt", "claude", "git", "docs")
BAD_RECORD = (AttributeError, KeyError, TypeError, ValueError, OverflowError, IndexError)


@dataclass
class Report:
    """What one ingest run did."""

    kind: str
    path: str
    read: int = 0
    unchanged: int = 0
    skipped: int = 0
    dropped: int = 0
    forgotten: int = 0
    items: int = 0
    bad_records: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """
        Give the report as a plain dict.

        :returns: The fields
        """
        return dict(self.__dict__)


def default_path(kind: str) -> Path | None:
    """
    Give where an adapter reads from when no path is named.

    :param kind: The adapter
    :returns: The default location, or None when the adapter needs a path
    """
    home = Path.home()
    if kind == "claude-code":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser() / "projects"
    if kind == "codex":
        return Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser() / "sessions"
    if kind == "opencode":
        return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "opencode" / "opencode.db"
    return None


def fingerprint(path: Path) -> str:
    """
    Identify a file's version by its size and modification time.

    :param path: The file
    :returns: The fingerprint
    """
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")
        except OverflowError, OSError, ValueError:
            return None
    return str(value)


def _deny(brain: Brain) -> list[str]:
    extra = brain.setting("ingest", "deny", [])
    return [str(x) for x in extra] if isinstance(extra, list) else []


def _identity(brain: Brain) -> tuple[set[str], set[str]]:
    emails = {str(e).lower() for e in brain.setting("identity", "emails", []) or []}
    names = {str(n).lower() for n in brain.setting("identity", "names", []) or []}
    git = shutil.which("git")
    if git and not emails and not names:
        for key, bucket in (("user.email", emails), ("user.name", names)):
            out = subprocess.run(  # nosec B603: git config with fixed arguments
                [git, "config", "--global", key], capture_output=True, text=True, check=False
            ).stdout.strip()
            if out:
                bucket.add(out.lower())
    return emails, names


def _signature(brain: Brain) -> str:
    """Fold the settings that change what is stored into every fingerprint, so changing them rereads the sources."""
    emails, names = _identity(brain)
    raw = json.dumps([sorted(_deny(brain)), sorted(emails), sorted(names)])
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _texts(content: Any, types: tuple[str, ...] = ("text",)) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n\n".join(
        str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") in types and b.get("text")
    )


def _prompt(text: str) -> str:
    text = WRAPPER.sub(" ", text).strip()
    return "" if text in NOISE else text


def _title(items: list[Item], fallback: str) -> str:
    first = next((i.body for i in items if i.role == "user"), "")
    line = " ".join(first.split())
    return (line[:77] + "...") if len(line) > 80 else (line or fallback)


def _jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            try:
                record = json.loads(line)
            except ValueError, RecursionError:
                continue
            if isinstance(record, dict):
                yield line_no, record


# ---------------------------------------------------------------- AI agent sessions


@dataclass
class Parsed:
    """What a session parser found."""

    items: list[Item]
    project: str | None
    bad: int = 0


def parse_claude_code(path: Path) -> Parsed:
    """
    Read one Claude Code session transcript: the person's prompts and the assistant's replies, nothing else.

    Tool calls, tool results, thinking, injected reminders and subagent side chains are left out.

    :param path: A ``<session>.jsonl`` file under ``~/.claude/projects``
    :returns: The messages, and the project folder the session ran in
    """
    out = Parsed([], None)
    title = None
    session = path.stem
    for line_no, d in _jsonl(path):
        try:
            kind = d.get("type")
            if kind in ("ai-title", "custom-title"):
                found = d.get("aiTitle") or d.get("customTitle") or d.get("title")
                title = str(found) if found else title
                continue
            if (
                kind not in ("user", "assistant")
                or d.get("isSidechain")
                or d.get("isMeta")
                or d.get("isCompactSummary")
            ):
                continue
            if isinstance(d.get("cwd"), str) and not out.project:
                out.project = d["cwd"]
            message = d.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            text = _prompt(_texts(content)) if kind == "user" else _texts(content).strip()
            if not text:
                continue
            out.items.append(
                Item(
                    "message",
                    "claude-code",
                    None,
                    session,
                    None,
                    len(out.items),
                    kind,
                    None,
                    kind == "user",
                    _iso(d.get("timestamp")),
                    f"{path}:{line_no}",
                    text,
                )
            )
        except BAD_RECORD:
            out.bad += 1
    name = title or _title(out.items, session)
    for item in out.items:
        item.title, item.project = name, out.project
    return out


def _codex_wrapper(text: str) -> bool:
    stripped = text.strip()
    m = re.match(r"^<([a-z][a-z0-9_ -]*)>", stripped)
    if m and stripped.endswith(f"</{m[1]}>"):
        return True
    return stripped.startswith("# AGENTS.md instructions")


def parse_codex(path: Path) -> Parsed:
    """
    Read one Codex CLI rollout: the person's prompts and the assistant's replies.

    :param path: A ``rollout-*.jsonl`` file under ``~/.codex/sessions``
    :returns: The messages, and the project folder the session ran in
    """
    out = Parsed([], None)
    session = path.stem.removeprefix("rollout-")
    for line_no, d in _jsonl(path):
        try:
            raw = d.get("payload")
            payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
            if d.get("type") == "session_meta":
                out.project = payload["cwd"] if isinstance(payload.get("cwd"), str) else out.project
                session = str(payload.get("id") or session)
                continue
            if d.get("type") != "response_item" or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _texts(payload.get("content"), ("input_text", "output_text", "text")).strip()
            if not text or (role == "user" and _codex_wrapper(text)):
                continue
            out.items.append(
                Item(
                    "message",
                    "codex",
                    None,
                    session,
                    None,
                    len(out.items),
                    str(role),
                    None,
                    role == "user",
                    _iso(d.get("timestamp")),
                    f"{path}:{line_no}",
                    text,
                )
            )
        except BAD_RECORD:
            out.bad += 1
    name = _title(out.items, session)
    for item in out.items:
        item.title, item.project = name, out.project
    return out


def _sessions(
    store: Store,
    report: Report,
    kind: str,
    files: list[Path],
    parse: Callable[[Path], Parsed],
    deny: list[str],
    sig: str,
    force: bool,
    root: Path | None = None,
) -> set[str]:
    seen: set[str] = set()
    for path in files:
        try:
            real = path.resolve(strict=True)
        except OSError:
            continue
        outside = path.is_symlink() and root is not None and not real.is_relative_to(root)
        if is_denied(path, deny) or is_denied(real, deny) or outside:
            report.skipped += 1
            continue
        if store.is_forgotten(kind, str(path)):
            report.forgotten += 1
            continue
        seen.add(str(path))
        try:
            mark = f"{fingerprint(path)}|{sig}"
        except OSError:
            continue
        sid, old = store.source(kind, str(path))
        if old == mark and not force:
            report.unchanged += 1
            continue
        try:
            parsed = parse(path)
        except (OSError, *BAD_RECORD) as exc:
            store.fail(sid, str(exc))
            report.errors.append(f"{path}: {exc}")
            continue
        report.bad_records += parsed.bad
        with store.transaction():
            if parsed.project and is_denied(parsed.project, deny):
                store.replace(sid, [], mark)
                report.skipped += 1
                continue
            store.source(kind, str(path), parsed.project)
            report.items += store.replace(sid, parsed.items, mark)
        report.read += 1
    return seen


def _opencode(store: Store, report: Report, db: Path, deny: list[str], sig: str, force: bool) -> None:
    if store.is_forgotten("opencode", str(db)):
        report.forgotten += 1
        return
    wal = db.with_name(db.name + "-wal")
    mark = fingerprint(db) + (f"+{fingerprint(wal)}" if wal.exists() else "") + f"|{sig}"
    sid, old = store.source("opencode", str(db), "OpenCode")
    if old == mark and not force:
        report.unchanged += 1
        return
    items: list[Item] = []
    try:
        src = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        store.fail(sid, str(exc))
        report.errors.append(f"{db}: {exc}")
        return
    try:
        src.row_factory = sqlite3.Row
        sessions = {r["id"]: r for r in src.execute("SELECT id, title, directory FROM session")}
        parts: dict[str, list[str]] = {}
        for row in src.execute("SELECT message_id, data FROM part ORDER BY time_created"):
            try:
                data = json.loads(row["data"])
            except ValueError, TypeError, RecursionError:
                report.bad_records += 1
                continue
            if isinstance(data, dict) and data.get("type") == "text" and data.get("text"):
                parts.setdefault(row["message_id"], []).append(str(data["text"]))
        seq: dict[str, int] = {}
        for row in src.execute("SELECT id, session_id, time_created, data FROM message ORDER BY time_created"):
            text = "\n\n".join(parts.get(row["id"], [])).strip()
            if not text:
                continue
            try:
                meta = json.loads(row["data"])
                role = str(meta.get("role", "assistant")) if isinstance(meta, dict) else "assistant"
            except ValueError, TypeError, RecursionError:
                role = "assistant"
            session = sessions.get(row["session_id"])
            if session and session["directory"] and is_denied(str(session["directory"]), deny):
                report.skipped += 1
                continue
            n = seq.get(row["session_id"], 0)
            seq[row["session_id"]] = n + 1
            items.append(
                Item(
                    "message",
                    "opencode",
                    str(session["directory"]) if session and session["directory"] else None,
                    str(row["session_id"]),
                    str(session["title"]) if session and session["title"] else None,
                    n,
                    role,
                    None,
                    role == "user",
                    _iso(row["time_created"]),
                    f"{db}#session/{row['session_id']}/message/{row['id']}",
                    text,
                )
            )
    except sqlite3.Error as exc:
        store.fail(sid, f"OpenCode database not in a shape kea knows: {exc}")
        report.errors.append(f"{db}: {exc}")
        return
    finally:
        src.close()
    with store.transaction():
        report.items += store.replace(sid, items, mark)
    report.read += 1


# ---------------------------------------------------------------- chat exports


def _export_files(path: Path, pattern: str) -> Iterator[tuple[str, IO[bytes]]]:
    rx = re.compile(pattern)
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if rx.search(info.filename.rsplit("/", 1)[-1]) and not info.is_dir():
                    with archive.open(info) as fh:
                        yield info.filename, fh
    elif path.is_dir():
        for member in sorted(path.rglob("*.json")):
            if rx.search(member.name):
                with member.open("rb") as fh:
                    yield str(member.relative_to(path)), fh
    elif path.is_file() and rx.search(path.name):
        with path.open("rb") as fh:
            yield path.name, fh


def _load(fh: IO[bytes]) -> Any:
    try:
        return json.load(fh)
    except ValueError, RecursionError:
        return None


def parse_chatgpt(path: Path) -> Parsed:
    """
    Read a ChatGPT data export: each conversation along the branch that was kept.

    :param path: The export zip, its unpacked folder, or ``conversations.json``
    :returns: The messages
    """
    out = Parsed([], None)
    for member, fh in _export_files(path, r"^conversations(-\d+)?\.json$"):
        data = _load(fh)
        for conv in data if isinstance(data, list) else []:
            if not isinstance(conv, dict):
                out.bad += 1
                continue
            try:
                cid = str(conv.get("conversation_id") or conv.get("id") or "")
                mapping = _dict(conv.get("mapping"))
                chain: list[tuple[str, Any]] = []
                node, seen = conv.get("current_node"), set()
                while isinstance(node, str) and node in mapping and node not in seen:
                    seen.add(node)
                    record = _dict(mapping[node])
                    chain.append((node, record.get("message")))
                    node = record.get("parent")
                chain.reverse()
                seq = 0
                for node_id, msg in chain:
                    if not isinstance(msg, dict) or not isinstance(msg.get("author"), dict):
                        continue
                    role = msg["author"].get("role")
                    parts = _list(_dict(msg.get("content")).get("parts"))
                    text = "\n\n".join(
                        p if isinstance(p, str) else str(p.get("text", "")) if isinstance(p, dict) else ""
                        for p in parts
                    ).strip()
                    if role not in ("user", "assistant") or not text:
                        continue
                    out.items.append(
                        Item(
                            "message",
                            "chatgpt",
                            None,
                            cid,
                            str(conv.get("title") or "untitled"),
                            seq,
                            str(role),
                            None,
                            role == "user",
                            _iso(msg.get("create_time") or conv.get("create_time")),
                            f"{path}#{member}/{cid}/{node_id}",
                            text,
                        )
                    )
                    seq += 1
            except BAD_RECORD:
                out.bad += 1
    return out


def parse_claude_export(path: Path) -> Parsed:
    """
    Read a Claude.ai data export: conversations, and the documents kept in projects.

    :param path: The export zip, its unpacked folder, or ``conversations.json``
    :returns: The messages and project documents
    """
    out = Parsed([], None)
    for member, fh in _export_files(path, r"^conversations\.json$"):
        data = _load(fh)
        for conv in data if isinstance(data, list) else []:
            if not isinstance(conv, dict):
                out.bad += 1
                continue
            try:
                cid = str(conv.get("uuid") or "")
                messages = _list(conv.get("chat_messages"))
                for seq, msg in enumerate(messages):
                    if not isinstance(msg, dict):
                        continue
                    role = {"human": "user", "assistant": "assistant"}.get(str(msg.get("sender")), "user")
                    text = (_texts(msg.get("content")) or str(msg.get("text") or "")).strip()
                    if text:
                        out.items.append(
                            Item(
                                "message",
                                "claude",
                                None,
                                cid,
                                str(conv.get("name") or "untitled"),
                                seq,
                                role,
                                None,
                                role == "user",
                                _iso(msg.get("created_at")),
                                f"{path}#{member}/{cid}/{seq}",
                                text,
                            )
                        )
            except BAD_RECORD:
                out.bad += 1
    for member, fh in _export_files(path, r"^projects\.json$|^[0-9a-f-]{36}\.json$"):
        data = _load(fh)
        projects = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for project in projects:
            try:
                if not isinstance(project, dict) or not isinstance(project.get("docs"), list):
                    continue
                for seq, doc in enumerate(project["docs"]):
                    if not isinstance(doc, dict):
                        continue
                    text = str(doc.get("content") or "").strip()
                    if text:
                        out.items.append(
                            Item(
                                "doc",
                                "claude",
                                str(project.get("name") or "") or None,
                                str(project.get("uuid") or ""),
                                str(doc.get("filename") or "document"),
                                seq,
                                None,
                                None,
                                False,
                                _iso(doc.get("created_at")),
                                f"{path}#{member}/{doc.get('uuid', seq)}",
                                text,
                            )
                        )
            except BAD_RECORD:
                out.bad += 1
    return out


def _export(store: Store, report: Report, kind: str, path: Path, sig: str, force: bool) -> None:
    if store.is_forgotten(kind, str(path)):
        report.forgotten += 1
        return
    mark = (fingerprint(path) if path.is_file() else _tree_mark(path)) + f"|{sig}"
    sid, old = store.source(kind, str(path), path.name)
    if old == mark and not force:
        report.unchanged += 1
        return
    try:
        parsed = parse_chatgpt(path) if kind == "chatgpt" else parse_claude_export(path)
    except (OSError, zipfile.BadZipFile, *BAD_RECORD) as exc:
        store.fail(sid, str(exc))
        report.errors.append(f"{path}: {exc}")
        return
    report.bad_records += parsed.bad
    with store.transaction():
        report.items += store.replace(sid, parsed.items, mark)
    report.read += 1


def _tree_mark(folder: Path) -> str:
    digest = hashlib.sha256()
    for member in sorted(folder.rglob("*.json")):
        digest.update(f"{member}:{fingerprint(member)}".encode())
    return digest.hexdigest()[:24]


# ---------------------------------------------------------------- git


def find_repos(path: Path, depth: int = 2) -> list[Path]:
    """
    Find git repositories at a path or up to two folders below it.

    :param path: A repository or a folder of repositories
    :param depth: How deep to look
    :returns: Repository roots
    """
    if (path / ".git").exists():
        return [path]
    if depth == 0 or not path.is_dir():
        return []
    found = []
    try:
        children = sorted(p for p in path.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)
    except OSError:
        return []
    for child in children:
        if not child.name.startswith("."):
            found += find_repos(child, depth - 1)
    return found


def parse_git(repo: Path, emails: set[str], names: set[str]) -> list[Item]:
    """
    Read a repository's history: every commit on every branch, with its files.

    Every commit prints exactly seven NUL bytes, one before each field and one after the body, and git never allows a
    NUL inside a commit, so the output splits into groups of seven fields whatever the messages hold.

    :param repo: The repository root
    :param emails: Your author emails, lowercase
    :param names: Your author names, lowercase
    :returns: The commits, oldest first
    :raises OSError: When git is not installed or the log cannot be read
    """
    git = shutil.which("git")
    if not git:
        raise OSError("git is not installed")
    fmt = "%x00%H%x00%an%x00%ae%x00%aI%x00%s%x00%b%x00"
    proc = subprocess.run(  # nosec B603: git log, fixed arguments, the repo path as a separate argument
        [git, "-C", str(repo), "log", "--all", "--no-color", "--name-only", f"--format={fmt}"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip()[:300] or f"git log failed in {repo}")
    commits = []
    tokens = proc.stdout.split("\x00")[1:]
    for i in range(0, len(tokens) - 6, 7):
        sha, name, email, when, subject, body, files_text = tokens[i : i + 7]
        files = [f for f in files_text.split("\n") if f.strip()]
        mine = email.lower() in emails or name.lower() in names
        text = subject + ("\n\n" + body.strip() if body.strip() else "")
        if files:
            text += "\n\nFiles: " + ", ".join(files[:30]) + (f" and {len(files) - 30} more" if len(files) > 30 else "")
        commits.append((when, sha, name, email, subject, text, mine))
    commits.reverse()
    return [
        Item(
            "commit",
            "git",
            str(repo),
            repo.name,
            subject,
            seq,
            None,
            f"{name} <{email}>",
            mine,
            when,
            f"{repo}@{sha[:12]}",
            text,
        )
        for seq, (when, sha, name, email, subject, text, mine) in enumerate(commits)
    ]


def _git_mark(repo: Path) -> str:
    git = shutil.which("git") or "git"
    refs = subprocess.run(  # nosec B603: git for-each-ref, fixed arguments
        [git, "-C", str(repo), "for-each-ref", "--format=%(objectname)"], capture_output=True, text=True, check=False
    ).stdout
    return hashlib.sha256(refs.encode()).hexdigest()[:24]


def _git(brain: Brain, store: Store, report: Report, path: Path, deny: list[str], sig: str, force: bool) -> None:
    emails, names = _identity(brain)
    seen = set()
    for repo in find_repos(path):
        if is_denied(repo.resolve(), deny):
            report.skipped += 1
            continue
        if store.is_forgotten("git", str(repo)):
            report.forgotten += 1
            continue
        seen.add(str(repo))
        mark = f"{_git_mark(repo)}|{sig}"
        sid, old = store.source("git", str(repo), repo.name)
        if old == mark and not force:
            report.unchanged += 1
            continue
        try:
            items = parse_git(repo, emails, names)
        except OSError as exc:
            store.fail(sid, str(exc))
            report.errors.append(f"{repo}: {exc}")
            continue
        with store.transaction():
            report.items += store.replace(sid, items, mark)
        report.read += 1
    if path.is_dir() and not (path / ".git").exists():
        report.dropped += store.drop_missing("git", seen, str(path))


# ---------------------------------------------------------------- documents and the brain


def sections(path: Path, platform: str, ref_base: str, project: str | None) -> list[Item]:
    """
    Split a text file into searchable sections: by heading for Markdown, by paragraphs otherwise.

    :param path: The file
    :param platform: ``docs`` or ``brain``
    :param ref_base: How the file is named in refs
    :param project: The folder it belongs to
    :returns: The sections, each pointing at its first line
    """
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    markdown = path.suffix.lower() in (".md", ".markdown")
    kind = "note" if platform == "brain" else "doc"
    pieces: list[tuple[int, str, list[str]]] = []
    title, start = path.stem, 1
    buf: list[str] = []
    in_code = False
    for i, line in enumerate(lines, 1):
        if markdown and line.startswith("```"):
            in_code = not in_code
        heading = re.match(r"^#{1,6} +(.+)", line) if markdown and not in_code else None
        too_long = sum(len(x) + 1 for x in buf) > MAX_SECTION and not line.strip()
        if heading or too_long:
            if any(x.strip() for x in buf):
                pieces.append((start, title, buf))
            if heading:
                title = heading.group(1).strip()
            start, buf = i, []
        buf.append(line)
    if any(x.strip() for x in buf):
        pieces.append((start, title, buf))
    return [
        Item(
            kind,
            platform,
            project,
            ref_base,
            title,
            seq,
            None,
            None,
            False,
            None,
            f"{ref_base}:{line}",
            "\n".join(body).strip(),
        )
        for seq, (line, title, body) in enumerate(pieces)
    ]


def _readable(path: Path, root: Path, deny: list[str], outside_links: bool = False) -> bool:
    """Tell whether kea may read a text file: right suffix, not hidden or too big, not denied, not linked outside."""
    if path.name.startswith(".") or path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        target = path.resolve(strict=True)
        if target.stat().st_size > MAX_DOC_BYTES:
            return False
    except OSError:
        return False
    if is_denied(path, deny) or is_denied(target, deny):
        return False
    return outside_links or not path.is_symlink() or target.is_relative_to(root.resolve())


def text_files(
    folder: Path,
    deny: list[str],
    skip: tuple[Path, ...] = (),
    outside_links: bool = False,
    refused: list[Path] | None = None,
) -> list[Path]:
    """
    List the text files under a folder, leaving out hidden, build and denied paths, and links that point outside it.

    :param folder: Where to look
    :param deny: Extra path patterns to leave out
    :param skip: Folders to leave out entirely
    :param outside_links: Accept links that point outside the folder (the brain's own files may live elsewhere)
    :param refused: Collects the text files left out for a deny rule or a link leading outside
    :returns: The files
    """
    out = []
    for root, dirs, files in os.walk(folder):
        here = Path(root)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS and (here / d) not in skip)
        for name in sorted(files):
            path = here / name
            if _readable(path, folder, deny, outside_links):
                out.append(path)
            elif refused is not None and not name.startswith(".") and path.suffix.lower() in TEXT_SUFFIXES:
                refused.append(path)
    return out


def _docs(
    store: Store, report: Report, folder: Path, deny: list[str], sig: str, force: bool, platform: str, base: Path | None
) -> None:
    if folder.is_file():
        if not _readable(folder, folder.parent, deny):
            raise ValueError(f"refused: {folder} is not a readable text file (.md, .txt, .rst, .org, not hidden)")
        files = [folder]
    elif platform == "brain":
        skip = (folder / INDEX_DIR, folder / "council" / "briefing", folder / "plans" / "_template", folder / "digests")
        found = text_files(folder, deny, skip, outside_links=True)
        files = [p for p in found if str(p.relative_to(folder)) not in BRAIN_BOILERPLATE]
    else:
        refused: list[Path] = []
        files = text_files(folder, deny, refused=refused)
        report.skipped += len(refused)
    kind = "brain" if platform == "brain" else "docs"
    seen = set()
    for path in files:
        if store.is_forgotten(kind, str(path)):
            report.forgotten += 1
            continue
        seen.add(str(path))
        mark = f"{fingerprint(path)}|{sig}"
        sid, old = store.source(kind, str(path), (base or folder).name)
        if old == mark and not force:
            report.unchanged += 1
            continue
        ref = str(path.relative_to(base)) if base and path.is_relative_to(base) else str(path)
        try:
            items = sections(path, platform, ref, str(base or folder))
        except OSError as exc:
            store.fail(sid, str(exc))
            report.errors.append(f"{path}: {exc}")
            continue
        with store.transaction():
            report.items += store.replace(sid, items, mark)
        report.read += 1
    if folder.is_dir():
        report.dropped += store.drop_missing(kind, seen, str(folder))


def refresh_brain(brain: Brain, store: Store, force: bool = False) -> Report:
    """
    Index the brain's own Markdown, so search covers STATE.md, decisions, plans and notes. Cheap when nothing changed.

    kea's own boilerplate (AGENTS.md, the templates, the protocol) is left out.

    :param brain: The brain
    :param store: Its index
    :param force: Read every file again
    :returns: The report
    """
    report = Report("brain", str(brain.root))
    _docs(store, report, brain.root, _deny(brain), _signature(brain), force, "brain", brain.root)
    return report


def ingest(
    brain: Brain, store: Store, kind: str, path: str | None = None, force: bool = False, revive: bool = True
) -> Report:
    """
    Read one source into the index and remember it, so ``kea ingest`` alone scans it again.

    Naming the exact path of a source that was forgotten brings it back.

    :param brain: The brain
    :param store: Its index
    :param kind: One of claude-code, codex, opencode, chatgpt, claude, git, docs
    :param path: What to read; the adapter's default location when omitted
    :param force: Read everything again, even what has not changed
    :param revive: Bring the named path back if it was forgotten; a rescan of known roots passes False
    :returns: The report
    :raises ValueError: When the kind is unknown, the path is missing, or the path is refused
    """
    if kind not in KINDS:
        raise ValueError(f"unknown source {kind!r}; choose from {', '.join(KINDS)}")
    target = Path(path).expanduser() if path else default_path(kind)
    if target is None:
        raise ValueError(f"{kind} needs a path: kea ingest {kind} <path>")
    deny = _deny(brain)
    for candidate in (target, target.resolve()):
        if is_denied(candidate):
            raise ValueError(f"refused: {candidate} names a credential store")
        if is_denied(candidate, deny):
            raise ValueError(f"refused: {candidate} is covered by your [ingest] deny in kea.toml")
    target = target.resolve()
    if not target.exists():
        raise ValueError(f"not found: {target}")
    if path and revive:
        store.remember(kind, str(target))
    report = Report(kind, str(target))
    sig = _signature(brain)
    if kind == "claude-code":
        files = [target] if target.is_file() else sorted(target.glob("*/*.jsonl"))
        seen = _sessions(store, report, kind, files, parse_claude_code, deny, sig, force, target)
        if target.is_dir():
            report.dropped = store.drop_missing(kind, seen, str(target))
    elif kind == "codex":
        files = [target] if target.is_file() else sorted(target.rglob("rollout-*.jsonl"))
        seen = _sessions(store, report, kind, files, parse_codex, deny, sig, force, target)
        if target.is_dir():
            report.dropped = store.drop_missing(kind, seen, str(target))
    elif kind == "opencode":
        _opencode(store, report, target, deny, sig, force)
    elif kind in ("chatgpt", "claude"):
        _export(store, report, kind, target, sig, force)
    elif kind == "git":
        _git(brain, store, report, target, deny, sig, force)
    else:
        _docs(store, report, target, deny, sig, force, "docs", None)
    store.add_root(kind, str(target))
    return report


def ingest_known(brain: Brain, store: Store, force: bool = False) -> list[Report]:
    """
    Scan the brain and every source ingested before, again. One broken source never stops the others.

    :param brain: The brain
    :param store: Its index
    :param force: Read everything again
    :returns: One report per source
    """
    return [refresh_brain(brain, store, force), *rescan(brain, store, store.roots(), force)]


def rescan(brain: Brain, store: Store, roots: list[tuple[str, str]], force: bool = False) -> list[Report]:
    """
    Scan known roots again, one report each.

    A root a deny rule now covers is dropped with everything read from it, and a root that fails is reported without
    stopping the others.

    :param brain: The brain
    :param store: Its index
    :param roots: Pairs of adapter and path, as :meth:`Store.roots` gives them
    :param force: Read everything again
    :returns: One report per root
    """
    reports = []
    deny = _deny(brain)
    for kind, path in roots:
        if is_denied(path) or is_denied(path, deny):
            reports.append(Report(kind, path, dropped=store.drop_root(kind, path)))
            continue
        try:
            reports.append(ingest(brain, store, kind, path, force, revive=False))
        except Exception as exc:  # noqa: BLE001  one broken root must not stop the rescan of the others
            reports.append(Report(kind, path, errors=[f"{type(exc).__name__}: {exc}"]))
    return reports
