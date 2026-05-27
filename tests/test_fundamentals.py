"""
Unit tests for yfinance fundamentals enrichment, earnings-proximity dates,
news merge/dedup, and sector-aware macro parsing. No API keys / network needed.
Run: python -m pytest tests/test_fundamentals.py -v
"""

import sys
import types
import unittest.mock as mock
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal settings stand-in so modules importing `config` load without a real .env.
_settings = types.SimpleNamespace(ANTHROPIC_API_KEY="test-key", GEMINI_API_KEY="")
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_settings)


@pytest.fixture(autouse=True)
def _bind_settings():
    """news_fetcher reads SETTINGS.GEMINI_API_KEY at call time; bind this file's
    stand-in regardless of collection order so the macro fetch short-circuits."""
    import enrichment.news_fetcher as nf
    nf.SETTINGS = _settings
    yield


# ── yfinance fundamentals ──────────────────────────────────────────────────────

class _FakeTicker:
    def __init__(self, info, earnings_dates=None):
        self.info = info
        self._earnings = earnings_dates

    def get_earnings_dates(self, limit=8):
        if self._earnings is None:
            raise RuntimeError("no earnings data")
        idx = pd.DatetimeIndex(self._earnings)
        return pd.DataFrame({"EPS Estimate": [None] * len(idx)}, index=idx)

    @property
    def calendar(self):
        return {}


class TestFundamentals:
    def test_fills_pe_market_cap_and_volume_ratio(self, monkeypatch):
        from enrichment import fundamentals
        monkeypatch.setattr(fundamentals, "_DELAY", 0)
        monkeypatch.setattr(fundamentals.yf, "Ticker", lambda t: _FakeTicker({
            "trailingPE": 18.234, "forwardPE": 16.0,
            "marketCap": 1_500_000_000_000,  # 1.5e12 INR → 150000 cr
            "sector": "Financial Services", "averageVolume": 1_000_000,
        }))
        out = fundamentals.enrich_fundamentals(
            [{"symbol": "HDFCBANK", "groww_volume": 2_000_000}], ref_date=date(2026, 5, 25)
        )[0]
        assert out["pe_ratio"] == 18.23
        assert out["forward_pe"] == 16.0
        assert out["market_cap_cr"] == 150000.0
        assert out["sector"] == "Financial Services"
        assert out["volume_ratio"] == 2.0

    def test_does_not_overwrite_existing_pe(self, monkeypatch):
        from enrichment import fundamentals
        monkeypatch.setattr(fundamentals, "_DELAY", 0)
        monkeypatch.setattr(fundamentals.yf, "Ticker", lambda t: _FakeTicker({"trailingPE": 99.0}))
        out = fundamentals.enrich_fundamentals(
            [{"symbol": "X", "pe_ratio": 12.0, "sector": "IT"}], ref_date=date(2026, 5, 25)
        )[0]
        assert out["pe_ratio"] == 12.0  # Screener-provided value preserved

    def test_sector_pe_is_median_within_sector(self):
        from enrichment.fundamentals import _assign_sector_pe
        stocks = [
            {"symbol": "A", "sector": "Banks", "pe_ratio": 10},
            {"symbol": "B", "sector": "Banks", "pe_ratio": 20},
            {"symbol": "C", "sector": "Banks", "pe_ratio": 30},
            {"symbol": "D", "sector": "IT", "pe_ratio": 25},
        ]
        _assign_sector_pe(stocks)
        assert stocks[0]["sector_pe"] == 20  # median(10,20,30)
        assert stocks[3]["sector_pe"] == 25

    def test_earnings_days_negative_when_just_reported(self, monkeypatch):
        from enrichment import fundamentals
        monkeypatch.setattr(fundamentals, "_DELAY", 0)
        # Result reported 2 days before ref_date → days_to_earnings == -2
        monkeypatch.setattr(fundamentals.yf, "Ticker", lambda t: _FakeTicker(
            {"trailingPE": 30.0}, earnings_dates=["2026-05-23", "2026-08-20"]
        ))
        out = fundamentals.enrich_fundamentals(
            [{"symbol": "APOLLOHOSP"}], ref_date=date(2026, 5, 25)
        )[0]
        assert out["days_to_earnings"] == -2
        assert out["last_earnings_date"] == "2026-05-23"
        assert out["next_earnings_date"] == "2026-08-20"

    def test_failed_fetch_is_graceful(self, monkeypatch):
        from enrichment import fundamentals

        def _boom(t):
            raise RuntimeError("network down")

        monkeypatch.setattr(fundamentals, "_DELAY", 0)
        monkeypatch.setattr(fundamentals.yf, "Ticker", _boom)
        out = fundamentals.enrich_fundamentals([{"symbol": "X"}], ref_date=date(2026, 5, 25))[0]
        assert out["symbol"] == "X"
        assert out.get("pe_ratio") is None


# ── News merge / dedup ──────────────────────────────────────────────────────────

class TestNewsMerge:
    def test_results_headlines_prioritised_and_deduped(self, monkeypatch):
        from enrichment import news_fetcher

        results = [{"title": "Apollo Hospitals Q4 profit jumps 30%", "ts": None}]
        general = [
            {"title": "Apollo Hospitals Q4 profit jumps 30%", "ts": None},  # dup of results
            {"title": "Apollo Hospitals opens new unit in Pune", "ts": None},
        ]
        calls = {"n": 0}

        def fake_rss(query):
            calls["n"] += 1
            return results if calls["n"] == 1 else general  # results query first

        monkeypatch.setattr(news_fetcher, "_rss_items", fake_rss)
        out = news_fetcher._fetch_via_rss("APOLLOHOSP", "Apollo Hospitals")
        assert out["headlines"][0] == "Apollo Hospitals Q4 profit jumps 30%"
        assert out["headlines"] == [
            "Apollo Hospitals Q4 profit jumps 30%",
            "Apollo Hospitals opens new unit in Pune",
        ]


# ── Sector-aware macro parsing ──────────────────────────────────────────────────

class TestMacroSplit:
    def test_split_extracts_summary_and_sector_map(self):
        from enrichment.news_fetcher import _split_macro
        text = (
            "- FII selling continues\n- Crude dropped 5%\n\n"
            '```json\n{"Energy": {"impact": "positive", "driver": "crude drop helps OMCs"}}\n```'
        )
        summary, sector_map = _split_macro(text, ["Energy"])
        assert "Crude dropped 5%" in summary
        assert "```" not in summary
        assert sector_map["Energy"]["impact"] == "positive"

    def test_split_returns_empty_map_on_bad_json(self):
        from enrichment.news_fetcher import _split_macro
        summary, sector_map = _split_macro("Just a plain summary, no JSON.", [])
        assert sector_map == {}
        assert summary.startswith("Just a plain summary")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
