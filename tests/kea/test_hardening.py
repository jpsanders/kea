"""Regression tests for the findings of the three independent reviews made before the 1.0.0 release."""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from kea import cli, council, ingest, ledger, mcp, providers
from kea.brain import Brain, BrainError, default_brain, init, set_default_brain
from kea.markdown import clean_part, read_lines
from kea.providers import ProviderError, Seat
from kea.redact import has_secret, is_denied, redact
from kea.store import Item, Store

Run = Callable[..., tuple[int, str, str]]

# ---------------------------------------------------------------- secrets and paths


@pytest.mark.parametrize(
    "text",
    [
        "export MISTRAL_API_KEY=Xk3m9Qp2Lr8Tz5Wv1Ny7Bc4Hd6Fj0Gs",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "DB_PASSWORD=correct-horse-battery",
        "SECRET_KEY = 'django-insecure-abcdef123456'",
        "CEREBRAS_API_KEY=csk-abcdefghijklmnopqrstuvwxyz123",
        "token xai-abcdefghijklmnopqrstuvwxyz0123",
        "refresh_token: 1//0gAbCdEfGhIjKlMnOpQrStUv",
        "Authorization: Basic dXNlcjpwYXNzd29yZGxvbmdlbm91Z2g=",
    ],
)
def test_env_style_and_provider_secrets_are_redacted(text: str) -> None:
    out = redact(text)
    assert "[REDACTED]" in out and has_secret(text)
    value = text.replace("=", " ").replace(":", " ").split()[-1].strip("'")
    assert value not in out


def test_url_passwords_and_pgp_blocks() -> None:
    assert redact("postgres://admin:S3cretPass@db:5432/x") == "postgres://admin:[REDACTED]@db:5432/x"
    block = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nabc\n-----END PGP PRIVATE KEY BLOCK-----"
    assert redact(f"a {block} b") == "a [REDACTED] b"
    for prose in ("author: Ada", "Tokens: 12,345", "commit 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"):
        assert redact(prose) == prose


@pytest.mark.parametrize(
    "path",
    ["/h/.env.local", "/h/.zsh_history", "/h/.config/gcloud/adc.json", "/h/.git-credentials", "/h/k/server.pem"],
)
def test_credential_files_are_denied(path: str) -> None:
    assert is_denied(path)


def test_ordinary_paths_are_not_denied() -> None:
    assert not is_denied("/h/notes/project_history/a.md") and not is_denied("/h/a/environment.md")


def test_single_file_ingest_refuses_hidden_and_foreign_files(brain: Brain, tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("API_KEY=abcdefghijkl")
    (tmp_path / ".notes.md").write_text("hidden")
    (tmp_path / "data.json").write_text("{}")
    with Store(brain.index_path) as store:
        for name in (".env.local", ".notes.md", "data.json"):
            with pytest.raises(ValueError):
                ingest.ingest(brain, store, "docs", str(tmp_path / name))


def test_symlinks_to_credentials_or_outside_are_skipped(brain: Brain, tmp_path: Path) -> None:
    secrets = tmp_path / ".aws"
    secrets.mkdir()
    (secrets / "credentials.md").write_text("aws_secret_access_key = abcdefghijklmnop")
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("outside words")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "real.md").write_text("inside words")
    os.symlink(secrets / "credentials.md", notes / "aws-setup.md")
    os.symlink(elsewhere, notes / "link.md")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "docs", str(notes))
        assert report.read == 1
        assert store.search("inside") and not store.search("outside") and not store.search("aws_secret_access_key")


def test_titles_and_projects_are_redacted_in_the_store(brain: Brain, tmp_path: Path) -> None:
    key = "ghp_" + "a1B2c3D4" * 5
    item = Item("doc", "docs", f"/p/{key}", "t", f"Rotate {key}", 0, None, None, False, None, "r", "body")
    with Store(brain.index_path) as store:
        sid, _ = store.source("docs", "/x")
        store.replace(sid, [item], "f")
        row = store.db.execute("SELECT title, project FROM items").fetchone()
        assert key not in row["title"] and key not in row["project"]


def test_the_index_is_private(brain: Brain) -> None:
    with Store(brain.index_path):
        pass
    assert oct(brain.index_path.stat().st_mode & 0o777) == "0o600"
    assert oct(brain.index_path.parent.stat().st_mode & 0o777) == "0o700"


# ---------------------------------------------------------------- forget, deny, robustness


def session(folder: Path, name: str, cwd: str, words: str) -> Path:
    path = folder / "-proj" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"type": "user", "cwd": cwd, "message": {"content": words}}
    path.write_text(json.dumps(row) + "\n")
    return path


def test_forget_survives_a_rescan_until_named_again(brain: Brain, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    one = session(root, "a", "/work/a", "secret recipe")
    session(root, "b", "/work/b", "public notes")
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "claude-code", str(root))
        sid = next(s["id"] for s in store.sources() if s["path"] == str(one))
        assert store.forget(sid) == 1
        ingest.ingest_known(brain, store)
        assert not store.search("recipe")
        ingest.ingest(brain, store, "claude-code", str(one))
        assert store.search("recipe")


def test_a_new_deny_rule_purges_what_was_indexed(brain: Brain, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    session(root, "a", "/work/clientx", "client roadmap")
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "claude-code", str(root))
        assert store.search("roadmap", platform="claude-code")
        toml = brain.file("kea.toml")
        toml.write_text(toml.read_text().replace("deny = []", 'deny = ["clientx"]'))
        ingest.ingest(Brain.load(brain.root), store, "claude-code", str(root))
        assert not store.search("roadmap", platform="claude-code")


def test_malformed_records_are_counted_not_fatal(brain: Brain, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    bad = root / "-p" / "bad.jsonl"
    bad.parent.mkdir(parents=True)
    rows = [{"type": "user", "cwd": 7, "message": {"content": "fine words"}}, {"type": "user", "message": "odd"}]
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + "[" * 100000 + "\n")
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps([{"mapping": {"n": "x"}, "current_node": ["unhashable"]}, {"chat": 1}, 5]))
    with Store(brain.index_path) as store:
        assert ingest.ingest(brain, store, "claude-code", str(root)).items == 1
        report = ingest.ingest(brain, store, "chatgpt", str(export))
        assert report.items == 0 and not report.errors
        assert ingest.ingest(brain, store, "claude", str(export)).bad_records >= 1


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_messages_with_separator_bytes_and_empty_bodies(brain: Brain, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@x"}
    env |= {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q")
    for i, message in enumerate(["plain subject", "odd \x1e\x1f\x1d bytes", "with body\n\nbody text"]):
        (repo / f"f{i}.txt").write_text(str(i))
        git("add", ".")
        git("commit", "-q", "-m", message)
    items = ingest.parse_git(repo, set(), set())
    assert [i.title for i in items] == ["plain subject", "odd \x1e\x1f\x1d bytes", "with body"]
    assert "body text" in items[2].body and "f2.txt" in items[2].body


# ---------------------------------------------------------------- the ledger keeps what people wrote


def test_revisit_and_score_append_instead_of_overwriting(brain: Brain) -> None:
    ledger.decide(brain, "Supplier", "Use Bob", why="cheapest", revisit="+30d")
    path = brain.file("DECISIONS.md")
    text = path.read_text().replace(
        "- **Why:** cheapest",
        "- **Why:** cheapest\n  - by 12%\n  - delivers in 2 days\n- **Note:** Bob negotiated\n- **Note:** second note",
    )
    path.write_text(text)
    ledger.revisit(brain, "1", "Fine so far", next_revisit="+90d", lesson="Ask early")
    ledger.revisit(brain, "1", "Quiet in December", lesson="Ask twice")
    out = path.read_text()
    assert "  - by 12%" in out and "Bob negotiated" in out and "second note" in out
    assert "- **Outcome:** Fine so far" in out and "- **Outcome:** (2026-11-13) Quiet in December" in out
    assert "- **Lesson:** Ask early" in out and "(2026-11-13) Ask twice" in out
    assert ledger.decisions(brain)[0]["outcome"] == "(2026-11-13) Quiet in December"
    ledger.predict(brain, "It works", "+3d", 50)
    ppath = brain.file("PREDICTIONS.md")
    ppath.write_text(ppath.read_text().replace("- **Status:** OPEN", "- **Status:** OPEN\n- **Note:** mine"))
    ledger.score(brain, "1", "true", note="it did")
    assert "- **Note:** mine" in ppath.read_text() and "(2026-11-13) it did" in ppath.read_text()


def test_separators_in_titles_and_claims(brain: Brain) -> None:
    assert clean_part("a · b | c") == "a, b, c"
    p = ledger.predict(brain, "Sales | margin · both up", "+3d", 50)
    assert p["claim"] == "Sales, margin, both up"
    assert ledger.predictions(brain)[0]["claim"] == "Sales, margin, both up"


def test_facts_go_into_the_first_table_and_keep_extra_columns(brain: Brain) -> None:
    state = brain.file("STATE.md")
    text = state.read_text().replace(
        "| --- | --- | --- | --- |",
        "| --- | --- | --- | --- |\n| Rent | 900 | lease | 2026-11-01 | monthly |\n\n"
        "Old figures:\n\n| a | b |\n| - | - |",
    )
    state.write_text(text)
    ledger.set_fact(brain, "Staff", "3", "rota")
    ledger.set_fact(brain, "Rent", "950", "lease")
    facts = {f.fact: f for f in ledger.facts(brain)}
    assert set(facts) == {"Rent", "Staff"}
    assert "| Rent | 950 | lease | 2026-11-13 | monthly |" in state.read_text()


def test_roadmap_add_keeps_sub_points_with_their_item(brain: Brain) -> None:
    path = brain.file("ROADMAP.md")
    text = path.read_text().replace(
        "- [ ] Make past AI sessions searchable",
        "- [ ] Parent item\n  - detail one\n\n  continued detail\n- [ ] Make past AI sessions searchable",
    )
    path.write_text(text)
    ledger.roadmap_add(brain, "Newest", "next")
    lines = read_lines(path)
    newest = next(i for i, ln in enumerate(lines) if "Newest" in ln)
    assert lines[newest - 1].startswith("- [ ] Teach your agents")
    detail = next(i for i, ln in enumerate(lines) if "continued detail" in ln)
    assert detail < newest
    with pytest.raises(ledger.LedgerError):
        ledger.roadmap_done(brain, "  ")


def test_impossible_log_dates_are_reported_not_fatal(brain: Brain) -> None:
    state = brain.file("STATE.md")
    state.write_text(state.read_text() + "\n### 2026-11-31 09:00 · Typo date\n\nText\n")
    findings = [f for f in ledger.check(brain) if "does not exist" in f.message]
    assert findings and findings[0].level == "error"
    assert ledger.status(brain)["state"]["bad_dates"]


def test_line_endings_permissions_and_symlinks_survive_writes(brain: Brain, tmp_path: Path) -> None:
    ideas = brain.file("IDEAS.md")
    ideas.write_bytes(ideas.read_bytes().replace(b"\n", b"\r\n") + "line with a separator\r\n".encode())
    ideas.chmod(0o640)
    ledger.idea(brain, "Keep endings")
    raw = ideas.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert "line with a separator" in raw.decode()
    assert oct(ideas.stat().st_mode & 0o777) == "0o640"
    real = tmp_path / "real-ideas.md"
    shutil.move(ideas, real)
    os.symlink(real, ideas)
    ledger.idea(brain, "Through the link")
    assert ideas.is_symlink() and "Through the link" in real.read_text()


def test_unverified_counts_only_facts_and_log(brain: Brain) -> None:
    assert ledger.state(brain)["unverified"] == 0
    ledger.set_fact(brain, "Hours", "UNVERIFIED: about 40", "guess")
    assert ledger.state(brain)["unverified"] == 1


def test_missing_files_point_at_a_safe_restore(brain: Brain) -> None:
    brain.file("ROADMAP.md").unlink()
    with pytest.raises(ledger.LedgerError, match="puts back missing files"):
        ledger.roadmap_add(brain, "x")
    ledger.decide(brain, "Keep me", "Kept")
    init(brain.root, git=False)
    assert brain.file("ROADMAP.md").is_file() and "Keep me" in brain.file("DECISIONS.md").read_text()


def test_force_keeps_a_backup(brain: Brain) -> None:
    ledger.decide(brain, "Keep me", "Kept")
    init(brain.root, force=True, git=False)
    backups = list(brain.file(".kea").glob("backup-*/DECISIONS.md"))
    assert backups and "Keep me" in backups[0].read_text()


# ---------------------------------------------------------------- finding the brain


def test_init_refuses_home_and_git_inits_only_new_folders(tmp_path: Path) -> None:
    with pytest.raises(BrainError):
        init(Path.home())
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "old.md").write_text("mine")
    init(folder, git=True)
    assert not (folder / ".git").exists()
    init(tmp_path / "new", git=True)
    assert (tmp_path / "new" / ".git").exists() or shutil.which("git") is None


def test_stale_default_is_ignored_and_mcp_prefers_the_default(tmp_path: Path) -> None:
    home_brain, project = tmp_path / "brain", tmp_path / "project"
    init(home_brain, git=False)
    init(project, git=False)
    set_default_brain(tmp_path / "gone")
    assert default_brain() is None
    set_default_brain(home_brain)
    assert Brain.locate(cwd=project).root == project.resolve()
    assert Brain.locate(cwd=project, prefer_default=True).root == home_brain.resolve()


def test_example_does_not_take_the_default(run: Run, tmp_path: Path) -> None:
    run("init", str(tmp_path / "demo"), "--example", "--no-git")
    assert default_brain() is None
    code, out, _ = run("init", str(tmp_path / "real"), "--no-git")
    assert "now your default brain" in out and default_brain() == (tmp_path / "real").resolve()


# ---------------------------------------------------------------- the council


@pytest.fixture
def tricky_server() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        seen: list[dict[str, str]] = []

        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            Handler.seen.append({k.lower(): v for k, v in self.headers.items()})
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://example.com/steal")
                self.end_headers()
                return
            body = b'{"choices": ["not a dict"]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_redirects_are_refused_and_odd_shapes_are_seat_errors(
    tricky_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kea.providers.time.sleep", lambda s: None)
    monkeypatch.setenv("K", "secret-key")
    redirect = Seat.from_config({"name": "r", "base_url": tricky_server + "/redirect", "model": "m", "key_env": "K"})
    with pytest.raises(ProviderError, match="HTTP 302"):
        providers.complete(redirect, "s", "u", retries=1)
    odd = Seat.from_config({"name": "o", "base_url": tricky_server, "model": "m"})
    with pytest.raises(ProviderError, match="unexpected response shape"):
        providers.complete(odd, "s", "u", retries=1)


def test_seat_names_hosts_and_locality() -> None:
    with pytest.raises(ProviderError):
        Seat.from_config({"name": "bad_name", "provider": "ollama", "model": "m"})
    cloud = Seat.from_config({"name": "c", "provider": "ollama", "model": "gpt-oss:120b-cloud"})
    relay = Seat.from_config({"name": "r", "provider": "ollama", "model": "qwen3", "remote": True})
    here = Seat.from_config({"name": "h", "provider": "ollama", "model": "qwen3"})
    assert not cloud.stays_here and not relay.stays_here and here.stays_here and here.host == "localhost:11434"
    custom = Seat.from_config({"name": "x", "provider": "openai", "base_url": "https://evil/v1", "model": "m"})
    keyed = Seat.from_config({"name": "y", "base_url": "https://evil/v1", "model": "m", "key_env": "OPENAI_API_KEY"})
    assert custom.custom and custom.key_env is None and keyed.custom and keyed.key_env == "OPENAI_API_KEY"


def test_the_packet_is_confined_and_redacted(brain: Brain, tmp_path: Path) -> None:
    (tmp_path / "outside.md").write_text("outside secret")
    toml = brain.file("kea.toml")
    toml.write_text(
        toml.read_text().replace('include = ["STATE.md"', 'include = ["../outside.md", ".kea/x", "STATE.md"')
    )
    logged = ledger.log(brain, "New key is sk-proj-" + "a" * 30)
    assert "sk-proj-aaaa" not in brain.file("STATE.md").read_text() and "[REDACTED]" in logged["text"]
    state = brain.file("STATE.md")
    state.write_text(state.read_text() + "\nHand written: ghp_" + "b2C3d4E5" * 5 + "\n")
    council.plan_new(brain, "p")
    packet = council.brief(Brain.load(brain.root), "p")
    text = packet.path.read_text()
    assert "outside secret" not in text and "ghp_b2C3" not in text
    assert packet.refused == ["../outside.md", ".kea/x"] and packet.redacted == 1


def test_one_crashing_seat_never_costs_the_others(brain: Brain) -> None:
    brain.file("council", "roster.toml").write_text(
        '[[seat]]\nname = "a"\nprovider = "lmstudio"\nmodel = "m"\n\n[[seat]]\nname = "b"\nprovider = "lmstudio"\n'
        'model = "m"\n'
    )
    council.plan_new(brain, "p")
    council.brief(brain, "p")

    def ask(seat: Seat, system: str, user: str) -> str:
        if seat.name == "b":
            raise AttributeError("'str' object has no attribute 'get'")
        return "fine"

    import kea.council as module

    original = module._ready
    module._ready = lambda seat: True
    try:
        manifest = council.run(brain, "p", ask=ask)
    finally:
        module._ready = original
    assert {r["seat"]: r["ok"] for r in manifest["results"]} == {"a": True, "b": False}
    assert brain.file("plans", "p", "reviews", "manifest.json").is_file()


def test_review_parsing_takes_the_last_block_and_synth_survives_bad_types(brain: Brain) -> None:
    text = (
        '<!--COUNCIL-JSON {"seat": "<your seat>", "verdict": PROCEED|STOP} COUNCIL-JSON-->\n'
        '<!--COUNCIL-JSON {"seat": "real", "verdict": "STOP"} COUNCIL-JSON-->'
    )
    assert council.parse_review(text) == {"seat": "real", "verdict": "STOP"}
    council.plan_new(brain, "p")
    reviews = brain.file("plans", "p", "reviews")
    (reviews / "2026-11-13_a_m.md").write_text(
        '<!--COUNCIL-JSON {"seat": "a", "recommendations": [{"claim": 42}, {"claim": "Real", "confidence": "high"}]}'
        " COUNCIL-JSON-->"
    )
    (reviews / "2026-11-13_b_m.md").write_text('<!--COUNCIL-JSON {"seat": "b", "recommendations": 5} COUNCIL-JSON-->')
    out = council.synth(brain, "p")
    assert out["parsed"] == 1 and out["clusters"] == 1 and out["unparsed"] == ["2026-11-13_b_m.md"]


def test_slug_rejects_a_trailing_newline() -> None:
    with pytest.raises(council.CouncilError):
        council.check_slug("plan\n")


def test_new_plans_carry_their_own_date(brain: Brain) -> None:
    council.plan_new(brain, "p")
    assert "**Date:** 2026-11-13" in brain.file("plans", "p", "00-proposal.md").read_text()


# ---------------------------------------------------------------- the MCP server


def test_mcp_validates_arguments_and_caps_output(example: Brain) -> None:
    server = mcp.Server(str(example.root))

    def call(name: str, **args: Any) -> dict[str, Any]:
        reply = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )
        assert reply is not None
        return reply

    assert "must be a integer" in call("search", query="x", limit="5")["error"]["message"]
    assert "from 1 to 50" in call("search", query="x", limit=-1)["error"]["message"]
    assert "one of" in call("search", query="x", kind="nope")["error"]["message"]
    assert "list of strings" in call("council_run", slug="p", confirm=True, seats=[1])["error"]["message"]
    old = mcp.MAX_TEXT
    mcp.MAX_TEXT = 50
    try:
        result = call("brain_status")["result"]
        assert "structuredContent" not in result and result["content"][0]["text"].endswith("limit)")
    finally:
        mcp.MAX_TEXT = old


def test_mcp_survives_surrogates_and_deep_nesting(example: Brain) -> None:
    lines = [
        '{"jsonrpc": "2.0", "id": "\\ud83d", "method": "ping"}',
        "[" * 100000,
        '{"jsonrpc": "2.0", "id": 2, "method": "ping"}',
    ]
    out = io.StringIO()
    mcp.serve(mcp.Server(str(example.root)), io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(replies) == 3 and replies[-1]["id"] == 2


def test_mcp_council_runs_only_local_seats_unless_allowed(example: Brain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    server = mcp.Server(str(example.root))
    council.brief(example, "second-cafe")
    args = {"slug": "second-cafe", "confirm": True}
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "council_run", "arguments": args}}
    )
    assert reply is not None and reply["result"]["isError"]
    assert "agents_may_send" in reply["result"]["content"][0]["text"]


def test_mcp_ingest_only_rescans_what_the_user_added(example: Brain, tmp_path: Path) -> None:
    server = mcp.Server(str(example.root))
    secret = tmp_path / "notes"
    secret.mkdir()
    args = {"source": "docs", "path": str(secret)}
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ingest", "arguments": args}}
    )
    assert (
        reply is not None and reply["result"]["isError"] and "the user added" in reply["result"]["content"][0]["text"]
    )
    with Store(example.index_path) as store:
        ingest.ingest(example, store, "docs", str(secret))
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ingest", "arguments": args}}
    )
    assert reply is not None and not reply["result"]["isError"]


def test_mcp_initialize_names_the_brain(example: Brain) -> None:
    reply = mcp.Server(str(example.root)).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert reply is not None and "Tidewater Coffee" in reply["result"]["instructions"]


# ---------------------------------------------------------------- the command line


def test_digest_goes_to_the_brain_or_refuses_a_git_tree(run: Run, example: Brain, tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n")
    code, _, _ = run("--brain", str(example.root), "digest", str(transcript))
    assert code == 0 and (example.root / "digests" / "s" / "digest.md").is_file()
    assert oct((example.root / "digests" / "s" / "digest.md").stat().st_mode & 0o777) == "0o600"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    os.chdir(repo)
    code, _, err = run("digest", str(transcript))
    assert code == 1 and "git working tree" in err


def test_plan_new_without_a_slug_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as done:
        cli.main(["plan", "new"])
    assert done.value.code == 2


def test_a_damaged_index_gives_one_clear_line(run: Run, example: Brain) -> None:
    example.index_path.parent.mkdir(exist_ok=True)
    example.index_path.write_bytes(b"not a database at all" * 100)
    code, out, err = run("--brain", str(example.root), "search", "x", "--json")
    assert code == 1 and "delete the brain's .kea folder" in err and json.loads(out)["error"]


def test_since_accepts_offsets_and_rejects_nonsense(run: Run, example: Brain) -> None:
    code, _, _ = run("--brain", str(example.root), "search", "wholesale", "--since", "-30d")
    assert code == 0
    code, _, err = run("--brain", str(example.root), "search", "wholesale", "--since", "lately")
    assert code == 1 and "--since takes a date" in err


def test_predictions_without_a_confidence_still_list(run: Run, brain: Brain) -> None:
    path = brain.file("PREDICTIONS.md")
    path.write_text(path.read_text() + "\n### P-0001 · by 2026-12-01 · lots · A claim\n\n- **Status:** OPEN\n")
    code, out, _ = run("--brain", str(brain.root), "predictions")
    assert code == 0 and "?%" in out


def test_terminal_control_characters_are_stripped(run: Run, brain: Brain, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "x.md").write_text("# Title\x1b]0;pwned\x07\n\nescape \x1b[2J words\n")
    run("--brain", str(brain.root), "ingest", "docs", str(notes))
    code, out, _ = run("--brain", str(brain.root), "search", "escape")
    assert code == 0 and "\x1b" not in out and "\x07" not in out


def test_probe_of_a_custom_endpoint_with_a_key_asks_first(
    run: Run, brain: Brain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_KEY", "k")
    brain.file("council", "roster.toml").write_text(
        '[[seat]]\nname = "x"\nprovider = "openai"\nbase_url = "https://example.invalid/v1"\nmodel = "m"\n'
        'key_env = "MY_KEY"\n'
    )
    code, _, err = run("--brain", str(brain.root), "seats", "--probe")
    assert code == 1 and "custom endpoints" in err
    code, out, _ = run("--brain", str(brain.root), "council", "p", "--dry-run")
    assert "example.invalid" in out and "custom endpoint" in out


def test_sqlite_tombstones_table_exists(brain: Brain) -> None:
    with Store(brain.index_path) as store:
        names = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"forgotten", "roots", "sources", "items"} <= names
    assert isinstance(sqlite3.sqlite_version, str)
