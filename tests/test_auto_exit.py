"""Protective auto-exit guardrails + monitor live-wiring. No network, no SDK.

Run: python -m pytest tests/test_auto_exit.py -v
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    TRADING_MODE="live",
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    TRADING_CAPITAL_INR=100_000.0,
    POSITIONS_FILE="/tmp/__ae_pos__.json",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_CHAT_ID="",
    OUTPUT_DIR="output",
    # auto-exit guardrails
    AUTO_TRADE_ALLOWLIST=frozenset({"AAA"}),
    MAX_DAILY_NOTIONAL=50_000.0,
    MAX_ORDERS_PER_DAY=10,
    AUTO_TRADE_WINDOW="00:00-23:59",            # wide open so tests aren't clock-dependent
    AUTO_TRADE_LEDGER="/tmp/__ae_ledger__.json",
)

def _live_trading():
    return getattr(_cfg, "TRADING_MODE", "") == "live"

def _trading_enabled():
    return getattr(_cfg, "TRADING_MODE", "off") != "off"

sys.modules["config"] = types.SimpleNamespace(
    SETTINGS=_cfg,
    live_trading=_live_trading,
    trading_enabled=_trading_enabled,
)

from agents.contracts import Position, PortfolioState  # noqa: E402
from agents.broker import auto_exit as ae  # noqa: E402
from agents.broker import groww_trader as gt  # noqa: E402
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import monitoring as mon  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from persistence import store as store_mod  # noqa: E402
from agents.state import RunStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _bind(tmp_path, monkeypatch):
    _cfg.AUTO_TRADE_LEDGER = str(tmp_path / "ledger.json")
    _cfg.POSITIONS_FILE = str(tmp_path / "positions.json")
    _cfg.TRADING_MODE = "live"
    _cfg.AUTO_TRADE_ALLOWLIST = frozenset({"AAA"})
    _cfg.AUTO_TRADE_WINDOW = "00:00-23:59"
    _cfg.MAX_DAILY_NOTIONAL = 50_000.0
    _cfg.MAX_ORDERS_PER_DAY = 10
    for m in (_sup, _base, mon, ae, gt, store_mod):
        m.SETTINGS = _cfg
    # never place a real order or hit the broker SDK
    monkeypatch.setattr(ae, "place_order", lambda proposal, mode="live": ("OID-1", "placed"))
    monkeypatch.setattr(ae, "_broker_qty", lambda t: 10)   # broker confirms 10 held
    monkeypatch.setattr(mon, "_notify", lambda alerts: None)
    yield


def _pos(ticker="AAA", qty=10):
    return Position(ticker=ticker, qty=qty, avg_price=100.0, stop_price=95.0, sector="IT")


# ── happy path ────────────────────────────────────────────────────────────────

def test_auto_exit_places_sell_and_records_ledger():
    oid, status = ae.auto_exit(_pos(), price=90.0, reason="stop_triggered")
    assert (oid, status) == ("OID-1", "placed")
    led = ae._ledger()
    assert led["count"] == 1
    assert led["notional"] == pytest.approx(900.0)   # 10 * 90


# ── guard: feature flag (allowlist empty = disabled) ────────────────────────────

def test_disabled_flag_refuses():
    _cfg.AUTO_TRADE_ALLOWLIST = frozenset()
    with pytest.raises(ae.ExitRefused, match="AUTO_TRADE_ALLOWLIST empty"):
        ae.auto_exit(_pos(), price=90.0, reason="stop")


# ── guard: allowlist ────────────────────────────────────────────────────────────

def test_symbol_not_in_allowlist_refuses():
    with pytest.raises(ae.ExitRefused, match="not in AUTO_TRADE_ALLOWLIST"):
        ae.auto_exit(_pos(ticker="ZZZ"), price=90.0, reason="stop")


# ── guard: time window ──────────────────────────────────────────────────────────

def test_outside_window_refuses():
    _cfg.AUTO_TRADE_WINDOW = "09:20-09:21"   # 1-min window, almost certainly outside now
    # force determinism: patch _within_window indirectly via a window that can't contain now
    import datetime as _dt
    # if the real clock happens to be in that minute, widen-impossible by using 00:00-00:00
    _cfg.AUTO_TRADE_WINDOW = "00:00-00:00" if _dt.datetime.now().strftime("%H:%M") != "00:00" else "23:59-23:59"
    with pytest.raises(ae.ExitRefused, match="outside trade window"):
        ae.auto_exit(_pos(), price=90.0, reason="stop")


# ── guard: reconciliation ───────────────────────────────────────────────────────

def test_reconcile_failure_refuses(monkeypatch):
    monkeypatch.setattr(ae, "_broker_qty", lambda t: None)   # couldn't verify
    with pytest.raises(ae.ExitRefused, match="reconcile failed"):
        ae.auto_exit(_pos(), price=90.0, reason="stop")


def test_broker_holds_zero_refuses(monkeypatch):
    monkeypatch.setattr(ae, "_broker_qty", lambda t: 0)      # drift: book says 10, broker says 0
    with pytest.raises(ae.ExitRefused, match="position drift"):
        ae.auto_exit(_pos(), price=90.0, reason="stop")


def test_qty_clamped_to_broker_held(monkeypatch):
    sold = {}
    monkeypatch.setattr(ae, "_broker_qty", lambda t: 4)      # broker holds fewer than book
    monkeypatch.setattr(ae, "place_order",
                        lambda proposal, mode="live": sold.update(qty=proposal.qty) or ("OID", "placed"))
    ae.auto_exit(_pos(qty=10), price=90.0, reason="stop")
    assert sold["qty"] == 4                                  # never sell more than held


# ── guard: daily caps ───────────────────────────────────────────────────────────

def test_daily_order_cap_refuses():
    _cfg.MAX_ORDERS_PER_DAY = 1
    ae.auto_exit(_pos(), price=90.0, reason="stop")          # 1st ok
    with pytest.raises(ae.ExitRefused, match="daily order cap"):
        ae.auto_exit(_pos(), price=90.0, reason="stop")      # 2nd blocked


def test_daily_notional_cap_refuses():
    _cfg.MAX_DAILY_NOTIONAL = 1000.0
    with pytest.raises(ae.ExitRefused, match="daily notional cap"):
        ae.auto_exit(_pos(qty=10), price=200.0, reason="stop")   # 2000 > 1000


# ── ledger daily reset ──────────────────────────────────────────────────────────

def test_ledger_resets_on_new_day(monkeypatch):
    ae._commit({"date": "1999-01-01", "count": 9, "notional": 49_000.0})
    led = ae._ledger()                                       # stale date → fresh ledger
    assert led["count"] == 0 and led["notional"] == 0.0


# ── monitor → auto_exit wiring ──────────────────────────────────────────────────

def test_monitor_live_auto_exits_via_guard(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: 90.0)   # below stop 95
    book = PortfolioState(cash=0.0, positions=[_pos()])
    out = mon.monitoring_node({"book": book, "mode": "monitor"})
    assert out["status"] == RunStatus.COMPLETED
    assert out["book"].positions == []                       # exited from the book
    assert any("AUTO-EXITED" in a.message for a in out["alerts"])


def test_monitor_live_refusal_keeps_position(monkeypatch):
    monkeypatch.setattr(mon, "_current_price", lambda t: 90.0)
    _cfg.AUTO_TRADE_ALLOWLIST = frozenset()                  # nothing eligible → refuse
    book = PortfolioState(cash=0.0, positions=[_pos()])
    out = mon.monitoring_node({"book": book, "mode": "monitor"})
    assert [p.ticker for p in out["book"].positions] == ["AAA"]   # kept (HITL)
    assert any("auto-exit refused" in a.message for a in out["alerts"])
