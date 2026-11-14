"""
The council: independent models review a plan from one identical packet, and nobody sees anybody else's review.

The loop is propose, brief, review, synthesize, decide. ``brief`` builds one packet from the facts, the frameworks the
plan names and the plan itself, and hashes it, so every seat provably read the same thing. ``run`` sends it to every
seat in parallel. ``synth`` reads the machine readable block at the end of each review and builds the scaffold the
Chair (your own agent) turns into the synthesis and the production plan. The Chair reviews first, from the packet
alone, before reading any other seat.
"""

import concurrent.futures
import hashlib
import json
import math
import re
import shutil
import socket
import threading
import time
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kea.brain import INDEX_DIR, Brain
from kea.ledger import is_decided
from kea.providers import ProviderError, Seat, complete, probe
from kea.redact import is_denied, redact_count

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
JSON_BLOCK = re.compile(r"<!--\s*COUNCIL-JSON\s*(.*?)\s*COUNCIL-JSON\s*-->", re.S)
DEFAULT_INCLUDE = ["STATE.md", "METRICS.md", "DECISIONS.md"]
STOP = set(
    "the a an and or of to in for on with is are be by at as it this that from your you we not no if then than but so "
    "do does can will would should must have has plan".split()
)

SYSTEM = """You are the {role} seat on an independent review council for {owner}.

Your lens: {lens}

Rules:
1. You review INDEPENDENTLY. You have not seen any other reviewer's work. Do not guess at what other models think.
2. Facts marked UNVERIFIED are not facts. When a recommendation leans on an unverified number, say so and say how
    to verify it.
3. Survival risks first. No encouragement and no hedging for comfort.
4. Every recommendation gets a confidence from 0 to 100 and one line naming its key uncertainty.
5. Be specific. "Improve pricing" is worthless; "charge 49 a month, 490 a year, paid upfront" is useful.
6. If the plan is right, say it is right. Manufactured disagreement is as useless as manufactured agreement.

Write plainly: short declarative sentences, active voice, concrete numbers, dates and names. No filler words.
Use the exact section headings the briefing asks for, and end with the machine readable block it describes."""

CONTRACT = """

---

## MANDATORY OUTPUT CONTRACT

Answer every review question in the briefing, in order, under `## N. <question>` headings. For each: what the plan
gets right, what it gets wrong, what you would change, and a confidence from 0 to 100 with the key uncertainty.

Then add these sections:

## Top 3 Survival Risks
Ranked. Probability, impact, and whether the plan addresses each.

## What Everyone Will Miss
The one thing you believe is true that a competent reviewer would probably overlook.

## Confidence Table
| # | Recommendation | Confidence | Key uncertainty |

Close with EXACTLY this block, valid JSON, no code fences inside it:

<!--COUNCIL-JSON
{"seat":"<your seat>","verdict":"PROCEED|PROCEED_WITH_CHANGES|STOP","headline":"<one sentence>",
"top_risk":"<one sentence>","overall_confidence":<0-100>,
"recommendations":[{"id":"R1","claim":"<one sentence, actionable>","confidence":<0-100>,"question":<number>}]}
COUNCIL-JSON-->
"""

BANNER = """# COUNCIL BRIEFING PACKET

**Plan:** {slug}
**Packet built:** {date}
**Packet hash:** {hash}

You are one seat on an independent review council. You have NOT seen any other reviewer's output and you will not.
Everything below is the complete context. If a fact you need is missing, say so instead of assuming it. Facts marked
UNVERIFIED are not facts.

---

"""


class CouncilError(Exception):
    """A council step could not run: a missing plan, packet or roster."""


def check_slug(slug: str) -> str:
    """
    Validate a plan's slug.

    :param slug: Such as ``2026-12-pricing``
    :returns: The slug
    :raises CouncilError: When it has characters a folder name should not
    """
    if not SLUG.fullmatch(slug):
        raise CouncilError("a plan slug is lowercase letters, digits and hyphens, such as 2026-12-pricing")
    return slug


def plan_new(brain: Brain, slug: str) -> list[str]:
    """
    Start a plan from the brain's plan template.

    :param brain: The brain
    :param slug: The plan's slug
    :returns: The files made, relative to the brain
    :raises CouncilError: When the plan exists already
    """
    target = brain.file("plans", check_slug(slug))
    if target.exists():
        raise CouncilError(f"plans/{slug} exists already")
    own = brain.file("plans", "_template")
    if own.is_dir():
        shutil.copytree(own, target)
    else:
        packaged = resources.files("kea") / "data" / "templates" / "plans" / "_template"
        target.mkdir(parents=True)
        for item in packaged.iterdir():
            if item.is_file():
                (target / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
    (target / "reviews").mkdir(exist_ok=True)
    for path in target.rglob("*.md"):
        text = path.read_text(encoding="utf-8").replace("<slug>", slug)
        path.write_text(text.replace("{{plan_date}}", brain.today().isoformat()), encoding="utf-8")
    return sorted(str(p.relative_to(brain.root)) for p in target.rglob("*") if p.is_file())


def plans(brain: Brain) -> list[dict[str, Any]]:
    """
    List plans and how far each has come: proposal, packet, reviews, synthesis, production plan.

    :param brain: The brain
    :returns: The plans, newest slug first
    """
    folder = brain.file("plans")
    out = []
    for plan in sorted(folder.iterdir() if folder.is_dir() else [], reverse=True):
        if not plan.is_dir() or plan.name.startswith(("_", ".")):
            continue
        reviews = [p for p in (plan / "reviews").glob("*.md")] if (plan / "reviews").is_dir() else []
        steps = {
            "proposal": (plan / "00-proposal.md").is_file(),
            "packet": brain.file("council", "briefing", f"{plan.name}.md").is_file(),
            "reviews": len(reviews),
            "synthesis": (plan / "11-synthesis.md").is_file() or (plan / "10-synthesis-scaffold.md").is_file(),
            "decided": is_decided(plan),
        }
        if steps["decided"]:
            stage, next_step = "decided", "execute, log it in 30-execution-log.md, score its predictions"
        elif steps["synthesis"]:
            stage, next_step = "synthesized", "write 20-production-plan.md and the decision"
        elif reviews:
            stage, next_step = "reviewed", f"kea synth {plan.name}"
        elif steps["packet"]:
            stage, next_step = "briefed", f"Chair review first, then kea council {plan.name}"
        else:
            stage, next_step = "proposal", f"finish 00-proposal.md, then kea brief {plan.name}"
        out.append({"slug": plan.name, "stage": stage, "next": next_step, **steps})
    return out


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    pairs = (line.split(":", 1) for line in m[1].splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in pairs}


@dataclass
class Packet:
    """A built briefing packet."""

    path: Path
    hash: str
    chars: int
    frameworks: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    redacted: int = 0


def brief(brain: Brain, slug: str) -> Packet:
    """
    Build the briefing packet every seat reads: facts first, then frameworks, then the plan, then the questions.

    :param brain: The brain
    :param slug: The plan
    :returns: The packet, written to ``council/briefing/<slug>.md``
    :raises CouncilError: When the plan has no proposal
    """
    plan = brain.file("plans", check_slug(slug))
    proposal = plan / "00-proposal.md"
    if not proposal.is_file():
        raise CouncilError(f"plans/{slug}/00-proposal.md is missing; start it with kea plan new {slug}")
    include = brain.setting("brief", "include", DEFAULT_INCLUDE)
    parts, names, refused = [], [], []
    root = brain.root.resolve()
    for rel in include if isinstance(include, list) else DEFAULT_INCLUDE:
        path = brain.file(str(rel))
        real = path.resolve()
        if not real.is_relative_to(root) or is_denied(real) or INDEX_DIR in real.relative_to(root).parts[:1]:
            refused.append(str(rel))
            continue
        if path.is_file():
            parts.append(
                f"## BRIEFING SECTION: {rel}\n**Source:** `{rel}`\n\n{path.read_text(encoding='utf-8').strip()}"
            )
            names.append(str(rel))
    text = proposal.read_text(encoding="utf-8")
    used = []
    folder = brain.file("frameworks")
    for fw in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if fw.name.startswith("_") or fw.name == "INDEX.md":
            continue
        body = fw.read_text(encoding="utf-8")
        always = _frontmatter(body).get("always_apply", "false").lower() == "true"
        if always or re.search(rf"\b{re.escape(fw.stem)}\b", text, re.I):
            parts.append(
                f"## BRIEFING SECTION: FRAMEWORK {fw.stem}\n**Source:** `frameworks/{fw.name}`\n\n{body.strip()}"
            )
            used.append(fw.stem)
    parts.append(
        f"## BRIEFING SECTION: THE PLAN UNDER REVIEW\n**Source:** `plans/{slug}/00-proposal.md`\n\n{text.strip()}"
    )
    questions = plan / "01-review-questions.md"
    if questions.is_file():
        parts.append(f"## BRIEFING SECTION: REVIEW QUESTIONS\n\n{questions.read_text(encoding='utf-8').strip()}")
    body, hidden = redact_count("\n\n---\n\n".join(parts) + "\n")
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    packet = BANNER.format(slug=slug, date=brain.today().isoformat(), hash=digest) + body
    dest = brain.file("council", "briefing", f"{slug}.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    return Packet(dest, digest, len(packet), used, names, refused, hidden)


@dataclass
class Roster:
    """The council's seats and the pauses between calls to one provider."""

    seats: list[Seat]
    gaps: dict[str, float]
    errors: list[str]


def roster(brain: Brain) -> Roster:
    """
    Read ``council/roster.toml``.

    :param brain: The brain
    :returns: The roster; seats that do not parse are reported in ``errors``
    :raises CouncilError: When the file is missing or not valid TOML
    """
    path = brain.file("council", "roster.toml")
    if not path.is_file():
        raise CouncilError(f"council/roster.toml is missing; kea init {brain.root} puts back missing files only")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CouncilError(f"council/roster.toml: {exc}") from exc
    seats, errors = [], []
    entries = raw.get("seat", [])
    if not isinstance(entries, list):
        errors.append("seats are written [[seat]], one table each; found a single [seat] or a plain value")
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"a seat must be a [[seat]] table, not {entry!r}")
            continue
        try:
            seats.append(Seat.from_config(entry))
        except (ProviderError, KeyError, TypeError, ValueError, AttributeError) as exc:
            errors.append(str(exc))
    limits = raw.get("rate_limits") or {}
    if not isinstance(limits, dict):
        errors.append("[rate_limits] must be a table of provider = seconds")
        limits = {}
    gaps = {str(k): float(v) for k, v in limits.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return Roster(seats, gaps, errors)


def seats(brain: Brain, live: bool = False) -> list[dict[str, Any]]:
    """
    List the seats and whether each can run, optionally asking each model to answer.

    :param brain: The brain
    :param live: Send each ready seat a one word test
    :returns: One row per seat
    """
    out = []
    for seat in roster(brain).seats:
        row: dict[str, Any] = {
            "seat": seat.name,
            "role": seat.role,
            "provider": seat.provider,
            "model": seat.model,
            "enabled": seat.enabled,
            "key": seat.key_env or "none needed",
            "ready": _ready(seat),
            "local": seat.stays_here,
            "host": seat.host,
            "custom": seat.custom and bool(seat.key_env),
        }
        if live and seat.ready:
            row["answered"], row["detail"] = probe(seat)
        out.append(row)
    return out


def _ready(seat: Seat) -> bool:
    return seat.ready and (not seat.local or _listening(seat.base_url))


def _listening(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 80), timeout=0.3):
            return True
    except OSError:
        return False


Complete = Callable[[Seat, str, str], str]


def run(
    brain: Brain,
    slug: str,
    only: list[str] | None = None,
    resume: bool = False,
    ask: Complete | None = None,
    local_only: bool = False,
) -> dict[str, Any]:
    """
    Send the packet to every ready seat in parallel and save each review where no other seat will read it.

    Calls to one provider are made one at a time, spaced by the roster's ``[rate_limits]``, so free tiers hold.

    :param brain: The brain
    :param slug: The plan
    :param only: Seat names to run; every ready seat when omitted
    :param resume: Skip seats that already have a review for this plan
    :param ask: The function that calls a model; the real providers when omitted
    :param local_only: Run only the seats whose model is on this machine
    :returns: The manifest: one result per seat, with the packet hash
    :raises CouncilError: When there is no packet or no seat can run
    """
    packet_path = brain.file("council", "briefing", f"{check_slug(slug)}.md")
    if not packet_path.is_file():
        raise CouncilError(f"no packet for {slug}; build it with kea brief {slug}")
    packet = packet_path.read_text(encoding="utf-8")
    m = re.search(r"\*\*Packet hash:\*\* (\w+)", packet)
    packet_hash = m[1] if m else "unknown"
    team = roster(brain)
    chosen = [s for s in team.seats if (not only or s.name in only) and (s.stays_here or not local_only) and _ready(s)]
    out_dir = brain.file("plans", slug, "reviews")
    out_dir.mkdir(parents=True, exist_ok=True)
    if resume:
        done = set()
        for review in out_dir.glob("*.md"):
            m = re.search(r"^\| Seat \| (.+?) \|$", review.read_text(encoding="utf-8")[:2000], re.M)
            if m:
                done.add(m[1].strip())
        chosen = [s for s in chosen if s.name not in done]
    if not chosen:
        raise CouncilError("no seat can run: set the API keys named in council/roster.toml, or start Ollama")
    call = ask or (lambda seat, system, user: complete(seat, system, user))
    locks: dict[str, threading.Lock] = {}
    last: dict[str, float] = {}
    guard = threading.Lock()

    def one(seat: Seat) -> dict[str, Any]:
        with guard:
            lock = locks.setdefault(seat.provider, threading.Lock())
        started = time.monotonic()
        with lock:
            wait = team.gaps.get(seat.provider, 0) - (time.monotonic() - last.get(seat.provider, -1e9))
            if wait > 0:
                time.sleep(wait)
            try:
                system = SYSTEM.format(role=seat.role, owner=brain.owner, lens=seat.lens)
                text = call(seat, system, packet + CONTRACT)
                error = None
            except Exception as exc:  # noqa: BLE001  one seat failing must never cost the others their reviews
                text, error = "", str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: {exc}"
            finally:
                last[seat.provider] = time.monotonic()
        result: dict[str, Any] = {"seat": seat.name, "provider": seat.provider, "model": seat.model}
        if error:
            return {**result, "ok": False, "error": error}
        name = f"{brain.today().isoformat()}_{seat.name}_{re.sub(r'[^A-Za-z0-9.]+', '-', seat.model)}.md"
        header = (
            f"# Council review: {seat.role}\n\n| Field | Value |\n| --- | --- |\n| Seat | {seat.name} |\n"
            f"| Provider | {seat.provider} |\n| Model | {seat.model} |\n| Date | {brain.today().isoformat()} |\n"
            f"| Packet hash | {packet_hash} |\n| Independent | yes, the packet only |\n"
            f"| Seconds | {time.monotonic() - started:.0f} |\n\n---\n\n"
        )
        (out_dir / name).write_text(header + text.strip() + "\n", encoding="utf-8")
        return {**result, "ok": True, "file": f"plans/{slug}/reviews/{name}", "chars": len(text)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        results = list(pool.map(one, chosen))
    manifest = {
        "plan": slug,
        "date": brain.now().isoformat(timespec="seconds"),
        "packet_hash": packet_hash,
        "packet_chars": len(packet),
        "results": sorted(results, key=lambda r: r["seat"]),
        "skipped": [s.name for s in team.seats if not s.ready],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# ---------------------------------------------------------------- synthesis


def parse_review(text: str) -> dict[str, Any] | None:
    """
    Read the last COUNCIL-JSON block of a review that parses, forgiving prose or a second object after the JSON.

    :param text: The review
    :returns: The block, or None when there is none or none parses
    """
    for m in reversed(list(JSON_BLOCK.finditer(text))):
        raw = m[1].strip()
        try:
            data = json.loads(raw)
        except ValueError, RecursionError:
            try:
                data = json.JSONDecoder().raw_decode(raw[raw.index("{") :])[0]
            except ValueError, RecursionError:
                continue
        if isinstance(data, dict):
            return data
    return None


def _finite(value: Any) -> bool:
    """Tell whether a value is a usable confidence: a number from 0 to 100."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return 0 <= value <= 100
    return math.isfinite(value) and 0 <= value <= 100


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


def cluster(recommendations: list[dict[str, Any]], threshold: float = 0.2) -> list[dict[str, Any]]:
    """
    Group recommendations that call for the same action, by word overlap (Jaccard), greedily.

    :param recommendations: Rows with ``seat``, ``claim`` and ``confidence``
    :param threshold: The overlap at which two claims join
    :returns: Clusters, the most widely shared first
    """
    groups: list[dict[str, Any]] = []
    for rec in recommendations:
        words = _words(rec.get("claim", ""))
        for group in groups:
            union = len(words | group["words"]) or 1
            if len(words & group["words"]) / union >= threshold:
                group["members"].append(rec)
                group["words"] |= words
                break
        else:
            groups.append({"words": set(words), "members": [rec]})
    out = []
    for group in groups:
        confs = [m["confidence"] for m in group["members"] if _finite(m.get("confidence"))]
        out.append(
            {
                "label": max((m.get("claim", "") for m in group["members"]), key=len),
                "seats": sorted({m["seat"] for m in group["members"]}),
                "members": group["members"],
                "confidence": round(sum(confs) / len(confs)) if confs else None,
                "spread": (max(confs) - min(confs)) if len(confs) > 1 else 0,
            }
        )
    out.sort(key=lambda c: (-len(c["seats"]), -(c["confidence"] or 0)))
    return out


def synth(brain: Brain, slug: str) -> dict[str, Any]:
    """
    Build the synthesis scaffold: verdicts, where seats converge, where they split, and what only one seat saw.

    The scaffold is mechanical. The Chair writes the real synthesis on top of it, including what every seat missed.

    :param brain: The brain
    :param slug: The plan
    :returns: Counts and the file written
    :raises CouncilError: When the plan has no reviews
    """
    folder = brain.file("plans", check_slug(slug), "reviews")
    files = sorted(folder.glob("*.md")) if folder.is_dir() else []
    if not files:
        raise CouncilError(f"no reviews in plans/{slug}/reviews yet")
    reviews: list[dict[str, Any]] = []
    recs: list[dict[str, Any]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        data = parse_review(text)
        if data is not None and not isinstance(data.get("recommendations", []), list):
            data = None
        header = re.search(r"^\| Seat \| (.+?) \|$", text[:2000], re.M)
        named = header[1].strip() if header else (data or {}).get("seat")
        seat = named or (path.name.split("_")[1] if path.name.count("_") >= 2 else path.stem)
        reviews.append({"file": path.name, "seat": str(seat), "data": data})
        for rec in (data or {}).get("recommendations") or []:
            if isinstance(rec, dict) and isinstance(rec.get("claim"), str) and rec["claim"].strip():
                conf = rec.get("confidence")
                rec = {
                    **rec,
                    "confidence": conf if _finite(conf) else None,
                }
                recs.append({**rec, "seat": str(seat)})
    parsed = [r for r in reviews if r["data"]]
    seats_n = len({r["seat"] for r in parsed}) or 1
    strong_at = max(3, round(seats_n * 0.6))
    groups = cluster(recs)
    strong = [g for g in groups if len(g["seats"]) >= strong_at]
    debate = [g for g in groups if 1 < len(g["seats"]) < strong_at or g["spread"] >= 25]
    solo = [g for g in groups if len(g["seats"]) == 1]
    verdicts = Counter(str(r["data"].get("verdict", "?")) for r in parsed)

    def esc(text: Any) -> str:
        return " ".join(str(text or "").split()).replace("|", "/")

    lines = [
        f"# Council synthesis scaffold: {slug}",
        "",
        f"**Built:** {brain.today().isoformat()}  ",
        f"**Reviews parsed:** {len(parsed)} of {len(reviews)}  ",
        "**Status:** MECHANICAL. The Chair writes the real synthesis, 11-synthesis.md, on top of this.",
        "",
        "## Verdicts",
        "",
        "| Seat | File | Verdict | Confidence | Headline | Top risk |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in reviews:
        d = r["data"]
        if not d:
            lines.append(f"| {r['seat']} | {r['file']} | NO JSON BLOCK | | read this review by hand | |")
            continue
        lines.append(
            f"| {esc(r['seat'])} | {r['file']} | **{esc(d.get('verdict'))}** | {esc(d.get('overall_confidence'))} "
            f"| {esc(d.get('headline'))} | {esc(d.get('top_risk'))} |"
        )
    tally = ", ".join(f"{k} {v}" for k, v in verdicts.most_common()) or "none"
    lines += ["", f"**Tally:** {tally}", "", "## Convergence", ""]
    lines += ["| Seats | Signal | Confidence | Spread | Recommendation |", "| --- | --- | --- | --- | --- |"]
    for g in groups:
        n = len(g["seats"])
        signal = "STRONG" if n >= strong_at else ("split" if n > 1 else "solo")
        lines.append(
            f"| {n} of {seats_n} ({', '.join(g['seats'])}) | {signal} | {g['confidence']} | {g['spread']} "
            f"| {esc(g['label'])} |"
        )
    lines += ["", "## Strong signal: accept unless you have a specific reason not to", ""]
    lines += [f"{i}. {g['label']} ({', '.join(g['seats'])}, {g['confidence']})" for i, g in enumerate(strong, 1)]
    lines += [] if strong else ["None. That itself is a finding: the council agrees on nothing."]
    lines += ["", "## Debate: the seats split or their confidence is far apart", ""]
    lines += [f"{i}. {g['label']} ({', '.join(g['seats'])}, spread {g['spread']})" for i, g in enumerate(debate, 1)]
    lines += [] if debate else ["None."]
    lines += ["", "## Solo: one seat only. Noise, or the most valuable line in the review. Check each by hand.", ""]
    lines += [f"- **{g['seats'][0]}, {g['confidence']}:** {g['label']}" for g in solo] or ["None."]
    lines += ["", "## Chair worksheet", ""]
    lines += [
        "| # | Recommendation | Seats | Accept, modify or reject | Why | Revisit |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {i} | {esc(g['label'])} | {', '.join(g['seats'])} | | | |" for i, g in enumerate(groups[:30], 1)]
    lines += [
        "",
        "## What the scaffold cannot write",
        "",
        "1. **What every seat missed.** The models share training data and so share blind spots. Name what none said.",
        "2. **Falsifiable predictions.** At least three, dated, with a confidence: `kea predict ... --plan "
        + slug
        + "`.",
        "3. **The kill list.** What this plan stops. A plan that only adds is a wish.",
        "4. **The cheapest next test** of the biggest assumption.",
    ]
    dest = brain.file("plans", slug, "10-synthesis-scaffold.md")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "plan": slug,
        "file": f"plans/{slug}/10-synthesis-scaffold.md",
        "reviews": len(reviews),
        "parsed": len(parsed),
        "unparsed": [r["file"] for r in reviews if not r["data"]],
        "verdicts": dict(verdicts),
        "clusters": len(groups),
        "strong": len(strong),
        "debate": len(debate),
        "solo": len(solo),
    }
