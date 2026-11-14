"""
A Model Context Protocol server, so any agent can read the brain, search the evidence and keep the ledger.

It speaks JSON-RPC 2.0 over stdio (``kea mcp``), one message per line, with only the standard library. Tools map onto
the CLI's commands and their arguments are checked against each tool's schema. Read tools are marked read only. The
one tool that can send data off the machine, ``council_run``, runs only the seats on this machine unless the brain's
kea.toml sets ``[council] agents_may_send = true``: an agent can never decide that on the user's behalf. One bad
message gets one error back and never stops the server.
"""

import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from kea import __version__
from kea.brain import Brain, BrainError

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 150_000

INSTRUCTIONS = (
    "kea is the user's second brain: verified facts in STATE.md, decisions with revisit dates, predictions that get "
    "scored, a roadmap, plans reviewed by a council of independent models, and a local search index over their past "
    "AI sessions, chat exports, git history and notes. Start with brain_status. Treat only STATE.md facts (with a "
    "source and a date) as facts; mark anything else UNVERIFIED. Search the evidence before answering questions about "
    "the user's own past work, and cite the ref of what you used. Log what becomes true with log_entry or "
    "record_fact, decisions with decide, and expectations with predict. Questions for the user go on the roadmap "
    "under waiting. council_run sends the plan's packet to outside model providers: ask the user first."
)

Handler = Callable[[Brain, dict[str, Any]], Any]


def _s(description: str, kind: str = "string", **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


def _schema(props: dict[str, dict[str, Any]], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def _status(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.status(brain)


def _state(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.state(brain, int(args.get("entries", 5)))


def _search(brain: Brain, args: dict[str, Any]) -> Any:
    from kea.ingest import refresh_brain
    from kea.store import Store

    with Store(brain.index_path) as store:
        refresh_brain(brain, store)
        hits = store.search(
            str(args["query"]),
            kind=args.get("kind"),
            platform=args.get("platform"),
            mine=bool(args.get("mine")),
            since=args.get("since"),
            limit=min(int(args.get("limit", 10)), 50),
            any_word=bool(args.get("any")),
        )
    return {"query": args["query"], "hits": hits}


def _show(brain: Brain, args: dict[str, Any]) -> Any:
    from kea.store import Store

    with Store(brain.index_path) as store:
        item = store.get(int(args["id"]))
        if item is None:
            raise ValueError(f"no item {args['id']}")
        return {"item": item, "context": store.around(item, min(int(args.get("context", 0)), 20))}


def _decisions(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.decisions(brain, due=bool(args.get("due")), closed=bool(args.get("all")))


def _predictions(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    rows = ledger.predictions(brain, due=bool(args.get("due")), open_only=bool(args.get("open")))
    return {"predictions": rows, "calibration": ledger.calibration(brain)}


def _roadmap(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.roadmap(brain)


def _check(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return [asdict(f) for f in ledger.check(brain)]


def _log(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.log(brain, str(args["text"]), args.get("title"), args.get("source"))


def _fact(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.set_fact(brain, str(args["fact"]), str(args["value"]), str(args["source"]), args.get("verified"))


def _decide(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.decide(
        brain, str(args["title"]), str(args["decision"]), args.get("why"), args.get("revisit"), args.get("source")
    )


def _revisit(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    surprise = args.get("surprise")
    return ledger.revisit(
        brain,
        str(args["id"]),
        str(args["outcome"]),
        args.get("status"),
        int(surprise) if surprise is not None else None,
        args.get("lesson"),
        args.get("next_revisit"),
    )


def _predict(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.predict(
        brain, str(args["claim"]), str(args["by"]), int(args["confidence"]), args.get("plan"), args.get("decision")
    )


def _score(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.score(brain, str(args["id"]), str(args["outcome"]), args.get("note"))


def _idea(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.idea(brain, str(args["text"]), args.get("why"))


def _roadmap_add(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.roadmap_add(brain, str(args["text"]), str(args.get("section", "next")))


def _roadmap_done(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import ledger

    return ledger.roadmap_done(brain, str(args["item"]), args.get("proof"))


def _plans(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    return council.plans(brain)


def _plan_new(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    return {"files": council.plan_new(brain, str(args["slug"]))}


def _brief(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    packet = council.brief(brain, str(args["slug"]))
    return {**asdict(packet), "path": str(packet.path)}


def _synth(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    return council.synth(brain, str(args["slug"]))


def _seats(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    return council.seats(brain)


def _council_run(brain: Brain, args: dict[str, Any]) -> Any:
    from kea import council

    if args.get("confirm") is not True:
        raise ValueError("council_run may send the packet to outside providers: ask the user, then pass confirm: true")
    allowed = brain.setting("council", "agents_may_send", False) is True
    try:
        return council.run(
            brain, str(args["slug"]), args.get("seats"), bool(args.get("resume")), local_only=not allowed
        )
    except council.CouncilError as exc:
        if allowed:
            raise
        raise ValueError(
            f"{exc}. From an agent, only seats on this machine run; the user can run kea council in a terminal, or "
            "allow agents with [council] agents_may_send = true in kea.toml"
        ) from exc


def _ingest(brain: Brain, args: dict[str, Any]) -> Any:
    from pathlib import Path

    from kea.ingest import default_path, ingest, ingest_known
    from kea.store import Store

    with Store(brain.index_path) as store:
        if not args.get("source"):
            return [r.as_dict() for r in ingest_known(brain, store)]
        kind = str(args["source"])
        known = {path for k, path in store.roots() if k == kind}
        default = default_path(kind)
        if default:
            known.add(str(default.resolve()))
        wanted = str(Path(args["path"]).expanduser().resolve()) if args.get("path") else None
        if wanted and wanted not in known:
            raise ValueError(
                "agents may only rescan sources the user added: the user adds new ones with kea ingest in a terminal"
            )
        return ingest(brain, store, kind, wanted, revive=False).as_dict()


def _effort(brain: Brain, args: dict[str, Any]) -> Any:
    from kea.store import Store

    with Store(brain.index_path) as store:
        return store.effort(args.get("since"), bool(args.get("months")))


ID = _s("an id such as D-0002 or P-0004, or just its number")
DATE = _s("a date (2026-12-01) or an offset (+30d, +6w, +3m)")
SLUG = _s("the plan's slug, such as 2026-12-pricing")

# name: (description, input schema, handler, read only, open world)
TOOLS: dict[str, tuple[str, dict[str, Any], Handler, bool, bool]] = {
    "brain_status": (
        "The whole brain at a glance: freshness, due decisions and predictions, roadmap counts, plans, health checks.",
        _schema({}),
        _status,
        True,
        False,
    ),
    "state": (
        "What is true now: every fact in STATE.md with its source and verified date, and the newest log entries.",
        _schema({"entries": _s("how many log entries, default 5", "integer", minimum=1, maximum=50)}),
        _state,
        True,
        False,
    ),
    "search": (
        "Search the evidence: past AI agent sessions, chat exports, git commits, documents and the brain's own notes. "
        "Every hit has a ref back to its exact source.",
        _schema(
            {
                "query": _s('free text; "quote a phrase"'),
                "kind": _s("message, commit, doc or note", enum=["message", "commit", "doc", "note"]),
                "platform": _s("claude-code, codex, opencode, chatgpt, claude, git, docs or brain"),
                "mine": _s("only what the user wrote", "boolean"),
                "since": _s("only items on or after this ISO date"),
                "limit": _s("hits to return, default 10, at most 50", "integer", minimum=1, maximum=50),
                "any": _s("match any word instead of every word", "boolean"),
            },
            ("query",),
        ),
        _search,
        True,
        False,
    ),
    "show": (
        "One search hit in full, with its provenance and, optionally, the turns around it.",
        _schema(
            {
                "id": _s("the hit's id", "integer"),
                "context": _s("items on each side, default 0", "integer", minimum=0, maximum=20),
            },
            ("id",),
        ),
        _show,
        True,
        False,
    ),
    "decisions": (
        "Decisions with why and when to revisit. Default: the active ones.",
        _schema({"due": _s("only those due for revisit", "boolean"), "all": _s("include closed ones", "boolean")}),
        _decisions,
        True,
        False,
    ),
    "predictions": (
        "Predictions with their dates and confidence, and the calibration so far (Brier score, buckets).",
        _schema({"due": _s("only those past their date", "boolean"), "open": _s("only unscored ones", "boolean")}),
        _predictions,
        True,
        False,
    ),
    "roadmap": (
        "Open work: waiting for the owner, next, running by itself, and dates.",
        _schema({}),
        _roadmap,
        True,
        False,
    ),
    "check": (
        "Lint the brain: unsourced facts, stale state, overdue items, malformed entries.",
        _schema({}),
        _check,
        True,
        False,
    ),
    "plans": ("Plans and how far each has come, with the next step for each.", _schema({}), _plans, True, False),
    "seats": (
        "The council's seats and whether each can run (keys set, model local).",
        _schema({}),
        _seats,
        True,
        False,
    ),
    "effort": (
        "Where attention went: the user's own prompts to AI agents per project, optionally per month.",
        _schema({"since": _s("ISO date"), "months": _s("split by month", "boolean")}),
        _effort,
        True,
        False,
    ),
    "log_entry": (
        "Add a dated entry at the top of STATE.md's log: something that happened or is now true, with its source.",
        _schema(
            {"text": _s("what happened"), "title": _s("short title"), "source": _s("where to check it")}, ("text",)
        ),
        _log,
        False,
        False,
    ),
    "record_fact": (
        "Record or update a fact in STATE.md's facts table, with its source and verified date.",
        _schema(
            {
                "fact": _s("what it is about"),
                "value": _s("the value"),
                "source": _s("where it was read"),
                "verified": DATE,
            },
            ("fact", "value", "source"),
        ),
        _fact,
        False,
        False,
    ),
    "decide": (
        "Log a decision in DECISIONS.md with why it was made and when to revisit it.",
        _schema(
            {
                "title": _s("short title"),
                "decision": _s("what was decided, specifically"),
                "why": _s("the reasoning and the facts it leans on"),
                "revisit": _s("a date, an offset, or a trigger in words"),
                "source": _s("where the reasoning lives"),
            },
            ("title", "decision"),
        ),
        _decide,
        False,
        False,
    ),
    "revisit_decision": (
        "Close the loop on a decision: what happened, the surprise (0 to 10), the lesson, and its new status.",
        _schema(
            {
                "id": ID,
                "outcome": _s("what actually happened"),
                "status": _s("new status", enum=["ACTIVE", "COMPLETED", "SUPERSEDED", "REVERSED", "ABANDONED"]),
                "surprise": _s("0 to 10", "integer", minimum=0, maximum=10),
                "lesson": _s("what changes because of it"),
                "next_revisit": DATE,
            },
            ("id", "outcome"),
        ),
        _revisit,
        False,
        False,
    ),
    "predict": (
        "Write down a falsifiable prediction with the date it resolves and a confidence from 0 to 100.",
        _schema(
            {
                "claim": _s("what will be true, specific enough to score"),
                "by": DATE,
                "confidence": _s("0 to 100", "integer", minimum=0, maximum=100),
                "plan": _s("the plan it belongs to"),
                "decision": _s("the decision it tests, such as D-0003"),
            },
            ("claim", "by", "confidence"),
        ),
        _predict,
        False,
        False,
    ),
    "score_prediction": (
        "Score a prediction against what happened: true, false, partial, or void.",
        _schema(
            {"id": ID, "outcome": _s("the outcome", enum=["true", "false", "partial", "void"]), "note": _s("evidence")},
            ("id", "outcome"),
        ),
        _score,
        False,
        False,
    ),
    "add_idea": (
        "Park an idea in IDEAS.md in the user's own words. Nothing starts until they say so.",
        _schema({"text": _s("the idea, in their words"), "why": _s("why it might matter")}, ("text",)),
        _idea,
        False,
        False,
    ),
    "roadmap_add": (
        "Add an open item to ROADMAP.md. Questions for the user go under waiting.",
        _schema(
            {"text": _s("the item"), "section": _s("where", enum=["waiting", "next", "running"])},
            ("text",),
        ),
        _roadmap_add,
        False,
        False,
    ),
    "roadmap_done": (
        "Close a roadmap item, by its number from roadmap or a unique piece of its text, with the proof.",
        _schema({"item": _s("number or text"), "proof": _s("how you know it is done")}, ("item",)),
        _roadmap_done,
        False,
        False,
    ),
    "plan_new": (
        "Start a plan from the brain's plan template.",
        _schema({"slug": SLUG}, ("slug",)),
        _plan_new,
        False,
        False,
    ),
    "brief": (
        "Build a plan's council packet: facts, named frameworks, the proposal and the questions, hashed.",
        _schema({"slug": SLUG}, ("slug",)),
        _brief,
        False,
        False,
    ),
    "synth": (
        "Build the synthesis scaffold from a plan's council reviews: verdicts, convergence, splits and solo insights.",
        _schema({"slug": SLUG}, ("slug",)),
        _synth,
        False,
        False,
    ),
    "council_run": (
        "Send a plan's packet to the ready council seats. Unless the user allowed agents in kea.toml, only seats on "
        "this machine run. Ask the user first, then pass confirm: true.",
        _schema(
            {
                "slug": SLUG,
                "seats": _s("seat names to run; all ready seats when omitted", "array", items={"type": "string"}),
                "resume": _s("skip seats that already reviewed this plan", "boolean"),
                "confirm": _s("true once the user agreed to send the packet out", "boolean"),
            },
            ("slug", "confirm"),
        ),
        _council_run,
        False,
        True,
    ),
    "ingest": (
        "Rescan the local index: with no source, everything known; with a source, its usual place or a path the user "
        "added before. What it reads becomes searchable through search and show, and so can reach you.",
        _schema(
            {
                "source": _s(
                    "the adapter", enum=["claude-code", "codex", "opencode", "chatgpt", "claude", "git", "docs"]
                ),
                "path": _s("what to read; the adapter's default when omitted"),
            }
        ),
        _ingest,
        False,
        False,
    ),
}

RESOURCES = {
    "STATE.md": "What is true now: facts with sources and the dated log",
    "ROADMAP.md": "Open work, what waits for the owner, dates",
    "DECISIONS.md": "Decisions with why and revisit dates",
    "PREDICTIONS.md": "Predictions and how they were scored",
    "AGENTS.md": "How an agent works in this brain",
}

PROMPTS = {
    "start_session": (
        "Load the brain before work: read the roadmap, the newest state, and what is due.",
        "Start this session from the brain. Call brain_status, then roadmap, then state. Tell me in five lines or "
        "fewer: what is due, what waits for me, and what the roadmap says comes next. Ask before starting anything.",
    ),
    "end_session": (
        "Close the session so nothing is lost: roadmap, state, check.",
        "We are ending this session. 1. Close finished roadmap items with roadmap_done, each with its proof. "
        "2. Add new open items, and put every question for me under waiting. 3. Write one log_entry saying what "
        "changed and where the files are. 4. Record new facts with record_fact and decisions with decide. "
        "5. Run check and fix what it finds. Then tell me what you recorded.",
    ),
    "chair_review": (
        "Write the Chair's independent review of a plan, from the packet alone, before reading any other seat.",
        "You are the Chair of the council for plan {slug}. Build the packet with brief, then read ONLY "
        "council/briefing/{slug}.md: no other reviews, no other files. Answer every review question, then the Top 3 "
        "Survival Risks, What Everyone Will Miss, and a confidence table, and end with the COUNCIL-JSON block the "
        "packet describes. Save it as plans/{slug}/reviews/<today>_chair_<your model>.md before reading anything else.",
    ),
}


_TYPES: dict[str, tuple[type, ...]] = {"string": (str,), "integer": (int,), "boolean": (bool,), "array": (list,)}


def _validate(schema: dict[str, Any], args: dict[str, Any]) -> None:
    """
    Check tool arguments against the tool's schema: required, known, types, enums and bounds.

    :param schema: The tool's input schema
    :param args: The arguments
    :raises ValueError: On the first argument that does not fit
    """
    missing = [k for k in schema["required"] if k not in args]
    unknown = [k for k in args if k not in schema["properties"]]
    if missing or unknown:
        raise ValueError(f"missing {missing}, unknown {unknown}" if unknown else f"missing {missing}")
    for key, value in args.items():
        rule = schema["properties"][key]
        kinds = _TYPES.get(rule.get("type", ""), (object,))
        if not isinstance(value, kinds) or (rule.get("type") == "integer" and isinstance(value, bool)):
            raise ValueError(f"{key} must be a {rule.get('type')}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{key} must be one of {rule['enum']}")
        if isinstance(value, int) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                raise ValueError(f"{key} must be from {rule.get('minimum')} to {rule.get('maximum')}")
        if isinstance(value, list) and not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} must be a list of strings")


class Server:
    """Answers MCP messages for one brain."""

    def __init__(self, brain_path: str | None = None) -> None:
        self.brain_path = brain_path
        self._brain: Brain | None = None

    def brain(self) -> Brain:
        """
        Find the brain on first use, so the server starts even before one exists.

        :returns: The brain
        """
        if self._brain is None:
            self._brain = Brain.locate(self.brain_path, prefer_default=True)
        return self._brain

    def handle(self, message: Any) -> dict[str, Any] | None:
        """
        Answer one JSON-RPC message. Never raises.

        :param message: The parsed message
        :returns: The response, or None for a notification
        """
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        mid = message.get("id")
        if mid is None:
            return None
        method = message.get("method")
        raw = message.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        try:
            result = self._dispatch(str(method), params)
        except KeyError:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        except (TypeError, ValueError) as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001  one bad request must never stop the server
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            asked = str(params.get("protocolVersion", ""))
            try:
                where = f" The brain in use: {self.brain().name}, at {self.brain().root}."
            except BrainError:
                where = " No brain was found yet: the user makes one with kea init, or sets KEA_BRAIN."
            return {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "kea", "title": "kea second brain", "version": __version__},
                "instructions": INSTRUCTIONS + where,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [self._tool(name) for name in TOOLS]}
        if method == "tools/call":
            return self._call(str(params.get("name", "")), params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": self._resources()}
        if method == "resources/read":
            return self._read(str(params.get("uri", "")))
        if method == "prompts/list":
            return {"prompts": self._prompts()}
        if method == "prompts/get":
            return self._prompt(str(params.get("name", "")), params.get("arguments") or {})
        raise KeyError(method)

    def _tool(self, name: str) -> dict[str, Any]:
        description, schema, _, readonly, open_world = TOOLS[name]
        return {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": readonly,
                "destructiveHint": False,
                "idempotentHint": readonly,
                "openWorldHint": open_world,
            },
        }

    def _call(self, name: str, args: Any) -> dict[str, Any]:
        if name not in TOOLS:
            raise ValueError(f"unknown tool {name!r}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        _validate(TOOLS[name][1], args)
        try:
            result = TOOLS[name][2](self.brain(), args)
        except BrainError as exc:
            return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
        except Exception as exc:  # noqa: BLE001  the tool's error goes back to the agent, the server lives on
            return {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}
        text = json.dumps(result, indent=1, default=str, ensure_ascii=False)
        cut = len(text) > MAX_TEXT
        if cut:
            text = text[:MAX_TEXT] + "\n... (cut; ask for less with limit)"
        out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(result, dict) and not cut:
            out["structuredContent"] = json.loads(json.dumps(result, default=str))
        return out

    def _resources(self) -> list[dict[str, Any]]:
        try:
            root = self.brain().root
        except BrainError:
            return []
        return [
            {"uri": f"kea://{name}", "name": name, "description": about, "mimeType": "text/markdown"}
            for name, about in RESOURCES.items()
            if (root / name).is_file()
        ]

    def _read(self, uri: str) -> dict[str, Any]:
        name = uri.removeprefix("kea://")
        if name not in RESOURCES:
            raise ValueError(f"unknown resource {uri}")
        path: Path = self.brain().file(name)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}

    def _prompts(self) -> list[dict[str, Any]]:
        out = []
        for name, (description, text) in PROMPTS.items():
            prompt: dict[str, Any] = {"name": name, "description": description}
            if "{slug}" in text:
                prompt["arguments"] = [{"name": "slug", "description": "the plan's slug", "required": True}]
            out.append(prompt)
        return out

    def _prompt(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in PROMPTS:
            raise ValueError(f"unknown prompt {name!r}")
        description, text = PROMPTS[name]
        if "{slug}" in text:
            slug = str(args.get("slug") or "")
            if not slug:
                raise ValueError("this prompt needs a slug")
            text = text.replace("{slug}", slug)
        return {"description": description, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def serve(server: Server, reader: TextIO | None = None, writer: TextIO | None = None) -> None:
    """
    Serve MCP over stdio until the input closes: one JSON message per line in, one per line out.

    :param server: The handler
    :param reader: Input; stdin when omitted
    :param writer: Output; stdout when omitted
    """
    src = reader or sys.stdin
    out = writer or sys.stdout
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            try:
                message = json.loads(line)
            except ValueError, RecursionError:
                replies: list[dict[str, Any]] = [
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                ]
            else:
                batch = message if isinstance(message, list) else [message]
                replies = [r for r in (server.handle(m) for m in batch) if r is not None]
            text = "".join(json.dumps(reply, default=str, ensure_ascii=True) + "\n" for reply in replies)
        except Exception as exc:  # noqa: BLE001  one bad line gets one error, the server lives on
            error = {"code": -32603, "message": f"{type(exc).__name__}"}
            text = json.dumps({"jsonrpc": "2.0", "id": None, "error": error}) + "\n"
        out.write(text)
        out.flush()
