from pathlib import Path

from kea.markdown import (
    Entry,
    insert_after_marker,
    is_separator,
    malformed_headings,
    next_id,
    parse_entries,
    parse_log,
    replace_entry,
    section_span,
    table_cells,
    table_row,
    write_lines,
)

LEDGER = """# DECISIONS

Intro text that kea must keep.

<!-- kea:entries (newest first) -->

### D-0002 · 2026-11-10 · Second one

- **Decision:** Do the second thing.
- **Status:** ACTIVE
    carried on onto a second line
- **Revisit:** 2026-12-01

A note the person wrote by hand.

### D-0001 | 2026-11-01 | First one

- **Decision:** Do the first thing.

## Another section
"""


def test_parse_entries_reads_ids_heads_fields_and_extra_lines() -> None:
    entries = parse_entries(LEDGER.splitlines())
    assert [e.id for e in entries] == ["D-0002", "D-0001"]
    second, first = entries
    assert second.head == ["2026-11-10", "Second one"]
    assert second.get("status") == "ACTIVE carried on onto a second line"
    assert second.body[-1] == "A note the person wrote by hand."
    assert first.head == ["2026-11-01", "First one"]
    assert first.get("missing", "x") == "x"


def test_replace_entry_changes_only_that_entry() -> None:
    lines = LEDGER.splitlines()
    entry = parse_entries(lines)[0]
    entry.set("Status", "COMPLETED")
    entry.set("Outcome", "It worked")
    out = replace_entry(lines, entry)
    text = "\n".join(out)
    assert "- **Status:** COMPLETED" in text
    assert "- **Outcome:** It worked" in text
    assert "A note the person wrote by hand." in text
    assert "Intro text that kea must keep." in text
    assert parse_entries(out)[1].get("Decision") == "Do the first thing."


def test_insert_after_marker_puts_the_newest_first_and_falls_back_to_the_end() -> None:
    lines = LEDGER.splitlines()
    out = insert_after_marker(lines, "kea:entries", ["### D-0003 · 2026-11-13 · Third"])
    assert [e.id for e in parse_entries(out)] == ["D-0003", "D-0002", "D-0001"]
    plain = insert_after_marker(["# Title", "", ""], "kea:entries", ["new"])
    assert plain == ["# Title", "", "new"]


def test_next_id_and_render_round_trip() -> None:
    entries = parse_entries(LEDGER.splitlines())
    assert next_id(entries, "D") == "D-0003"
    assert next_id([], "P") == "P-0001"
    entry = Entry("P-0001", ["by 2026-12-01", "60%", "A claim"])
    entry.set("Status", "OPEN")
    again = parse_entries(entry.render())[0]
    assert again.head == entry.head and again.fields == entry.fields == {"Status": "OPEN"}


def test_malformed_headings_are_reported() -> None:
    bad = malformed_headings(["### D-12 broken", "### D-0001 · ok · fine", "### Not an entry"])
    assert bad == [(1, "### D-12 broken")]


def test_log_entries_and_sections() -> None:
    lines = ["# STATE", "## Facts", "| a |", "## Log", "### 2026-11-13 10:20 · Hello", "", "Body", "", "## End"]
    log = parse_log(lines)
    assert len(log) == 1 and log[0].title == "Hello" and log[0].time == "10:20" and log[0].body == ["Body"]
    assert section_span(lines, "facts") == (1, 3)
    assert section_span(lines, "nothing") is None


def test_table_cells_escape_pipes() -> None:
    row = table_row(["a | b", "  spaced   out ", "c"])
    assert row == "| a \\| b | spaced out | c |"
    assert table_cells(row) == ["a | b", "spaced out", "c"]
    assert is_separator("| --- | :---: |") and not is_separator("| a | b |")


def test_write_lines_is_atomic_and_ends_with_one_newline(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "file.md"
    write_lines(target, ["a", "b", "", ""])
    assert target.read_text() == "a\nb\n"
    assert not list(target.parent.glob(".*kea-tmp"))
