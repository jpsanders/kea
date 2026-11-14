"""
The command line: ``kea``.

One ``cmd_*`` function per command. Every command that prints data takes ``--json`` and prints one JSON document with
stable fields, so scripts and AI agents read the same facts people do. Exit status 0 means success, 1 a failure (or
``kea check`` finding errors) and 2 a mistake in the arguments, with the reason on standard error.

Imports are lazy (``lazy import``, new in Python 3.15), so ``kea --version`` and ``kea status`` load only what they use.
The manual that ``guide`` prints and ``man`` shows is built from one file, ``docs/MANUAL.md``.
"""

import argparse
import re  # argparse loads it anyway, so it costs nothing to start
import sys
from collections.abc import Callable, Sequence
from typing import Any
lazy import json
lazy import os
lazy import shutil
lazy import subprocess  # nosec B404: runs man with fixed arguments only
lazy from dataclasses import asdict
lazy from importlib import resources
lazy from pathlib import Path

lazy from kea import __version__
lazy from kea import brain as brain_mod
lazy from kea import council, ingest, ledger, skills, store
lazy from kea.brain import Brain, BrainError
lazy from kea.council import CouncilError
lazy from kea.ledger import LedgerError
lazy from kea.providers import ProviderError

PROG = "kea"
TAGLINE = "a second brain that checks its facts"
# The same list as kea.ingest.KINDS, kept here so building the parser imports nothing heavy; a test keeps them equal.
INGEST_KINDS = ("claude-code", "codex", "opencode", "chatgpt", "claude", "git", "docs")


# ---------------------------------------------------------------- output


class Paint:
    """ANSI colour when writing to a terminal, plain text otherwise or when NO_COLOR is set."""

    def __init__(self) -> None:
        self.on = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
        truecolor = os.environ.get("COLORTERM", "") in ("truecolor", "24bit")
        self.accent_code = "38;2;201;121;54" if truecolor else "38;5;173"

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def bold(self, text: str) -> str:
        """
        Bold text.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        """
        Dim text.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap("2", text)

    def good(self, text: str) -> str:
        """
        Green text.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap("32", text)

    def warn(self, text: str) -> str:
        """
        Yellow text.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap("33", text)

    def bad(self, text: str) -> str:
        """
        Red text.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap("31", text)

    def accent(self, text: str) -> str:
        """
        Text in kea's orange.

        :param text: The text
        :returns: It, styled
        """
        return self._wrap(self.accent_code, text)


def emit(args: argparse.Namespace, data: Any, human: Callable[[Paint], None]) -> int:
    """
    Print a result as JSON when ``--json`` was given, or for people otherwise.

    :param args: Parsed arguments
    :param data: The result
    :param human: Prints it for people
    :returns: Exit code 0
    """
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        human(Paint())
    return 0


def _brain(args: argparse.Namespace) -> "Brain":
    return Brain.locate(getattr(args, "brain", None))


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _safe(text: str) -> str:
    """Drop terminal control characters from text read from outside, so it can never move the cursor or retitle."""
    return _CONTROL.sub("", text)


def _since(text: str | None, brain: "Brain") -> str | None:
    if not text:
        return None
    when = brain_mod.parse_date(text, brain.today())
    if when is None:
        raise ValueError(f"--since takes a date such as 2026-11-01, today, or -30d; not {text!r}")
    return when.isoformat()


def _ago(days: int) -> str:
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path


# ---------------------------------------------------------------- brain and ledger


def cmd_init(args: argparse.Namespace) -> int:
    """
    Make a new brain.

    :param args: Parsed arguments
    :returns: Exit code
    """
    folder = Path(args.path or ".").expanduser().resolve()
    existed = (folder / brain_mod.MARKER).is_file()
    written = brain_mod.init(folder, args.name, args.owner, args.example, args.force, not args.no_git)
    default = None
    current = brain_mod.default_brain()
    if args.default or (not args.no_default and not args.example and current is None):
        default = str(brain_mod.set_default_brain(folder))
        current = folder
    data = {
        "path": str(folder),
        "files": written,
        "default_config": default,
        "default_brain": str(current) if current else None,
        "example": args.example,
        "repaired": existed and not args.force,
    }

    def human(p: Paint) -> None:
        if existed and not args.force:
            restored = f"put back {_plural(len(written), 'missing file')}" if written else "found nothing missing"
            print(f"{p.accent('kea')} {_home(str(folder))} is already a brain: {restored}; nothing else changed")
            return
        print(f"{p.accent('kea')} made a brain in {p.bold(_home(str(folder)))} ({_plural(len(written), 'file')})")
        if default:
            print(f"  it is now your default brain {p.dim('(' + _home(default) + ')')}")
        elif current and current.resolve() != folder:
            hint = "kea init --default " + _home(str(folder))
            print(f"  your default brain is still {_home(str(current))} {p.dim('· to change it: ' + hint)}")
        print("next:")
        print(f"  cd {_home(str(folder))} && kea status")
        print(f"  kea skill install    {p.dim('# teach Claude Code, Codex and OpenCode to use it')}")
        print(f"  kea ingest claude-code    {p.dim('# make your past agent sessions searchable')}")

    return emit(args, data, human)


def _bar(value: int, width: int = 20) -> str:
    filled = round(max(0, min(100, value)) / 100 * width)
    return "█" * filled + "·" * (width - filled)


def cmd_status(args: argparse.Namespace) -> int:
    """
    Show the whole brain at a glance, with its health checks.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    data = ledger.status(brain)
    if brain.index_path.is_file():
        with store.Store(brain.index_path) as db:
            data["evidence"] = db.summary()
    else:
        data["evidence"] = None
    data["seats_ready"] = None
    try:
        rows = council.seats(brain)
        data["seats_ready"] = {"ready": sum(1 for r in rows if r["ready"]), "total": len(rows)}
    except CouncilError:
        pass

    def human(p: Paint) -> None:
        s, d, pr, r = data["state"], data["decisions"], data["predictions"], data["roadmap"]
        print(f"{p.accent(p.bold('kea'))} {p.bold(data['brain'])} {p.dim('· ' + _home(data['root']))}")
        age = "no entries yet" if s["age_days"] is None else f"updated {_ago(s['age_days'])}"
        state_line = f"{age} · {_plural(s['facts'], 'fact')}"
        if s["stale_facts"]:
            state_line += f", {s['stale_facts']} past recheck"
        if s["unverified"]:
            state_line += f" · {s['unverified']} UNVERIFIED"
        if s["bad_dates"]:
            state_line += p.bad(f" · impossible dates on lines {', '.join(map(str, s['bad_dates']))}")
        print(f"  {'state':<12} {state_line}")
        due = f" · {len(d['due'])} due for revisit ({', '.join(d['due'])})" if d["due"] else ""
        print(f"  {'decisions':<12} {d['active']} active{due}")
        pline = f"{pr['open']} open"
        if pr["due"]:
            pline += f" · {len(pr['due'])} due to score ({', '.join(pr['due'])})"
        if pr["scored"]:
            skill = round(100 * pr["skill"])
            pline += f" · {pr['scored']} scored, Brier {pr['brier']:.2f} ({skill:+d}% vs a coin)"
        print(f"  {'predictions':<12} {pline}")
        rline = f"{r['waiting']} waiting for {data['owner']} · {r['next']} next"
        if r["soon"]:
            rline += f" · {_plural(len(r['soon']), 'date')} in 14 days"
        print(f"  {'roadmap':<12} {rline}")
        if data["ideas"]:
            print(f"  {'ideas':<12} {data['ideas']} parked")
        for plan in data["plans"][:4]:
            reviews = f", {plan['reviews']} reviews" if plan["reviews"] else ""
            print(f"  {'plan':<12} {plan['slug']}: {plan['stage']}{reviews} {p.dim('→ ' + plan['next'])}")
        if data["seats_ready"]:
            seats_line = f"{data['seats_ready']['ready']} of {data['seats_ready']['total']} seats ready"
            print(f"  {'council':<12} {seats_line} {p.dim('· the Chair is your own agent')}")
        ev = data["evidence"]
        if ev:
            print(f"  {'evidence':<12} {ev['items']:,} items from {_plural(ev['sources'], 'source')}")
        else:
            print(f"  {'evidence':<12} {p.dim('nothing indexed yet: kea ingest claude-code')}")
        print()
        print(p.bold("health"))
        for h in data["health"]:
            mark = p.good("✓") if h["ok"] else p.warn("!")
            print(f"  {mark} {h['check']} {p.dim('· ' + h['detail'])}")
        n = data["attention"]
        if n >= 3:
            print(p.bad(f"  {n} of {len(data['health'])} checks need attention. Fix the oldest first."))
        elif n:
            print(p.warn(f"  {n} of {len(data['health'])} checks need attention."))
        else:
            print(p.good("  every check passes."))

    return emit(args, data, human)


def cmd_check(args: argparse.Namespace) -> int:
    """
    Lint the brain and exit 1 when it finds errors (or warnings, with --strict).

    :param args: Parsed arguments
    :returns: Exit code
    """
    found = ledger.check(_brain(args))
    rows = [asdict(f) for f in found]

    def human(p: Paint) -> None:
        if not found:
            print(p.good("✓ nothing to fix"))
        for f in found:
            where = f"{f.file}:{f.line}" if f.line else f.file
            label = {"error": p.bad("error"), "warn": p.warn("warn ")}.get(f.level, f.level)
            print(f"{label} {where}: {f.message}")

    emit(args, rows, human)
    failing = [f for f in found if f.level == "error" or (args.strict and f.level == "warn")]
    return 1 if failing else 0


def cmd_state(args: argparse.Namespace) -> int:
    """
    Show the facts and the newest log entries.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.state(_brain(args), args.entries)

    def human(p: Paint) -> None:
        print(p.bold("facts"))
        width = max((len(f["fact"]) for f in data["facts"]), default=4)
        for f in data["facts"]:
            print(f"  {f['fact']:<{width}}  {p.bold(f['value'])}  {p.dim(f['source'] + ' · ' + f['verified'])}")
        if not data["facts"]:
            print(p.dim("  none yet: kea fact 'Cash on hand' 'EUR 4,200' --source 'bank export'"))
        print(p.bold(f"\nlog ({data['log_total']} entries, newest first)"))
        for e in data["log"]:
            print(f"  {p.accent(e['date'])} {e['time']} {p.bold(e['title'])}")
        if data["unverified"]:
            print(p.warn(f"\n{data['unverified']} UNVERIFIED markers in STATE.md"))

    return emit(args, data, human)


def cmd_log(args: argparse.Namespace) -> int:
    """
    Add a dated entry to STATE.md.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.log(_brain(args), " ".join(args.text), args.title, args.source)
    return emit(args, data, lambda p: print(f"logged {p.accent(data['date'])} {data['time']} {data['title']}"))


def cmd_fact(args: argparse.Namespace) -> int:
    """
    Record a fact with its source and verified date.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.set_fact(_brain(args), args.fact, args.value, args.source, args.verified)
    verb = "recorded" if data["new"] else "updated"
    return emit(args, data, lambda p: print(f"{verb} {p.bold(data['fact'])} = {data['value']} ({data['verified']})"))


def cmd_decide(args: argparse.Namespace) -> int:
    """
    Log a decision.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.decide(_brain(args), args.title, args.decision, args.why, args.revisit, args.source)
    return emit(args, data, lambda p: print(f"{p.accent(data['id'])} {data['title']} · revisit {data['revisit']}"))


def cmd_decisions(args: argparse.Namespace) -> int:
    """
    List decisions.

    :param args: Parsed arguments
    :returns: Exit code
    """
    rows = ledger.decisions(_brain(args), args.due, args.all)

    def human(p: Paint) -> None:
        for d in rows:
            flag = p.warn(" due") if d["due"] else ""
            status = "" if d["status"] == "ACTIVE" else p.dim(f" [{d['status']}]")
            print(f"{p.accent(d['id'])} {d['date']} {p.bold(d['title'])}{status}{flag}")
            print(f"  {d['decision']}")
            print(p.dim(f"  revisit: {d['revisit']}"))
        if not rows:
            print(p.dim("no decisions here" + (" are due" if args.due else "")))

    return emit(args, rows, human)


def cmd_revisit(args: argparse.Namespace) -> int:
    """
    Record what happened after a decision.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.revisit(_brain(args), args.id, args.outcome, args.status, args.surprise, args.lesson, args.next)
    return emit(args, data, lambda p: print(f"{p.accent(data['id'])} revisited · status {data['status']}"))


def cmd_predict(args: argparse.Namespace) -> int:
    """
    Write down a prediction.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.predict(_brain(args), " ".join(args.claim), args.by, args.confidence, args.plan, args.decision)
    return emit(
        args, data, lambda p: print(f"{p.accent(data['id'])} {data['confidence']}% by {data['by']}: {data['claim']}")
    )


def cmd_score(args: argparse.Namespace) -> int:
    """
    Score a prediction.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.score(_brain(args), args.id, args.outcome, args.note)
    return emit(args, data, lambda p: print(f"{p.accent(data['id'])} scored {data['status']}"))


def cmd_predictions(args: argparse.Namespace) -> int:
    """
    List predictions.

    :param args: Parsed arguments
    :returns: Exit code
    """
    rows = ledger.predictions(_brain(args), args.due, args.open)

    def human(p: Paint) -> None:
        for r in rows:
            status = r["status"]
            paint = {"TRUE": p.good, "FALSE": p.bad, "PARTIAL": p.warn}.get(status, str)
            shown = p.warn(f"{'DUE':<7}") if r["due"] else paint(f"{status:<7}")
            conf = f"{r['confidence']:>3}%" if r["confidence"] is not None else "  ?%"
            print(f"{p.accent(r['id'])} {conf} by {r['by']} {shown} {r['claim']}")
        if not rows:
            print(p.dim("no predictions here"))

    return emit(args, rows, human)


def cmd_calibration(args: argparse.Namespace) -> int:
    """
    Show how well calibrated the predictions were.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.calibration(_brain(args))

    def human(p: Paint) -> None:
        if not data["scored"]:
            print("nothing scored yet. Predict with kea predict, score with kea score when the date comes.")
            return
        skill = round(100 * data["skill"])
        print(f"{p.bold(str(data['scored']))} scored predictions · Brier {p.accent(f'{data['brier']:.3f}')} ", end="")
        print(p.dim(f"(0 is perfect, 0.25 is a coin) · {skill:+d}% skill over a coin"))
        drift = data["stated"] - data["observed"]
        verdict = "overconfident" if drift > 5 else "underconfident" if drift < -5 else "about right"
        print(f"said {data['stated']}% on average, {data['observed']}% came true: {p.bold(verdict)}")
        print()
        print(p.dim(f"  {'said':<10} {'count':>5}  came true"))
        for b in data["buckets"]:
            bar = p.accent(_bar(b["observed"]))
            print(f"  {b['range'] + '%':<10} {b['count']:>5}  {bar} {b['observed']:>3}%")

    return emit(args, data, human)


def cmd_idea(args: argparse.Namespace) -> int:
    """
    Park an idea.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = ledger.idea(_brain(args), " ".join(args.text), args.why)
    return emit(args, data, lambda p: print(f"{p.accent(data['id'])} parked: {data['title']}"))


def cmd_ideas(args: argparse.Namespace) -> int:
    """
    List parked ideas.

    :param args: Parsed arguments
    :returns: Exit code
    """
    rows = ledger.ideas(_brain(args))

    def human(p: Paint) -> None:
        for i in rows:
            print(f"{p.accent(i['id'])} {i['date']} {i['title']} {p.dim(i['status'])}")
        if not rows:
            print(p.dim("no ideas parked"))

    return emit(args, rows, human)


def cmd_roadmap(args: argparse.Namespace) -> int:
    """
    Show the roadmap, add to it, or close an item.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    if args.action == "add":
        section = "waiting" if args.waiting else "running" if args.running else "next"
        data = ledger.roadmap_add(brain, " ".join(args.text), section)
        return emit(args, data, lambda p: print(f"added to {section}: {data['text']}"))
    if args.action == "done":
        data = ledger.roadmap_done(brain, " ".join(args.text), args.proof)
        return emit(args, data, lambda p: print(f"{p.good('✓')} {data['text']}"))
    view = ledger.roadmap(brain)

    def human(p: Paint) -> None:
        for key, title in (("waiting", f"waiting for {view['owner']}"), ("next", "next"), ("running", "running")):
            print(p.bold(title))
            for item in view[key]:
                print(f"  {p.accent(str(item['n']).rjust(2))} {item['text']}")
            if not view[key]:
                print(p.dim("   nothing"))
        if view["dates"]:
            print(p.bold("dates"))
            for d in view["dates"]:
                print(f"   {p.accent(d['date'])} {d['what']}")

    return emit(args, view, human)


# ---------------------------------------------------------------- council


def cmd_plan(args: argparse.Namespace) -> int:
    """
    Start a plan, or list plans and their next step.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    if args.action == "new":
        files = council.plan_new(brain, args.slug)
        data: Any = {"plan": args.slug, "files": files}
        return emit(
            args,
            data,
            lambda p: print(
                f"started plans/{args.slug}: write 00-proposal.md and 01-review-questions.md, "
                f"then kea brief {args.slug}"
            ),
        )
    rows = council.plans(brain)

    def human(p: Paint) -> None:
        for r in rows:
            print(f"{p.accent(r['slug'])} {p.bold(r['stage'])} {p.dim('→ ' + r['next'])}")
        if not rows:
            print(p.dim("no plans yet: kea plan new 2026-12-pricing"))

    return emit(args, rows, human)


def cmd_brief(args: argparse.Namespace) -> int:
    """
    Build a plan's council packet.

    :param args: Parsed arguments
    :returns: Exit code
    """
    packet = council.brief(_brain(args), args.slug)
    data = {**asdict(packet), "path": str(packet.path)}

    def human(p: Paint) -> None:
        print(f"packet  {_home(str(packet.path))}")
        print(f"hash    {p.accent(packet.hash)} · {packet.chars:,} characters, about {packet.chars // 4:,} tokens")
        print(f"facts   {', '.join(packet.sections) or 'none'}")
        print(f"lenses  {', '.join(packet.frameworks) or 'none named in the proposal'}")
        print(p.dim(f"next: the Chair (your agent) reviews from the packet alone, then kea council {args.slug}"))
        if args.show:
            print("\n" + packet.path.read_text(encoding="utf-8"))

    return emit(args, data, human)


def cmd_council(args: argparse.Namespace) -> int:
    """
    Send the packet to every ready seat.

    :param args: Parsed arguments
    :returns: Exit code
    :raises CouncilError: When the packet is to go out without a yes
    """
    brain = _brain(args)
    rows = [r for r in council.seats(brain) if r["ready"] and (not args.seat or r["seat"] in args.seat)]
    packet = brain.file("council", "briefing", f"{council.check_slug(args.slug)}.md")
    size = packet.stat().st_size if packet.is_file() else 0
    if args.dry_run:
        data = {"plan": args.slug, "packet_bytes": size, "seats": rows}

        def human(p: Paint) -> None:
            if not size:
                print(f"no packet yet: kea brief {args.slug}")
            if not rows:
                print("no seat is ready: set the keys named in council/roster.toml, or start Ollama (kea seats)")
                return
            print(f"would send {size:,} bytes to {_plural(len(rows), 'seat')}:")
            for r in rows:
                where = f"this machine ({r['host']})" if r["local"] else r["host"]
                flag = p.warn(" · your key goes to a custom endpoint") if r["custom"] else ""
                print(f"  {p.accent(f'{r['seat']:<12}')} {r['model']} {p.dim('· ' + where)}{flag}")

        return emit(args, data, human)
    outside = sorted({r["host"] for r in rows if not r["local"]})
    if outside and not args.yes:
        if not sys.stdin.isatty():
            raise CouncilError(f"this sends the packet to {', '.join(outside)}; add --yes to confirm")
        answer = input(f"send {size:,} bytes of your brain to {', '.join(outside)}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing sent")
            return 1
    manifest = council.run(brain, args.slug, args.seat or None, args.resume)

    def show(p: Paint) -> None:
        for r in manifest["results"]:
            if r["ok"]:
                print(f"  {p.good('ok  ')} {r['seat']:<12} {r['model']} {p.dim('→ ' + r['file'])}")
            else:
                print(f"  {p.bad('fail')} {r['seat']:<12} {r['model']} {p.dim(r['error'][:100])}")
        ok = sum(1 for r in manifest["results"] if r["ok"])
        print(f"{ok} of {len(manifest['results'])} seats returned · next: kea synth {args.slug}")

    emit(args, manifest, show)
    return 0 if any(r["ok"] for r in manifest["results"]) else 1


def cmd_synth(args: argparse.Namespace) -> int:
    """
    Build the synthesis scaffold from the reviews.

    :param args: Parsed arguments
    :returns: Exit code
    """
    data = council.synth(_brain(args), args.slug)

    def human(p: Paint) -> None:
        tally = ", ".join(f"{k} {v}" for k, v in data["verdicts"].items()) or "none"
        print(f"{data['parsed']} of {data['reviews']} reviews parsed · verdicts: {tally}")
        print(
            f"{data['clusters']} recommendations: {data['strong']} strong, {data['debate']} split, {data['solo']} solo"
        )
        for name in data["unparsed"]:
            print(p.warn(f"  no COUNCIL-JSON block in {name}: read it by hand"))
        print(f"wrote {data['file']} {p.dim('· now the Chair writes 11-synthesis.md: what every seat missed')}")

    return emit(args, data, human)


def cmd_seats(args: argparse.Namespace) -> int:
    """
    List the council's seats and whether each can run.

    :param args: Parsed arguments
    :returns: Exit code
    :raises CouncilError: When a probe would send a key to a custom endpoint without a yes
    """
    brain = _brain(args)
    rows = council.seats(brain)
    custom = sorted({r["host"] for r in rows if r["ready"] and r["custom"]})
    if args.probe and custom and not args.yes:
        if not sys.stdin.isatty():
            raise CouncilError(f"--probe sends your API key to custom endpoints ({', '.join(custom)}); add --yes")
        answer = input(f"--probe sends your API key to {', '.join(custom)}. Go ahead? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing sent")
            return 1
    if args.probe:
        rows = council.seats(brain, True)

    def human(p: Paint) -> None:
        for r in rows:
            missing = "not running" if r["key"] == "none needed" else "no key"
            word = "ready" if r["ready"] else missing if r["enabled"] else "off"
            state = (p.good if r["ready"] else p.dim)(f"{word:<11}")
            if "answered" in r:
                state = p.good(f"{'answered':<11}") if r["answered"] else p.bad(f"{'failed':<11}")
            where = f"this machine ({r['host']})" if r["local"] else r["host"]
            print(f"  {p.accent(f'{r['seat']:<12}')} {state} {r['model']} {p.dim('· ' + where + ' · ' + r['key'])}")
            if r.get("detail") and not r.get("answered"):
                print(p.dim(f"      {r['detail']}"))
        print(p.dim("the Chair is the agent you work with: it reviews first, from the packet alone"))

    return emit(args, rows, human)


# ---------------------------------------------------------------- evidence


def cmd_ingest(args: argparse.Namespace) -> int:
    """
    Read sources into the local index.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    with store.Store(brain.index_path) as db:
        if args.source:
            reports = [ingest.ingest(brain, db, args.source, args.path, args.force)]
        else:
            reports = ingest.ingest_known(brain, db, args.force)
        summary = db.summary()
    data = {"reports": [r.as_dict() for r in reports], "index": summary}

    def human(p: Paint) -> None:
        for r in reports:
            line = f"{r.read} read, {r.unchanged} unchanged"
            if r.skipped:
                line += f", {r.skipped} skipped (denied)"
            if r.dropped:
                line += f", {r.dropped} gone"
            print(f"  {p.accent(f'{r.kind:<12}')} {line} · {_plural(r.items, 'item')} {p.dim(_home(r.path))}")
            for e in r.errors[:5]:
                print(p.warn(f"      {e[:150]}"))
        print(f"index: {summary['items']:,} items from {_plural(summary['sources'], 'source')}")

    emit(args, data, human)
    return 1 if any(r.errors and not r.read and not r.unchanged for r in reports) else 0


def cmd_sources(args: argparse.Namespace) -> int:
    """
    List what the index was read from.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    with store.Store(brain.index_path) as db:
        rows, roots, summary = db.sources(), db.roots(), db.summary()
    data = {"roots": [{"kind": k, "path": v} for k, v in roots], "summary": summary, "sources": rows}

    def human(p: Paint) -> None:
        for kind, path in roots:
            count = sum(r["items"] for r in rows if r["kind"] == kind and r["path"].startswith(path))
            print(f"  {p.accent(f'{kind:<12}')} {_home(path)} {p.dim('· ' + _plural(count, 'item'))}")
        brain_items = sum(r["items"] for r in rows if r["kind"] == "brain")
        print(f"  {p.accent(f'{'brain':<12}')} {_home(str(brain.root))} {p.dim('· ' + _plural(brain_items, 'item'))}")
        errors = [r for r in rows if r["error"]]
        for r in errors[:10]:
            print(p.warn(f"  error #{r['id']} {_home(r['path'])}: {r['error'][:100]}"))
        print(
            f"{summary['items']:,} items · {_plural(summary['sources'], 'source')} · last scan {summary['last_scan']}"
        )

    return emit(args, data, human)


def cmd_forget(args: argparse.Namespace) -> int:
    """
    Delete sources from the index.

    :param args: Parsed arguments
    :returns: Exit code
    :raises ValueError: When neither an id nor a kind is given
    """
    brain = _brain(args)
    if args.id is None and not args.kind:
        raise ValueError("name a source id (from kea sources --json) or --kind")
    with store.Store(brain.index_path) as db:
        n = db.forget(args.id, args.kind)
    data = {"forgotten": n}
    return emit(args, data, lambda p: print(f"forgot {_plural(n, 'source')} and everything read from them"))


def cmd_search(args: argparse.Namespace) -> int:
    """
    Search the evidence and the brain.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    query = " ".join(args.query)
    since = _since(args.since, brain)
    with store.Store(brain.index_path) as db:
        if not args.no_refresh:
            ingest.refresh_brain(brain, db)
        hits = db.search(query, args.kind, args.platform, args.mine, since, args.limit, args.any)

    def human(p: Paint) -> None:
        for h in hits:
            when = (h["ts"] or "")[:10]
            who = f" · {h['role']}" if h["role"] else ""
            title = _safe(h["title"] or "")[:70]
            print(f"{p.accent(str(h['id']).rjust(6))} {p.dim(h['platform'] + who)} {p.bold(title)} {p.dim(when)}")
            snippet = " ".join(_safe(h["snippet"] or "").split())
            if p.on:
                snippet = snippet.replace("[", "\033[1m").replace("]", "\033[0m")
            print(f"       {snippet[:220]}")
            print(p.dim(f"       {_safe(_home(h['ref']))}"))
        if not hits:
            print(p.dim("no matches. Index more with kea ingest, or try fewer words or --any"))

    return emit(args, {"query": query, "hits": hits}, human)


def cmd_show(args: argparse.Namespace) -> int:
    """
    Show one item with its provenance.

    :param args: Parsed arguments
    :returns: Exit code
    :raises ValueError: When there is no such item
    """
    brain = _brain(args)
    with store.Store(brain.index_path) as db:
        item = db.get(args.id)
        if item is None:
            raise ValueError(f"no item {args.id}; ids come from kea search")
        around = db.around(item, args.context)
    data = {"item": item, "context": around}

    def human(p: Paint) -> None:
        print(f"{p.accent('#' + str(item['id']))} {p.bold(_safe(item['title'] or ''))}")
        print(p.dim(f"from   {item['source_kind']} · {_safe(_home(item['source_path']))}"))
        print(p.dim(f"ref    {_safe(_home(item['ref']))}"))
        print(p.dim(f"when   {item['ts'] or 'undated'} · {item['role'] or item['kind']}"))
        print()
        for row in around:
            if len(around) > 1:
                marker = p.accent("▶") if row["id"] == item["id"] else " "
                print(f"{marker} {p.bold(row['role'] or '')} {p.dim(str(row['ts'] or ''))}")
            print(_safe(row["body"]))
            print()

    return emit(args, data, human)


def cmd_effort(args: argparse.Namespace) -> int:
    """
    Show where attention went, per project.

    :param args: Parsed arguments
    :returns: Exit code
    """
    brain = _brain(args)
    with store.Store(brain.index_path) as db:
        rows = db.effort(_since(args.since, brain), args.months)

    def human(p: Paint) -> None:
        if not rows:
            print(p.dim("no prompts indexed: kea ingest claude-code (or codex, opencode)"))
            return
        total = sum(r["prompts"] for r in rows) or 1
        for r in rows[: args.limit]:
            share = round(100 * r["prompts"] / total)
            month = f"{r['month']} " if args.months else ""
            first, last = (r["first"] or "")[:10], (r["last"] or "")[:10]
            name = _home(str(r["project"]))[-40:]
            print(
                f"  {month}{p.accent(_bar(share, 12))} {share:>3}% {r['prompts']:>6,} {name:<40} "
                + p.dim(f"{first} to {last}")
            )
        stale = sum(1 for r in rows if r["last"] and r["last"][:10] < brain.today().isoformat()[:8] + "01")
        print(p.dim(f"{len(rows)} projects · {total:,} prompts · {stale} not touched this month"))

    return emit(args, rows, human)


def cmd_digest(args: argparse.Namespace) -> int:
    """
    Turn a Claude Code transcript into readable files.

    :param args: Parsed arguments
    :returns: Exit code
    :raises ValueError: When no transcript is named or found
    """
    from kea import digest as digest_mod

    source = Path(args.transcript).expanduser() if args.transcript else digest_mod.latest_transcript(Path.cwd())
    if source is None or not source.is_file():
        raise ValueError("name a transcript (.jsonl), or run from a project folder with Claude Code sessions")
    if args.out:
        out = Path(args.out).expanduser()
    else:
        try:
            out = _brain(args).file("digests")
        except BrainError:
            out = Path.cwd()
            if any((folder / ".git").exists() for folder in (out, *out.parents)):
                raise ValueError(
                    "refusing to write a private transcript into this git working tree: name a folder with -o"
                ) from None
    result = digest_mod.digest(source, out, args.label, args.tz)
    data = dict(result.__dict__)

    def human(p: Paint) -> None:
        print(f"{p.accent('digest')} {_home(result.folder)}")
        print(f"  {result.prompts} prompts, {result.replies} replies, {result.compactions} compactions")
        print(p.dim("  digest.md, prompts.md, turns.md, meta.json · may hold private text, keep it out of git"))

    return emit(args, data, human)


# ---------------------------------------------------------------- agents and docs


def cmd_mcp(args: argparse.Namespace) -> int:
    """
    Serve the brain to agents over MCP on stdio.

    :param args: Parsed arguments
    :returns: Exit code
    """
    from kea import mcp

    mcp.serve(mcp.Server(getattr(args, "brain", None)))
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    """
    Install, list or print kea's agent skills.

    :param args: Parsed arguments
    :returns: Exit code
    """
    if args.action == "show":
        print(skills.text(args.name), end="")
        return 0
    if args.action == "list":
        names = list(skills.bundled())
        return emit(args, names, lambda p: print("\n".join(names)))
    rows = skills.install(args.agent or None, args.dry_run)

    def human(p: Paint) -> None:
        for r in rows:
            print(f"  {p.accent(f'{r['agent']:<10}')} {r['skill']:<16} {r['action']:<11} {p.dim(_home(r['path']))}")
        if not rows:
            print("no agent folders found; name one with --agent claude, codex, opencode or agents")

    return emit(args, rows, human)


def _data(name: str) -> str:
    return (resources.files("kea") / "data" / name).read_text(encoding="utf-8")


def cmd_guide(args: argparse.Namespace) -> int:
    """
    Print the full manual as Markdown, for people and agents alike.

    :param args: Parsed arguments
    :returns: Exit code
    """
    print(_data("MANUAL.md"), end="")
    return 0


def man_folder() -> "Path":
    """
    Find the per user man folder that ``man`` searches without any setup.

    :returns: ``$XDG_DATA_HOME/man/man1``, or ``~/.local/share/man/man1``
    """
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "man" / "man1"


def cmd_man(args: argparse.Namespace) -> int:
    """
    Show the man page, print it as roff, or install it so ``man kea`` works.

    :param args: Parsed arguments
    :returns: Exit code
    """
    page = _data(f"{PROG}.1")
    if args.install:
        target = man_folder() / f"{PROG}.1"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        print(f"installed {target}; open it with: man {PROG}")
        return 0
    man = shutil.which("man")
    if man and sys.stdout.isatty() and not args.raw:
        with resources.as_file(resources.files("kea") / "data" / f"{PROG}.1") as path:
            return subprocess.call([man, "-l", str(path)])  # nosec B603: man, fixed arguments
    print(page, end="")
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser with every command.

    :returns: The parser
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=f"kea: {TAGLINE}. Facts with sources, decisions with revisit dates, predictions that get scored, "
        "a council of rival models for big plans, and local search over your AI sessions. "
        "Run kea guide for the manual.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument(
        "--brain", metavar="PATH", help="the brain to use (default: $KEA_BRAIN, this folder, or your default)"
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    def add(
        name: str, func: Callable[[argparse.Namespace], int], about: str, json_flag: bool = True
    ) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=about, description=about)
        if json_flag:
            p.add_argument("--json", action="store_true", help="print JSON")
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "make a new brain")
    p.add_argument("path", nargs="?", help="where (default: this folder)")
    p.add_argument("--name", help="the brain's name")
    p.add_argument("--owner", help="whose brain it is: the person 'waiting for' means")
    p.add_argument("--example", action="store_true", help="fill it with a made up example brain to explore")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.add_argument("--no-git", action="store_true", help="do not run git init")
    p.add_argument("--default", action="store_true", help="make it your default brain even if you have one")
    p.add_argument("--no-default", action="store_true", help="never make it your default brain")

    add("status", cmd_status, "the whole brain at a glance, with health checks")
    p = add("check", cmd_check, "lint the brain; exit 1 on errors")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    p = add("state", cmd_state, "the facts and the newest log entries")
    p.add_argument("--entries", type=int, default=5, metavar="N", help="log entries to show (default 5)")

    p = add("log", cmd_log, "add a dated entry to STATE.md")
    p.add_argument("text", nargs="+", help="what happened or is now true")
    p.add_argument("--title", help="a short title (default: the first sentence)")
    p.add_argument("--source", help="where it can be checked")

    p = add("fact", cmd_fact, "record a fact with its source and verified date")
    p.add_argument("fact", help="what the fact is about")
    p.add_argument("value", help="its value")
    p.add_argument("--source", required=True, help="where it was read")
    p.add_argument("--verified", help="when it was checked (default: today)")

    p = add("decide", cmd_decide, "log a decision with why and when to revisit")
    p.add_argument("title", help="a short title")
    p.add_argument("--decision", required=True, help="what was decided, specifically")
    p.add_argument("--why", help="the reasoning and the facts it leans on")
    p.add_argument("--revisit", help="a date (2027-01-15), an offset (+90d) or a trigger in words")
    p.add_argument("--source", help="where the reasoning lives")
    p = add("decisions", cmd_decisions, "list decisions (default: the active ones)")
    p.add_argument("--due", action="store_true", help="only those due for revisit")
    p.add_argument("--all", action="store_true", help="include closed decisions")
    p = add("revisit", cmd_revisit, "record what happened after a decision")
    p.add_argument("id", help="such as D-0002 or 2")
    p.add_argument("--outcome", required=True, help="what actually happened")
    p.add_argument("--status", help="ACTIVE, COMPLETED, SUPERSEDED, REVERSED or ABANDONED")
    p.add_argument("--surprise", type=int, choices=range(11), metavar="0-10", help="how surprising, 0 to 10")
    p.add_argument("--lesson", help="what changes because of it")
    p.add_argument("--next", help="when to revisit again, for a decision that stays active")

    p = add("predict", cmd_predict, "write down a prediction with a date and a confidence")
    p.add_argument("claim", nargs="+", help="what will be true, specific enough to score")
    p.add_argument("--by", required=True, help="when it resolves: 2026-12-01, +30d, +6w or +3m")
    p.add_argument(
        "--confidence", type=int, required=True, choices=range(101), metavar="0-100", help="how sure, 0 to 100"
    )
    p.add_argument("--plan", help="the plan it belongs to")
    p.add_argument("--decision", help="the decision it tests, such as D-0003")
    p = add("score", cmd_score, "score a prediction: true, false, partial or void")
    p.add_argument("id", help="such as P-0004 or 4")
    p.add_argument("outcome", help="true, false, partial or void")
    p.add_argument("--note", help="what happened, with its source")
    p = add("predictions", cmd_predictions, "list predictions")
    p.add_argument("--due", action="store_true", help="only those past their date")
    p.add_argument("--open", action="store_true", help="only unscored ones")
    add("calibration", cmd_calibration, "how well calibrated your predictions were")

    p = add("idea", cmd_idea, "park an idea in IDEAS.md")
    p.add_argument("text", nargs="+", help="the idea, in your words")
    p.add_argument("--why", help="why it might matter")
    add("ideas", cmd_ideas, "list parked ideas")

    p = add("roadmap", cmd_roadmap, "open work: show it, add to it (add), close an item (done)")
    p.add_argument("action", nargs="?", choices=["add", "done"], help="add an item, or close one")
    p.add_argument("text", nargs="*", help="the item to add, or its number or text to close")
    p.add_argument("--waiting", action="store_true", help="add it under 'waiting for' the owner")
    p.add_argument("--running", action="store_true", help="add it under 'running by itself'")
    p.add_argument("--proof", help="how you know it is done")

    p = add("plan", cmd_plan, "start a plan (new SLUG) or list plans")
    p.add_argument("action", nargs="?", choices=["new", "list"], default="list")
    p.add_argument("slug", nargs="?", help="such as 2026-12-pricing")
    p = add("brief", cmd_brief, "build a plan's council packet")
    p.add_argument("slug")
    p.add_argument("--show", action="store_true", help="print the packet too")
    p = add("council", cmd_council, "send the packet to every ready seat, in parallel")
    p.add_argument("slug")
    p.add_argument("--seat", action="append", help="run only this seat (repeatable)")
    p.add_argument("--resume", action="store_true", help="skip seats that already reviewed")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    p.add_argument("--yes", action="store_true", help="send without asking")
    p = add("synth", cmd_synth, "build the synthesis scaffold from the reviews")
    p.add_argument("slug")
    p = add("seats", cmd_seats, "the council's seats and whether each can run")
    p.add_argument("--probe", action="store_true", help="ask each ready seat to answer")
    p.add_argument("--yes", action="store_true", help="probe custom endpoints without asking")

    p = add("ingest", cmd_ingest, "index sources locally: claude-code, codex, opencode, chatgpt, claude, git, docs")
    p.add_argument("source", nargs="?", choices=INGEST_KINDS, help="the adapter (default: rescan everything known)")
    p.add_argument("path", nargs="?", help="what to read (default: the adapter's usual place)")
    p.add_argument("--force", action="store_true", help="read everything again")
    add("sources", cmd_sources, "what the index was read from")
    p = add("forget", cmd_forget, "delete sources and everything read from them")
    p.add_argument("id", nargs="?", type=int, help="a source id")
    p.add_argument("--kind", help="every source of one kind")
    p = add("search", cmd_search, "search your sessions, exports, commits, documents and the brain")
    p.add_argument("query", nargs="+", help='free text; "quote a phrase"')
    p.add_argument("--kind", choices=["message", "commit", "doc", "note"])
    p.add_argument("--platform", help="claude-code, codex, opencode, chatgpt, claude, git, docs or brain")
    p.add_argument("--mine", action="store_true", help="only what you wrote")
    p.add_argument("--since", metavar="DATE", help="only on or after this date: 2026-11-01, today, -30d")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--any", action="store_true", help="match any word instead of every word")
    p.add_argument("--no-refresh", action="store_true", help="skip reading the brain's own files again first")
    p = add("show", cmd_show, "one search hit in full, with where it came from")
    p.add_argument("id", type=int)
    p.add_argument("--context", type=int, default=0, metavar="N", help="items on each side (default 0)")
    p = add("effort", cmd_effort, "where your attention went: your prompts per project")
    p.add_argument("--since", metavar="DATE", help="only from this date: 2026-11-01 or -90d")
    p.add_argument("--months", action="store_true", help="split by month")
    p.add_argument("--limit", type=int, default=25)
    p = add("digest", cmd_digest, "turn a Claude Code transcript into readable files")
    p.add_argument("transcript", nargs="?", help="a .jsonl transcript (default: this folder's newest session)")
    p.add_argument("-o", "--out", help="where to write (default: the brain's digests folder, which git ignores)")
    p.add_argument("--label", help="the folder's name (default: the session id)")
    p.add_argument("--tz", help="time zone for the times shown, such as Europe/London")

    add("mcp", cmd_mcp, "serve the brain to agents over MCP (stdio)", json_flag=False)
    p = add("skill", cmd_skill, "install kea's skills into Claude Code, Codex, OpenCode")
    p.add_argument("action", nargs="?", choices=["install", "list", "show"], default="install")
    p.add_argument("name", nargs="?", default="kea", help="the skill to show")
    p.add_argument("--agent", action="append", help="claude, codex, opencode or agents (default: every one found)")
    p.add_argument("--dry-run", action="store_true", help="only say what would be written")
    add("guide", cmd_guide, "print the full manual, for people and agents", json_flag=False)
    man = add("man", cmd_man, "show the man page", json_flag=False)
    man.add_argument("--install", action="store_true", help=f"install it so `man {PROG}` works")
    man.add_argument("--raw", action="store_true", help="print the roff source")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the command line.

    :param argv: Arguments, without the program name; ``sys.argv`` when omitted
    :returns: Exit code
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        try:
            Brain.locate(args.brain)
        except BrainError:
            parser.print_help()
            print("\nno brain yet. Start one: kea init ~/brain   (or explore: kea init /tmp/demo --example)")
            return 0
        args = parser.parse_args([*(argv or sys.argv[1:]), "status"])
    if args.command == "plan" and args.action == "new" and not args.slug:
        parser.error("kea plan new needs a slug, such as 2026-12-pricing")
    try:
        result: int = args.func(args)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001  every failure ends as one clear line, and as JSON under --json
        known = (BrainError, LedgerError, CouncilError, ProviderError, ValueError, KeyError, OSError)
        message = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
        if type(exc).__module__ == "sqlite3" or "sqlite3" in type(exc).__qualname__:
            message = f"the search index could not be used ({exc}); delete the brain's .kea folder and run kea ingest"
        elif not isinstance(exc, known):
            message = f"{type(exc).__name__}: {exc}"
        if getattr(args, "json", False):
            print(json.dumps({"error": message}))
        print(f"{PROG}: {message}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    sys.exit(main())
