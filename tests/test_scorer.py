"""
Unit tests for the scoring pipeline.
Tests use mock data so no API keys are required.
Run: python -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal config mock so we can import without real .env
import unittest.mock as mock
mock_config = mock.MagicMock()
mock_config.ANTHROPIC_API_KEY = "test-key"
mock_config.GROWW_API_KEY = "test-key"
mock_config.SCREENER_EMAIL = "test@test.com"
mock_config.SCREENER_PASSWORD = "test"
mock_config.SCREENER_SCREEN_ID = "123"
mock_config.SCORING_MODEL = "claude-haiku-4-5"
mock_config.REPORT_MODEL = "claude-sonnet-4-6"
mock_config.SIGNAL_WEIGHTS = {
    "news_sentiment": 0.20, "bulk_deals": 0.20, "momentum": 0.15, "value": 0.20,
    "delivery_pct": 0.10, "52w_position": 0.05, "institutional_trend": 0.05, "sector_rotation": 0.05,
}
mock_config.SCREENER_FILTERS = {
    "max_pe_ratio_vs_sector": 1.0,
    "min_delivery_pct": 50.0,
    "min_volume_ratio": 1.5,
    "max_debt_equity": 1.5,
    "min_market_cap_cr": 500,
}
mock_config.TOP_N_STOCKS = 15
mock_config.DRY_RUN_STOCK_COUNT = 5
mock_config.SCORING_BATCH_SIZE = 10
mock_config.OHLC_LOOKBACK_DAYS = 10
mock_config.GROWW_RATE_LIMIT_DELAY_MS = 0
mock_config.GROWW_BASE_URL = "https://api.groww.in/v1/market"
mock_config.OUTPUT_DIR = "output"
sys.modules["config"] = mock_config


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_SCORECARD = {
    "ticker": "HDFCBANK",
    "composite_score": 7.8,
    "signals": {
        "news_sentiment":      {"score": 8, "reason": "Strong Q4 beat, FII interest"},
        "bulk_deals":          {"score": 9, "reason": "SBI MF bulk buy 5L shares"},
        "momentum":            {"score": 7, "reason": "3 of 5 sessions closed higher"},
        "value":               {"score": 8, "reason": "PE 18.2 vs sector 22.1"},
        "delivery_pct":        {"score": 8, "reason": "68% delivery on 2x volume"},
        "52w_position":        {"score": 5, "reason": "Mid-range, no breakout yet"},
        "institutional_trend": {"score": 8, "reason": "FII +1.2% last quarter"},
        "sector_rotation":     {"score": 7, "reason": "Banking sector in FII favour"},
    },
    "earnings_proximity": False,
    "investment_rationale": "Strong fundamentals with institutional backing.",
    "risk_flags": [],
}

SAMPLE_STOCK = {
    "symbol": "HDFCBANK",
    "company": "HDFC Bank Ltd",
    "sector": "Banking",
    "pe_ratio": 18.2,
    "sector_pe": 22.1,
    "delivery_pct": 68.3,
    "volume_ratio": 2.1,
    "ohlc_5d": [
        ["2026-05-07", 1620, 1648, 1612, 1641],
        ["2026-05-08", 1641, 1660, 1630, 1655],
        ["2026-05-09", 1655, 1675, 1640, 1670],
        ["2026-05-12", 1670, 1690, 1660, 1685],
        ["2026-05-13", 1685, 1700, 1675, 1695],
    ],
    "52w_high": 1850,
    "52w_low": 1420,
    "ltp": 1695.0,
    "bulk_deals": [{"client": "SBI MF", "action": "BUY", "qty": 500000, "price": 1680}],
}


# ── Prompt tests ─────────────────────────────────────────────────────────────

class TestPrompts:
    def test_build_user_prompt_is_valid_json(self):
        from scoring.prompts import build_user_prompt
        headlines = ["HDFC Bank Q4 profit rises 22% YoY"]
        result = build_user_prompt(SAMPLE_STOCK, headlines)
        parsed = json.loads(result)
        assert parsed["ticker"] == "HDFCBANK"
        assert parsed["pe_ratio"] == 18.2
        assert len(parsed["news_headlines"]) == 1
        assert len(parsed["ohlc_5d"]) == 5

    def test_build_user_prompt_handles_missing_fields(self):
        from scoring.prompts import build_user_prompt
        minimal = {"symbol": "INFY", "company": "Infosys"}
        result = build_user_prompt(minimal, [])
        parsed = json.loads(result)
        assert parsed["ticker"] == "INFY"
        assert parsed["bulk_deals"] == []
        assert parsed["ohlc_5d"] == []


# ── Ranker tests ─────────────────────────────────────────────────────────────

class TestRanker:
    def test_composite_score_weighted(self):
        from scoring.ranker import compute_composite
        score = compute_composite(SAMPLE_SCORECARD)
        # Manual calculation:
        # (8*0.20 + 9*0.20 + 7*0.15 + 8*0.20 + 8*0.10 + 5*0.05 + 8*0.05 + 7*0.05)
        expected = round(8*0.20 + 9*0.20 + 7*0.15 + 8*0.20 + 8*0.10 + 5*0.05 + 8*0.05 + 7*0.05, 2)
        assert score == expected

    def test_composite_score_missing_signals(self):
        from scoring.ranker import compute_composite
        partial = {"ticker": "X", "signals": {"news_sentiment": {"score": 8}, "momentum": {"score": 6}}}
        score = compute_composite(partial)
        # Only 2 signals; normalised by total weight present
        assert 1.0 <= score <= 10.0

    def test_rank_stocks_returns_sorted_top_n(self):
        from scoring.ranker import rank_stocks
        cards = []
        for i, score in enumerate([5, 9, 3, 7, 8]):
            c = dict(SAMPLE_SCORECARD)
            c["ticker"] = f"STOCK{i}"
            c["composite_score"] = score
            c["signals"] = {k: {"score": score} for k in mock_config.SIGNAL_WEIGHTS}
            cards.append(c)
        top = rank_stocks(cards, top_n=3)
        assert len(top) == 3
        assert top[0]["composite_score"] >= top[1]["composite_score"] >= top[2]["composite_score"]

    def test_rank_stocks_flags_earnings_proximity(self):
        from scoring.ranker import rank_stocks
        card = dict(SAMPLE_SCORECARD)
        card["earnings_proximity"] = True
        card["signals"] = {k: {"score": 8} for k in mock_config.SIGNAL_WEIGHTS}
        result = rank_stocks([card], top_n=5)
        assert result[0]["earnings_proximity"] is True  # not excluded, just kept


# ── Backtest engine tests ─────────────────────────────────────────────────────

class TestBacktestEngine:
    def test_nth_trading_day_skips_weekends(self):
        from backtest.engine import nth_trading_day
        from datetime import date
        # 2026-05-13 is Wednesday; T+1 should be Thursday 05-14
        result = nth_trading_day(date(2026, 5, 13), 1)
        assert result == date(2026, 5, 14)

    def test_nth_trading_day_skips_weekend(self):
        from backtest.engine import nth_trading_day
        from datetime import date
        # Friday 2026-05-15; T+1 should skip Sat/Sun → Mon 2026-05-18
        result = nth_trading_day(date(2026, 5, 15), 1)
        assert result == date(2026, 5, 18)

    def test_is_trading_day_weekend(self):
        from backtest.engine import is_trading_day
        from datetime import date
        assert not is_trading_day(date(2026, 5, 16))  # Saturday
        assert not is_trading_day(date(2026, 5, 17))  # Sunday
        assert is_trading_day(date(2026, 5, 18))       # Monday


# ── NSE scraper tests ─────────────────────────────────────────────────────────

class TestScreenerScraper:
    def test_apply_filters_excludes_low_market_cap(self):
        from scrapers.screener_scraper import ScreenerScraper
        stocks = [
            {"symbol": "SMALL", "market_cap_cr": 100, "debt_equity": 0.5, "delivery_pct": 60},
            {"symbol": "BIG",   "market_cap_cr": 1000, "debt_equity": 0.5, "delivery_pct": 60},
        ]
        result = ScreenerScraper._apply_filters(stocks)
        assert len(result) == 1
        assert result[0]["symbol"] == "BIG"

    def test_apply_filters_excludes_high_debt(self):
        from scrapers.screener_scraper import ScreenerScraper
        stocks = [
            {"symbol": "HIGHDEBT", "market_cap_cr": 1000, "debt_equity": 3.0, "delivery_pct": 60},
            {"symbol": "LOWDEBT",  "market_cap_cr": 1000, "debt_equity": 0.5, "delivery_pct": 60},
        ]
        result = ScreenerScraper._apply_filters(stocks)
        assert len(result) == 1
        assert result[0]["symbol"] == "LOWDEBT"

    def test_apply_filters_excludes_low_delivery(self):
        from scrapers.screener_scraper import ScreenerScraper
        stocks = [
            {"symbol": "SPEC",  "market_cap_cr": 1000, "debt_equity": 0.5, "delivery_pct": 20},
            {"symbol": "SOLID", "market_cap_cr": 1000, "debt_equity": 0.5, "delivery_pct": 65},
        ]
        result = ScreenerScraper._apply_filters(stocks)
        assert len(result) == 1
        assert result[0]["symbol"] == "SOLID"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
