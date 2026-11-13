from datetime import date
from pathlib import Path

import pytest

from kea import brain as brain_mod
from kea.brain import Brain, BrainError, init, parse_date


def test_init_writes_the_templates_with_values(tmp_path: Path) -> None:
    written = init(tmp_path / "b", name="Mine", owner="Ada", git=False)
    assert "kea.toml" in written and ".gitignore" in written and "plans/_template/00-proposal.md" in written
    brain = Brain.load(tmp_path / "b")
    assert brain.name == "Mine" and brain.owner == "Ada"
    assert "Waiting for Ada" in brain.file("ROADMAP.md").read_text()
    assert "{{" not in "".join(p.read_text() for p in (tmp_path / "b").rglob("*.md"))
    assert (tmp_path / "b" / ".kea").is_dir()
    with pytest.raises(BrainError):
        init(tmp_path / "b", git=False)


def test_init_keeps_existing_files_unless_forced(tmp_path: Path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    (folder / "STATE.md").write_text("mine")
    init(folder, git=False)
    assert (folder / "STATE.md").read_text() == "mine"
    init(folder, git=False, force=True)
    assert (folder / "STATE.md").read_text() != "mine"


def test_init_runs_git_when_asked(tmp_path: Path) -> None:
    init(tmp_path / "g", git=True)
    assert (tmp_path / "g" / ".git").exists()


def test_example_has_no_placeholders_and_relative_dates(tmp_path: Path) -> None:
    init(tmp_path / "e", example=True, git=False)
    text = "".join(p.read_text() for p in (tmp_path / "e").rglob("*.md"))
    assert "{{" not in text and "Tidewater" in text and "2026-11-12" in text


def test_locate_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    init(a, git=False)
    init(b, git=False)
    deep = a / "plans" / "x"
    deep.mkdir(parents=True)
    assert Brain.locate(cwd=deep).root == a.resolve()
    assert Brain.locate(str(b), cwd=deep).root == b.resolve()
    monkeypatch.setenv("KEA_BRAIN", str(b))
    assert Brain.locate(cwd=deep).root == b.resolve()
    monkeypatch.delenv("KEA_BRAIN")
    with pytest.raises(BrainError):
        Brain.locate(cwd=tmp_path)
    brain_mod.set_default_brain(b)
    assert Brain.locate(cwd=tmp_path).root == b.resolve()
    with pytest.raises(BrainError):
        Brain.load(tmp_path)


def test_default_brain_config_keeps_other_lines(tmp_path: Path) -> None:
    config = brain_mod.user_config_path()
    config.parent.mkdir(parents=True)
    config.write_text('brain = "/old"\n[extra]\nkey = 1\n')
    brain_mod.set_default_brain(tmp_path)
    text = config.read_text()
    assert f'brain = "{tmp_path.resolve()}"' in text and "[extra]" in text and "/old" not in text
    assert brain_mod.default_brain() == tmp_path.resolve()
    config.write_text("not = [valid")
    assert brain_mod.default_brain() is None


def test_bad_toml_is_reported(tmp_path: Path) -> None:
    (tmp_path / "kea.toml").write_text("[brain\n")
    with pytest.raises(BrainError):
        Brain.load(tmp_path)


def test_parse_date_forms() -> None:
    base = date(2026, 1, 31)
    assert parse_date("2026-12-01") == date(2026, 12, 1)
    assert parse_date("today", base) == base
    assert parse_date("tomorrow", base) == date(2026, 2, 1)
    assert parse_date("+10d", base) == date(2026, 2, 10)
    assert parse_date("+2w", base) == date(2026, 2, 14)
    assert parse_date("+1m", base) == date(2026, 2, 28)
    assert parse_date("+13m", base) == date(2027, 2, 28)
    assert parse_date("by 2026-03-04", base) == date(2026, 3, 4)
    assert parse_date("2026-02-31") is None
    assert parse_date("someday") is None


def test_clock_honours_the_brain_time_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEA_TODAY")
    monkeypatch.delenv("KEA_NOW")
    init(tmp_path / "z", git=False)
    toml = tmp_path / "z" / "kea.toml"
    toml.write_text(toml.read_text().replace('# timezone = "Europe/London"', 'timezone = "Pacific/Auckland"'))
    brain = Brain.load(tmp_path / "z")
    assert brain.timezone == "Pacific/Auckland"
    assert str(brain.now().tzinfo) == "Pacific/Auckland"
    assert brain_mod.now("Not/AZone").tzinfo is not None
