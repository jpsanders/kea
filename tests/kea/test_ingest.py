import json
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from kea import ingest
from kea.brain import Brain
from kea.ingest import parse_claude_code, parse_codex
from kea.store import Store, fts_query

SECRET = "ghp_" + "a1B2c3D4" * 5


def jsonl(path: Path, rows: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return path


def claude_session(folder: Path, cwd: str = "/work/shop") -> Path:
    return jsonl(
        folder / "-work-shop" / "s1.jsonl",
        [
            {"type": "ai-title", "aiTitle": "Pricing work"},
            {
                "type": "user",
                "cwd": cwd,
                "timestamp": "2026-11-01T10:00:00Z",
                "message": {
                    "role": "user",
                    "content": "<system-reminder>noise</system-reminder>Raise the price to 4.20",
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-01T10:00:05Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Done. Token " + SECRET},
                        {"type": "tool_use", "name": "Bash", "input": {}},
                    ],
                },
            },
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "side"}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
            ["not", "a", "dict"],
        ],
    )


def test_parse_claude_code_keeps_only_prompts_and_replies(tmp_path: Path) -> None:
    items, cwd = parse_claude_code(claude_session(tmp_path))
    assert cwd == "/work/shop"
    assert [(i.role, i.mine) for i in items] == [("user", True), ("assistant", False)]
    assert items[0].body == "Raise the price to 4.20"
    assert SECRET not in items[1].body and "[REDACTED]" in items[1].body
    assert items[0].title == "Pricing work" and items[0].ref.endswith(":2")


def test_parse_codex_skips_injected_context(tmp_path: Path) -> None:
    path = jsonl(
        tmp_path / "2026" / "rollout-2026-11-01-abc.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": "/work/app", "id": "abc"}},
            {
                "type": "response_item",
                "timestamp": "2026-11-01T09:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix the failing test"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Fixed it."}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "rules"}],
                },
            },
            {"type": "response_item", "payload": {"type": "reasoning"}},
        ],
    )
    items, cwd = parse_codex(path)
    assert cwd == "/work/app" and [i.body for i in items] == ["Fix the failing test", "Fixed it."]
    assert items[0].thread == "abc" and items[0].title == "Fix the failing test"


def test_ingest_claude_code_is_incremental_and_searchable(brain: Brain, tmp_path: Path) -> None:
    root = tmp_path / "claude" / "projects"
    claude_session(root)
    with Store(brain.index_path) as store:
        first = ingest.ingest(brain, store, "claude-code", str(root))
        assert first.read == 1 and first.items == 2
        again = ingest.ingest(brain, store, "claude-code", str(root))
        assert again.read == 0 and again.unchanged == 1
        hits = store.search("price")
        assert hits and hits[0]["platform"] == "claude-code" and hits[0]["project"] == "/work/shop"
        item = store.get(hits[0]["id"])
        assert item is not None and len(store.around(item, 1)) == 2
        assert store.effort()[0]["prompts"] == 1
        assert store.effort(months=True)[0]["month"] == "2026-11"
        assert store.roots() == [("claude-code", str(root.resolve()))]
        (root / "-work-shop" / "s1.jsonl").unlink()
        gone = ingest.ingest(brain, store, "claude-code", str(root))
        assert gone.dropped == 1 and store.search("price") == []


def test_denied_project_is_never_indexed(brain: Brain, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    claude_session(root, cwd="/home/x/.ssh/keys")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "claude-code", str(root))
        assert report.skipped == 1 and store.summary()["items"] == 0
        with pytest.raises(ValueError):
            ingest.ingest(brain, store, "docs", str(tmp_path / ".ssh"))


def test_default_paths_follow_the_agents_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    assert ingest.default_path("claude-code") == tmp_path / "cc" / "projects"
    assert ingest.default_path("codex") == tmp_path / "cx" / "sessions"
    assert str(ingest.default_path("opencode")).endswith("opencode/opencode.db")
    assert ingest.default_path("docs") is None


def test_unknown_or_missing_sources_are_errors(brain: Brain, tmp_path: Path) -> None:
    with Store(brain.index_path) as store:
        with pytest.raises(ValueError):
            ingest.ingest(brain, store, "fax", None)
        with pytest.raises(ValueError):
            ingest.ingest(brain, store, "docs", None)
        with pytest.raises(ValueError):
            ingest.ingest(brain, store, "docs", str(tmp_path / "nope"))


def test_codex_and_opencode(brain: Brain, tmp_path: Path) -> None:
    jsonl(
        tmp_path / "sessions" / "2026" / "11" / "rollout-x.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Deploy the bakery site"}],
                },
            }
        ],
    )
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE session (id TEXT, title TEXT, directory TEXT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
        INSERT INTO session VALUES ('s1', 'Bakery', '/work/bakery');
        INSERT INTO message VALUES ('m1', 's1', 1762000000000, '{"role": "user"}');
        INSERT INTO message VALUES ('m2', 's1', 1762000001000, 'not json');
        INSERT INTO part VALUES ('p1', 'm1', 1, '{"type": "text", "text": "Bake sourdough tonight"}');
        INSERT INTO part VALUES ('p2', 'm2', 2, '{"type": "tool", "state": {}}');
        INSERT INTO part VALUES ('p3', 'm2', 3, 'bad');
        """)
    con.commit()
    con.close()
    with Store(brain.index_path) as store:
        assert ingest.ingest(brain, store, "codex", str(tmp_path / "sessions")).items == 1
        report = ingest.ingest(brain, store, "opencode", str(db))
        assert report.items == 1 and report.read == 1
        assert ingest.ingest(brain, store, "opencode", str(db)).unchanged == 1
        hit = store.search("sourdough")[0]
        assert hit["platform"] == "opencode" and hit["project"] == "/work/bakery" and hit["ts"].startswith("2025")
        broken = tmp_path / "broken.db"
        sqlite3.connect(broken).close()
        bad = ingest.ingest(brain, store, "opencode", str(broken))
        assert bad.errors


def chatgpt_export(path: Path) -> Path:
    conv = {
        "id": "c1",
        "title": "Espresso ratios",
        "create_time": 1762000000.5,
        "current_node": "n3",
        "mapping": {
            "n0": {"message": None, "parent": None},
            "n1": {"message": {"author": {"role": "system"}, "content": {"parts": ["sys"]}}, "parent": "n0"},
            "n2": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["What ratio for a flat white?"]},
                    "create_time": 1762000001,
                },
                "parent": "n1",
            },
            "n3": {
                "message": {"author": {"role": "assistant"}, "content": {"parts": ["One to two.", {"x": 1}]}},
                "parent": "n2",
            },
            "dead": {
                "message": {"author": {"role": "user"}, "content": {"parts": ["abandoned branch"]}},
                "parent": "n1",
            },
        },
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps([conv, "junk"]))
        z.writestr("chat.html", "<html></html>")
    return path


def claude_export(path: Path) -> Path:
    convs = [
        {
            "uuid": "u1",
            "name": "Roast profile",
            "chat_messages": [
                {"sender": "human", "text": "Light roast for filter?", "created_at": "2026-10-01T08:00:00Z"},
                {"sender": "assistant", "content": [{"type": "text", "text": "Yes, drop at first crack plus 60 s."}]},
                {"sender": "assistant", "text": ""},
            ],
        },
    ]
    projects = [{"uuid": "p1", "name": "Cafe", "docs": [{"filename": "menu.md", "content": "Flat white 4.20"}]}]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps(convs))
        z.writestr("projects.json", json.dumps(projects))
    return path


def test_chat_exports(brain: Brain, tmp_path: Path) -> None:
    with Store(brain.index_path) as store:
        gpt = ingest.ingest(brain, store, "chatgpt", str(chatgpt_export(tmp_path / "gpt.zip")))
        assert gpt.items == 2
        assert store.search("abandoned") == []
        assert store.search("flat white ratio")[0]["title"] == "Espresso ratios"
        claude = ingest.ingest(brain, store, "claude", str(claude_export(tmp_path / "claude.zip")))
        assert claude.items == 3
        kinds = {h["kind"] for h in store.search("flat white", any_word=True, limit=20)}
        assert {"message", "doc"} <= kinds
        (tmp_path / "bad.zip").write_text("not a zip")
        bad = ingest.ingest(brain, store, "chatgpt", str(tmp_path / "bad.zip"))
        assert bad.items == 0


def test_chat_export_folder_and_plain_json(brain: Brain, tmp_path: Path) -> None:
    folder = tmp_path / "unpacked"
    folder.mkdir()
    with zipfile.ZipFile(chatgpt_export(tmp_path / "g.zip")) as z:
        z.extractall(folder)
    with Store(brain.index_path) as store:
        assert ingest.ingest(brain, store, "chatgpt", str(folder)).items == 2
        assert ingest.ingest(brain, store, "chatgpt", str(folder / "conversations.json")).items == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_history_marks_my_commits(brain: Brain, tmp_path: Path) -> None:
    repo = tmp_path / "code" / "shop"
    repo.mkdir(parents=True)

    def git(*args: str, email: str = "me@example.com", name: str = "Me") -> None:
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q")
    (repo / "a.txt").write_text("a")
    git("add", ".")
    git("commit", "-q", "-m", "Add the price list", "-m", "Body text")
    (repo / "b.txt").write_text("b")
    git("add", ".")
    git("commit", "-q", "-m", "Their change", email="them@example.com", name="Them")
    toml = brain.file("kea.toml")
    toml.write_text(toml.read_text().replace("emails = []", 'emails = ["me@example.com"]'))
    brain = Brain.load(brain.root)
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "git", str(tmp_path / "code"))
        assert report.items == 2 and report.read == 1
        assert [h["title"] for h in store.search("price", mine=True)] == ["Add the price list"]
        assert store.search("change", mine=True) == []
        assert ingest.ingest(brain, store, "git", str(tmp_path / "code")).unchanged == 1
        assert ingest.find_repos(tmp_path / "nothing") == []


def test_docs_and_the_brain_itself(brain: Brain, tmp_path: Path) -> None:
    docs = tmp_path / "notes"
    (docs / "sub").mkdir(parents=True)
    (docs / "plan.md").write_text("# Plan\n\nIntro\n\n## Pricing\n\nCharge 120 an hour\n\n```\n# not a heading\n```\n")
    (docs / "sub" / "notes.txt").write_text("plain words about roasting\n\n" + "filler " * 2000)
    (docs / ".hidden.md").write_text("secret plan")
    (docs / "node_modules").mkdir()
    (docs / "node_modules" / "x.md").write_text("vendored")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "docs", str(docs))
        assert report.read == 2
        hit = store.search("charge")[0]
        assert hit["title"] == "Pricing" and hit["ref"].endswith("plan.md:5")
        assert store.search("vendored") == [] and store.search("secret plan") == []
        assert store.search("roasting")[0]["kind"] == "doc"
        first = ingest.refresh_brain(brain, store)
        assert first.read > 10
        assert ingest.refresh_brain(brain, store).read == 0
        hit = store.search("source of facts", platform="brain")[0]
        assert hit["ref"].startswith("STATE.md:") and hit["kind"] == "note"
        reports = ingest.ingest_known(brain, store)
        assert [r.kind for r in reports] == ["brain", "docs"]
        assert store.forget(kind="docs") == 2 and store.search("charge") == []
        assert store.forget() == 0


def test_fts_query_and_like_fallback(brain: Brain, tmp_path: Path) -> None:
    assert fts_query('flat "white coffee" c++') == '"flat" "white coffee" "c"'
    assert fts_query("a b", any_word=True) == '"a" OR "b"'
    assert fts_query("!!!") == ""
    with Store(brain.index_path) as store:
        ingest.refresh_brain(brain, store)
        assert store.search("!!!") == []
        store.fts = False
        hits = store.search("source of facts", since="2000-01-01")
        assert hits == [] or all(h["platform"] == "brain" for h in hits)
        assert store.search("...") == []
        summary = store.summary()
        assert summary["items"] > 0 and summary["platforms"]["brain"] == summary["items"]
