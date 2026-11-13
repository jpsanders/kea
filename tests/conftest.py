"""Shared fixtures: every test runs against a fresh brain, with the clock fixed and the home folder isolated."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from kea import cli
from kea.brain import Brain, init

TODAY = "2026-11-13"


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("KEA_TODAY", TODAY)
    monkeypatch.setenv("KEA_NOW", f"{TODAY}T10:30:00+00:00")
    monkeypatch.setenv("NO_COLOR", "1")
    for name in ("KEA_BRAIN", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def brain(tmp_path: Path) -> Brain:
    folder = tmp_path / "brain"
    init(folder, name="Test Brain", owner="Ada", git=False)
    return Brain.load(folder)


@pytest.fixture
def example(tmp_path: Path) -> Brain:
    folder = tmp_path / "example"
    init(folder, example=True, git=False)
    return Brain.load(folder)


Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    def call(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


@pytest.fixture
def run_json(run: Run) -> Callable[..., Any]:
    def call(*argv: str) -> Any:
        code, out, err = run(*argv, "--json")
        assert code == 0, err
        return json.loads(out)

    return call
