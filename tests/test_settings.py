"""Tests for the typed Settings dataclass."""
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from settings import Settings  # noqa: E402


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("INTRADAY_TOP_N", "7")
    s = Settings.from_env()
    assert s.ANTHROPIC_API_KEY == "k"
    assert s.INTRADAY_TOP_N == 7
    assert s.SIGNAL_WEIGHTS  # dict populated


def test_settings_frozen():
    s = Settings.from_env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.INTRADAY_TOP_N = 99  # type: ignore[misc]


def test_settings_partial_construction_uses_defaults():
    s = Settings(ANTHROPIC_API_KEY="x")
    assert s.ANTHROPIC_API_KEY == "x"
    assert s.TOP_N_STOCKS == 15
    assert s.OUTPUT_DIR == "output"


def test_kill_switch_file_tracks_output_dir(monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", "/tmp/run42")
    s = Settings.from_env()
    assert s.KILL_SWITCH_FILE == "/tmp/run42/kill_switch.flag"
    assert s.POSITIONS_FILE == "/tmp/run42/positions.json"
