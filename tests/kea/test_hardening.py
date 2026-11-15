"""Regression tests for the findings of the reviews and verification rounds made before the 1.0.0 release."""

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
from kea.markdown import clean_part, read_lines, write_lines
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
    with pytest.raises(BrainError, match="holds other files"):
        init(folder, git=True)
    init(folder, git=True, force=True)
    assert not (folder / ".git").exists() and (folder / "old.md").read_text() == "mine"
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
        Seat.from_config({"name": "bad name", "provider": "ollama", "model": "m"})
    assert Seat.from_config({"name": "my_seat", "provider": "ollama", "model": "m"}).name == "my_seat"
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
    assert code == 1 and "delete .kea/index.sqlite" in err and json.loads(out)["error"]


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


# ---------------------------------------------------------------- second round: the fixes' own regressions


@pytest.mark.parametrize(
    "text",
    [
        "Food authority: Westminster Council inspected the roastery",
        "Author: Jonathan Strange",
        "Tokenizer: sentencepiece_model",
        "Authorised: Christopher Wren approved",
        "pwd: /home/ann/projects/shop",
        "basic authentication-middleware",
        "the basic reasoning is sound",
        "oauth_url: https://example.com/callback",
    ],
)
def test_ordinary_words_are_never_redacted(text: str) -> None:
    assert redact(text) == text


def test_ledger_writes_keep_ordinary_words(brain: Brain) -> None:
    entry = ledger.log(brain, "Author: Bartholomew Jones signed. pwd: /home/ann/projects/shop")
    assert entry["title"] == "Author: Bartholomew Jones signed"
    assert "pwd: /home/ann/projects/shop" in brain.file("STATE.md").read_text()
    assert "[REDACTED]" in ledger.log(brain, "rotated, the new one is ghp_" + "a1B2c3D4" * 5)["text"]


@pytest.mark.parametrize("size", [20_000, 200_000])
def test_redaction_runs_in_linear_time(size: int) -> None:
    import time

    start = time.perf_counter()
    redact("deadbeef" * (size // 8))
    redact("word: " * (size // 6))
    assert time.perf_counter() - start < 2


def test_set_writes_where_get_reads(brain: Brain) -> None:
    ledger.predict(brain, "It ships", "+3d", 50)
    path = brain.file("PREDICTIONS.md")
    path.write_text(path.read_text().replace("- **Status:** OPEN", "- **Status:** OPEN\n- **Status:** OPEN (by hand)"))
    assert ledger.score(brain, "1", "true")["status"] == "TRUE"
    assert ledger.calibration(brain)["scored"] == 1


def test_hand_written_headings_keep_their_separators(brain: Brain) -> None:
    path = brain.file("PREDICTIONS.md")
    path.write_text(path.read_text() + "\n### P-0009 | by 2026-12-01 | 70% | Hand claim\n\n- **Status:** OPEN\n")
    assert ledger.predictions(brain)[0]["claim"] == "Hand claim"
    ledger.score(brain, "9", "true")
    assert "### P-0009 | by 2026-12-01 | 70% | Hand claim" in path.read_text()


def test_one_crlf_line_does_not_convert_a_file(brain: Brain) -> None:
    ideas = brain.file("IDEAS.md")
    ideas.write_bytes(ideas.read_bytes().replace(b"\n", b"\r\n", 1))
    ledger.idea(brain, "Mostly LF")
    raw = ideas.read_bytes()
    assert raw.count(b"\r\n") <= 1


def test_concurrent_writes_never_fail(brain: Brain) -> None:
    errors: list[BaseException] = []

    def loop() -> None:
        try:
            for _ in range(50):
                write_lines(brain.file("IDEAS.md"), read_lines(brain.file("IDEAS.md")))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=loop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and not list(brain.root.glob(".IDEAS.md.*"))


def test_facts_after_a_blank_line_still_count(brain: Brain) -> None:
    state = brain.file("STATE.md")
    state.write_text(
        state.read_text().replace(
            "| --- | --- | --- | --- |",
            "| --- | --- | --- | --- |\n| Cash | 5 | bank | 2026-11-01 |\n\n| Staff | 3 | payroll | 2026-11-01 |",
        )
    )
    assert [f.fact for f in ledger.facts(brain)] == ["Cash", "Staff"]
    ledger.set_fact(brain, "Staff", "4", "payroll")
    assert state.read_text().count("| Staff |") == 1


def test_nested_roadmap_items_list_and_close(brain: Brain) -> None:
    path = brain.file("ROADMAP.md")
    path.write_text(
        path.read_text().replace("- [ ] Teach your agents", "- [ ] Parent\n  - [ ] Nested step\n- [ ] Teach")
    )
    view = ledger.roadmap(brain)
    nested = [i for i in view["next"] if i["nested"]]
    assert nested and nested[0]["text"] == "Nested step"
    ledger.roadmap_done(brain, "Nested step")
    assert "  - [x] Nested step" in path.read_text()


def test_example_and_non_brain_folders(tmp_path: Path) -> None:
    init(tmp_path / "real", git=False)
    with pytest.raises(BrainError, match="example goes in a new folder"):
        init(tmp_path / "real", example=True, git=False)
    work = tmp_path / "code"
    (work / "app").mkdir(parents=True)
    with pytest.raises(BrainError, match="holds other files"):
        init(work, git=False)


def test_backups_never_collide(brain: Brain) -> None:
    init(brain.root, force=True, git=False)
    init(brain.root, force=True, git=False)
    assert len(list(brain.file(".kea").glob("backup-*"))) == 2


def test_opencode_honours_forget_and_deny(brain: Brain, tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE session (id TEXT, title TEXT, directory TEXT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
        INSERT INTO session VALUES ('s1', 'Client', '/work/clientx'), ('s2', 'Mine', '/work/mine');
        INSERT INTO message VALUES ('m1', 's1', 1, '{"role": "user"}'), ('m2', 's2', 2, '{"role": "user"}');
        INSERT INTO part VALUES ('p1', 'm1', 1, '{"type": "text", "text": "client walrus"}');
        INSERT INTO part VALUES ('p2', 'm2', 2, '{"type": "text", "text": "own walrus"}');
        """)
    con.commit()
    con.close()
    toml = brain.file("kea.toml")
    toml.write_text(toml.read_text().replace("deny = []", 'deny = ["clientx"]'))
    brain = Brain.load(brain.root)
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "opencode", str(db))
        assert [h["project"] for h in store.search("walrus")] == ["/work/mine"]
        sid = next(s["id"] for s in store.sources() if s["kind"] == "opencode")
        store.forget(sid)
        ingest.ingest_known(brain, store)
        assert store.search("walrus") == []


def test_a_root_denied_later_is_dropped(brain: Brain, tmp_path: Path) -> None:
    notes = tmp_path / "work" / "clientx" / "notes"
    notes.mkdir(parents=True)
    (notes / "a.md").write_text("platypus plans")
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "docs", str(notes))
        toml = brain.file("kea.toml")
        toml.write_text(toml.read_text().replace("deny = []", 'deny = ["clientx"]'))
        reports = ingest.ingest_known(Brain.load(brain.root), store)
        assert any(r.dropped for r in reports) and store.search("platypus") == [] and store.roots() == []


def test_brain_files_cannot_be_forgotten_and_digests_are_not_notes(brain: Brain) -> None:
    (brain.root / "digests" / "s").mkdir(parents=True)
    (brain.root / "digests" / "s" / "digest.md").write_text("private transcript words")
    with Store(brain.index_path) as store:
        ingest.refresh_brain(brain, store)
        assert store.search("transcript", platform="brain") == []
        sid = next(s["id"] for s in store.sources() if s["kind"] == "brain")
        with pytest.raises(ValueError, match="always searchable"):
            store.forget(sid)


def test_a_symlinked_ledger_stays_searchable(brain: Brain, tmp_path: Path) -> None:
    elsewhere = tmp_path / "sync"
    elsewhere.mkdir()
    shutil.move(brain.file("IDEAS.md"), elsewhere / "IDEAS.md")
    os.symlink(elsewhere / "IDEAS.md", brain.file("IDEAS.md"))
    ledger.idea(brain, "Echidna tours")
    with Store(brain.index_path) as store:
        ingest.refresh_brain(brain, store)
        assert store.search("echidna", platform="brain")


def test_partial_hits_are_marked_and_single_letters_ignored(brain: Brain, tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("The quokka ate lunch.")
    (notes / "b.md").write_text("A zebra crossed. It's raining.")
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "docs", str(notes))
        hits = store.search("quokka zebra")
        assert {h["partial"] for h in hits} == {True} and len(hits) == 2
        assert all(not h["partial"] for h in store.search("quokka lunch"))
        assert store.search("Bob's quokka")[0]["partial"] is True


def test_mcp_since_is_parsed_and_ingest_never_adds_defaults(example: Brain, tmp_path: Path) -> None:
    server = mcp.Server(str(example.root))

    def call(name: str, **args: Any) -> dict[str, Any]:
        reply = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )
        assert reply is not None
        return reply

    assert not call("search", query="wholesale", since="-30d")["result"]["isError"]
    assert call("search", query="wholesale", since="lately")["result"]["isError"]
    refused = call("ingest", source="codex")["result"]
    assert refused["isError"] and "the user added" in refused["content"][0]["text"]


def test_questions_go_to_stderr_and_index_errors_are_told_apart(
    run: Run, brain: Brain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_KEY", "k")
    brain.file("council", "roster.toml").write_text(
        '[[seat]]\nname = "x"\nprovider = "openai"\nbase_url = "https://example.invalid/v1"\nmodel = "m"\n'
        'key_env = "MY_KEY"\n'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    code, out, err = run("--brain", str(brain.root), "seats", "--probe", "--json")
    assert code == 1 and "Go ahead?" in err and "Go ahead?" not in out
    assert "busy" in cli._index_error(sqlite3.OperationalError("database is locked"))
    assert "cannot be written" in cli._index_error(sqlite3.OperationalError("attempt to write a readonly database"))
    assert "damaged" in cli._index_error(sqlite3.DatabaseError("file is not a database"))


def test_synth_ignores_non_finite_confidences(brain: Brain) -> None:
    council.plan_new(brain, "p")
    reviews = brain.file("plans", "p", "reviews")
    (reviews / "2026-11-13_a_m.md").write_text(
        '<!--COUNCIL-JSON {"seat": "a", "recommendations": [{"claim": "Do it", "confidence": NaN},'
        ' {"claim": "Do it now", "confidence": Infinity}]} COUNCIL-JSON-->'
    )
    assert council.synth(brain, "p")["parsed"] == 1


def test_resume_reads_the_seat_from_the_review(brain: Brain) -> None:
    brain.file("council", "roster.toml").write_text('[[seat]]\nname = "my_seat"\nprovider = "lmstudio"\nmodel = "m"\n')
    council.plan_new(brain, "p")
    council.brief(brain, "p")
    import kea.council as module

    original = module._ready
    module._ready = lambda seat: True
    try:
        council.run(brain, "p", ask=lambda seat, system, user: "review")
        with pytest.raises(council.CouncilError, match="no seat can run"):
            council.run(brain, "p", resume=True, ask=lambda seat, system, user: "again")
    finally:
        module._ready = original


# ---------------------------------------------------------------- third round


@pytest.mark.parametrize(
    "text, secret",
    [
        ("curl https://api.example/v1/now?api_key=Wk3yLiveA1b2C3d4E5&units=metric", "Wk3yLiveA1b2C3d4E5"),
        ("mysql -u root --password=Hunter2Secret99", "Hunter2Secret99"),
        ("jdbc:postgresql://db:5432/app?user=app&password=PgSecret2026x", "PgSecret2026x"),
        ("Cookie: sessionid=abc123def456ghi; csrftoken=zzz", "abc123def456ghi"),
        ("--api-key=abcd1234efgh5678", "abcd1234efgh5678"),
    ],
)
def test_secrets_inside_urls_flags_and_cookies(text: str, secret: str) -> None:
    assert secret not in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "token = get_token(user)",
        "secret_name = os.environ.get(name)",
        "credentials_path = Path.home() / 'creds'",
        "docker run -u 1000:1000 app",
        "podman exec -u root:root ctr sh",
        "rsync -u deploy@host:/srv/app",
        "TOKEN_COUNT=123456789",
    ],
)
def test_code_and_commands_are_not_secrets(text: str) -> None:
    assert redact(text) == text and redact(text, generic=False) == text


def test_long_pairs_stay_linear_and_long_secrets_hide_whole() -> None:
    import time

    start = time.perf_counter()
    redact("a=b" * 30000)
    redact("TOKEN_" * 6000, generic=False)
    assert time.perf_counter() - start < 2
    assert redact("password=" + "y" * 900 + " tail") == "password=[REDACTED] tail"


def test_ids_search_as_phrases_and_any_marks_only_partial_hits(brain: Brain) -> None:
    ledger.decide(brain, "First", "One")
    ledger.decide(brain, "Second", "Two")
    ledger.predict(brain, "Claim", "+3d", 50)
    ledger.predict(brain, "Other", "+3d", 50)
    with Store(brain.index_path) as store:
        ingest.refresh_brain(brain, store)
        hits = store.search("P-0002")
        assert hits[0]["ref"].startswith("PREDICTIONS.md") and hits[0]["partial"] is False
        assert all(h["partial"] for h in hits[1:])
        anyhits = store.search("Second Two", any_word=True)
        assert any(not h["partial"] for h in anyhits)


def test_init_add_keeps_your_files_and_merges_gitignore(run: Run, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "README.md").write_text("# My vault\n")
    (vault / "CLAUDE.md").write_text("Always answer in French.\n")
    (vault / ".gitignore").write_text("secret-stuff/\n")
    code, _, err = run("init", str(vault), "--no-git")
    assert code == 1 and "--add" in err
    code, out, _ = run("init", str(vault), "--add", "--no-git")
    assert code == 0 and "kept" in out
    assert (vault / "README.md").read_text() == "# My vault\n"
    assert (vault / "CLAUDE.md").read_text() == "Always answer in French.\n"
    ignore = (vault / ".gitignore").read_text()
    assert ignore.startswith("secret-stuff/\n") and ".kea/" in ignore
    code, out, _ = run("init", str(vault), "--force", "--no-git")
    assert "the files it replaced are in" in out and (vault / ".gitignore").read_text().startswith("secret-stuff/")


def test_mcp_ingest_by_source_survives_a_missing_root_and_drops_a_denied_one(example: Brain, tmp_path: Path) -> None:
    gone, kept, client = tmp_path / "a-notes", tmp_path / "b-notes", tmp_path / "clients" / "notes"
    for folder, word in ((gone, "alpha"), (kept, "bravo"), (client, "charlie")):
        folder.mkdir(parents=True)
        (folder / "x.md").write_text(f"{word} words")
    with Store(example.index_path) as store:
        for folder in (gone, kept, client):
            ingest.ingest(example, store, "docs", str(folder))
    shutil.rmtree(gone)
    (kept / "y.md").write_text("pepino arrives")
    toml = example.file("kea.toml")
    toml.write_text(toml.read_text().replace("deny = []", 'deny = ["clients"]'))
    server = mcp.Server(str(example.root))
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ingest", "arguments": {"source": "docs"}},
        }
    )
    assert reply is not None and not reply["result"]["isError"]
    with Store(example.index_path) as store:
        assert store.search("pepino") and store.search("bravo") and not store.search("charlie")


def test_session_links_are_judged_by_their_target(brain: Brain, tmp_path: Path) -> None:
    secret = tmp_path / ".aws"
    secret.mkdir()
    real = secret / "s.jsonl"
    real.write_text(json.dumps({"type": "user", "message": {"content": "rambutan secret"}}) + "\n")
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(json.dumps({"type": "user", "message": {"content": "rambutan outside"}}) + "\n")
    projects = tmp_path / "projects" / "p"
    projects.mkdir(parents=True)
    os.symlink(real, projects / "a.jsonl")
    os.symlink(elsewhere, projects / "b.jsonl")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "claude-code", str(tmp_path / "projects"))
        assert report.skipped == 2 and store.search("rambutan") == []


def test_docs_count_the_links_they_skip(brain: Brain, tmp_path: Path) -> None:
    outside = tmp_path / "Dropbox"
    outside.mkdir()
    (outside / "taxes.md").write_text("tax words")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "own.md").write_text("own words")
    os.symlink(outside / "taxes.md", notes / "taxes.md")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "docs", str(notes))
        assert report.read == 1 and report.skipped == 1


def test_synth_names_seats_from_the_review_header(brain: Brain) -> None:
    council.plan_new(brain, "p")
    reviews = brain.file("plans", "p", "reviews")
    block = '<!--COUNCIL-JSON {"recommendations": [{"claim": "Same claim here", "confidence": 1e308}]} COUNCIL-JSON-->'
    for seat in ("risk_officer", "risk_analyst"):
        (reviews / f"2026-11-14_{seat}_m.md").write_text(f"| Seat | {seat} |\n\n{block}")
    (reviews / "2026-11-14_big_m.md").write_text(
        '| Seat | big |\n\n<!--COUNCIL-JSON {"recommendations": [{"claim": "x", "confidence": ' + "9" * 400 + "}]}"
        " COUNCIL-JSON-->"
    )
    out = council.synth(brain, "p")
    scaffold = brain.file("plans", "p", "10-synthesis-scaffold.md").read_text()
    assert out["parsed"] == 3 and "risk_analyst, risk_officer" in scaffold


def test_roster_mistakes_show_in_check_and_dry_run(run: Run, brain: Brain) -> None:
    brain.file("council", "roster.toml").write_text(
        '[[seat]]\nname = "risk officer"\nprovider = "ollama"\nmodel = "m"\n'
    )
    assert any("risk officer" in f.message for f in ledger.check(brain))
    council.plan_new(brain, "p")
    code, out, _ = run("--brain", str(brain.root), "council", "p", "--dry-run")
    assert "roster:" in out


def test_mixed_heading_separators_still_read(brain: Brain) -> None:
    path = brain.file("PREDICTIONS.md")
    path.write_text(
        path.read_text() + "\n### P-0010 | by 2026-12-01 · 60% · Mixed separators claim\n\n- **Status:** OPEN\n"
    )
    p = ledger.predictions(brain)[0]
    assert p["confidence"] == 60 and "Mixed separators claim" in p["claim"]


def test_saying_no_under_json_prints_a_json_error(run: Run, brain: Brain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "k")
    brain.file("council", "roster.toml").write_text(
        '[[seat]]\nname = "x"\nprovider = "openai"\nbase_url = "https://example.invalid/v1"\nmodel = "m"\n'
        'key_env = "MY_KEY"\n'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    code, out, err = run("--brain", str(brain.root), "seats", "--probe", "--json")
    assert code == 1 and json.loads(out)["error"].startswith("nothing sent")


# ---------------------------------------------------------------- the release gate round


def _gpt(text: str) -> str:
    node = {"message": {"author": {"role": "user"}, "content": {"parts": [text]}}, "parent": None}
    return json.dumps([{"id": "c1", "title": "t", "current_node": "n1", "mapping": {"n1": node}}])


def test_example_and_add_force_are_refused_on_a_folder_of_files(tmp_path: Path) -> None:
    work = tmp_path / "vault"
    work.mkdir()
    (work / "README.md").write_text("# mine\n")
    with pytest.raises(BrainError, match="example goes in a new folder"):
        init(work, example=True, add=True, git=False)
    with pytest.raises(BrainError, match="choose one"):
        init(work, add=True, force=True, git=False)
    assert sorted(f.name for f in work.iterdir()) == ["README.md"]


def test_repair_leaves_gitignore_alone(brain: Brain) -> None:
    ignore = brain.file(".gitignore")
    ignore.write_text("node_modules/\n")
    assert ".gitignore" not in init(brain.root, git=False)
    assert ignore.read_text() == "node_modules/\n"


def test_gitignore_merge_respects_negations_bytes_and_links(tmp_path: Path) -> None:
    negated = tmp_path / "negated"
    negated.mkdir()
    (negated / ".gitignore").write_text("!digests/\n")
    init(negated, add=True, git=False)
    merged = (negated / ".gitignore").read_text()
    assert ".kea/" in merged and "\ndigests/" not in merged
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / ".gitignore").write_bytes(b"# caf\xe9 build output\r\nbuild/\r\n")
    assert ".gitignore" in init(raw, add=True, git=False)
    merged_bytes = (raw / ".gitignore").read_bytes()
    assert merged_bytes.startswith(b"# caf\xe9 build output\r\nbuild/\r\n\r\n# kea\r\n.kea/\r\n")
    assert b"digests/\r\n" in merged_bytes and (raw / "kea.toml").is_file()
    wide = tmp_path / "wide"
    wide.mkdir()
    (wide / ".gitignore").write_bytes("build/\n".encode("utf-16"))
    notes: list[str] = []
    assert ".gitignore" not in init(wide, add=True, git=False, notes=notes)
    assert (wide / ".gitignore").read_bytes() == "build/\n".encode("utf-16")
    assert notes and ".kea/" in notes[0] and "regular, readable UTF-8 file" in notes[0]
    shared = tmp_path / "shared-ignore"
    shared.write_text("shared/\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    os.symlink(shared, linked / ".gitignore")
    notes = []
    init(linked, add=True, git=False, notes=notes)
    assert shared.read_text() == "shared/\n" and (linked / ".gitignore").is_symlink()
    assert notes and "is a link" in notes[0]


def test_kea_folder_is_private_after_a_forced_init(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / ".kea").mkdir(parents=True)
    (work / ".kea").chmod(0o755)
    (work / "notes.md").write_text("mine\n")
    init(work, force=True, git=False)
    assert (work / ".kea").stat().st_mode & 0o777 == 0o700
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "notes.md").write_text("mine\n")
    init(fresh, force=True, git=False)
    assert (fresh / ".kea").stat().st_mode & 0o777 == 0o700


def test_a_single_seat_table_is_a_finding_not_a_crash(brain: Brain) -> None:
    brain.file("council", "roster.toml").write_text(
        '[seat]\nname = "x"\nprovider = "openai"\nmodel = "m"\nkey_env = "K"\n'
    )
    findings = ledger.check(brain)
    assert any(f.file == "council/roster.toml" and "[[seat]]" in f.message for f in findings)
    server = mcp.Server(str(brain.root))
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "check", "arguments": {}}}
    )
    assert reply is not None and not reply["result"]["isError"]


@pytest.mark.parametrize(
    ("heading", "confidence", "claim"),
    [
        ("### P-0012 · by 2026-12-01 · 62% | Two part dot then pipe", 62, "Two part dot then pipe"),
        ("### P-0013 | by 2026-12-01 | 63% · Two part pipe then dot", 63, "Two part pipe then dot"),
    ],
)
def test_two_part_mixed_headings_read(brain: Brain, heading: str, confidence: int, claim: str) -> None:
    path = brain.file("PREDICTIONS.md")
    path.write_text(path.read_text() + f"\n{heading}\n\n- **Status:** OPEN\n")
    p = ledger.predictions(brain)[0]
    assert p["confidence"] == confidence and p["claim"] == claim
    assert not [f for f in ledger.check(brain) if f.file == "PREDICTIONS.md" and f.level == "error"]


def test_a_linked_session_folder_is_skipped(brain: Brain, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "proj"
    outside.mkdir(parents=True)
    (outside / "x.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "durian outside"}}) + "\n")
    projects = tmp_path / "projects"
    projects.mkdir()
    os.symlink(outside, projects / "q")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "claude-code", str(projects))
        assert report.read == 0 and report.skipped == 1 and store.search("durian") == []


def test_export_folder_links_are_judged_by_their_target(brain: Brain, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "c.json").write_text(_gpt("lychee from elsewhere"))
    (tmp_path / ".aws" / "sub").mkdir(parents=True)
    (tmp_path / ".aws" / "sub" / "c.json").write_text(_gpt("lychee from aws"))
    export = tmp_path / "export"
    export.mkdir()
    (export / "conversations-2.json").write_text(_gpt("lychee at home"))
    os.symlink(tmp_path / "elsewhere" / "c.json", export / "conversations.json")
    os.symlink(tmp_path / ".aws" / "sub" / "c.json", export / "conversations-1.json")
    with Store(brain.index_path) as store:
        ingest.ingest(brain, store, "chatgpt", str(export))
        bodies = [h["snippet"] for h in store.search("lychee")]
        assert len(bodies) == 1 and "home" in bodies[0]


def test_a_linked_repo_outside_the_folder_is_skipped(brain: Brain, tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    outside = tmp_path / "elsewhere" / "repo"
    outside.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@example.com"}
    env |= {"GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@example.com"}
    for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "kumquat outside"]):
        subprocess.run(["git", "-C", str(outside), *argv], check=True, env=env)
    code = tmp_path / "code"
    code.mkdir()
    os.symlink(outside, code / "linked")
    with Store(brain.index_path) as store:
        report = ingest.ingest(brain, store, "git", str(code))
        assert report.read == 0 and report.skipped == 1 and store.search("kumquat") == []


def test_forced_init_of_a_folder_of_files_names_its_backup(run: Run, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("Always answer in French.\n")
    code, out, _ = run("init", str(vault), "--force", "--no-git", "--no-default")
    assert code == 0 and "the files it replaced are in" in out
    backup = brain_backup(vault)
    assert (backup / "CLAUDE.md").read_text() == "Always answer in French.\n"
    code, out, _ = run("init", str(vault), "--no-git", "--no-default")
    assert code == 0 and "replaced" not in out and "no file changed" in out


def brain_backup(folder: Path) -> Path:
    found = sorted((folder / ".kea").glob("backup-*"))
    assert len(found) == 1
    return found[0]


# ---------------------------------------------------------------- the final gate round


def test_add_and_force_are_refused_on_an_existing_brain(brain: Brain) -> None:
    state = brain.file("STATE.md")
    state.write_text(state.read_text() + "\nblue tin fact\n")
    with pytest.raises(BrainError, match="choose one"):
        init(brain.root, add=True, force=True, git=False)
    assert "blue tin fact" in state.read_text()


def test_init_json_names_the_gitignore_kea_could_not_change(run_json: Callable[..., Any], tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / ".gitignore").write_bytes("build/\n".encode("utf-16"))
    data = run_json("init", str(folder), "--add", "--no-git", "--no-default")
    assert data["notes"] and "council/briefing/" in data["notes"][0]


def test_init_never_writes_through_links(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "README.md").write_text("team readme, do not touch\n")
    outside = tmp_path / "outside-journal"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notes.md").write_text("mine\n")
    os.symlink(shared / "README.md", vault / "README.md")
    os.symlink(shared / "AGENTS.md", vault / "AGENTS.md")  # dangling
    os.symlink(outside, vault / "journal")
    init(vault, add=True, git=False)
    assert (shared / "README.md").read_text() == "team readme, do not touch\n"
    assert not (shared / "AGENTS.md").exists() and list(outside.iterdir()) == []
    forced = tmp_path / "forced"
    forced.mkdir()
    (forced / "notes.md").write_text("mine\n")
    os.symlink(shared / "README.md", forced / "README.md")
    init(forced, force=True, git=False)
    assert (shared / "README.md").read_text() == "team readme, do not touch\n"
    assert not (forced / "README.md").is_symlink() and (forced / "README.md").is_file()
    kept = next((forced / ".kea").glob("backup-*")) / "README.md"
    assert kept.is_symlink() and kept.resolve() == (shared / "README.md").resolve()


@pytest.mark.parametrize(
    "roster",
    [
        b"# notas: n\xe3o\n[[seat]]\nname = 'x'\nprovider = 'openai'\nmodel = 'm'\n",
        b"[[seat]]\nname = 'x'\nprovider = 'openai'\nmodel = 'm'\nmax_tokens = inf\n",
        b"[[seat]]\nname = 'x'\nprovider = 'openai'\nmodel = 'm'\ntimeout = -1\n",
        b"[rate_limits]\nopenai = inf\n[[seat]]\nname = 'x'\nprovider = 'openai'\nmodel = 'm'\n",
    ],
)
def test_a_broken_roster_never_crashes_check_or_status(run: Run, brain: Brain, roster: bytes) -> None:
    brain.file("council", "roster.toml").write_bytes(roster)
    code, out, err = run("--brain", str(brain.root), "check")
    assert "Traceback" not in err and "Error:" not in err
    code, out, err = run("--brain", str(brain.root), "status")
    assert code == 0, err
    server = mcp.Server(str(brain.root))
    reply = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "check", "arguments": {}}}
    )
    assert reply is not None and not reply["result"]["isError"]


# ---------------------------------------------------------------- the sixth round


def test_git_keeps_the_index_out_when_the_gitignore_cannot(tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    shared = tmp_path / "shared-ignore"
    shared.write_text(".kea/\ncouncil/briefing/\ndigests/\n")
    for name in ("wide", "linked"):
        folder = tmp_path / name
        folder.mkdir()
        subprocess.run(["git", "init", "-q", str(folder)], check=True)
        if name == "wide":
            (folder / ".gitignore").write_bytes("build/\n".encode("utf-16"))
        else:
            os.symlink(shared, folder / ".gitignore")
        notes: list[str] = []
        init(folder, add=True, git=False, notes=notes)
        assert notes and ".git/info/exclude" in notes[0]
        (folder / ".kea" / "index.sqlite").write_text("x")
        (folder / "digests").mkdir(exist_ok=True)
        (folder / "digests" / "d.md").write_text("x")
        status = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert ".kea/" not in status and "digests/" not in status


def test_init_names_the_files_it_left_out_of_a_linked_folder(run: Run, tmp_path: Path) -> None:
    outside = tmp_path / "shared-council"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notes.md").write_text("mine\n")
    os.symlink(outside, vault / "council")
    code, out, _ = run("init", str(vault), "--add", "--no-git", "--no-default")
    assert code == 0 and "council/ is a link" in out and "council/roster.toml" in out
    assert list(outside.iterdir()) == []
    code, out, _ = run("init", str(vault), "--no-git", "--no-default")
    assert "put back nothing" in out and "council/roster.toml" in out


# ---------------------------------------------------------------- the seventh round


def _git(*argv: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@example.com"}
    env |= {"GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@example.com"}
    return subprocess.run(["git", *argv], capture_output=True, text=True, check=True, env=env).stdout


def _untracked(repo: Path) -> str:
    return _git("-C", str(repo), "status", "--porcelain", "--untracked-files=all")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_brain_in_a_subfolder_or_a_worktree_stays_out_of_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", str(repo))
    _git("-C", str(repo), "commit", "-q", "--allow-empty", "-m", "start")
    _git("-C", str(repo), "worktree", "add", "-q", str(tmp_path / "wt"))
    for folder in (repo / "notes" / "brain", tmp_path / "wt" / "brain"):
        folder.mkdir(parents=True)
        (folder / ".gitignore").write_bytes("build/\n".encode("utf-16"))
        notes: list[str] = []
        init(folder, add=True, git=False, notes=notes)
        assert notes and "info/exclude" in notes[0] and "UTF-16" in notes[0]
        (folder / ".kea" / "index.sqlite").write_text("x")
        (folder / "digests" / "d.md").parent.mkdir(exist_ok=True)
        (folder / "digests" / "d.md").write_text("x")
        (folder / "council" / "briefing").mkdir(parents=True, exist_ok=True)
        (folder / "council" / "briefing" / "p.md").write_text("x")
        status = _untracked(folder)
        assert ".kea/" not in status and "digests/" not in status and "briefing/" not in status, status


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_an_unreadable_gitignore_gets_its_own_reason(tmp_path: Path) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    ignore = folder / ".gitignore"
    ignore.write_text("build/\n")
    ignore.chmod(0)
    notes: list[str] = []
    try:
        init(folder, add=True, git=False, notes=notes)
    finally:
        ignore.chmod(0o644)
    assert notes and "cannot be read" in notes[0] and "UTF-16" not in notes[0]


def test_init_names_a_dangling_link_and_what_force_left_in_a_linked_folder(
    run: Run, brain: Brain, tmp_path: Path
) -> None:
    gone = tmp_path / "synced" / "STATE.md"
    state = brain.file("STATE.md")
    state.unlink()
    os.symlink(gone, state)
    code, out, _ = run("init", str(brain.root), "--no-git", "--no-default")
    assert code == 0 and "STATE.md is a link" in out and "which is missing" in out
    assert not gone.exists()
    shared = tmp_path / "shared-council"
    shutil.move(str(brain.file("council")), shared)
    os.symlink(shared, brain.file("council"))
    state.unlink()
    state.write_text("# State\n")
    code, out, _ = run("init", str(brain.root), "--force", "--no-git", "--no-default")
    assert code == 0 and "council/ is a link" in out and "council/roster.toml" in out
    assert (shared / "roster.toml").read_text() != ""


# ---------------------------------------------------------------- found by CI on macOS


def test_a_reset_while_reading_an_error_body_is_a_seat_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    from email.message import Message

    class Reset(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:
            raise ConnectionResetError(54, "Connection reset by peer")

    def fail(*args: Any, **kwargs: Any) -> str:
        body: Any = Reset()
        raise urllib.error.HTTPError("https://example.invalid/v1", 503, "Service Unavailable", Message(), body)

    monkeypatch.setattr(providers, "_call", fail)
    monkeypatch.setattr("kea.providers.time.sleep", lambda s: None)
    monkeypatch.setenv("K", "k")
    seat = Seat.from_config({"name": "r", "base_url": "https://example.invalid/v1", "model": "m", "key_env": "K"})
    with pytest.raises(ProviderError, match="HTTP 503: the server closed the connection"):
        providers.complete(seat, "s", "u", retries=2)
