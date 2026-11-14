import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kea import cli, ingest
from kea.brain import Brain

Run = Callable[..., tuple[int, str, str]]


def test_version_and_help(run: Run) -> None:
    with pytest.raises(SystemExit) as done:
        cli.main(["--version"])
    assert done.value.code == 0
    code, out, _ = run()
    assert code == 0 and "no brain yet" in out


def test_init_sets_the_default_and_status_works_anywhere(run: Run, tmp_path: Path) -> None:
    code, out, _ = run("init", str(tmp_path / "b"), "--owner", "Ada", "--no-git")
    assert code == 0 and "made a brain" in out and "default brain" in out
    code, out, _ = run("status")
    assert code == 0 and "health" in out and "waiting for Ada" in out
    code, out, _ = run("init", str(tmp_path / "c"), "--no-git")
    assert "now your default brain" not in out
    (tmp_path / "b" / "IDEAS.md").unlink()
    code, out, _ = run("init", str(tmp_path / "b"), "--no-git")
    assert code == 0 and "put back 1 missing file" in out and (tmp_path / "b" / "IDEAS.md").is_file()


def test_example_status_and_listings(run: Run, run_json: Callable[..., Any], example: Brain) -> None:
    root = str(example.root)
    code, out, _ = run("--brain", root, "status")
    assert "Tidewater Coffee" in out and "D-0002" in out and "3 of 6 checks need attention" in out
    data = run_json("--brain", root, "status")
    assert data["predictions"]["due"] == ["P-0007"] and data["evidence"] is None
    for command in ("state", "decisions", "predictions", "calibration", "ideas", "roadmap", "plan", "seats"):
        code, out, err = run("--brain", root, command)
        assert code == 0 and out, (command, err)
    assert run_json("--brain", root, "calibration")["scored"] == 6
    assert run_json("--brain", root, "decisions", "--all")[2]["status"] == "COMPLETED"
    assert len(run_json("--brain", root, "predictions", "--open")) == 3
    code, out, _ = run("--brain", root, "check")
    assert code == 0 and "warn" in out
    code, out, _ = run("--brain", root, "check", "--strict")
    assert code == 1


def test_ledger_writes(run_json: Callable[..., Any], brain: Brain) -> None:
    b = ("--brain", str(brain.root))
    assert run_json(*b, "log", "Opened", "the", "shop.", "--source", "photo")["title"] == "Opened the shop"
    assert run_json(*b, "fact", "Rent", "EUR 900", "--source", "lease")["new"] is True
    assert run_json(*b, "decide", "Go", "--decision", "Open on Mondays", "--revisit", "+7d")["id"] == "D-0001"
    assert run_json(*b, "revisit", "1", "--outcome", "Busy", "--surprise", "2", "--next", "+30d")["revisit"]
    assert run_json(*b, "predict", "Ten", "covers", "--by", "+7d", "--confidence", "70")["id"] == "P-0001"
    assert run_json(*b, "score", "P-0001", "true")["status"] == "TRUE"
    assert run_json(*b, "idea", "A", "newsletter")["id"] == "I-0001"
    assert run_json(*b, "roadmap", "add", "Ask", "the", "bank", "--waiting")["section"] == "waiting"
    assert run_json(*b, "roadmap", "add", "Backup", "--running")["section"] == "running"
    assert run_json(*b, "roadmap", "done", "bank", "--proof", "asked")["proof"] == "asked"
    assert run_json(*b, "state", "--entries", "1")["facts"][0]["fact"] == "Rent"
    assert run_json(*b, "plan", "new", "go")["plan"] == "go"
    assert run_json(*b, "plan")[0]["stage"] == "proposal"
    assert run_json(*b, "brief", "go")["hash"]


def test_errors_print_json_and_exit_1(run: Run, brain: Brain) -> None:
    code, out, err = run("--brain", str(brain.root), "score", "P-0042", "true", "--json")
    assert code == 1 and json.loads(out)["error"] == "no entry P-0042" and "kea: no entry" in err
    code, _, err = run("--brain", str(brain.root), "predict", "x", "--by", "whenever", "--confidence", "5")
    assert code == 1 and "not a date" in err
    code, _, err = run("--brain", "/nowhere/at/all", "status")
    assert code == 1 and "not a kea brain" in err
    code, _, err = run("--brain", str(brain.root), "forget")
    assert code == 1 and "name a source" in err
    code, _, err = run("--brain", str(brain.root), "show", "12345")
    assert code == 1 and "no item" in err
    with pytest.raises(SystemExit) as bad:
        cli.main(["predict", "x", "--by", "+1d", "--confidence", "500"])
    assert bad.value.code == 2


def test_council_commands(run: Run, run_json: Callable[..., Any], example: Brain) -> None:
    b = ("--brain", str(example.root))
    assert run_json(*b, "brief", "second-cafe")["frameworks"] == ["constraints"]
    dry = run_json(*b, "council", "second-cafe", "--dry-run")
    assert dry["packet_bytes"] > 1000 and dry["seats"] == []
    code, _, err = run(*b, "council", "second-cafe")
    assert code == 1 and "no seat can run" in err
    assert run_json(*b, "synth", "second-cafe")["strong"] == 1
    code, out, _ = run(*b, "synth", "second-cafe")
    assert "3 of 3 reviews parsed" in out


def test_council_asks_before_sending(run: Run, example: Brain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b = ("--brain", str(example.root))
    run(*b, "brief", "second-cafe")
    code, _, err = run(*b, "council", "second-cafe")
    assert code == 1 and "add --yes" in err


def test_search_ingest_show_sources_forget_and_effort(
    run: Run, run_json: Callable[..., Any], example: Brain, tmp_path: Path
) -> None:
    b = ("--brain", str(example.root))
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "beans.md").write_text("# Beans\n\nThe Ethiopian lot sells out by Friday.\n")
    report = run_json(*b, "ingest", "docs", str(notes))
    assert report["reports"][0]["items"] == 1
    hits = run_json(*b, "search", "Ethiopian", "lot")["hits"]
    assert hits[0]["platform"] == "docs"
    code, out, _ = run(*b, "search", "Ethiopian")
    assert code == 0 and "beans.md" in out
    shown = run_json(*b, "show", str(hits[0]["id"]), "--context", "1")
    assert shown["item"]["source_kind"] == "docs"
    code, out, _ = run(*b, "show", str(hits[0]["id"]))
    assert "sells out" in out
    sources = run_json(*b, "sources")
    assert sources["roots"][0]["kind"] == "docs"
    code, out, _ = run(*b, "sources")
    assert "docs" in out
    code, out, _ = run(*b, "ingest")
    assert code == 0 and "brain" in out
    assert run_json(*b, "effort") == []
    code, out, _ = run(*b, "effort")
    assert "no prompts indexed" in out
    code, out, _ = run(*b, "search", "zzzqqq")
    assert "no matches" in out
    assert run_json(*b, "forget", "--kind", "docs")["forgotten"] == 1


def test_digest_command(run: Run, tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "hello there"}}) + "\n")
    code, out, _ = run("digest", str(transcript), "-o", str(tmp_path / "d"))
    assert code == 0 and "1 prompts" in out and (tmp_path / "d" / "s" / "digest.md").is_file()
    code, _, err = run("digest")
    assert code == 1 and "name a transcript" in err


def test_skill_guide_and_man(run: Run, tmp_path: Path) -> None:
    code, out, _ = run("skill", "show")
    assert out.startswith("---\nname: kea")
    code, out, _ = run("skill", "list")
    assert out.split() == ["kea", "kea-council", "kea-frameworks"]
    code, out, _ = run("skill", "install", "--agent", "claude", "--dry-run")
    assert "would write" in out
    code, out, _ = run("skill", "install")
    assert "no agent folders found" in out
    code, out, _ = run("guide")
    assert out.startswith("# kea") and "## COMMANDS" in out
    code, out, _ = run("man", "--raw")
    assert out.startswith(".TH KEA 1")
    code, out, _ = run("man", "--install")
    assert code == 0 and (Path.home() / ".local" / "share" / "man" / "man1" / "kea.1").is_file()


def test_mcp_command_serves_stdio(example: Brain, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"))
    assert cli.main(["--brain", str(example.root), "mcp"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == 7


def test_the_cli_knows_every_ingest_source() -> None:
    assert tuple(cli.INGEST_KINDS) == ingest.KINDS
