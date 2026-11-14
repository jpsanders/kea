"""
Find a brain, read its settings, keep its clock, and make new ones.

A brain is a folder with a ``kea.toml`` in it. kea finds one in this order: ``--brain``, then ``$KEA_BRAIN``, then the
current folder and its parents, then the default brain named in ``~/.config/kea/config.toml``. A folder without
``kea.toml`` is never treated as a brain, so notes that kea did not make are never touched.
"""

import calendar
import os
import re
import shutil
import subprocess  # nosec B404: runs git init with fixed arguments only
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MARKER = "kea.toml"
INDEX_DIR = ".kea"
# Template files are shipped without a leading dot, so packaging tools never skip them.
DOTFILES = {"gitignore": ".gitignore"}


class BrainError(Exception):
    """A brain could not be found, read or made."""


def user_config_path() -> Path:
    """
    Find the per user config file.

    :returns: ``$XDG_CONFIG_HOME/kea/config.toml``, or ``~/.config/kea/config.toml``
    """
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kea" / "config.toml"


def default_brain() -> Path | None:
    """
    Read the default brain from the per user config.

    :returns: Its folder, or None when no default is set
    """
    path = user_config_path()
    if not path.is_file():
        return None
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8")).get("brain")
    except tomllib.TOMLDecodeError:
        return None
    folder = Path(value).expanduser() if isinstance(value, str) and value else None
    return folder if folder and (folder / MARKER).is_file() else None


def set_default_brain(folder: Path) -> Path:
    """
    Name a brain as the default, keeping any other lines in the config.

    :param folder: The brain folder
    :returns: The config file written
    """
    path = user_config_path()
    line = f"brain = {_toml_string(str(folder.resolve()))}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else ["# kea per user settings"]
    lines = [ln for ln in lines if not re.match(r"^\s*brain\s*=", ln)]
    first_table = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), len(lines))
    lines.insert(first_table, line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class Brain:
    """A brain folder and its settings."""

    root: Path
    config: dict[str, Any]

    @classmethod
    def load(cls, root: Path) -> "Brain":
        """
        Open the brain in a folder.

        :param root: The folder holding ``kea.toml``
        :returns: The brain
        :raises BrainError: When the folder holds no ``kea.toml`` or it is not valid TOML
        """
        root = root.expanduser().resolve()
        marker = root / MARKER
        if not marker.is_file():
            raise BrainError(f"{root} is not a kea brain (no {MARKER}); make one with: kea init {root}")
        try:
            config = tomllib.loads(marker.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise BrainError(f"{marker}: {exc}") from exc
        return cls(root, config)

    @classmethod
    def locate(cls, explicit: str | None = None, cwd: Path | None = None, prefer_default: bool = False) -> "Brain":
        """
        Find the brain to work on.

        :param explicit: A folder from ``--brain``, which wins over everything else
        :param cwd: Where to start looking upwards; the current folder when omitted
        :param prefer_default: Try the default brain before the folders above cwd, as the MCP server does, so a
            ``kea.toml`` inside a project an agent happens to run in never takes over
        :returns: The brain
        :raises BrainError: When no brain is found
        """
        chosen = explicit or os.environ.get("KEA_BRAIN")
        if chosen:
            return cls.load(Path(chosen))
        fallback = default_brain()
        if prefer_default and fallback:
            return cls.load(fallback)
        here = (cwd or Path.cwd()).resolve()
        for folder in (here, *here.parents):
            if (folder / MARKER).is_file():
                return cls.load(folder)
        if fallback:
            return cls.load(fallback)
        raise BrainError(
            "no kea brain found here, in any parent folder, or as the default. "
            "Make one with: kea init ~/brain, or point at one with --brain or KEA_BRAIN"
        )

    def setting(self, section: str, key: str, default: Any = None) -> Any:
        """
        Read one setting from ``kea.toml``.

        :param section: The table, such as ``brain``
        :param key: The key
        :param default: What to return when it is missing
        :returns: The value
        """
        table = self.config.get(section)
        return table.get(key, default) if isinstance(table, dict) else default

    @property
    def name(self) -> str:
        """
        The brain's display name.

        :returns: The brain's display name
        """
        return str(self.setting("brain", "name", self.root.name))

    @property
    def owner(self) -> str:
        """
        Who the brain belongs to: the person "Waiting for" means.

        :returns: The owner's name
        """
        return str(self.setting("brain", "owner", "") or "the owner")

    @property
    def timezone(self) -> str | None:
        """
        The brain's IANA time zone, when set.

        :returns: The brain's IANA time zone, when set
        """
        value = self.setting("brain", "timezone")
        return str(value) if value else None

    @property
    def index_path(self) -> Path:
        """
        Where the local evidence index lives.

        :returns: Where the local evidence index lives
        """
        return self.root / INDEX_DIR / "index.sqlite"

    def file(self, *parts: str) -> Path:
        """
        Build a path inside the brain.

        :param parts: Path parts
        :returns: The path
        """
        return self.root.joinpath(*parts)

    def today(self) -> date:
        """
        Give today's date in the brain's time zone.

        :returns: Today
        """
        return today(self.timezone)

    def now(self) -> datetime:
        """
        Give the time now in the brain's time zone.

        :returns: Now
        """
        return now(self.timezone)


def now(tz: str | None = None) -> datetime:
    """
    Give the time now. ``$KEA_NOW`` fixes it, for reproducible demos and tests.

    :param tz: An IANA time zone; the system's when omitted or unknown
    :returns: An aware datetime
    """
    fixed = os.environ.get("KEA_NOW")
    if fixed:
        moment = datetime.fromisoformat(fixed)
        return moment if moment.tzinfo else moment.astimezone()
    if tz:
        try:
            return datetime.now(ZoneInfo(tz))
        except ZoneInfoNotFoundError, ValueError:
            pass
    return datetime.now().astimezone()


def today(tz: str | None = None) -> date:
    """
    Give today's date. ``$KEA_TODAY`` fixes it, for reproducible demos and tests.

    :param tz: An IANA time zone
    :returns: Today
    """
    fixed = os.environ.get("KEA_TODAY")
    return date.fromisoformat(fixed) if fixed else now(tz).date()


def parse_date(text: str, base: date | None = None) -> date | None:
    """
    Read a date: ``2026-12-01``, ``today``, ``tomorrow``, or an offset such as ``+30d``, ``+2w``, ``+3m`` or ``-7d``.

    :param text: The text
    :param base: The date offsets count from; today when omitted
    :returns: The date, or None when the text is not a date
    """
    text = text.strip().lower()
    start = base or today()
    if text == "today":
        return start
    if text == "tomorrow":
        return start + timedelta(days=1)
    m = re.fullmatch(r"([+-])(\d{1,4})([dwm])", text)
    if m:
        n, unit = int(m[2]) * (1 if m[1] == "+" else -1), m[3]
        if unit == "d":
            return start + timedelta(days=n)
        if unit == "w":
            return start + timedelta(weeks=n)
        month = start.month - 1 + n
        year, month = start.year + month // 12, month % 12 + 1
        if not 1 <= year <= 9999:
            return None
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if m:
        try:
            return date.fromisoformat(m[0])
        except ValueError:
            return None
    return None


def _render(text: str, values: dict[str, str], base: date) -> str:
    def swap(m: re.Match[str]) -> str:
        key = m[1]
        if key.startswith("d:"):
            return (base + timedelta(days=int(key[2:]))).isoformat()
        return values.get(key, m[0])

    return re.sub(r"\{\{\s*(name|owner|today|year|d:[+-]\d+)\s*\}\}", swap, text)


def _walk(folder: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    out = []
    for item in sorted(folder.iterdir(), key=lambda t: t.name):
        rel = f"{prefix}{item.name}"
        if item.is_dir():
            out += _walk(item, rel + "/")
        elif not item.name.endswith((".pyc", ".pyo")):
            out.append((rel, item))
    return out


def init(
    folder: Path,
    name: str | None = None,
    owner: str | None = None,
    example: bool = False,
    force: bool = False,
    git: bool = True,
) -> list[str]:
    """
    Make a new brain from the bundled templates, or put back the files an existing brain is missing.

    On a folder that is already a brain, only missing files are written. ``force`` overwrites the rest too, after
    copying each one to ``.kea/backup-<time>/``. ``git init`` runs only for a new or empty folder.

    :param folder: Where to make it; created when missing
    :param name: The brain's name; the folder's name when omitted
    :param owner: Whose brain it is
    :param example: Fill it with the example brain, a made up coffee roastery, to explore
    :param force: Overwrite files that already exist, keeping a backup of each
    :param git: Run ``git init`` when the folder is new or empty
    :returns: The files written, relative to the folder
    :raises BrainError: When the folder is the home folder or the file system root
    """
    folder = folder.expanduser().resolve()
    if folder == Path.home().resolve() or folder == Path(folder.anchor):
        raise BrainError(f"make the brain in a folder of its own, such as {Path.home() / 'brain'}, not in {folder}")
    fresh = not folder.exists() or not any(folder.iterdir())
    base = today()
    values = {
        "name": name or ("Tidewater Coffee" if example else folder.name.replace("-", " ").replace("_", " ").title()),
        "owner": owner or ("Mara" if example else "me"),
        "today": base.isoformat(),
        "year": str(base.year),
    }
    data = resources.files("kea") / "data"
    layers = [data / "templates"] + ([data / "example"] if example else [])
    planned: dict[str, Traversable] = {}
    for layer in layers:
        for rel, item in _walk(layer):
            parts = rel.split("/")
            parts[-1] = DOTFILES.get(parts[-1], parts[-1])
            planned["/".join(parts)] = item
    written = []
    backup = folder / INDEX_DIR / f"backup-{now():%Y%m%d-%H%M%S}"
    for rel, item in planned.items():
        target = folder / rel
        if target.exists():
            if not force:
                continue
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render(item.read_text(encoding="utf-8"), values, base), encoding="utf-8")
        written.append(rel)
    (folder / INDEX_DIR).mkdir(mode=0o700, exist_ok=True)
    git_bin = shutil.which("git")
    if git and fresh and git_bin and not (folder / ".git").exists():
        subprocess.run([git_bin, "init", "-q", str(folder)], check=False)  # nosec B603: git init, fixed arguments
    return sorted(written)
