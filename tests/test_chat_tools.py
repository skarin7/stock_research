"""Chat agent tools: snapshot screening, live wrappers, deep-dive cap. No network/keys."""

import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    LLM_PROVIDER="anthropic",
    DATABASE_URL="",
    OUTPUT_DIR="output",
    SNAPSHOT_STALE_DAYS=3,
    MAX_DEBATE_ROUNDS=1,
    POSITIONS_FILE="/tmp/__chat_positions__.json",
    TRADING_CAPITAL_INR=100000.0,
    SIGNAL_WEIGHTS={"value": 1.0},
    TOP_N_STOCKS=15,
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from agents.chat import tools as tools_mod  # noqa: E402
from persistence import store as store_mod  # noqa: E402

_TODAY = date.today().isoformat()
_ROWS = [
    {"symbol": "AAA", "company": "Alpha", "sector": "IT", "pe_ratio": 12.0,
     "sector_pe": 20.0, "market_cap_cr": 5000.0, "ltp": 100.0, "delivery_pct": 60.0,
     "volume_ratio": 1.5, "week52_high": 120.0, "week52_low": 80.0,
     "composite_score": 7.4, "signals": {"value": {"score": 8, "reason": "cheap"}},
     "news": ["Alpha wins order"], "rationale": "cheap", "risk_flags": ["fii"],
     "earnings_proximity": False},
    {"symbol": "BBB", "company": "Beta", "sector": "Pharma", "pe_ratio": 35.0,
     "composite_score": 6.1, "news": [], "rationale": "", "risk_flags": []},
    {"symbol": "CCC", "company": "Gamma", "sector": "IT", "pe_ratio": None,
     "composite_score": 8.0, "news": ["Gamma surges"], "rationale": "momo",
     "risk_flags": []},
]


@pytest.fixture(autouse=True)
def _bind(tmp_path):
    _cfg.OUTPUT_DIR = str(tmp_path)
    _cfg.POSITIONS_FILE = str(tmp_path / "positions.json")
    store_mod.SETTINGS = _cfg
    tools_mod.SETTINGS = _cfg
    tools_mod.reset_turn_state()
    yield


def _write_snapshot(run_date=_TODAY, rows=_ROWS):
    store_mod.save_daily_snapshot(run_date, rows)


# ── screen_snapshot ───────────────────────────────────────────────────────────

def test_screen_filters_pe_and_news():
    _write_snapshot()
    out = tools_mod.screen_snapshot.func(pe_max=15, has_news=True)
    assert out["as_of"] == _TODAY
    assert out["stale"] is False
    assert [s["symbol"] for s in out["stocks"]] == ["AAA"]  # CCC has no PE, BBB too expensive


def test_screen_sector_and_sort_by_score():
    _write_snapshot()
    out = tools_mod.screen_snapshot.func(sector="it")
    assert [s["symbol"] for s in out["stocks"]] == ["CCC", "AAA"]


def test_screen_sort_by_pe_puts_missing_pe_last():
    _write_snapshot()
    out = tools_mod.screen_snapshot.func(sort_by="pe_ratio")
    assert [s["symbol"] for s in out["stocks"]] == ["AAA", "BBB", "CCC"]


def test_screen_flags_stale_snapshot():
    old = (date.today() - timedelta(days=10)).isoformat()
    _write_snapshot(run_date=old)
    out = tools_mod.screen_snapshot.func()
    assert out["as_of"] == old
    assert out["stale"] is True


def test_screen_no_snapshot_returns_error_not_raise():
    out = tools_mod.screen_snapshot.func()
    assert "error" in out


def test_screen_internal_failure_returns_error(monkeypatch):
    monkeypatch.setattr(store_mod, "load_latest_snapshot",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    out = tools_mod.screen_snapshot.func()
    assert out == {"error": "db down"}


# ── live_quote / fetch_news ───────────────────────────────────────────────────

def test_live_quote_caps_symbols_and_wraps_errors(monkeypatch):
    class FakeProvider:
        def get_quote(self, sym):
            if sym == "BAD":
                raise RuntimeError("nope")
            return {"ltp": 42.0}

    import enrichment.market_data as md
    monkeypatch.setattr(md, "get_default_provider", lambda: FakeProvider())

    out = tools_mod.live_quote.func(["aaa", "BAD", "c", "d", "e", "f", "g"])
    assert out["quotes"]["AAA"] == {"ltp": 42.0}
    assert "error" in out["quotes"]["BAD"]
    assert len(out["quotes"]) == 5  # capped


def test_fetch_news_uses_snapshot_company_names(monkeypatch):
    _write_snapshot()
    seen = {}

    def fake_batch(stocks):
        seen["stocks"] = stocks
        return {s["symbol"]: {"headlines": [f"news {s['symbol']}"], "sentiment": "neutral"}
                for s in stocks}

    import enrichment.news_fetcher as nf
    monkeypatch.setattr(nf, "fetch_news_batch", fake_batch)

    out = tools_mod.fetch_news.func(["AAA"])
    assert out["news"]["AAA"] == ["news AAA"]
    assert seen["stocks"][0]["company"] == "Alpha"


# ── score_subset ──────────────────────────────────────────────────────────────

def test_score_subset_scores_snapshot_rows(monkeypatch):
    _write_snapshot()

    import enrichment.news_fetcher as nf
    import scoring.claude_scorer as scorer
    monkeypatch.setattr(nf, "fetch_news_batch", lambda stocks: {s["symbol"]: {"headlines": []} for s in stocks})
    monkeypatch.setattr(
        scorer, "score_stocks",
        lambda stocks, news_map, macro_context="", sector_map=None: [
            {"ticker": s["symbol"], "signals": {"value": {"score": 7, "reason": "ok"}},
             "investment_rationale": "fine", "risk_flags": []}
            for s in stocks
        ],
    )

    class FakeProvider:
        def get_quote(self, sym):
            return {"ltp": 111.0}

    import enrichment.market_data as md
    monkeypatch.setattr(md, "get_default_provider", lambda: FakeProvider())

    out = tools_mod.score_subset.func(["AAA", "BBB"])
    assert out["as_of"] == _TODAY
    assert {s["ticker"] for s in out["scores"]} == {"AAA", "BBB"}
    assert all(s["composite_score"] == 7.0 for s in out["scores"])


def test_score_subset_unknown_symbols_error():
    _write_snapshot()
    out = tools_mod.score_subset.func(["ZZZ"])
    assert "error" in out


# ── deep_dive ─────────────────────────────────────────────────────────────────

def _fake_subgraph(result):
    class FakeGraph:
        def invoke(self, state, cfg):
            return result
    return FakeGraph()


def test_deep_dive_runs_debate_once_per_turn(monkeypatch):
    _write_snapshot()
    import agents.nodes.debate as debate
    monkeypatch.setattr(debate, "build_debate_subgraph", lambda: _fake_subgraph({
        "conviction": {"direction": "long", "conviction": 0.8},
        "bull_case": "cheap", "bear_case": "fii selling", "tokens": 100,
    }))

    out = tools_mod.deep_dive.func("aaa")
    assert out["ticker"] == "AAA"
    assert out["direction"] == "long"
    assert out["conviction"] == 0.8

    again = tools_mod.deep_dive.func("BBB")
    assert "error" in again  # capped at 1 per turn

    tools_mod.reset_turn_state()
    assert tools_mod.deep_dive.func("AAA")["direction"] == "long"


def test_deep_dive_unknown_ticker(monkeypatch):
    _write_snapshot()
    out = tools_mod.deep_dive.func("ZZZ")
    assert "error" in out


# ── get_portfolio ─────────────────────────────────────────────────────────────

def test_get_portfolio_returns_book():
    out = tools_mod.get_portfolio.func()
    assert out["cash"] == 100000.0
    assert out["positions"] == []
