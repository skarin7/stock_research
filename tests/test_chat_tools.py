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
    TAVILY_API_KEY="",
    MACRO_SEARCH_MAX_RESULTS=5,
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
    # Ensure ranker uses our config regardless of module import order.
    # Restore the original binding after each test to prevent cross-file contamination.
    import scoring.ranker as ranker_mod
    _orig_ranker_settings = ranker_mod.SETTINGS
    ranker_mod.SETTINGS = _cfg
    yield
    ranker_mod.SETTINGS = _orig_ranker_settings


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
    assert out == {"error": "db down", "_source": "snapshot_cache"}


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
    # Without headline_items, falls back to text-only dicts
    assert out["news"]["AAA"][0]["text"] == "news AAA"
    assert seen["stocks"][0]["company"] == "Alpha"


def test_fetch_news_returns_headline_items_with_url(monkeypatch):
    """fetch_news tool returns structured headline_items when news_fetcher provides them."""
    def fake_batch(stocks):
        sym = stocks[0]["symbol"]
        return {sym: {
            "headlines": ["HDFC profit rises"],
            "headline_items": [{"text": "HDFC profit rises", "url": "https://news.google.com/x", "date": "2026-06-28"}],
            "sentiment": "neutral",
        }}

    import enrichment.news_fetcher as nf
    monkeypatch.setattr(nf, "fetch_news_batch", fake_batch)

    # Patch snapshot so fetch_news can resolve company names
    monkeypatch.setattr(store_mod, "load_latest_snapshot", lambda: ("2026-06-27", [
        {"symbol": "HDFCBANK", "company": "HDFC Bank"}
    ]))

    result = tools_mod.fetch_news.func(["HDFCBANK"])
    items = result["news"]["HDFCBANK"]
    assert isinstance(items, list)
    assert items[0]["text"] == "HDFC profit rises"
    assert items[0]["url"] == "https://news.google.com/x"
    assert items[0]["date"] == "2026-06-28"


def test_fetch_news_falls_back_to_headlines_when_no_headline_items(monkeypatch):
    """fetch_news tool degrades gracefully when headline_items key is absent."""
    def fake_batch(stocks):
        sym = stocks[0]["symbol"]
        return {sym: {"headlines": ["HDFC profit rises"], "sentiment": "neutral"}}

    import enrichment.news_fetcher as nf
    monkeypatch.setattr(nf, "fetch_news_batch", fake_batch)
    monkeypatch.setattr(store_mod, "load_latest_snapshot", lambda: ("2026-06-27", [
        {"symbol": "HDFCBANK", "company": "HDFC Bank"}
    ]))

    result = tools_mod.fetch_news.func(["HDFCBANK"])
    items = result["news"]["HDFCBANK"]
    assert isinstance(items, list)
    assert items[0]["text"] == "HDFC profit rises"


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


# ── macro_search ──────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_macro_search_no_key_returns_error():
    _cfg.TAVILY_API_KEY = ""
    out = tools_mod.macro_search.func("iran war impact")
    assert "error" in out


def test_macro_search_returns_results(monkeypatch):
    _cfg.TAVILY_API_KEY = "k"
    payload = {
        "answer": "Crude spikes hurt importers.",
        "results": [
            {"title": "Oil up", "url": "http://a", "content": "brent rises"},
            {"title": "Aviation hit", "url": "http://b", "content": "fuel costs"},
        ],
    }
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(payload))

    out = tools_mod.macro_search.func("iran war impact on india")
    assert out["answer"] == "Crude spikes hurt importers."
    assert [r["url"] for r in out["results"]] == ["http://a", "http://b"]
    assert out["results"][0]["snippet"] == "brent rises"
    assert out["fetched_at"] == _TODAY


def test_macro_search_caps_results(monkeypatch):
    _cfg.TAVILY_API_KEY = "k"
    _cfg.MACRO_SEARCH_MAX_RESULTS = 2
    payload = {"answer": "", "results": [
        {"title": f"t{i}", "url": f"http://{i}", "content": f"c{i}"} for i in range(5)]}
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(payload))

    out = tools_mod.macro_search.func("q")
    assert len(out["results"]) == 2


def test_macro_search_wraps_errors(monkeypatch):
    _cfg.TAVILY_API_KEY = "k"
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))
    out = tools_mod.macro_search.func("q")
    assert out == {"error": "timeout", "_source": "tavily_api"}


# ── timing ────────────────────────────────────────────────────────────────────

def _ramp_candles(n=40, start=100.0, step=1.0):
    """Ascending daily candles; last close is a new high → breakout."""
    out = []
    for i in range(n):
        c = start + i * step
        out.append((f"2026-05-{i + 1:02d}", c - 0.5, c + 0.5, c - 1.0, c, 1000.0 + i))
    return out


def test_timing_computes_metrics(monkeypatch):
    candles = _ramp_candles()

    class FakeProvider:
        def get_ohlcv(self, sym, days=400, to_date=None):
            return candles

        def get_quote(self, sym):
            return {"ltp": candles[-1][4]}

    import enrichment.market_data as md
    monkeypatch.setattr(md, "get_default_provider", lambda: FakeProvider())

    out = tools_mod.timing.func("aaa")
    assert out["ticker"] == "AAA"
    assert out["ltp"] == candles[-1][4]
    assert out["rsi14"] is not None and out["rsi14"] > 70  # straight uptrend
    assert out["breakout_20d"] is True
    assert out["resistance"] == max(c[2] for c in candles[-21:-1])  # prior-20d high
    assert out["support"] is not None
    assert 0.0 <= out["pct_52w_position"] <= 1.0


def test_timing_empty_candles_returns_nulls(monkeypatch):
    class FakeProvider:
        def get_ohlcv(self, sym, days=400, to_date=None):
            return []

        def get_quote(self, sym):
            return {}

    import enrichment.market_data as md
    monkeypatch.setattr(md, "get_default_provider", lambda: FakeProvider())

    out = tools_mod.timing.func("ZZZ")
    assert "error" not in out
    assert out["rsi14"] is None
    assert out["breakout_20d"] is None
    assert out["pct_52w_position"] is None


def test_timing_wraps_errors(monkeypatch):
    class FakeProvider:
        def get_ohlcv(self, sym, days=400, to_date=None):
            raise RuntimeError("provider down")

        def get_quote(self, sym):
            return {}

    import enrichment.market_data as md
    monkeypatch.setattr(md, "get_default_provider", lambda: FakeProvider())

    out = tools_mod.timing.func("AAA")
    assert out == {"error": "provider down", "_source": "intraday_technicals"}


# ── recall ────────────────────────────────────────────────────────────────────

def test_recall_returns_past_calls(monkeypatch):
    monkeypatch.setattr(store_mod, "recent_calls", lambda t, limit=5: [
        {"ticker": "AAA", "score": 8.0, "conviction": 0.7, "rationale": "cheap",
         "regime": "risk-on", "outcome": "+2%", "date": "2026-06-13", "junk": "drop"},
    ])
    out = tools_mod.recall.func("aaa")
    assert out["ticker"] == "AAA"
    assert len(out["past_calls"]) == 1
    call = out["past_calls"][0]
    assert call["score"] == 8.0
    assert call["regime"] == "risk-on"
    assert "junk" not in call  # trimmed to known fields


def test_recall_empty_when_no_memory(monkeypatch):
    monkeypatch.setattr(store_mod, "recent_calls", lambda t, limit=5: [])
    out = tools_mod.recall.func("AAA")
    assert out == {"ticker": "AAA", "past_calls": [], "_source": "memory_store"}


def test_recall_wraps_errors(monkeypatch):
    monkeypatch.setattr(store_mod, "recent_calls",
                        lambda t, limit=5: (_ for _ in ()).throw(RuntimeError("boom")))
    out = tools_mod.recall.func("AAA")
    assert out == {"error": "boom", "_source": "memory_store"}
