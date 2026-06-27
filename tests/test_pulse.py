"""Market-pulse shock-watcher tests — fully mocked (no network, no LLM)."""

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.contracts import Position, PortfolioState  # noqa: E402
from agents.nodes import pulse  # noqa: E402
from settings import (  # noqa: E402
    _default_pulse_global_sector_map,
    _default_pulse_global_tickers,
    _default_pulse_shock_keywords,
)


def _settings():
    """Concrete pulse settings — isolates these tests from other modules that
    overwrite sys.modules['config'] with a mock (e.g. test_intraday)."""
    return types.SimpleNamespace(
        PULSE_INDEX_DROP_PCT=1.5,
        PULSE_VIX_SPIKE_PCT=15.0,
        PULSE_HOLDING_DROP_PCT=4.0,
        PULSE_ALERT_COOLDOWN_MIN=20,
        PULSE_NEWS_ENABLED=True,
        PULSE_NEWS_MIN_GAP_MIN=5,
        PULSE_GLOBAL_ENABLED=True,
        PULSE_GLOBAL_TICKERS=_default_pulse_global_tickers(),
        PULSE_GLOBAL_SECTOR_MAP=_default_pulse_global_sector_map(),
        PULSE_SHOCK_KEYWORDS=_default_pulse_shock_keywords(),
        TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="",
    )

# A Monday (2026-06-01 is a Monday → +28 days = Monday).
MON = datetime(2026, 6, 29)


def _ist(h, m=0, day=29):
    return datetime(2026, 6, day, h, m, tzinfo=pulse.IST)


def _run():
    """Call the node bypassing the agent_node guard wrapper."""
    return pulse.pulse_node.__wrapped__({
        "run_id": "t", "report_date": "2026-06-29", "mode": "pulse",
        "status": __import__("agents.state", fromlist=["RunStatus"]).RunStatus.RUNNING,
        "cost_usd": 0.0, "tokens": 0,
    })


@pytest.fixture
def wire(monkeypatch):
    """Default: market open, nothing breaching, no positions, no shock."""
    state = {}
    clock = {"now": _ist(10, 0)}

    monkeypatch.setattr(pulse, "SETTINGS", _settings())
    monkeypatch.setattr(pulse, "_now_ist", lambda: clock["now"])
    monkeypatch.setattr(pulse, "load_pulse_state", lambda: state)
    monkeypatch.setattr(pulse, "save_pulse_state", lambda s: state.update(s))
    monkeypatch.setattr(pulse, "load_portfolio", lambda: PortfolioState(cash=100000.0))
    monkeypatch.setattr(pulse, "_notify", lambda alerts: None)

    monkeypatch.setattr(pulse, "index_levels",
                        lambda: {"nifty_pct": -0.1, "nifty_level": 25000, "vix_pct": 1.0, "vix_level": 13})
    monkeypatch.setattr(pulse, "global_signals",
                        lambda t: {s: {"pct": 0.0, "level": 1, "threshold": v, "breached": False}
                                   for s, v in t.items()})
    monkeypatch.setattr(pulse, "shock_headlines", lambda kw: [])
    monkeypatch.setattr(pulse, "classify_shock",
                        lambda h: {"is_shock": False, "severity": "low", "summary": ""})
    monkeypatch.setattr(pulse, "_ticker_pct", lambda sym: (0.0, 100.0))
    return {"state": state, "clock": clock, "monkeypatch": monkeypatch}


class TestMarketOpen:
    def test_open_during_session(self):
        assert pulse._market_open(_ist(10, 0)) is True

    def test_closed_preopen(self):
        assert pulse._market_open(_ist(8, 0)) is False

    def test_closed_weekend(self):
        assert pulse._market_open(_ist(10, 0, day=27)) is False  # 2026-06-27 = Saturday


class TestIndexVix:
    def test_index_drop_fires_and_names_holdings(self, wire):
        mp = wire["monkeypatch"]
        mp.setattr(pulse, "index_levels",
                   lambda: {"nifty_pct": -2.0, "nifty_level": 24000, "vix_pct": 1.0, "vix_level": 13})
        mp.setattr(pulse, "load_portfolio",
                   lambda: PortfolioState(cash=0.0, positions=[Position(ticker="TCS", qty=1, avg_price=3000, sector="IT")]))
        out = _run()
        msgs = [a.message for a in out["alerts"] if a.ticker == "NIFTY"]
        assert msgs and "TCS" in msgs[0]

    def test_vix_spike_fires(self, wire):
        wire["monkeypatch"].setattr(
            pulse, "index_levels",
            lambda: {"nifty_pct": -0.1, "nifty_level": 25000, "vix_pct": 22.0, "vix_level": 18})
        out = _run()
        assert any(a.ticker == "INDIAVIX" for a in out["alerts"])

    def test_no_alert_when_calm(self, wire):
        out = _run()
        assert out["alerts"] == []


class TestSessionGating:
    def test_index_vix_dormant_preopen(self, wire):
        wire["clock"]["now"] = _ist(8, 0)  # pre-open

        def _boom():
            raise AssertionError("index_levels must not be called pre-open")

        wire["monkeypatch"].setattr(pulse, "index_levels", _boom)
        # Global still evaluates pre-open.
        wire["monkeypatch"].setattr(
            pulse, "global_signals",
            lambda t: {"^KS11": {"pct": -3.0, "level": 2500, "threshold": -2.0, "breached": True}})
        out = _run()
        assert any(a.ticker == "GLOBAL" for a in out["alerts"])
        assert not any(a.ticker in ("NIFTY", "INDIAVIX") for a in out["alerts"])


class TestGlobal:
    def test_crude_spike_maps_sector_and_exposed(self, wire):
        mp = wire["monkeypatch"]
        mp.setattr(pulse, "global_signals",
                   lambda t: {"BZ=F": {"pct": 5.0, "level": 95, "threshold": 4.0, "breached": False}})
        mp.setattr(pulse, "load_portfolio",
                   lambda: PortfolioState(cash=0.0, positions=[Position(ticker="INDIGO", qty=1, avg_price=4000, sector="Aviation")]))
        out = _run()
        g = [a for a in out["alerts"] if a.ticker == "GLOBAL"]
        assert g and "Aviation" in g[0].message and "INDIGO" in g[0].message

    def test_closed_market_none_pct_no_breach(self, wire):
        wire["monkeypatch"].setattr(
            pulse, "global_signals",
            lambda t: {"^KS11": {"pct": None, "level": None, "threshold": -2.0, "breached": False}})
        out = _run()
        assert not any(a.ticker == "GLOBAL" for a in out["alerts"])


class TestDebounce:
    def test_once_per_episode_then_rearm(self, wire):
        mp, clock = wire["monkeypatch"], wire["clock"]
        mp.setattr(pulse, "index_levels",
                   lambda: {"nifty_pct": -2.0, "nifty_level": 24000, "vix_pct": 1.0, "vix_level": 13})

        clock["now"] = _ist(10, 0)
        assert any(a.ticker == "NIFTY" for a in _run()["alerts"])      # 1st fires

        clock["now"] = _ist(10, 1)
        assert not any(a.ticker == "NIFTY" for a in _run()["alerts"])  # suppressed (armed False)

        # Metric normalises → re-arm.
        clock["now"] = _ist(10, 2)
        mp.setattr(pulse, "index_levels",
                   lambda: {"nifty_pct": -0.1, "nifty_level": 25000, "vix_pct": 1.0, "vix_level": 13})
        assert not any(a.ticker == "NIFTY" for a in _run()["alerts"])

        # Re-breach after cooldown floor (>20 min) → fires again.
        clock["now"] = _ist(10, 45)
        mp.setattr(pulse, "index_levels",
                   lambda: {"nifty_pct": -2.0, "nifty_level": 24000, "vix_pct": 1.0, "vix_level": 13})
        assert any(a.ticker == "NIFTY" for a in _run()["alerts"])


class TestNewsTiering:
    def test_llm_skipped_when_not_gated(self, wire):
        mp, clock = wire["monkeypatch"], wire["clock"]
        clock["now"] = _ist(8, 0)               # pre-open → no session elevation
        wire["state"]["last_news_check"] = _ist(7, 58).isoformat()  # 2 min ago < 5 min gap

        called = {"n": 0}

        def _spy(h):
            called["n"] += 1
            return {"is_shock": False, "severity": "low", "summary": ""}

        mp.setattr(pulse, "classify_shock", _spy)
        _run()
        assert called["n"] == 0                 # gate closed → LLM not invoked

    def test_shock_fires_when_gated(self, wire):
        mp = wire["monkeypatch"]
        mp.setattr(pulse, "shock_headlines", lambda kw: ["War escalates, oil spikes"])
        mp.setattr(pulse, "classify_shock",
                   lambda h: {"is_shock": True, "severity": "high", "summary": "Geopolitical shock"})
        out = _run()
        assert any(a.ticker == "NEWS" for a in out["alerts"])
