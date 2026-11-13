import io
import json
from typing import Any

import pytest

from kea import mcp
from kea.brain import Brain


def call(server: mcp.Server, method: str, params: dict[str, Any] | None = None, mid: int = 1) -> Any:
    reply = server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
    assert reply is not None
    return reply


def tool(server: mcp.Server, name: str, **args: Any) -> Any:
    reply = call(server, "tools/call", {"name": name, "arguments": args})
    result = reply["result"]
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def test_handshake_and_lists(example: Brain) -> None:
    server = mcp.Server(str(example.root))
    init = call(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "kea"
    assert call(server, "initialize", {"protocolVersion": "1999"})["result"]["protocolVersion"] == "2025-11-25"
    assert call(server, "ping")["result"] == {}
    tools = {t["name"]: t for t in call(server, "tools/list")["result"]["tools"]}
    assert tools["search"]["annotations"]["readOnlyHint"] and not tools["decide"]["annotations"]["readOnlyHint"]
    assert tools["council_run"]["annotations"]["openWorldHint"]
    assert len(tools) == len(mcp.TOOLS)
    uris = [r["uri"] for r in call(server, "resources/list")["result"]["resources"]]
    assert "kea://STATE.md" in uris
    text = call(server, "resources/read", {"uri": "kea://STATE.md"})["result"]["contents"][0]["text"]
    assert "Tidewater" in text
    prompts = {p["name"] for p in call(server, "prompts/list")["result"]["prompts"]}
    assert prompts == {"start_session", "end_session", "chair_review"}
    chair = call(server, "prompts/get", {"name": "chair_review", "arguments": {"slug": "second-cafe"}})["result"]
    assert "council/briefing/second-cafe.md" in chair["messages"][0]["content"]["text"]


def test_read_and_write_tools(example: Brain) -> None:
    server = mcp.Server(str(example.root))
    status = tool(server, "brain_status")
    assert status["brain"] == "Tidewater Coffee"
    assert tool(server, "state", entries=1)["log"][0]["title"] == "Five sample bags sent to cafes"
    hits = tool(server, "search", query="sample bags", limit=3)["hits"]
    assert hits and hits[0]["platform"] == "brain"
    assert tool(server, "show", id=hits[0]["id"], context=1)["item"]["id"] == hits[0]["id"]
    assert tool(server, "decisions", due=True)[0]["id"] == "D-0002"
    assert tool(server, "predictions", due=True)["calibration"]["scored"] == 6
    assert tool(server, "roadmap")["owner"] == "Mara"
    assert isinstance(tool(server, "check"), list)
    assert tool(server, "plans")[0]["slug"] == "second-cafe"
    assert len(tool(server, "seats")) == 6
    assert tool(server, "effort") == []
    assert tool(server, "log_entry", text="Two cafes ordered.", source="phone")["title"] == "Two cafes ordered"
    assert tool(server, "record_fact", fact="Wholesale accounts", value="4 cafes", source="orders")["new"] is False
    assert tool(server, "decide", title="Sign later", decision="Wait", revisit="+30d")["id"] == "D-0004"
    assert tool(server, "revisit_decision", id="D-0002", outcome="Held", surprise=1)["id"] == "D-0002"
    assert tool(server, "predict", claim="Four cafes by month end", by="+20d", confidence=50)["id"] == "P-0010"
    assert tool(server, "score_prediction", id="P-0007", outcome="partial")["status"] == "PARTIAL"
    assert tool(server, "add_idea", text="Cold brew cans")["id"] == "I-0003"
    assert tool(server, "roadmap_add", text="Call the agent", section="waiting")["section"] == "waiting"
    assert tool(server, "roadmap_done", item="Call the agent", proof="called")["proof"] == "called"
    assert tool(server, "plan_new", slug="cold-brew")["files"]
    assert tool(server, "brief", slug="second-cafe")["hash"]
    assert tool(server, "synth", slug="second-cafe")["parsed"] == 3
    assert tool(server, "ingest")[0]["kind"] == "brain"


def test_errors_never_stop_the_server(example: Brain, tmp_path: Any) -> None:
    server = mcp.Server(str(example.root))
    assert call(server, "nope")["error"]["code"] == -32601
    bad = call(server, "tools/call", {"name": "nope"})
    assert bad["error"]["code"] == -32602
    missing = call(server, "tools/call", {"name": "search", "arguments": {}})
    assert "missing" in missing["error"]["message"]
    extra = call(server, "tools/call", {"name": "search", "arguments": {"query": "x", "zzz": 1}})
    assert "unknown" in extra["error"]["message"]
    refused = call(
        server, "tools/call", {"name": "council_run", "arguments": {"slug": "second-cafe", "confirm": False}}
    )
    assert refused["result"]["isError"] and "confirm" in refused["result"]["content"][0]["text"]
    failed = call(server, "tools/call", {"name": "show", "arguments": {"id": 999999}})
    assert failed["result"]["isError"]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle("junk")["error"]["code"] == -32600  # type: ignore[index]
    assert call(server, "resources/read", {"uri": "kea://secret"})["error"]["code"] == -32602
    assert call(server, "prompts/get", {"name": "chair_review"})["error"]["code"] == -32602
    nowhere = mcp.Server(str(tmp_path / "missing"))
    assert call(nowhere, "resources/list")["result"]["resources"] == []
    lost = call(nowhere, "tools/call", {"name": "brain_status", "arguments": {}})
    assert lost["result"]["isError"]


def test_serve_over_stdio(example: Brain) -> None:
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        "",
        "{not json",
        json.dumps([{"jsonrpc": "2.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "method": "note"}]),
    ]
    out = io.StringIO()
    mcp.serve(mcp.Server(str(example.root)), io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700


@pytest.mark.parametrize("name", sorted(mcp.TOOLS))
def test_every_tool_has_a_valid_schema(name: str) -> None:
    schema = mcp.TOOLS[name][1]
    assert schema["type"] == "object" and set(schema["required"]) <= set(schema["properties"])
