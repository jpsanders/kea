"""
Build the man page, the packaged manual and the packaged skills from their one source each.

docs/MANUAL.md is copied into the package, so ``kea guide`` prints it, and converted to roff, so ``kea man`` and
``man kea`` show it. Each ``skills/<name>/SKILL.md`` is copied into the package, so ``kea skill install`` can put it
where agents look.

    python scripts/build_man.py           # write the generated files
    python scripts/build_man.py --check   # fail when any is out of date
"""

import argparse
import re
import sys
from pathlib import Path

PROG = "kea"
PACKAGE = "kea"
ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / "docs" / "MANUAL.md"
PACKAGED = ROOT / "src" / PACKAGE / "data" / "MANUAL.md"
MANPAGE = ROOT / "src" / PACKAGE / "data" / f"{PROG}.1"
SKILLS = ROOT / "skills"
PACKAGED_SKILLS = ROOT / "src" / PACKAGE / "data" / "skills"


def _escape(text: str) -> str:
    """
    Escape a backslash for roff.

    :param text: Plain text
    :returns: Text safe inside a roff line
    """
    return text.replace("\\", "\\e")


def _inline(text: str) -> str:
    """
    Convert inline Markdown (code, bold, italic, links) to roff fonts.

    :param text: One line of Markdown
    :returns: The line in roff
    """
    out = []
    for part in re.split(r"(`[^`]+`)", text):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append("\\fB" + _escape(part[1:-1]).replace("-", "\\-") + "\\fR")
            continue
        part = _escape(part)
        part = re.sub(r"\*\*([^*]+)\*\*", r"\\fB\1\\fR", part)
        part = re.sub(r"\*([^*]+)\*", r"\\fI\1\\fR", part)
        part = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", part)
        out.append(part)
    line = "".join(out)
    return "\\&" + line if line.startswith((".", "'")) else line


def to_roff(markdown: str) -> str:
    """
    Convert the manual's Markdown subset to roff with the man macros.

    Supported: ``##`` sections, ``###`` subsections, paragraphs, ``-`` lists, fenced code and two column tables.

    :param markdown: The manual
    :returns: The man page
    """
    out = [f'.TH {PROG.upper()} 1 "" "{PROG}" "User Commands"']
    para: list[str] = []
    in_code = in_table = False

    def flush() -> None:
        if para:
            out.extend([".PP", _inline(" ".join(para))])
            para.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            flush()
            out.extend([".fi", ".RE"] if in_code else [".RS 4", ".nf"])
            in_code = not in_code
            continue
        if in_code:
            text = _escape(line).replace("-", "\\-")
            out.append("\\&" + text if text.startswith((".", "'")) else text)
            continue
        if line.startswith(("<!--", "# ")):
            continue
        if line.startswith("## "):
            flush()
            out.append(f".SH {line[3:].upper()}")
            continue
        if line.startswith("### "):
            flush()
            out.append(f".SS {_inline(line[4:])}")
            continue
        if line.startswith("|"):
            flush()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("- "):
                continue
            if not in_table:
                in_table = True  # the header row
                continue
            out.extend([".TP", _inline(cells[0]), _inline(" ".join(cells[1:]))])
            continue
        in_table = False
        if line.startswith("- "):
            flush()
            out.extend([".IP \\(bu 2", _inline(line[2:])])
            continue
        if not line.strip():
            flush()
            continue
        para.append(line.strip())
    flush()
    page = "\n".join(out) + "\n"
    # The NAME line must be "name \- summary" with no paragraph macro, or whatis and apropos cannot read it.
    return re.sub(r"\.SH NAME\n\.PP\n(\S+) \\?- ", r".SH NAME\n\1 \\- ", page)


def main() -> int:
    """
    Build or check the generated files.

    :returns: Exit code
    """
    parser = argparse.ArgumentParser(description="Build the man page, the packaged manual and the packaged skills.")
    parser.add_argument("--check", action="store_true", help="fail when a generated file is out of date")
    args = parser.parse_args()
    manual = MANUAL.read_text(encoding="utf-8")
    wanted = {PACKAGED: manual, MANPAGE: to_roff(manual)}
    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        wanted[PACKAGED_SKILLS / skill.parent.name / "SKILL.md"] = skill.read_text(encoding="utf-8")
    stale = [p for p, text in wanted.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    extra = [p for p in PACKAGED_SKILLS.glob("*/SKILL.md") if p not in wanted]
    stale += extra
    if args.check:
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)} (run: make man)", file=sys.stderr)
        return 1 if stale else 0
    for path in extra:
        path.unlink()
        print(f"removed {path.relative_to(ROOT)}")
    for path, text in wanted.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
