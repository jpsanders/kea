"""
Read and write the brain's Markdown without losing a byte the person wrote.

The ledger files hold entries under ``###`` headings: an id, the parts of the heading, and ``- **Key:** value`` fields.
STATE.md holds a facts table and a dated log. kea parses these shapes, changes only the lines it owns, and keeps every
other line exactly as it was, so people and agents can edit the files by hand next to the CLI.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

SEP = " · "
HEADING = re.compile(r"^### (?P<id>[A-Z]-\d{3,})\s*(?:·|\|)\s*(?P<rest>.*?)\s*$")
FIELD = re.compile(r"^- \*\*(?P<key>[A-Za-z][A-Za-z ]*?):\*\*\s?(?P<value>.*)$")
LOG_HEADING = re.compile(
    r"^### (?P<date>\d{4}-\d{2}-\d{2})(?: (?P<time>\d{2}:\d{2}))?" r"\s*(?:·|\|)\s*(?P<title>.*?)\s*$"
)
SECTION = re.compile(r"^#{1,2} ")


@dataclass
class Entry:
    """One ``### X-0001 · a · b`` entry and its fields, with where it sits in the file."""

    id: str
    head: list[str]
    fields: dict[str, str]
    extra: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0

    def get(self, key: str, default: str = "") -> str:
        """
        Read a field, ignoring the case of its name.

        :param key: Field name
        :param default: What to return when the field is missing
        :returns: The value
        """
        wanted = key.lower()
        for name, value in self.fields.items():
            if name.lower() == wanted:
                return value
        return default

    def set(self, key: str, value: str) -> None:
        """
        Set a field, keeping its position when it exists.

        :param key: Field name
        :param value: New value, on one line
        """
        value = " ".join(value.split())
        for name in self.fields:
            if name.lower() == key.lower():
                self.fields[name] = value
                return
        self.fields[key] = value

    def render(self) -> list[str]:
        """
        Write the entry back as Markdown lines.

        :returns: Lines without newlines
        """
        lines = [f"### {self.id}{SEP}{SEP.join(self.head)}", ""]
        lines += [f"- **{k}:** {v}" if v else f"- **{k}:**" for k, v in self.fields.items()]
        extra = list(self.extra)
        while extra and not extra[-1].strip():
            extra.pop()
        if extra:
            lines += [""] + extra
        return lines


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
                head = [p.strip() for p in re.split(r"\s(?:·|\|)\s", m["rest"])] if m["rest"] else []
                current = Entry(id=m["id"], head=head, fields={}, start=i)
            continue
        if current is None:
            continue
        m = FIELD.match(line)
        if m and not current.extra:
            current.fields[m["key"].strip()] = m["value"].strip()
        elif line.startswith("  ") and current.fields and not current.extra and line.strip():
            last = next(reversed(current.fields))
            current.fields[last] = (current.fields[last] + " " + line.strip()).strip()
        elif line.strip() or current.extra:
            current.extra.append(line)
    if current:
        current.end = len(lines)
        out.append(current)
    for entry in out:
        while entry.extra and not entry.extra[-1].strip():
            entry.extra.pop()
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
    Read a file as lines, or nothing when it does not exist.

    :param path: The file
    :returns: Lines without newlines
    """
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def write_lines(path: Path, lines: list[str]) -> None:
    """
    Write lines atomically, so a crash never leaves half a ledger behind.

    :param path: The file
    :param lines: Lines without newlines
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.kea-tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(path)


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


def parse_log(lines: list[str]) -> list[LogEntry]:
    """
    Find the dated entries in STATE.md, in file order (newest first by convention).

    :param lines: The file's lines
    :returns: Log entries
    """
    out: list[LogEntry] = []
    current: LogEntry | None = None
    for i, line in enumerate(lines, 1):
        m = LOG_HEADING.match(line)
        if m:
            current = LogEntry(m["date"], m["time"] or "", m["title"], [], i)
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
