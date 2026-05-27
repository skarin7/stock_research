"""Unit tests for the intraday prediction system.
All data is mocked — no network or API keys needed.
Run: python -m pytest tests/test_intraday.py -v
"""

import sys
import unittest.mock as mock
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal config so intraday.pipeline / intraday.report import cleanly.
mock_config = mock.MagicMock()
mock_config.OUTPUT_DIR = "output"
mock_config.STOCK_UNIVERSE = "nifty50"
mock_config.DRY_RUN_STOCK_COUNT = 5
mock_config.INTRADAY_SCORE_THRESHOLD = 5
mock_config.INTRADAY_HIGH_CONVICTION = 7
mock_config.INTRADAY_TOP_N = 10
mock_config.INTRADAY_HISTORY_DAYS = 90
sys.modules["config"] = mock_config

from intraday import signals, technicals, report, pipeline  # noqa: E402


# ── technicals ────────────────────────────────────────────────────────────────

def _candle(d, c, h=None, v=1000):
    h = h if h is not None else c
    return [d, c, h, c, c, v]


class TestTechnicals:
    def test_rsi_all_gains_is_100(self):
        closes = list(range(1, 20))  # strictly increasing
        assert technicals.rsi(closes, 14) == 100.0

    def test_rsi_insufficient_data_none(self):
        assert technicals.rsi([1, 2, 3], 14) is None

    def test_rsi_midrange(self):
        # Alternating up/down should sit near 50.
        closes = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11]
        r = technicals.rsi(closes, 14)
        assert r is not None and 30 < r < 70

    def test_pct_change_today(self):
        assert technicals.pct_change_today([100, 105]) == 5.0

    def test_pct_change_ndays(self):
        assert technicals.pct_change_ndays([100, 101, 102, 103], 3) == 3.0

    def test_prior_high_excludes_today(self):
        highs = [10, 12, 11, 9]  # 3 prior + today(9)
        assert technicals.prior_high(highs, 3) == 12

    def test_avg_volume_excludes_today(self):
        vols = [100, 200, 300, 9999]
        assert technicals.avg_volume(vols, 3) == 200.0

    def test_compute_metrics_breakout(self):
        candles = [_candle(f"2026-01-{i:02d}", c, h=c) for i, c in
                   enumerate(range(100, 130), start=1)]
        m = technicals.compute_metrics(candles)
        assert m["close"] == 129
        assert m["high_20d"] is not None and m["close"] > m["high_20d"]

    def test_compute_metrics_empty(self):
        m = technicals.compute_metrics([])
        assert all(v is None for v in m.values())

    def test_compute_metrics_52w_high(self):
        # high_52w is the max high across all supplied candles.
        candles = [_candle("2026-01-01", 100, h=150)] + \
                  [_candle(f"2026-02-{i:02d}", 110, h=120) for i in range(1, 25)]
        m = technicals.compute_metrics(candles)
        assert m["high_52w"] == 150


# ── signals ─────────────────────────────────────────────────────────────────

class TestSignals:
    def test_board_meeting_strongest(self):
        r = signals.score_stock({"symbol": "X", "has_board_meeting_tomorrow": True})
        assert r["score"] == 3
        assert any("Board meeting" in x for x in r["reasons"])

    def test_volume_spike_and_low_volume_mutually_exclusive(self):
        spike = signals.score_stock({"symbol": "X", "volume_today": 200, "avg_volume_20d": 100})
        low = signals.score_stock({"symbol": "X", "volume_today": 50, "avg_volume_20d": 100})
        assert spike["score"] == 2
        assert low["score"] == -1

    def test_breakout(self):
        r = signals.score_stock({"symbol": "X", "close": 110, "high_20d": 100})
        assert r["score"] == 2

    def test_rsi_ideal_zone(self):
        assert signals.score_stock({"symbol": "X", "rsi14": 60})["score"] == 1

    def test_rsi_overbought_penalty(self):
        assert signals.score_stock({"symbol": "X", "rsi14": 80})["score"] == -2

    def test_near_52w_high(self):
        assert signals.score_stock({"symbol": "X", "close": 96, "high_52w": 100})["score"] == 1

    def test_pcr_bullish(self):
        assert signals.score_stock({"symbol": "X", "pcr": 1.5})["score"] == 1
        assert signals.score_stock({"symbol": "X", "pcr": 0.9})["score"] == 0

    def test_gentle_3d_momentum(self):
        assert signals.score_stock({"symbol": "X", "change_3d_pct": 3})["score"] == 1
        assert signals.score_stock({"symbol": "X", "change_3d_pct": 10})["score"] == 0

    def test_chase_risk_penalty(self):
        assert signals.score_stock({"symbol": "X", "today_change_pct": 9})["score"] == -2

    def test_nifty_weak_penalty(self):
        assert signals.score_stock({"symbol": "X", "nifty_change_pct": -0.8})["score"] == -2

    def test_asm_gsm_penalty(self):
        assert signals.score_stock({"symbol": "X", "in_asm_gsm": True})["score"] == -1

    def test_missing_data_scores_zero(self):
        r = signals.score_stock({"symbol": "X"})
        assert r["score"] == 0 and r["reasons"] == []

    def test_combined_high_conviction(self):
        # Board meeting + volume spike + breakout + RSI ideal + within-5%-52w = 9
        ctx = {
            "symbol": "FINPIPE",
            "has_board_meeting_tomorrow": True,
            "volume_today": 210, "avg_volume_20d": 100,
            "close": 178, "high_20d": 170, "high_52w": 180,
            "rsi14": 61,
            "pcr": 1.3,
        }
        r = signals.score_stock(ctx)
        assert r["score"] == 3 + 2 + 2 + 1 + 1 + 1  # board, vol, breakout, rsi, 52w, pcr
        assert signals.conviction(r["score"], 7) == "HIGH"

    def test_conviction_bands(self):
        assert signals.conviction(8, 7) == "HIGH"
        assert signals.conviction(5, 7) == "MODERATE"
        assert signals.conviction(3, 7) == "LOW"
        assert signals.conviction(1, 7) == "IGNORE"


# ── report ────────────────────────────────────────────────────────────────────

class TestReport:
    def test_build_alert_groups_by_conviction(self):
        wl = [
            {"symbol": "AAA", "score": 8, "conviction": "HIGH", "close": 100,
             "reasons": ["[+3] Board meeting tomorrow (results/dividend)"]},
            {"symbol": "BBB", "score": 5, "conviction": "MODERATE", "close": 200,
             "reasons": ["[+2] 20-day breakout"]},
        ]
        text = report.build_alert(wl, date(2026, 5, 27), nifty_change_pct=0.4)
        assert "HIGH CONVICTION" in text
        assert "WATCH LIST" in text
        assert "AAA" in text and "BBB" in text
        assert "Not financial advice" in text

    def test_build_alert_empty(self):
        text = report.build_alert([], date(2026, 5, 27))
        assert "No stocks scored" in text

    def test_write_watchlist(self, tmp_path):
        mock_config.OUTPUT_DIR = str(tmp_path)
        wl = [{"symbol": "AAA", "score": 7, "conviction": "HIGH", "close": 100, "reasons": []}]
        path = report.write_watchlist(wl, date(2026, 5, 27))
        assert path.exists()
        assert (path.parent / "intraday_watchlist.txt").exists()
        import json
        data = json.loads(path.read_text())
        assert data["watchlist"][0]["symbol"] == "AAA"
        mock_config.OUTPUT_DIR = "output"


# ── pipeline ──────────────────────────────────────────────────────────────────

class TestPipeline:
    def test_filter_sort_cap(self, monkeypatch):
        universe = [
            {"symbol": "HIGH1", "company": "H1", "sector": "IT"},
            {"symbol": "MID1", "company": "M1", "sector": "IT"},
            {"symbol": "LOW1", "company": "L1", "sector": "IT"},
        ]
        monkeypatch.setattr(pipeline, "_load_universe", lambda: universe)
        monkeypatch.setattr(pipeline.data_sources, "board_meetings_tomorrow",
                            lambda d: {"HIGH1"})  # +3
        monkeypatch.setattr(pipeline.data_sources, "asm_gsm_symbols", lambda: set())
        monkeypatch.setattr(pipeline.data_sources, "nifty_change_pct", lambda d: 0.2)
        monkeypatch.setattr(pipeline.data_sources, "option_chain_signals",
                            lambda s: {"pcr": None, "unusual_call_oi": False})

        def fake_history(sym, days, to_date):
            # HIGH1: flat history then a modest breakout bar on a volume spike.
            # Board meeting (+3) + volume spike (+2) clears the threshold without
            # tripping the chase (>8%) or overbought penalties.
            if sym == "HIGH1":
                base = [["d%02d" % i, 100, 100, 100, 100, 100] for i in range(40)]
                base.append(["d40", 100, 102, 100, 101, 300])  # +1% close, vol 3x
                return base
            return [["d%02d" % i, 100, 100, 100, 100, 100] for i in range(41)]

        monkeypatch.setattr(pipeline.data_sources, "fetch_history", fake_history)

        wl = pipeline.run_pipeline(date(2026, 5, 27))
        assert wl[0]["symbol"] == "HIGH1"
        assert all(r["score"] >= 5 for r in wl)
        # MID1/LOW1 are flat → below threshold → excluded.
        assert all(r["symbol"] == "HIGH1" for r in wl)

    def test_dry_run_limits_universe(self, monkeypatch):
        big = [{"symbol": f"S{i}", "company": "", "sector": "IT"} for i in range(50)]
        monkeypatch.setattr(pipeline, "_load_universe", lambda: big)
        monkeypatch.setattr(pipeline.data_sources, "board_meetings_tomorrow", lambda d: set())
        monkeypatch.setattr(pipeline.data_sources, "asm_gsm_symbols", lambda: set())
        monkeypatch.setattr(pipeline.data_sources, "nifty_change_pct", lambda d: 0.0)
        monkeypatch.setattr(pipeline.data_sources, "option_chain_signals",
                            lambda s: {"pcr": None, "unusual_call_oi": False})
        seen = []
        def fake_history(sym, days, to_date):
            seen.append(sym)
            return []
        monkeypatch.setattr(pipeline.data_sources, "fetch_history", fake_history)

        pipeline.run_pipeline(date(2026, 5, 27), dry_run=True)
        assert len(seen) == mock_config.DRY_RUN_STOCK_COUNT
