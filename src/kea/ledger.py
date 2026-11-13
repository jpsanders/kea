"""
The ledger: facts, the dated log, decisions, predictions, ideas and the roadmap, all in plain Markdown.

Every write goes through the same rules: one fact is one sourced, dated row; a decision carries why and when to look
again; a prediction carries a date and a confidence, and gets scored. :func:`check` lints the lot and :func:`status`
reads it back as one picture, health checks included.
"""

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from kea.brain import Brain, parse_date
from kea.markdown import (
    SEP,
    Entry,
    insert_after_marker,
    is_separator,
    malformed_headings,
    next_id,
    parse_entries,
    parse_log,
    read_lines,
    replace_entry,
    section_span,
    table_cells,
    table_row,
    write_lines,
)

STATE = "STATE.md"
DECISIONS = "DECISIONS.md"
PREDICTIONS = "PREDICTIONS.md"
IDEAS = "IDEAS.md"
ROADMAP = "ROADMAP.md"
METRICS = "METRICS.md"

DECISION_STATUSES = ("ACTIVE", "COMPLETED", "SUPERSEDED", "REVERSED", "ABANDONED")
OUTCOMES = {"TRUE": 1.0, "FALSE": 0.0, "PARTIAL": 0.5}
CLOSED_PREDICTION = {"TRUE", "FALSE", "PARTIAL", "VOID"}
ROADMAP_SECTIONS = {"waiting": "Waiting for", "next": "Next", "running": "Running by itself"}


class LedgerError(Exception):
    """A ledger write was refused: a bad id, a bad value, or a file kea cannot read."""


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def _need_file(brain: Brain, name: str) -> Path:
    path = brain.file(name)
    if not path.is_file():
        raise LedgerError(f"{name} is missing from {brain.root}; run kea init {brain.root} to restore it")
    return path


# ---------------------------------------------------------------- facts and the log


@dataclass
class Fact:
    """One row of STATE.md's facts table."""

    fact: str
    value: str
    source: str
    verified: str
    line: int


def facts(brain: Brain) -> list[Fact]:
    """
    Read STATE.md's facts table.

    :param brain: The brain
    :returns: The facts, in table order
    """
    lines = read_lines(brain.file(STATE))
    span = section_span(lines, "Facts")
    if not span:
        return []
    out = []
    header_seen = False
    for i in range(span[0] + 1, span[1]):
        line = lines[i]
        if not line.strip().startswith("|"):
            if header_seen and line.strip():
                break
            continue
        if is_separator(line):
            continue
        cells = table_cells(line) + ["", "", "", ""]
        if not header_seen:
            header_seen = True
            continue
        out.append(Fact(cells[0], cells[1], cells[2], cells[3], i + 1))
    return out


def set_fact(brain: Brain, fact: str, value: str, source: str, verified: str | None = None) -> dict[str, Any]:
    """
    Record a fact with its source and the date it was verified, replacing the row of the same name.

    :param brain: The brain
    :param fact: What the fact is about, such as ``Cash on hand``
    :param value: The value
    :param source: Where it was read: a file, a URL, a statement, a system
    :param verified: When it was checked; today when omitted
    :returns: The row written and whether it was new
    :raises LedgerError: When a field is empty or the date is not a date
    """
    fact, value, source = _clean(fact), _clean(value), _clean(source)
    if not (fact and value and source):
        raise LedgerError("a fact needs a name, a value and a source")
    when = parse_date(verified, brain.today()) if verified else brain.today()
    if when is None:
        raise LedgerError(f"not a date: {verified!r}")
    path = _need_file(brain, STATE)
    lines = read_lines(path)
    row = table_row([fact, value, source, when.isoformat()])
    existing = {f.fact.lower(): f for f in facts(brain)}
    created = fact.lower() not in existing
    if not created:
        lines[existing[fact.lower()].line - 1] = row
    else:
        span = section_span(lines, "Facts")
        if span is None:
            title = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1)
            block = ["## Facts", "", table_row(["Fact", "Value", "Source", "Verified"]), "| --- | --- | --- | --- |"]
            lines[title + 1 : title + 1] = [""] + block + [row, ""]
        else:
            last = span[0]
            for i in range(span[0] + 1, span[1]):
                if lines[i].strip().startswith("|"):
                    last = i
            if last == span[0]:
                header = [table_row(["Fact", "Value", "Source", "Verified"]), "| --- | --- | --- | --- |"]
                lines[last + 1 : last + 1] = [""] + header + [row]
            else:
                lines.insert(last + 1, row)
    write_lines(path, lines)
    return {"fact": fact, "value": value, "source": source, "verified": when.isoformat(), "new": created}


def log(brain: Brain, text: str, title: str | None = None, source: str | None = None) -> dict[str, Any]:
    """
    Add a dated entry at the top of STATE.md's log.

    :param brain: The brain
    :param text: What happened or what is now true
    :param title: A short title; the first sentence of the text when omitted
    :param source: Where it can be checked
    :returns: The entry written
    :raises LedgerError: When the text is empty
    """
    text = text.strip()
    if not text:
        raise LedgerError("a log entry needs text")
    if not title:
        first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        title = first if len(first) <= 80 else first[:77].rsplit(" ", 1)[0] + "..."
    title = _clean(title).rstrip(".")
    moment = brain.now()
    body = text + (f"\n\nSource: {_clean(source)}" if source else "")
    block = [f"### {moment:%Y-%m-%d %H:%M}{SEP}{title}", ""] + body.splitlines()
    path = _need_file(brain, STATE)
    write_lines(path, insert_after_marker(read_lines(path), "kea:log", block))
    return {"date": f"{moment:%Y-%m-%d}", "time": f"{moment:%H:%M}", "title": title, "text": text, "source": source}


def state(brain: Brain, entries: int = 5) -> dict[str, Any]:
    """
    Read what is true now: every fact and the newest log entries.

    :param brain: The brain
    :param entries: How many log entries to return
    :returns: Facts, entries and the count of UNVERIFIED markers
    """
    lines = read_lines(brain.file(STATE))
    log_entries = parse_log(lines)
    log_entries.sort(key=lambda e: (e.date, e.time), reverse=True)
    return {
        "facts": [{k: v for k, v in asdict(f).items() if k != "line"} for f in facts(brain)],
        "log": [
            {"date": e.date, "time": e.time, "title": e.title, "text": "\n".join(e.body)} for e in log_entries[:entries]
        ],
        "log_total": len(log_entries),
        "unverified": sum(line.count("UNVERIFIED") for line in lines),
    }


# ---------------------------------------------------------------- decisions


def _entries(brain: Brain, name: str) -> tuple[Path, list[str], list[Entry]]:
    path = brain.file(name)
    lines = read_lines(path)
    return path, lines, parse_entries(lines)


def _find(entries: list[Entry], ident: str) -> Entry:
    wanted = ident.strip().upper()
    if re.fullmatch(r"\d+", wanted) and entries:
        wanted = f"{entries[0].id.split('-')[0]}-{int(wanted):04d}"
    for entry in entries:
        if entry.id == wanted:
            return entry
    raise LedgerError(f"no entry {ident}")


def _decision_view(entry: Entry, today: date) -> dict[str, Any]:
    revisit = entry.get("Revisit")
    when = parse_date(revisit, today) if revisit and re.search(r"\d{4}-\d{2}-\d{2}", revisit) else None
    status = entry.get("Status", "ACTIVE").upper().split()[0] if entry.get("Status") else "ACTIVE"
    return {
        "id": entry.id,
        "date": entry.head[0] if entry.head else "",
        "title": entry.head[1] if len(entry.head) > 1 else "",
        "decision": entry.get("Decision"),
        "status": status,
        "why": entry.get("Why"),
        "revisit": revisit,
        "source": entry.get("Source"),
        "outcome": entry.get("Outcome"),
        "due": bool(status == "ACTIVE" and when and when <= today),
    }


def decide(
    brain: Brain,
    title: str,
    decision: str,
    why: str | None = None,
    revisit: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """
    Log a decision with why it was made and when to look at it again.

    :param brain: The brain
    :param title: A short title
    :param decision: What was decided, specifically
    :param why: The reasoning and the facts it leaned on
    :param revisit: A date or offset (``2027-01-15``, ``+90d``), or a trigger in words
    :param source: Where the reasoning lives, such as a plan or a journal note
    :returns: The decision written
    :raises LedgerError: When the title or the decision is empty
    """
    if not (title.strip() and decision.strip()):
        raise LedgerError("a decision needs a title and the decision itself")
    path, lines, entries = _entries(brain, DECISIONS)
    if not path.is_file():
        raise LedgerError(f"{DECISIONS} is missing; run kea init {brain.root} to restore it")
    when = parse_date(revisit, brain.today()) if revisit else None
    entry = Entry(next_id(entries, "D"), [brain.today().isoformat(), _clean(title)], {})
    entry.set("Decision", decision)
    entry.set("Status", "ACTIVE")
    if why:
        entry.set("Why", why)
    entry.set("Revisit", when.isoformat() if when else (revisit or "not set"))
    if source:
        entry.set("Source", source)
    write_lines(path, insert_after_marker(lines, "kea:entries", entry.render()))
    return _decision_view(entry, brain.today())


def decisions(brain: Brain, due: bool = False, closed: bool = False) -> list[dict[str, Any]]:
    """
    List decisions, active ones first.

    :param brain: The brain
    :param due: Only the active decisions whose revisit date has come
    :param closed: Include decisions that are no longer active
    :returns: The decisions
    """
    views = [_decision_view(e, brain.today()) for e in _entries(brain, DECISIONS)[2]]
    if due:
        return [v for v in views if v["due"]]
    return [v for v in views if closed or v["status"] == "ACTIVE"]


def revisit(
    brain: Brain,
    ident: str,
    outcome: str,
    status: str | None = None,
    surprise: int | None = None,
    lesson: str | None = None,
    next_revisit: str | None = None,
) -> dict[str, Any]:
    """
    Close the loop on a decision: what actually happened, how surprising it was, and what changes.

    :param brain: The brain
    :param ident: The decision's id, such as ``D-0002`` or ``2``
    :param outcome: What actually happened
    :param status: The new status: ACTIVE, COMPLETED, SUPERSEDED, REVERSED or ABANDONED; unchanged when omitted
    :param surprise: How much reality differed from the expectation, 0 to 10
    :param lesson: What changes in the brain because of it
    :param next_revisit: When to look again, for a decision that stays active
    :returns: The decision as it now stands
    :raises LedgerError: When the id, the status or the surprise is out of range
    """
    path, lines, entries = _entries(brain, DECISIONS)
    entry = _find(entries, ident)
    if status and status.upper() not in DECISION_STATUSES:
        raise LedgerError(f"status must be one of {', '.join(DECISION_STATUSES)}")
    if surprise is not None and not 0 <= surprise <= 10:
        raise LedgerError("surprise is 0 to 10")
    entry.set("Outcome", outcome)
    if surprise is not None:
        entry.set("Surprise", str(surprise))
    if lesson:
        entry.set("Lesson", lesson)
    entry.set("Revisited", brain.today().isoformat())
    if status:
        entry.set("Status", status.upper())
    if next_revisit:
        when = parse_date(next_revisit, brain.today())
        entry.set("Revisit", when.isoformat() if when else next_revisit)
    write_lines(path, replace_entry(lines, entry))
    return _decision_view(entry, brain.today())


# ---------------------------------------------------------------- predictions


def _prediction_view(entry: Entry, today: date) -> dict[str, Any]:
    by_text = entry.head[0] if entry.head else ""
    by = parse_date(by_text.removeprefix("by").strip(), today) if by_text else None
    conf_text = entry.head[1] if len(entry.head) > 1 else ""
    m = re.search(r"(\d{1,3})", conf_text)
    confidence = int(m[1]) if m else None
    status = (entry.get("Status") or "OPEN").upper().split()[0]
    return {
        "id": entry.id,
        "by": by.isoformat() if by else by_text,
        "confidence": confidence,
        "claim": entry.head[2] if len(entry.head) > 2 else entry.get("Claim"),
        "status": status,
        "made": entry.get("Made"),
        "plan": entry.get("Plan"),
        "decision": entry.get("Decision"),
        "scored": entry.get("Scored"),
        "note": entry.get("Note"),
        "due": bool(status == "OPEN" and by and by <= today),
    }


def predict(
    brain: Brain,
    claim: str,
    by: str,
    confidence: int,
    plan: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """
    Write down a falsifiable prediction, with the date it resolves and how sure you are.

    :param brain: The brain
    :param claim: What will be true, specific enough that anyone could score it
    :param by: The date it resolves, or an offset such as ``+30d``
    :param confidence: How likely it is to come true, 0 to 100
    :param plan: The plan it belongs to, such as ``2026-12-pricing``
    :param decision: The decision it tests, such as ``D-0003``
    :returns: The prediction written
    :raises LedgerError: When the claim is empty, the date is not a date or the confidence is out of range
    """
    claim = _clean(claim)
    if not claim:
        raise LedgerError("a prediction needs a claim")
    when = parse_date(by, brain.today())
    if when is None:
        raise LedgerError(f"not a date: {by!r} (use 2026-12-01, +30d, +6w or +3m)")
    if not 0 <= confidence <= 100:
        raise LedgerError("confidence is 0 to 100")
    path, lines, entries = _entries(brain, PREDICTIONS)
    if not path.is_file():
        raise LedgerError(f"{PREDICTIONS} is missing; run kea init {brain.root} to restore it")
    entry = Entry(next_id(entries, "P"), [f"by {when.isoformat()}", f"{confidence}%", claim], {})
    entry.set("Made", brain.today().isoformat())
    entry.set("Status", "OPEN")
    if plan:
        entry.set("Plan", plan)
    if decision:
        entry.set("Decision", decision.upper())
    write_lines(path, insert_after_marker(lines, "kea:entries", entry.render()))
    return _prediction_view(entry, brain.today())


def score(brain: Brain, ident: str, outcome: str, note: str | None = None) -> dict[str, Any]:
    """
    Score a prediction against what happened.

    :param brain: The brain
    :param ident: The prediction's id, such as ``P-0004`` or ``4``
    :param outcome: TRUE, FALSE, PARTIAL, or VOID when the question stopped making sense
    :param note: What happened, with its source
    :returns: The prediction as scored
    :raises LedgerError: When the id or the outcome is not known
    """
    word = outcome.strip().upper()
    word = {"YES": "TRUE", "Y": "TRUE", "NO": "FALSE", "N": "FALSE", "HALF": "PARTIAL"}.get(word, word)
    if word not in CLOSED_PREDICTION:
        raise LedgerError("outcome must be true, false, partial or void")
    path, lines, entries = _entries(brain, PREDICTIONS)
    entry = _find(entries, ident)
    entry.set("Status", word)
    entry.set("Scored", brain.today().isoformat())
    if note:
        entry.set("Note", note)
    write_lines(path, replace_entry(lines, entry))
    return _prediction_view(entry, brain.today())


def predictions(brain: Brain, due: bool = False, open_only: bool = False) -> list[dict[str, Any]]:
    """
    List predictions.

    :param brain: The brain
    :param due: Only open predictions whose date has come
    :param open_only: Only predictions not scored yet
    :returns: The predictions
    """
    views = [_prediction_view(e, brain.today()) for e in _entries(brain, PREDICTIONS)[2]]
    if due:
        return [v for v in views if v["due"]]
    if open_only:
        return [v for v in views if v["status"] == "OPEN"]
    return views


def calibration(brain: Brain) -> dict[str, Any]:
    """
    Score how well calibrated the predictions were: the Brier score, skill over a coin and ten buckets.

    PARTIAL counts as half true. VOID and open predictions are left out.

    :param brain: The brain
    :returns: The numbers, buckets included
    """
    scored = [
        (p["confidence"] / 100, OUTCOMES[p["status"]])
        for p in predictions(brain)
        if p["status"] in OUTCOMES and p["confidence"] is not None
    ]
    n = len(scored)
    if not n:
        return {"scored": 0, "brier": None, "skill": None, "stated": None, "observed": None, "buckets": []}
    brier = sum((p - o) ** 2 for p, o in scored) / n
    stated = sum(p for p, _ in scored) / n
    observed = sum(o for _, o in scored) / n
    buckets = []
    for low in range(0, 100, 10):
        inside = [(p, o) for p, o in scored if low <= p * 100 < low + 10 or (low == 90 and p == 1.0)]
        if inside:
            buckets.append(
                {
                    "range": f"{low} to {low + 9 if low < 90 else 100}",
                    "count": len(inside),
                    "stated": round(100 * sum(p for p, _ in inside) / len(inside)),
                    "observed": round(100 * sum(o for _, o in inside) / len(inside)),
                }
            )
    return {
        "scored": n,
        "brier": round(brier, 3),
        "skill": round(1 - brier / 0.25, 3),
        "stated": round(100 * stated),
        "observed": round(100 * observed),
        "buckets": buckets,
    }


# ---------------------------------------------------------------- ideas


def idea(brain: Brain, text: str, why: str | None = None) -> dict[str, Any]:
    """
    Park an idea in IDEAS.md, in the words it came in, so it stops taking up room in your head.

    :param brain: The brain
    :param text: The idea
    :param why: Why it might matter
    :returns: The idea written
    :raises LedgerError: When the text is empty or IDEAS.md is missing
    """
    text = _clean(text)
    if not text:
        raise LedgerError("an idea needs text")
    path, lines, entries = _entries(brain, IDEAS)
    if not path.is_file():
        raise LedgerError(f"{IDEAS} is missing; run kea init {brain.root} to restore it")
    title = text if len(text) <= 70 else text[:67].rsplit(" ", 1)[0] + "..."
    entry = Entry(next_id(entries, "I"), [brain.today().isoformat(), title], {})
    entry.set("Words", text)
    if why:
        entry.set("Why", why)
    entry.set("Status", "PARKED")
    write_lines(path, insert_after_marker(lines, "kea:entries", entry.render()))
    return {"id": entry.id, "date": entry.head[0], "title": title, "status": "PARKED"}


def ideas(brain: Brain) -> list[dict[str, Any]]:
    """
    List the parked ideas.

    :param brain: The brain
    :returns: The ideas
    """
    return [
        {
            "id": e.id,
            "date": e.head[0] if e.head else "",
            "title": e.head[1] if len(e.head) > 1 else "",
            "status": (e.get("Status") or "PARKED").upper(),
        }
        for e in _entries(brain, IDEAS)[2]
    ]


# ---------------------------------------------------------------- roadmap


ITEM = re.compile(r"^\s*- \[(?P<done>[ xX])\] (?P<text>.*)$")


def roadmap(brain: Brain) -> dict[str, Any]:
    """
    Read ROADMAP.md: what waits for the owner, what comes next, what runs by itself, and the dates.

    :param brain: The brain
    :returns: Open items per section, numbered as ``kea roadmap done`` counts them, and the dates
    """
    lines = read_lines(brain.file(ROADMAP))
    out: dict[str, Any] = {"owner": brain.owner, "waiting": [], "next": [], "running": [], "dates": []}
    number = 0
    for key, title in ROADMAP_SECTIONS.items():
        span = section_span(lines, title)
        if not span:
            continue
        for i in range(span[0] + 1, span[1]):
            m = ITEM.match(lines[i])
            if m and m["done"] == " ":
                number += 1
                out[key].append({"n": number, "text": m["text"].strip(), "line": i + 1})
    span = section_span(lines, "Dates")
    if span:
        for i in range(span[0] + 1, span[1]):
            if lines[i].strip().startswith("|") and not is_separator(lines[i]):
                cells = table_cells(lines[i])
                when = parse_date(cells[0], brain.today()) if cells and re.match(r"\d{4}-", cells[0]) else None
                if when:
                    out["dates"].append({"date": when.isoformat(), "what": " ".join(cells[1:]).strip()})
    out["dates"].sort(key=lambda d: d["date"])
    return out


def roadmap_add(brain: Brain, text: str, section: str = "next") -> dict[str, Any]:
    """
    Add an open item to a roadmap section.

    :param brain: The brain
    :param text: The item
    :param section: ``waiting`` (for the owner), ``next`` or ``running``
    :returns: The item written
    :raises LedgerError: When the section is unknown or the text empty
    """
    if section not in ROADMAP_SECTIONS:
        raise LedgerError(f"section must be one of {', '.join(ROADMAP_SECTIONS)}")
    text = _clean(text)
    if not text:
        raise LedgerError("a roadmap item needs text")
    path = _need_file(brain, ROADMAP)
    lines = read_lines(path)
    title = ROADMAP_SECTIONS[section] + (f" {brain.owner}" if section == "waiting" else "")
    item = f"- [ ] {text} (added {brain.today().isoformat()})"
    span = section_span(lines, ROADMAP_SECTIONS[section])
    if span is None:
        lines += ["", f"## {title}", "", item]
    else:
        last = span[0]
        for i in range(span[0] + 1, span[1]):
            if ITEM.match(lines[i]):
                last = i
        if last == span[0]:
            last = max((i for i in range(span[0], span[1]) if lines[i].strip()), default=span[0])
            lines[last + 1 : last + 1] = ["", item]
        else:
            lines.insert(last + 1, item)
    write_lines(path, lines)
    return {"section": section, "text": text}


def roadmap_done(brain: Brain, ref: str, proof: str | None = None) -> dict[str, Any]:
    """
    Close a roadmap item by its number in ``kea roadmap`` or by a unique piece of its text.

    :param brain: The brain
    :param ref: The number or the text
    :param proof: How you know it is done
    :returns: The item closed
    :raises LedgerError: When no single open item matches
    """
    view = roadmap(brain)
    items = view["waiting"] + view["next"] + view["running"]
    if ref.strip().isdigit():
        hits = [i for i in items if i["n"] == int(ref)]
    else:
        hits = [i for i in items if ref.strip().lower() in i["text"].lower()]
    if len(hits) != 1:
        raise LedgerError(f"{len(hits)} open items match {ref!r}; use the number from kea roadmap")
    path = _need_file(brain, ROADMAP)
    lines = read_lines(path)
    index = hits[0]["line"] - 1
    stamp = f" (done {brain.today().isoformat()}" + (f": {_clean(proof)})" if proof else ")")
    lines[index] = lines[index].replace("- [ ]", "- [x]", 1).rstrip() + stamp
    write_lines(path, lines)
    return {"text": hits[0]["text"], "done": brain.today().isoformat(), "proof": proof}


# ---------------------------------------------------------------- check and status


@dataclass
class Finding:
    """One thing ``kea check`` found."""

    level: str
    file: str
    line: int
    message: str


def _frameworks(brain: Brain) -> list[Path]:
    folder = brain.file("frameworks")
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.glob("*.md")) if not p.name.startswith("_") and p.name != "INDEX.md"]


def framework_gaps(path: Path) -> list[str]:
    """
    Find what a framework file lacks: boundary conditions, a retrodiction score.

    :param path: The framework file
    :returns: The gaps, in words
    """
    text = path.read_text(encoding="utf-8")
    gaps = []
    span = section_span(text.splitlines(), "Boundary Conditions")
    filled = False
    if span:
        rows = [ln for ln in text.splitlines()[span[0] : span[1]] if ln.strip().startswith("|")]
        body = [r for r in rows[1:] if not is_separator(r)]
        filled = any(any(c for c in table_cells(r)) for r in body)
    if not filled:
        gaps.append("no boundary conditions")
    m = re.search(r"^retrodiction_score:\s*(.*)$", text, re.M)
    if not m or not re.match(r"\d", m[1].strip()):
        gaps.append("no retrodiction score")
    return gaps


def check(brain: Brain) -> list[Finding]:
    """
    Lint the brain: unsourced facts, stale state, malformed entries, overdue decisions and predictions.

    :param brain: The brain
    :returns: Findings, errors first
    """
    found: list[Finding] = []
    today = brain.today()
    add = found.append
    stale = int(brain.setting("brain", "stale_after_days", 14))
    fact_stale = int(brain.setting("brain", "facts_stale_after_days", 90))
    if not brain.file(STATE).is_file():
        add(Finding("error", STATE, 0, "missing: STATE.md is the one source of facts"))
    else:
        entries = parse_log(read_lines(brain.file(STATE)))
        newest = max((e.date for e in entries), default=None)
        if newest is None:
            add(Finding("warn", STATE, 0, "the log has no entries yet: add one with kea log"))
        elif (today - date.fromisoformat(newest)).days > stale:
            add(Finding("warn", STATE, 0, f"last entry {newest}, more than {stale} days ago"))
        for f in facts(brain):
            if not f.source:
                add(Finding("error", STATE, f.line, f"fact {f.fact!r} has no source"))
            when = parse_date(f.verified, today) if f.verified else None
            if when is None:
                add(Finding("error", STATE, f.line, f"fact {f.fact!r} has no verified date"))
            elif (today - when).days > fact_stale:
                add(Finding("warn", STATE, f.line, f"fact {f.fact!r} last verified {when}, recheck it"))
    for name, prefix in ((DECISIONS, "D"), (PREDICTIONS, "P"), (IDEAS, "I")):
        lines = read_lines(brain.file(name))
        for line_no, text in malformed_headings(lines):
            add(Finding("error", name, line_no, f"heading does not parse: {text[:60]}"))
        seen: set[str] = set()
        for entry in parse_entries(lines):
            if entry.id in seen:
                add(Finding("error", name, entry.start + 1, f"duplicate id {entry.id}"))
            seen.add(entry.id)
            if not entry.id.startswith(prefix + "-"):
                add(Finding("error", name, entry.start + 1, f"{entry.id} does not belong in {name}"))
    for d in decisions(brain):
        if not d["decision"]:
            add(Finding("error", DECISIONS, 0, f"{d['id']} has no Decision field"))
        if d["due"]:
            add(Finding("warn", DECISIONS, 0, f"{d['id']} is due for revisit since {d['revisit']}: kea revisit"))
        elif not d["revisit"] or d["revisit"] == "not set":
            add(Finding("warn", DECISIONS, 0, f"{d['id']} has no revisit date or trigger"))
    for p in predictions(brain):
        if p["confidence"] is None or not 0 <= p["confidence"] <= 100:
            add(Finding("error", PREDICTIONS, 0, f"{p['id']} has no confidence from 0 to 100"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p["by"])):
            add(Finding("error", PREDICTIONS, 0, f"{p['id']} has no resolution date"))
        if p["due"]:
            add(Finding("warn", PREDICTIONS, 0, f"{p['id']} resolved on {p['by']} and is not scored: kea score"))
    for path in _frameworks(brain):
        for gap in framework_gaps(path):
            add(Finding("warn", f"frameworks/{path.name}", 0, gap))
    for plan in plans_with_production(brain):
        count = sum(1 for p in predictions(brain) if p["plan"] and plan in p["plan"])
        if count < 3:
            add(Finding("warn", f"plans/{plan}", 0, f"decided with {count} predictions; the rule is at least 3"))
    order = {"error": 0, "warn": 1, "info": 2}
    found.sort(key=lambda f: (order.get(f.level, 3), f.file, f.line))
    return found


def is_decided(plan: Path) -> bool:
    """
    Tell whether a plan was decided: its production plan carries a ``**Decided:**`` date, not just the template.

    :param plan: The plan's folder
    :returns: True once decided
    """
    path = plan / "20-production-plan.md"
    if not path.is_file():
        return False
    return bool(re.search(r"\*\*Decided:\*\*\s*\d{4}-\d{2}-\d{2}", path.read_text(encoding="utf-8")))


def plans_with_production(brain: Brain) -> list[str]:
    """
    List the plans that were decided.

    :param brain: The brain
    :returns: Plan slugs
    """
    folder = brain.file("plans")
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith("_") and is_decided(p))


def _age_words(age: int | None) -> str:
    if age is None:
        return "no entries yet"
    return "last entry " + ("today" if age == 0 else "yesterday" if age == 1 else f"{age} days ago")


def _days(text: str, today: date) -> int | None:
    when = parse_date(text, today) if text else None
    return (today - when).days if when else None


def status(brain: Brain) -> dict[str, Any]:
    """
    Read the whole brain back as one picture, with its health checks.

    :param brain: The brain
    :returns: Counts, due items and health checks
    """
    from kea import council  # loaded here only: the plan stages are the one thing status needs from it

    today = brain.today()
    now_state = state(brain, entries=1)
    newest = now_state["log"][0]["date"] if now_state["log"] else None
    fact_stale = int(brain.setting("brain", "facts_stale_after_days", 90))
    stale_facts = [f for f in now_state["facts"] if (_days(f["verified"], today) or 0) > fact_stale]
    all_decisions = decisions(brain, closed=True)
    active = [d for d in all_decisions if d["status"] == "ACTIVE"]
    preds = predictions(brain)
    cal = calibration(brain)
    road = roadmap(brain)
    soon = [d for d in road["dates"] if 0 <= (date.fromisoformat(d["date"]) - today).days <= 14]
    frameworks = {p.name: framework_gaps(p) for p in _frameworks(brain)}
    plan_list = council.plans(brain)
    metrics_debt = sum(line.count("NOT MEASURED") for line in read_lines(brain.file(METRICS)))
    age = (today - date.fromisoformat(newest)).days if newest else None
    stale_after = int(brain.setting("brain", "stale_after_days", 14))
    decided = plans_with_production(brain)
    short_plans = [p for p in decided if sum(1 for x in preds if x["plan"] and p in x["plan"]) < 3]
    health = [
        {
            "check": "STATE.md is fresh",
            "ok": age is not None and age <= stale_after,
            "detail": _age_words(age),
        },
        {
            "check": "facts are sourced and rechecked",
            "ok": not stale_facts,
            "detail": f"{len(stale_facts)} of {len(now_state['facts'])} facts past their recheck",
        },
        {
            "check": "decisions are revisited on time",
            "ok": not any(d["due"] for d in active),
            "detail": f"{sum(1 for d in active if d['due'])} due",
        },
        {
            "check": "predictions are scored",
            "ok": not any(p["due"] for p in preds),
            "detail": f"{sum(1 for p in preds if p['due'])} past their date",
        },
        {
            "check": "frameworks have boundary conditions",
            "ok": not any(frameworks.values()),
            "detail": f"{sum(1 for g in frameworks.values() if g)} of {len(frameworks)} incomplete",
        },
        {
            "check": "decided plans carry 3 predictions",
            "ok": not short_plans,
            "detail": f"{len(short_plans)} of {len(decided)} short" if decided else "no decided plans yet",
        },
    ]
    return {
        "brain": brain.name,
        "owner": brain.owner,
        "root": str(brain.root),
        "today": today.isoformat(),
        "state": {
            "last_entry": newest,
            "age_days": age,
            "facts": len(now_state["facts"]),
            "stale_facts": len(stale_facts),
            "unverified": now_state["unverified"],
        },
        "decisions": {
            "active": len(active),
            "due": [d["id"] for d in active if d["due"]],
            "total": len(all_decisions),
        },
        "predictions": {
            "open": sum(1 for p in preds if p["status"] == "OPEN"),
            "due": [p["id"] for p in preds if p["due"]],
            "scored": cal["scored"],
            "brier": cal["brier"],
            "skill": cal["skill"],
        },
        "ideas": sum(1 for i in ideas(brain) if i["status"] == "PARKED"),
        "roadmap": {
            "waiting": len(road["waiting"]),
            "next": len(road["next"]),
            "running": len(road["running"]),
            "soon": soon,
        },
        "metrics_not_measured": metrics_debt,
        "plans": plan_list,
        "health": health,
        "attention": sum(1 for h in health if not h["ok"]),
    }
