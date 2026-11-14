import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from kea import council, providers
from kea.brain import Brain
from kea.council import CouncilError
from kea.providers import ProviderError, Seat

REVIEW = (
    "## 1. Survival\nFine.\n<!--COUNCIL-JSON\n"
    '{"seat":"%s","verdict":"PROCEED","headline":"ok","top_risk":"none","overall_confidence":70,'
    '"recommendations":[{"id":"R1","claim":"Count staff hours from the rota first","confidence":80,"question":1}]}'
    "\nCOUNCIL-JSON-->"
)


class Fake(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []
    status = 200

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        headers = {k.lower(): v for k, v in self.headers.items()}
        Fake.calls.append({"path": self.path, "headers": headers, "body": body})
        if Fake.status != 200:
            self.send_response(Fake.status)
            self.end_headers()
            self.wfile.write(b'{"error": "slow down"}')
            return
        if self.path.endswith("/messages"):
            reply: dict[str, Any] = {"content": [{"type": "text", "text": "anthropic says hi"}]}
        elif ":generateContent" in self.path:
            reply = {"candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}}]}
        else:
            reply = {"choices": [{"message": {"content": REVIEW % body["model"]}}]}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server() -> Iterator[str]:
    Fake.calls, Fake.status = [], 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def seat(url: str, **extra: Any) -> Seat:
    return Seat.from_config({"name": "s", "provider": "openai", "base_url": url, "model": "m", **extra})


def test_wire_formats_and_headers(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "secret-key")
    out = providers.complete(seat(server + "/v1", key_env="K"), "sys", "hello")
    assert "COUNCIL-JSON" in out
    sent = Fake.calls[-1]
    assert sent["path"] == "/v1/chat/completions" and sent["headers"]["authorization"] == "Bearer secret-key"
    assert sent["headers"]["user-agent"].startswith("kea/")
    anthropic = seat(server, wire="anthropic", key_env="K")
    assert providers.complete(anthropic, "sys", "hi") == "anthropic says hi"
    assert Fake.calls[-1]["headers"]["x-api-key"] == "secret-key"
    gemini = seat(server, wire="gemini", key_env="K")
    assert providers.complete(gemini, "sys", "hi") == "gemini says hi"
    assert "key=" not in Fake.calls[-1]["path"] and Fake.calls[-1]["headers"]["x-goog-api-key"] == "secret-key"


def test_retries_then_reports_without_the_key(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kea.providers.time.sleep", lambda s: None)
    monkeypatch.setenv("K", "secret-key")
    Fake.status = 429
    with pytest.raises(ProviderError) as err:
        providers.complete(seat(server, key_env="K"), "s", "u", retries=2)
    assert "HTTP 429" in str(err.value) and "secret-key" not in str(err.value)
    assert len(Fake.calls) == 2
    Fake.status = 400
    with pytest.raises(ProviderError):
        providers.complete(seat(server), "s", "u", retries=3)
    assert len(Fake.calls) == 3


def test_seat_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K", "secret-key")
    remote = Seat.from_config({"name": "r", "base_url": "http://example.com/v1", "model": "m", "key_env": "K"})
    with pytest.raises(ProviderError, match="plain HTTP"):
        providers.complete(remote, "s", "u", retries=1)
    with pytest.raises(ProviderError, match="set NOPE"):
        providers.complete(seat("https://x", key_env="NOPE"), "s", "u")
    with pytest.raises(ProviderError):
        Seat.from_config({"name": "x", "provider": "nobody", "model": "m"})
    with pytest.raises(ProviderError):
        Seat.from_config({"name": "x", "provider": "openai"})
    preset = Seat.from_config({"name": "g", "provider": "gemini", "model": "gemini-flash-latest"})
    assert preset.key_env == "GEMINI_API_KEY" and not preset.ready and not preset.local
    assert Seat.from_config({"name": "o", "provider": "ollama", "model": "qwen3"}).local
    ok, detail = providers.probe(seat("ftp://nowhere"))
    assert not ok and "refused" in detail


def write_roster(brain: Brain, url: str) -> None:
    brain.file("council", "roster.toml").write_text(f"""
[[seat]]
name = "alpha"
provider = "openai"
base_url = "{url}/v1"
model = "model-a"

[[seat]]
name = "beta"
provider = "lmstudio"
base_url = "{url}/v1"
model = "model-b"

[[seat]]
name = "keyless"
provider = "gemini"
model = "gemini-flash-latest"

[[seat]]
name = "broken"

[rate_limits]
openai = 0
""")


def test_the_whole_council_loop(brain: Brain, server: str) -> None:
    write_roster(brain, server)
    files = council.plan_new(brain, "go-wholesale")
    assert "plans/go-wholesale/00-proposal.md" in files
    with pytest.raises(CouncilError):
        council.plan_new(brain, "go-wholesale")
    with pytest.raises(CouncilError):
        council.plan_new(brain, "Bad Slug")
    (brain.file("frameworks") / "constraints.md").write_text("---\nname: constraints\n---\n# Constraints\n")
    (brain.file("frameworks") / "always.md").write_text("---\nalways_apply: true\n---\n# Always\n")
    proposal = brain.file("plans", "go-wholesale", "00-proposal.md")
    proposal.write_text(proposal.read_text() + "\nWe apply constraints here.\n")
    packet = council.brief(brain, "go-wholesale")
    assert set(packet.frameworks) == {"constraints", "always"} and len(packet.hash) == 12
    assert council.brief(brain, "go-wholesale").hash == packet.hash
    rows = {r["seat"]: r for r in council.seats(brain)}
    assert rows["alpha"]["ready"] and not rows["keyless"]["ready"] and rows["beta"]["local"]
    assert council.roster(brain).errors
    manifest = council.run(brain, "go-wholesale")
    assert [r["seat"] for r in manifest["results"]] == ["alpha", "beta"]
    assert all(r["ok"] for r in manifest["results"]) and manifest["skipped"] == ["keyless"]
    review = brain.file(manifest["results"][0]["file"]).read_text()
    assert packet.hash in review and "Independent | yes" in review
    sent = Fake.calls[0]["body"]["messages"]
    assert "MANDATORY OUTPUT CONTRACT" in sent[1]["content"] and "Ada" in sent[0]["content"]
    with pytest.raises(CouncilError):
        council.run(brain, "go-wholesale", resume=True)
    result = council.synth(brain, "go-wholesale")
    assert result["parsed"] == 2 and result["clusters"] == 1 and result["verdicts"] == {"PROCEED": 2}
    stages = {p["slug"]: p["stage"] for p in council.plans(brain)}
    assert stages["go-wholesale"] == "synthesized"


def test_run_with_a_fake_model_and_failures(brain: Brain, server: str) -> None:
    write_roster(brain, server)
    council.plan_new(brain, "p")
    with pytest.raises(CouncilError):
        council.run(brain, "p")
    council.brief(brain, "p")

    def ask(s: Seat, system: str, user: str) -> str:
        if s.name == "beta":
            raise ProviderError("down")
        return "a review with no json"

    manifest = council.run(brain, "p", ask=ask)
    assert {r["seat"]: r["ok"] for r in manifest["results"]} == {"alpha": True, "beta": False}
    out = council.synth(brain, "p")
    assert out["unparsed"] and out["parsed"] == 0
    with pytest.raises(CouncilError):
        council.synth(brain, "nothing-here")


def test_review_parsing_is_forgiving() -> None:
    assert council.parse_review("no block") is None
    assert council.parse_review('<!--COUNCIL-JSON {"seat": "a"} trailing prose COUNCIL-JSON-->') == {"seat": "a"}
    assert council.parse_review("<!--COUNCIL-JSON not json COUNCIL-JSON-->") is None
    assert council.parse_review("<!--COUNCIL-JSON [1, 2] COUNCIL-JSON-->") is None


def test_clustering_groups_the_same_action() -> None:
    groups = council.cluster(
        [
            {"seat": "a", "claim": "Count the staff hours from the rota", "confidence": 80},
            {"seat": "b", "claim": "Count staff hours on the rota before calling", "confidence": 40},
            {"seat": "c", "claim": "Ask the agent to hold the site", "confidence": 60},
        ]
    )
    assert [g["seats"] for g in groups] == [["a", "b"], ["c"]]
    assert groups[0]["spread"] == 40 and groups[0]["confidence"] == 60


def test_missing_roster_and_proposal(brain: Brain) -> None:
    brain.file("council", "roster.toml").unlink()
    with pytest.raises(CouncilError):
        council.roster(brain)
    brain.file("council", "roster.toml").write_text("[[seat]\n")
    with pytest.raises(CouncilError):
        council.roster(brain)
    with pytest.raises(CouncilError):
        council.brief(brain, "nope")


def test_plan_new_without_the_brain_template(brain: Brain, tmp_path: Path) -> None:
    import shutil

    shutil.rmtree(brain.file("plans", "_template"))
    files = council.plan_new(brain, "fresh")
    assert "plans/fresh/00-proposal.md" in files
    assert "<slug>" not in brain.file("plans", "fresh", "00-proposal.md").read_text()
