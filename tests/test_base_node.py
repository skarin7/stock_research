"""Tests for the agent_node decorator — requires_trading gate."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.nodes.base as base_mod  # noqa: E402
from agents.state import RunStatus  # noqa: E402


def test_requires_trading_skips_when_off(monkeypatch):
    # Patch trading_enabled directly on the base module so we avoid
    # importlib.reload (which breaks when other test files stub sys.modules["config"]).
    monkeypatch.setattr(base_mod, "trading_enabled", lambda: False)

    @base_mod.agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert "executed" not in result
    assert result["audit"][0]["detail"] == "skipped (trading off)"


def test_requires_trading_runs_when_paper(monkeypatch):
    monkeypatch.setattr(base_mod, "trading_enabled", lambda: True)

    @base_mod.agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True, "status": RunStatus.RUNNING}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert result.get("executed") is True


def test_no_requires_trading_always_runs(monkeypatch):
    monkeypatch.setattr(base_mod, "trading_enabled", lambda: False)

    @base_mod.agent_node("always_on")
    def my_node(state):
        return {"ran": True, "status": RunStatus.RUNNING}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert result.get("ran") is True
