from pathlib import Path

import pytest

from kea import skills


def test_bundled_skills_match_the_repository() -> None:
    names = list(skills.bundled())
    assert names == ["kea", "kea-council", "kea-frameworks"]
    for name in names:
        text = skills.text(name)
        assert text.startswith(f"---\nname: {name}\ndescription: ")
    root = Path(__file__).resolve().parents[2] / "skills"
    if root.is_dir():
        assert skills.text("kea") == (root / "kea" / "SKILL.md").read_text()
    with pytest.raises(KeyError):
        skills.text("nope")


def test_install_into_found_agents(tmp_path: Path) -> None:
    home = Path.home()
    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    rows = skills.install(dry_run=True)
    assert {r["agent"] for r in rows} == {"claude", "agents"} and {r["action"] for r in rows} == {"would write"}
    assert not (home / ".claude" / "skills").exists()
    rows = skills.install()
    assert {r["action"] for r in rows} == {"installed"}
    assert (home / ".claude" / "skills" / "kea" / "SKILL.md").is_file()
    assert {r["action"] for r in skills.install()} == {"up to date"}
    assert not (home / ".codex" / "skills").exists()
    (home / ".agents" / "skills" / "kea" / "SKILL.md").write_text("old")
    assert "updated" in {r["action"] for r in skills.install(["agents"])}
    assert {r["action"] for r in skills.install(["codex"])} == {"installed"}
    with pytest.raises(KeyError):
        skills.install(["vim"])


def test_targets_follow_the_agents_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "c2"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    places = skills.targets()
    assert places["claude"] == tmp_path / "c2" / "skills" and places["codex"] == tmp_path / "cx" / "skills"
