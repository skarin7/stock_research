"""Tests for the agent_node decorator — requires_trading gate."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.state import RunStatus  # noqa: E402


def _make_settings(trading_mode):
    from settings import Settings
    return Settings(TRADING_MODE=trading_mode)


def test_requires_trading_skips_when_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _make_settings("off"))

    # Re-import to pick up monkeypatched SETTINGS
    import importlib
    import agents.nodes.base as base_mod
    importlib.reload(base_mod)

    @base_mod.agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert "executed" not in result


def test_requires_trading_runs_when_paper(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _make_settings("paper"))

    import importlib
    import agents.nodes.base as base_mod
    importlib.reload(base_mod)

    @base_mod.agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True, "status": RunStatus.RUNNING}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert result.get("executed") is True


def test_no_requires_trading_always_runs(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _make_settings("off"))

    import importlib
    import agents.nodes.base as base_mod
    importlib.reload(base_mod)

    @base_mod.agent_node("always_on")
    def my_node(state):
        return {"ran": True, "status": RunStatus.RUNNING}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert result.get("ran") is True
