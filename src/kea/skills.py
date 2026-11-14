"""
Install kea's agent skills where each agent looks for them.

The same ``SKILL.md`` files work in Claude Code, Codex, OpenCode and any agent that reads the shared
``~/.agents/skills`` folder. By default ``kea skill install`` writes two places: ``~/.claude/skills``, which Claude
Code and OpenCode read, and ``~/.agents/skills``, which Codex and other agents read. Writing an agent's own folder as
well would make it list every skill twice, so ``codex`` and ``opencode`` are written only when named. It touches
nothing but kea's own skill folders.
"""

import os
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path


def targets() -> dict[str, Path]:
    """
    Name each agent's skills folder, honouring the agents' own environment variables.

    :returns: Agent name to skills folder
    """
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return {
        "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser() / "skills",
        "codex": Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser() / "skills",
        "opencode": config / "opencode" / "skills",
        "agents": home / ".agents" / "skills",
    }


def bundled() -> dict[str, Traversable]:
    """
    List the skills shipped in the package.

    :returns: Skill name to its folder
    """
    folder = resources.files("kea") / "data" / "skills"
    return {item.name: item for item in sorted(folder.iterdir(), key=lambda t: t.name) if item.is_dir()}


def text(name: str = "kea") -> str:
    """
    Read one bundled skill.

    :param name: The skill's name
    :returns: Its SKILL.md
    :raises KeyError: When there is no such skill
    """
    skills = bundled()
    if name not in skills:
        raise KeyError(f"no skill {name!r}; there are: {', '.join(skills)}")
    return (skills[name] / "SKILL.md").read_text(encoding="utf-8")


def install(chosen: list[str] | None = None, dry_run: bool = False) -> list[dict[str, str]]:
    """
    Copy the bundled skills into agents' skills folders.

    :param chosen: Targets to install for; when omitted, claude and agents, each where its agents are installed
    :param dry_run: Only say what would be written
    :returns: One row per skill and agent, with the path and what happened
    :raises KeyError: When an agent name is unknown
    """
    places = targets()
    if chosen:
        unknown = [c for c in chosen if c not in places]
        if unknown:
            raise KeyError(f"unknown agent {unknown[0]!r}; choose from {', '.join(places)}")
        wanted = {c: places[c] for c in chosen}
    else:
        home = Path.home()
        wanted = {}
        if places["claude"].parent.is_dir() or (home / ".config" / "opencode").is_dir():
            wanted["claude"] = places["claude"]
        if places["agents"].parent.is_dir() or places["codex"].parent.is_dir():
            wanted["agents"] = places["agents"]
    rows = []
    for agent, folder in wanted.items():
        for name, source in bundled().items():
            target = folder / name / "SKILL.md"
            new = (source / "SKILL.md").read_text(encoding="utf-8")
            if target.is_file() and target.read_text(encoding="utf-8") == new:
                action = "up to date"
            else:
                action = "would write" if dry_run else ("updated" if target.exists() else "installed")
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(new, encoding="utf-8")
            rows.append({"agent": agent, "skill": name, "path": str(target), "action": action})
    return rows
