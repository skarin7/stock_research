"""
Unit tests for the PIT contamination guard in enrich_fundamentals.

A historical re-run (ref_date in the past) must (a) log a loud warning and
(b) stamp every stock pit_safe=False, since yfinance .info / news are not
point-in-time. A current run (ref_date today / None) stays pit_safe=True.
No API keys / network needed. Run: python -m pytest tests/test_pit_guard.py -v
"""

import logging
import sys
import types
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal settings stand-in so importing `config` works without a real .env.
sys.modules.setdefault(
    "config",
    types.SimpleNamespace(SETTINGS=types.SimpleNamespace(ANTHROPIC_API_KEY="test-key", GEMINI_API_KEY="")),
)


class _FakeTicker:
    def __init__(self, info):
        self.info = info

    def get_earnings_dates(self, limit=8):
        raise RuntimeError("no earnings data")

    @property
    def calendar(self):
        return {}


def _enrich(ref_date, monkeypatch):
    from enrichment import fundamentals
    monkeypatch.setattr(fundamentals, "_DELAY", 0)
    monkeypatch.setattr(fundamentals.yf, "Ticker", lambda t: _FakeTicker({"trailingPE": 18.0}))
    return fundamentals.enrich_fundamentals([{"symbol": "HDFCBANK"}], ref_date=ref_date)


class TestPitGuard:
    def test_past_ref_date_flags_unsafe_and_warns(self, monkeypatch, caplog):
        past = date.today() - timedelta(days=30)
        with caplog.at_level(logging.WARNING):
            out = _enrich(past, monkeypatch)[0]
        assert out["pit_safe"] is False
        assert any("point-in-time" in r.message for r in caplog.records)

    def test_today_ref_date_is_safe_no_warning(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING):
            out = _enrich(date.today(), monkeypatch)[0]
        assert out["pit_safe"] is True
        assert not any("point-in-time" in r.message for r in caplog.records)

    def test_none_ref_date_defaults_to_safe(self, monkeypatch):
        out = _enrich(None, monkeypatch)[0]
        assert out["pit_safe"] is True

    def test_snapshot_row_carries_pit_safe(self):
        from persistence import store
        rows = store.build_snapshot_rows(
            [{"symbol": "HDFCBANK", "pit_safe": False}],
            [{"ticker": "HDFCBANK", "composite_score": 7.0}],
            {},
        )
        assert rows[0]["pit_safe"] is False

    def test_snapshot_row_defaults_pit_safe_true(self):
        from persistence import store
        rows = store.build_snapshot_rows(
            [{"symbol": "HDFCBANK"}],
            [{"ticker": "HDFCBANK", "composite_score": 7.0}],
            {},
        )
        assert rows[0]["pit_safe"] is True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
