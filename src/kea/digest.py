"""
Turn a Claude Code session transcript into text a person or an agent can read, so a closed session is never lost.

Every prompt the person typed and every reply the assistant wrote stays verbatim. Tool calls and their results are
kept as a trimmed trail; images, injected reminders and long blobs are dropped. Every record carries its line number
in the raw transcript (``[L123]``), so the full record is one jump away when the trimmed form is not enough.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kea.redact import redact

BLOB = re.compile(r"[A-Za-z0-9+/=]{400,}")
REMINDER = re.compile(r"<system-reminder>(.*?)</system-reminder>", re.S)
WRAPPED = re.compile(
    r"<(local-command-caveat|local-command-stdout|command-name|command-message|command-args|task-notification)>"
    r".*?</\1>",
    re.S,
)
TOOL_LIMITS = {"Read": 300, "Edit": 300, "Write": 300, "Grep": 500, "Glob": 400, "Bash": 900, "Agent": 4000}


@dataclass
class Digest:
    """What a digest found and where it wrote it."""

    source: str
    folder: str
    prompts: int = 0
    replies: int = 0
    compactions: int = 0
    first: str | None = None
    last: str | None = None
    titles: list[str] = field(default_factory=list)
    tools: dict[str, int] = field(default_factory=dict)


def _cut(text: str, limit: int) -> str:
    text = BLOB.sub("[long blob removed]", text)
    return text if len(text) <= limit + 40 else text[:limit] + f" ...[{len(text) - limit} chars cut]"


def _local(ts: str | None, tz: ZoneInfo | None) -> str:
    if not ts:
        return ""
    try:
        moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    moment = moment.astimezone(tz) if tz else moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M %Z")


def _flat(content: Any) -> str:
    if isinstance(content, str):
        return content
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict):
            kind = block.get("type")
            out.append(str(block.get("text") or "") if kind == "text" else f"[{kind}]")
    return "\n".join(out)


def _prompt(text: str) -> str:
    text = REMINDER.sub("", text)
    return WRAPPED.sub("", text).strip()


def latest_transcript(cwd: Path, config_dir: Path | None = None) -> Path | None:
    """
    Find the newest Claude Code transcript for a project folder.

    :param cwd: The project folder the session ran in
    :param config_dir: Claude Code's config folder; ``~/.claude`` when omitted
    :returns: The transcript, or None
    """
    projects = (config_dir or Path.home() / ".claude") / "projects"
    here = str(cwd.resolve())
    for name in (re.sub(r"[^A-Za-z0-9]", "-", here), here.replace("/", "-")):
        folder = projects / name
        files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
        if files:
            return files[-1]
    return None


def digest(source: Path, out_dir: Path, label: str | None = None, tz: str | None = None) -> Digest:
    """
    Write a transcript out as four files a person or an agent can read.

    ``digest.md`` holds everything in order, ``prompts.md`` only what the person typed, ``turns.md`` each prompt with
    the assistant's closing reply, and ``meta.json`` the counts.

    :param source: The transcript, a ``.jsonl`` file
    :param out_dir: Where to write; a folder named after the label is made inside it
    :param label: The folder's name; the transcript's name when omitted
    :param tz: An IANA time zone for the times shown; the system's when omitted
    :returns: What it found and where it wrote
    """
    try:
        zone = ZoneInfo(tz) if tz else None
    except ZoneInfoNotFoundError, ValueError:
        zone = None
    folder = out_dir / (label or source.stem)
    folder.mkdir(parents=True, exist_ok=True)
    result = Digest(str(source), str(folder))
    tools: Counter[str] = Counter()
    names: dict[str, str] = {}
    records: list[tuple[str, str]] = []
    turns: list[dict[str, Any]] = []
    with source.open(encoding="utf-8", errors="replace") as fh:
        for ln, line in enumerate(fh, 1):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            kind, ts = d.get("type"), d.get("timestamp")
            if kind == "ai-title" and d.get("aiTitle") and d["aiTitle"] not in result.titles:
                result.titles.append(str(d["aiTitle"]))
                continue
            if kind == "system" and d.get("subtype") == "compact_boundary":
                result.compactions += 1
                records.append(("MARK", f"\n##### [L{ln}] CONTEXT COMPACTED {_local(ts, zone)} #####\n"))
                continue
            if kind not in ("user", "assistant"):
                continue
            if ts:
                result.first = result.first or ts
                result.last = ts
            side = "[SIDE] " if d.get("isSidechain") else ""
            content = (d.get("message") or {}).get("content")
            if kind == "user":
                if d.get("isCompactSummary"):
                    records.append(("SUMMARY", f"=== [L{ln}] COMPACT SUMMARY {_local(ts, zone)} ===\n{_flat(content)}"))
                    continue
                blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        name = names.get(str(block.get("tool_use_id")), "?")
                        body = _cut(_flat(block.get("content")), TOOL_LIMITS.get(name, 600))
                        records.append(("RESULT", f"{side}[L{ln}] RESULT {name}: {body}"))
                    elif block.get("type") == "text":
                        text = _prompt(str(block.get("text") or ""))
                        if not text:
                            continue
                        if d.get("isMeta") or side:
                            records.append(("NOTE", f"{side}[L{ln}] INJECTED: {_cut(text, 500)}"))
                            continue
                        result.prompts += 1
                        records.append(("USER", f"=== [L{ln}] USER {_local(ts, zone)} ===\n{text}"))
                        turns.append({"prompt": text, "when": _local(ts, zone), "replies": []})
                continue
            for block in content if isinstance(content, list) else [{"type": "text", "text": content or ""}]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    text = str(block["text"]).strip()
                    if side:
                        records.append(("NOTE", f"[SIDE] [L{ln}] SUBAGENT: {_cut(text, 700)}"))
                        continue
                    result.replies += 1
                    records.append(("ASSISTANT", f"=== [L{ln}] ASSISTANT {_local(ts, zone)} ===\n{text}"))
                    if turns:
                        turns[-1]["replies"].append(text)
                elif block.get("type") == "tool_use":
                    name = str(block.get("name"))
                    names[str(block.get("id"))] = name
                    tools[name] += 1
                    shown = json.dumps(block.get("input"), ensure_ascii=False)
                    records.append(("TOOL", f"{side}[L{ln}] TOOL {name}: {_cut(shown, 600)}"))
    result.tools = dict(tools.most_common())
    note = (
        "Notation: [L123] is the line in the raw transcript. USER and ASSISTANT blocks are verbatim; TOOL and RESULT "
        "lines are trimmed. Secrets shaped like keys are redacted. This folder may hold private text: keep it out of "
        "public places.\n"
    )
    spaced = {"USER", "ASSISTANT", "SUMMARY"}
    body = "\n".join(text + ("\n" if kind in spaced else "") for kind, text in records)
    (folder / "digest.md").write_text(redact(f"# Session digest: {folder.name}\n\nSource: {source}\n{note}\n{body}\n"))
    prompts = "\n\n".join(text for kind, text in records if kind == "USER")
    (folder / "prompts.md").write_text(redact(f"# Every prompt in {folder.name}, verbatim, in order\n\n{prompts}\n"))
    parts = [f"# {folder.name}: each prompt with the assistant's closing reply\n"]
    for i, turn in enumerate(turns, 1):
        last = turn["replies"][-1] if turn["replies"] else "(no reply text in this turn)"
        parts.append(f"## Turn {i}, {turn['when']}\n\n{turn['prompt']}\n\n**Reply:**\n\n{last}\n")
    (folder / "turns.md").write_text(redact("\n".join(parts)))
    meta = dict(result.__dict__)
    (folder / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n")
    return result
