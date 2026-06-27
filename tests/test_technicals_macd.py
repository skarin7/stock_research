"""MACD + breakout signal tests (pure, no I/O)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from enrichment.technical_signals import compute_technicals  # noqa: E402
from intraday.technicals import ema, macd, macd_cross  # noqa: E402


def _candle(close, high=None):
    h = high if high is not None else close
    return ["2026-01-01", close, h, close, close, 1000]


class TestEma:
    def test_seed_is_sma(self):
        out = ema([2, 4, 6, 8], period=2)
        assert out[0] == 3.0  # SMA of first 2

    def test_too_short_returns_none(self):
        assert ema([1, 2], period=5) is None


class TestMacd:
    def test_too_short_returns_none(self):
        assert macd([100.0] * 30) is None  # needs ~slow+signal bars

    def test_flat_series_near_zero(self):
        m = macd([100.0] * 60)
        assert m is not None
        assert abs(m["macd"]) < 1e-6
        assert abs(m["histogram"]) < 1e-6

    def test_uptrend_positive_macd(self):
        closes = [100.0 + i for i in range(60)]
        m = macd(closes)
        assert m["macd"] > 0  # fast EMA above slow EMA in an uptrend


class TestMacdCross:
    def test_flat_then_uptick_is_bullish(self):
        # 40 flat bars (hist≈0) then a single up bar → hist flips positive on the last bar.
        assert macd_cross([100.0] * 40 + [101.0]) == "bullish"

    def test_flat_then_downtick_is_bearish(self):
        assert macd_cross([100.0] * 40 + [99.0]) == "bearish"

    def test_flat_series_no_cross(self):
        assert macd_cross([100.0] * 41) is None

    def test_too_short_returns_none(self):
        assert macd_cross([100.0] * 20) is None


class TestComputeTechnicals:
    def test_breakout_detected(self):
        # 40 bars at high 100, then a final close above the prior 20-day high.
        candles = [_candle(100.0, high=100.0) for _ in range(40)]
        candles.append(_candle(105.0, high=105.0))
        stock = {"ohlc_10d": candles, "52w_high": 110.0}
        t = compute_technicals(stock)
        assert t["breakout_20d"] is True
        assert t["rsi14"] is not None
        assert t["macd"] is not None
        assert t["pct_from_52w_high"] is not None

    def test_no_breakout_when_below_prior_high(self):
        candles = [_candle(100.0, high=120.0) for _ in range(40)]
        candles.append(_candle(101.0, high=101.0))
        stock = {"ohlc_10d": candles}
        t = compute_technicals(stock)
        assert t["breakout_20d"] is False

    def test_short_series_yields_nones(self):
        stock = {"ohlc_10d": [_candle(100.0) for _ in range(5)]}
        t = compute_technicals(stock)
        assert t["macd"] is None
        assert t["macd_cross"] is None
        assert t["breakout_20d"] is None

    def test_empty_candles_no_crash(self):
        t = compute_technicals({"ohlc_10d": []})
        assert t["macd"] is None
        assert t["breakout_20d"] is None
