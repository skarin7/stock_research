"""Contract round-trip tests — the typed models must reproduce the exact dict
shapes the existing dict-based modules (ranker, telegram_notifier, scores.json)
consume. No config / API keys needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.contracts import (  # noqa: E402
    EnrichedStock,
    ProposalStatus,
    Scorecard,
    TradeProposal,
)

SAMPLE_STOCK = {
    "symbol": "HDFCBANK",
    "company": "HDFC Bank Ltd",
    "sector": "Banking",
    "pe_ratio": 18.2,
    "sector_pe": 22.1,
    "delivery_pct": 68.3,
    "volume_ratio": 2.1,
    "ohlc_5d": [["2026-05-07", 1620, 1648, 1612, 1641]],
    "52w_high": 1850,
    "52w_low": 1420,
    "ltp": 1695.0,
    "bulk_deals": [{"client": "SBI MF", "action": "BUY", "qty": 500000, "price": 1680}],
    "bhavcopy_close": 1694.0,          # not a promoted field → must land in extra
}

SAMPLE_SCORECARD = {
    "ticker": "HDFCBANK",
    "composite_score": 7.8,
    "signals": {
        "news_sentiment": {"score": 8, "reason": "Strong Q4 beat"},
        "value": {"score": 8, "reason": "PE 18.2 vs sector 22.1"},
    },
    "earnings_proximity": False,
    "investment_rationale": "Strong fundamentals.",
    "risk_flags": [],
}


class TestEnrichedStock:
    def test_round_trip_preserves_legacy_keys(self):
        es = EnrichedStock.from_legacy(SAMPLE_STOCK)
        assert es.symbol == "HDFCBANK"
        assert es.week52_high == 1850          # alias mapped from "52w_high"
        assert es.extra["bhavcopy_close"] == 1694.0   # unknown key preserved

        out = es.to_legacy_dict()
        assert out["52w_high"] == 1850         # alias restored to legacy key
        assert out["52w_low"] == 1420
        assert out["bhavcopy_close"] == 1694.0
        assert out["bulk_deals"] == SAMPLE_STOCK["bulk_deals"]
        # no non-legacy attribute leaks through
        assert "week52_high" not in out


class TestScorecard:
    def test_round_trip_matches_ranker_shape(self):
        sc = Scorecard.from_legacy(SAMPLE_SCORECARD)
        out = sc.to_legacy_dict()
        # ranker reads signals[k]["score"]; telegram reads ticker/risk_flags
        assert out["signals"]["news_sentiment"]["score"] == 8
        assert out["signals"]["value"]["reason"] == "PE 18.2 vs sector 22.1"
        assert out["ticker"] == "HDFCBANK"
        assert out["composite_score"] == 7.8
        assert out["risk_flags"] == []

    def test_round_trip_is_lossless(self):
        # from_legacy → to_legacy_dict must be a fixed point on the scorecard shape
        out = Scorecard.from_legacy(SAMPLE_SCORECARD).to_legacy_dict()
        again = Scorecard.from_legacy(out).to_legacy_dict()
        assert out == again
        assert set(out["signals"]) == set(SAMPLE_SCORECARD["signals"])


class TestTradeProposal:
    def test_defaults_and_status_enum(self):
        p = TradeProposal(proposal_id="p1", ticker="INFY", side="BUY", qty=10)
        assert p.status == ProposalStatus.PROPOSED
        assert p.created_at  # auto-set ISO timestamp
        dumped = p.model_dump()
        assert dumped["status"] == "proposed"
