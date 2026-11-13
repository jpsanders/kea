import json
import os
from pathlib import Path

from kea.digest import digest, latest_transcript


def write(path: Path) -> Path:
    rows = [
        {"type": "ai-title", "aiTitle": "Fix the build"},
        {
            "type": "user",
            "timestamp": "2026-11-13T09:00:00Z",
            "message": {"content": "<system-reminder>x</system-reminder>Please fix the build"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-11-13T09:00:10Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Looking."},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make"}},
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok " * 500}]},
        },
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-13T09:05:00Z"},
        {"type": "user", "isCompactSummary": True, "message": {"content": "Summary so far"}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "side work"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed. Key ghp_" + "x1" * 20}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n")
    return path


def test_digest_writes_four_files(tmp_path: Path) -> None:
    result = digest(write(tmp_path / "abc.jsonl"), tmp_path / "out", tz="Europe/London")
    folder = tmp_path / "out" / "abc"
    assert result.prompts == 1 and result.replies == 2 and result.compactions == 1
    assert result.titles == ["Fix the build"] and result.tools == {"Bash": 1}
    text = (folder / "digest.md").read_text()
    assert "Please fix the build" in text and "TOOL Bash" in text and "chars cut" in text
    assert "COMPACT SUMMARY" in text and "SUBAGENT" in text and "ghp_" not in text
    assert "GMT" in text or "BST" in text
    turns = (folder / "turns.md").read_text()
    assert "## Turn 1" in turns and "Fixed." in turns
    assert json.loads((folder / "meta.json").read_text())["prompts"] == 1
    assert "Please fix the build" in (folder / "prompts.md").read_text()
    again = digest(write(tmp_path / "abc.jsonl"), tmp_path / "out", label="named", tz="Bad/Zone")
    assert again.folder.endswith("named")


def test_latest_transcript(tmp_path: Path) -> None:
    project = tmp_path / "work" / "app"
    project.mkdir(parents=True)
    folder = tmp_path / "cc" / "projects" / str(project).replace("/", "-")
    folder.mkdir(parents=True)
    old, new = folder / "a.jsonl", folder / "b.jsonl"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1, 1))
    assert latest_transcript(project, tmp_path / "cc") == new
    assert latest_transcript(tmp_path, tmp_path / "cc") is None
