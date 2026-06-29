"""Monitoring agent: stop-loss detection, paper auto-exit, live alert-only. No network."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    TRADING_MODE="off",
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    TRADING_CAPITAL_INR=100_000.0,
    POSITIONS_FILE="/tmp/__mon_pos__.json",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_CHAT_ID="",
    OUTPUT_DIR="output",
)


def _live_trading():
    return getattr(_cfg, "TRADING_MODE", "off") == "live"


def _trading_enabled():
    return getattr(_cfg, "TRADING_MODE", "off") != "off"


sys.modules["config"] = types.SimpleNamespace(
    SETTINGS=_cfg,
    live_trading=_live_trading,
    trading_enabled=_trading_enabled,
)

from agents.contracts import PortfolioState, Position  # noqa: E402
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import monitoring as mon  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from agents.state import RunStatus  # noqa: E402
from persistence import store as store_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _bind(tmp_path, monkeypatch):
    _cfg.POSITIONS_FILE = str(tmp_path / "positions.json")
    _cfg.TRADING_MODE = "off"   # paper/off → auto-exit on stop; default for most tests
    for m in (_sup, _base, mon, store_mod):
        m.SETTINGS = _cfg
    # Patch live_trading directly on mon so it reads THIS file's _cfg, not another stub.
    monkeypatch.setattr(mon, "live_trading", _live_trading)
    monkeypatch.setattr(mon, "_notify", lambda alerts: None)   # never hit Telegram
    yield


def _book(stop=95.0):
    return PortfolioState(cash=0.0, positions=[
        Position(ticker="AAA", qty=10, avg_price=100.0, stop_price=stop, sector="IT")])


def test_stop_triggered_paper_auto_exits(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: 90.0)   # below stop 95
    out = mon.monitoring_node({"book": _book(), "mode": "monitor"})
    assert out["status"] == RunStatus.COMPLETED
    assert out["book"].positions == []                 # exited
    assert out["book"].cash == 900.0                   # 10 * 90 credited
    assert any(a.kind == "stop_triggered" and a.severity == "critical" for a in out["alerts"])


def test_price_above_stop_holds(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: 120.0)
    out = mon.monitoring_node({"book": _book()})
    assert [p.ticker for p in out["book"].positions] == ["AAA"]
    assert out["alerts"] == []


def test_missing_price_warns_and_holds(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: None)
    out = mon.monitoring_node({"book": _book()})
    assert [p.ticker for p in out["book"].positions] == ["AAA"]
    assert out["alerts"][0].kind == "anomaly" and out["alerts"][0].severity == "warn"


def test_live_mode_alerts_only_no_exit(monkeypatch):
    _cfg.TRADING_MODE = "live"
    monkeypatch.setattr(mon, "_current_price", lambda t: 90.0)
    out = mon.monitoring_node({"book": _book(), "mode": "monitor"})
    assert [p.ticker for p in out["book"].positions] == ["AAA"]   # NOT auto-sold
    assert any(a.kind == "stop_triggered" for a in out["alerts"])


def test_no_positions_completes_quietly(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: 100.0)
    out = mon.monitoring_node({"book": PortfolioState(cash=100.0)})
    assert out["status"] == RunStatus.COMPLETED
    assert "alerts" not in out
