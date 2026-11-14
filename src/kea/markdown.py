"""
Read and write the brain's Markdown without losing a byte the person wrote.

The ledger files hold entries under ``###`` headings: an id, the parts of the heading, and ``- **Key:** value`` fields.
STATE.md holds a facts table and a dated log. kea parses these shapes and changes only the lines it owns: an entry
keeps its raw lines, so hand written notes, repeated fields, nested lists and continuation lines survive every write.
Files keep their line endings and permissions, and a symbolic link stays a link.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SEP = " · "
HEADING = re.compile(r"^### (?P<id>[A-Z]-\d{3,})\s*(?:·|\|)\s*(?P<rest>.*?)\s*$")
FIELD = re.compile(r"^- \*\*(?P<key>[A-Za-z][A-Za-z ]*?):\*\*\s?(?P<value>.*)$")
LOG_HEADING = re.compile(
    r"^### (?P<date>\d{4}-\d{2}-\d{2})(?: (?P<time>\d{2}:\d{2}))?" r"\s*(?:·|\|)\s*(?P<title>.*?)\s*$"
)
SECTION = re.compile(r"^#{1,2} ")
PART_SPLIT = re.compile(r"\s(?:·|\|)\s")


def clean_part(text: str) -> str:
    """
    Make text safe as one part of an entry heading: one line, and no `` · `` or `` | `` that would split it.

    :param text: A title or a claim
    :returns: The text, cleaned
    """
    return re.sub(r"\s+(?:·|\|)\s+", ", ", " ".join(str(text).split()))


@dataclass
class Entry:
    """One ``### X-0001 · a · b`` entry: its heading parts and its raw body lines, with where it sits in the file."""

    id: str
    head: list[str]
    body: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0

    def spans(self) -> list[tuple[str, int, int]]:
        """
        Find every field: its name and its lines, continuation lines included.

        :returns: Name, first line and end line (exclusive), indices into ``body``
        """
        out = []
        i = 0
        while i < len(self.body):
            m = FIELD.match(self.body[i])
            if not m:
                i += 1
                continue
            j = i + 1
            while j < len(self.body) and self.body[j].strip() and self.body[j][:1] in (" ", "\t"):
                j += 1
            out.append((m["key"].strip(), i, j))
            i = j
        return out

    def _value(self, start: int, end: int) -> str:
        m = FIELD.match(self.body[start])
        first = m["value"].strip() if m else ""
        rest = " ".join(line.strip() for line in self.body[start + 1 : end])
        return " ".join(x for x in (first, rest) if x)

    def values(self, key: str) -> list[str]:
        """
        Read every value of a field, in order, ignoring the case of its name.

        :param key: Field name
        :returns: The values
        """
        return [self._value(a, b) for name, a, b in self.spans() if name.lower() == key.lower()]

    def get(self, key: str, default: str = "") -> str:
        """
        Read a field's latest value, ignoring the case of its name.

        :param key: Field name
        :param default: What to return when the field is missing
        :returns: The value
        """
        found = self.values(key)
        return found[-1] if found else default

    @property
    def fields(self) -> dict[str, str]:
        """
        Read every field, the latest value of each.

        :returns: Field names to values
        """
        out: dict[str, str] = {}
        for name, a, b in self.spans():
            out[name] = self._value(a, b)
        return out

    def _insert_at(self) -> int:
        spans = self.spans()
        return spans[-1][2] if spans else 0

    def set(self, key: str, value: str) -> None:
        """
        Set a field on one line, replacing its first occurrence where it stands, or adding it after the last field.

        :param key: Field name
        :param value: New value
        """
        line = _field_line(key, value)
        for name, a, b in self.spans():
            if name.lower() == key.lower():
                self.body[a:b] = [line]
                return
        self.body.insert(self._insert_at(), line)

    def add(self, key: str, value: str) -> None:
        """
        Add a field line after the last field, keeping any earlier value of the same field.

        :param key: Field name
        :param value: The value
        """
        self.body.insert(self._insert_at(), _field_line(key, value))

    def render(self) -> list[str]:
        """
        Write the entry back as Markdown lines.

        :returns: Lines without newlines
        """
        heading = f"### {self.id}{SEP}{SEP.join(self.head)}"
        return [heading, ""] + self.body if self.body else [heading]


def _field_line(key: str, value: str) -> str:
    value = " ".join(str(value).split())
    return f"- **{key}:** {value}" if value else f"- **{key}:**"


def parse_entries(lines: list[str]) -> list[Entry]:
    """
    Find every entry in a ledger file.

    :param lines: The file's lines, without newlines
    :returns: Entries in file order
    """
    out: list[Entry] = []
    current: Entry | None = None
    for i, line in enumerate(lines):
        if SECTION.match(line) or line.startswith("### "):
            if current:
                current.end = i
                out.append(current)
                current = None
            m = HEADING.match(line)
            if m:
                head = [p.strip() for p in PART_SPLIT.split(m["rest"])] if m["rest"] else []
                current = Entry(id=m["id"], head=head, start=i)
            continue
        if current is not None:
            current.body.append(line)
    if current:
        current.end = len(lines)
        out.append(current)
    for entry in out:
        while entry.body and not entry.body[-1].strip():
            entry.body.pop()
        while entry.body and not entry.body[0].strip():
            entry.body.pop(0)
    return out


def malformed_headings(lines: list[str]) -> list[tuple[int, str]]:
    """
    Find ``### X-...`` headings that look like entries but do not parse.

    :param lines: The file's lines
    :returns: Line numbers (1 based) and the lines
    """
    bad = []
    for i, line in enumerate(lines, 1):
        if re.match(r"^### [A-Z]-\S", line) and not HEADING.match(line):
            bad.append((i, line))
    return bad


def next_id(entries: list[Entry], prefix: str) -> str:
    """
    Give the next free id for a prefix, one past the highest in use.

    :param entries: Existing entries
    :param prefix: ``D``, ``P`` or ``I``
    :returns: Such as ``D-0004``
    """
    numbers = [int(e.id.split("-", 1)[1]) for e in entries if e.id.startswith(prefix + "-")]
    return f"{prefix}-{(max(numbers) + 1 if numbers else 1):04d}"


def read_lines(path: Path) -> list[str]:
    """
    Read a file as lines, or nothing when it does not exist. Only a newline ends a line.

    :param path: The file
    :returns: Lines without newlines or carriage returns
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    return lines[:-1] if text.endswith("\n") else lines


def write_lines(path: Path, lines: list[str]) -> None:
    """
    Write lines atomically, keeping the file's line endings and permissions, and writing through a symbolic link.

    :param path: The file
    :param lines: Lines without newlines
    """
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    newline, mode = "\n", None
    if target.is_file():
        newline = "\r\n" if b"\r\n" in target.read_bytes()[:65536] else "\n"
        mode = target.stat().st_mode & 0o7777
    body = list(lines)
    while body and not body[-1].strip():
        body.pop()
    tmp = target.with_name(f".{target.name}.kea-tmp")
    tmp.write_bytes((newline.join(body) + newline).encode("utf-8"))
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(target)


def insert_after_marker(lines: list[str], marker: str, block: list[str]) -> list[str]:
    """
    Insert a block right after a marker comment (newest first), or at the end when there is no marker.

    :param lines: The file's lines
    :param marker: The marker's text, such as ``kea:entries``
    :param block: Lines to insert
    :returns: The new lines
    """
    for i, line in enumerate(lines):
        if marker in line and line.strip().startswith("<!--"):
            return lines[: i + 1] + [""] + block + lines[i + 1 :]
    body = list(lines)
    while body and not body[-1].strip():
        body.pop()
    return body + [""] + block


def replace_entry(lines: list[str], entry: Entry) -> list[str]:
    """
    Put an edited entry back where it was.

    :param lines: The file's lines
    :param entry: An entry from :func:`parse_entries` on the same lines
    :returns: The new lines
    """
    tail = lines[entry.start : entry.end]
    trailing = 0
    while trailing < len(tail) and not tail[-1 - trailing].strip():
        trailing += 1
    return lines[: entry.start] + entry.render() + [""] * max(trailing, 1) + lines[entry.end :]


@dataclass
class LogEntry:
    """One dated ``### 2026-11-13 10:20 · Title`` entry in STATE.md's log."""

    date: str
    time: str
    title: str
    body: list[str]
    line: int
    valid: bool = True


def parse_log(lines: list[str]) -> list[LogEntry]:
    """
    Find the dated entries in STATE.md, in file order (newest first by convention).

    :param lines: The file's lines
    :returns: Log entries; one whose date does not exist, such as 2026-11-31, has ``valid`` set to False
    """
    out: list[LogEntry] = []
    current: LogEntry | None = None
    for i, line in enumerate(lines, 1):
        m = LOG_HEADING.match(line)
        if m:
            try:
                date.fromisoformat(m["date"])
                valid = True
            except ValueError:
                valid = False
            current = LogEntry(m["date"], m["time"] or "", m["title"], [], i, valid)
            out.append(current)
            continue
        if SECTION.match(line) or line.startswith("### "):
            current = None
            continue
        if current is not None:
            current.body.append(line)
    for entry in out:
        while entry.body and not entry.body[-1].strip():
            entry.body.pop()
        while entry.body and not entry.body[0].strip():
            entry.body.pop(0)
    return out


def section_span(lines: list[str], title_prefix: str) -> tuple[int, int] | None:
    """
    Find a ``## Section`` by the start of its title, ignoring case.

    :param lines: The file's lines
    :param title_prefix: Such as ``Facts`` or ``Waiting for``
    :returns: The index of the heading and the index where the section ends, or None
    """
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            if start is not None:
                return start, i
            if line[3:].strip().lower().startswith(title_prefix.lower()):
                start = i
    return (start, len(lines)) if start is not None else None


def first_table(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """
    Find the first Markdown table between two lines.

    :param lines: The file's lines
    :param start: Where to start looking
    :param end: Where to stop
    :returns: The index of its header row and the index after its last row, or None
    """
    for i in range(start, end):
        if lines[i].strip().startswith("|"):
            j = i
            while j < end and lines[j].strip().startswith("|"):
                j += 1
            return i, j
    return None


def table_cells(line: str) -> list[str]:
    r"""
    Split a Markdown table row into cells, honouring ``\|`` escapes.

    :param line: A row such as ``| a | b |``
    :returns: The cell texts
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [c.strip().replace("\\|", "|") for c in cells]


def table_row(cells: list[str]) -> str:
    """
    Write cells as a Markdown table row, escaping pipes.

    :param cells: The cell texts
    :returns: The row
    """
    return "| " + " | ".join(" ".join(c.split()).replace("|", "\\|") for c in cells) + " |"


def is_separator(line: str) -> bool:
    """
    Tell whether a table row is the ``| --- |`` line under the header.

    :param line: A table row
    :returns: True for the separator
    """
    return bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?", line.strip()))
