from pathlib import Path

import pytest

from kea import ledger
from kea.brain import Brain
from kea.ledger import LedgerError


def test_log_inserts_newest_first_with_a_derived_title(brain: Brain) -> None:
    first = ledger.log(brain, "Signed the lease. It runs five years.", source="journal/lease.md")
    assert first["title"] == "Signed the lease" and first["date"] == "2026-11-13" and first["time"] == "10:30"
    ledger.log(brain, "Second entry", title="Custom title")
    state = ledger.state(brain, entries=5)
    assert [e["title"] for e in state["log"]][:2] == ["Custom title", "Signed the lease"]
    assert "Source: journal/lease.md" in brain.file("STATE.md").read_text()
    with pytest.raises(LedgerError):
        ledger.log(brain, "   ")


def test_facts_are_added_updated_and_escaped(brain: Brain) -> None:
    added = ledger.set_fact(brain, "Cash on hand", "EUR 4,200", "bank export", "2026-11-01")
    assert added["new"] and added["verified"] == "2026-11-01"
    updated = ledger.set_fact(brain, "cash on hand", "EUR 5,000 | after rent", "bank export")
    assert not updated["new"] and updated["verified"] == "2026-11-13"
    facts = ledger.facts(brain)
    assert len(facts) == 1 and facts[0].value == "EUR 5,000 | after rent"
    with pytest.raises(LedgerError):
        ledger.set_fact(brain, "x", "", "y")
    with pytest.raises(LedgerError):
        ledger.set_fact(brain, "x", "1", "y", "not a date")


def test_facts_section_is_created_when_missing(brain: Brain) -> None:
    brain.file("STATE.md").write_text("# STATE\n\n## Log\n\n<!-- kea:log -->\n")
    ledger.set_fact(brain, "Staff", "3", "rota")
    assert [f.fact for f in ledger.facts(brain)] == ["Staff"]


def test_decisions_are_logged_listed_and_revisited(brain: Brain) -> None:
    d = ledger.decide(brain, "Open on Sundays", "Open 9 to 14", why="demand", revisit="+30d", source="plan")
    assert d["id"] == "D-0001" and d["revisit"] == "2026-12-13" and not d["due"]
    ledger.decide(brain, "Old one", "Something", revisit="2026-11-01")
    due = ledger.decisions(brain, due=True)
    assert [x["id"] for x in due] == ["D-0002"]
    closed = ledger.revisit(brain, "2", "It worked", status="completed", surprise=3, lesson="Do it sooner")
    assert closed["status"] == "COMPLETED" and not closed["due"]
    assert [x["id"] for x in ledger.decisions(brain)] == ["D-0001"]
    assert len(ledger.decisions(brain, closed=True)) == 2
    with pytest.raises(LedgerError):
        ledger.revisit(brain, "D-0001", "x", status="MAYBE")
    with pytest.raises(LedgerError):
        ledger.revisit(brain, "D-0001", "x", surprise=11)
    with pytest.raises(LedgerError):
        ledger.revisit(brain, "D-0099", "x")
    with pytest.raises(LedgerError):
        ledger.decide(brain, "", "x")


def test_revisit_can_set_the_next_date(brain: Brain) -> None:
    ledger.decide(brain, "Keep going", "Continue", revisit="2026-11-01")
    out = ledger.revisit(brain, "D-0001", "Still right", next_revisit="+14d")
    assert out["revisit"] == "2026-11-27" and out["status"] == "ACTIVE"


def test_predictions_score_and_calibrate(brain: Brain) -> None:
    for claim, conf in (("A", 90), ("B", 80), ("C", 60), ("D", 30)):
        ledger.predict(brain, claim, "2026-11-01", conf, plan="p1")
    ledger.score(brain, "P-0001", "true")
    ledger.score(brain, "2", "no", note="missed")
    ledger.score(brain, "P-0003", "partial")
    ledger.score(brain, "P-0004", "void")
    cal = ledger.calibration(brain)
    assert cal["scored"] == 3
    assert cal["brier"] == round(((0.9 - 1) ** 2 + (0.8 - 0) ** 2 + (0.6 - 0.5) ** 2) / 3, 3)
    assert cal["skill"] == round(1 - cal["brier"] / 0.25, 3)
    assert {b["range"] for b in cal["buckets"]} == {"90 to 100", "80 to 89", "60 to 69"}
    assert ledger.predictions(brain, open_only=True) == []


def test_prediction_validation_and_due(brain: Brain) -> None:
    p = ledger.predict(brain, "It ships", "2026-11-10", 70)
    assert p["due"] and p["claim"] == "It ships"
    assert [x["id"] for x in ledger.predictions(brain, due=True)] == ["P-0001"]
    with pytest.raises(LedgerError):
        ledger.predict(brain, "x", "someday", 50)
    with pytest.raises(LedgerError):
        ledger.predict(brain, "x", "+3d", 101)
    with pytest.raises(LedgerError):
        ledger.predict(brain, " ", "+3d", 50)
    with pytest.raises(LedgerError):
        ledger.score(brain, "P-0001", "maybe")
    assert ledger.calibration(brain)["scored"] == 0


def test_ideas_are_parked(brain: Brain) -> None:
    long = "word " * 30
    idea = ledger.idea(brain, long, why="because")
    assert idea["id"] == "I-0001" and idea["title"].endswith("...")
    assert ledger.ideas(brain)[0]["status"] == "PARKED"
    with pytest.raises(LedgerError):
        ledger.idea(brain, "")


def test_roadmap_add_list_and_done(brain: Brain) -> None:
    ledger.roadmap_add(brain, "Answer the landlord", "waiting")
    ledger.roadmap_add(brain, "Nightly backup", "running")
    view = ledger.roadmap(brain)
    assert view["owner"] == "Ada"
    assert [i["text"] for i in view["waiting"]][-1] == "Answer the landlord (added 2026-11-13)"
    assert view["running"][0]["text"].startswith("Nightly backup")
    text = brain.file("ROADMAP.md").read_text()
    assert text.index("Jobs, schedules") < text.index("Nightly backup")
    n = view["waiting"][-1]["n"]
    done = ledger.roadmap_done(brain, str(n), proof="emailed")
    assert done["proof"] == "emailed"
    assert (
        "- [x] Answer the landlord (added 2026-11-13) (done 2026-11-13: emailed)"
        in brain.file("ROADMAP.md").read_text()
    )
    ledger.roadmap_done(brain, "nightly")
    with pytest.raises(LedgerError):
        ledger.roadmap_done(brain, "nothing matches this")
    with pytest.raises(LedgerError):
        ledger.roadmap_add(brain, "x", "someday")


def test_roadmap_dates_are_read(example: Brain) -> None:
    dates = ledger.roadmap(example)["dates"]
    assert [d["date"] for d in dates] == ["2026-11-17", "2026-12-08"]


def test_check_finds_the_problems(brain: Brain) -> None:
    assert not [f for f in ledger.check(brain) if f.level == "error"]
    state = brain.file("STATE.md")
    state.write_text(
        state.read_text().replace("| --- | --- | --- | --- |", "| --- | --- | --- | --- |\n| Rent | 900 |  |  |")
    )
    brain.file("DECISIONS.md").write_text(brain.file("DECISIONS.md").read_text() + "\n### D-1 broken\n")
    ledger.predict(brain, "late", "2026-11-01", 50)
    ledger.decide(brain, "No date", "x")
    found = {(f.level, f.message.split(" ")[0]) for f in ledger.check(brain)}
    messages = " ".join(f.message for f in ledger.check(brain))
    assert ("error", "fact") in found
    assert "heading does not parse" in messages
    assert "is not scored" in messages
    assert "no revisit date" in messages


def test_check_flags_stale_state_and_incomplete_frameworks(brain: Brain) -> None:
    state = brain.file("STATE.md")
    state.write_text(state.read_text().replace("2026-11-13 09:00", "2026-09-01 09:00"))
    (brain.file("frameworks") / "lens.md").write_text("---\nname: lens\nretrodiction_score: <n of 3>\n---\n# Lens\n")
    messages = [f.message for f in ledger.check(brain)]
    assert any("more than 14 days ago" in m for m in messages)
    assert "no boundary conditions" in messages and "no retrodiction score" in messages


def test_check_wants_three_predictions_per_decided_plan(brain: Brain) -> None:
    plan = brain.file("plans", "go")
    plan.mkdir(parents=True)
    (plan / "20-production-plan.md").write_text("# Production plan\n\n**Decided:** 2026-11-01\n")
    ledger.predict(brain, "one", "+30d", 50, plan="go")
    assert any("decided with 1 predictions" in f.message for f in ledger.check(brain))


def test_status_of_the_example(example: Brain) -> None:
    s = ledger.status(example)
    assert s["brain"] == "Tidewater Coffee" and s["owner"] == "Mara"
    assert s["decisions"]["due"] == ["D-0002"]
    assert s["predictions"]["due"] == ["P-0007"]
    assert s["predictions"]["scored"] == 6
    assert s["state"]["unverified"] == 2 and s["state"]["stale_facts"] == 1
    assert s["metrics_not_measured"] == 2
    assert s["plans"][0]["slug"] == "second-cafe" and s["plans"][0]["reviews"] == 3
    assert s["attention"] == 3


def test_missing_files_are_reported(tmp_path: Path, brain: Brain) -> None:
    brain.file("DECISIONS.md").unlink()
    with pytest.raises(LedgerError):
        ledger.decide(brain, "x", "y")
    assert ledger.decisions(brain) == []
