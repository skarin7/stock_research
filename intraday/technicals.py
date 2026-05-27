"""Pure technical-indicator helpers for the intraday scorer.

Every function operates on plain Python candle lists (no I/O, no config) so they
are trivially unit-testable. A candle is ``[date_str, open, high, low, close,
volume]`` and candle lists are assumed sorted ascending (oldest → newest), which
is what ``data_sources.fetch_history`` returns.
"""

from __future__ import annotations

from typing import Optional, Sequence

Candle = Sequence  # [date, open, high, low, close, volume]

_CLOSE = 4
_HIGH = 2
_VOL = 5


def _col(candles: list[Candle], idx: int) -> list[float]:
    return [float(c[idx]) for c in candles]


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI(period). Returns None if there is not enough data."""
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    # Seed with a simple average over the first `period`, then Wilder-smooth.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        # No down moves: 100 if there were gains, undefined if perfectly flat.
        return 100.0 if avg_gain > 0 else None
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def pct_change_today(closes: list[float]) -> Optional[float]:
    """Percent change of the last close vs the prior close."""
    if len(closes) < 2 or closes[-2] == 0:
        return None
    return round((closes[-1] - closes[-2]) / closes[-2] * 100.0, 2)


def pct_change_ndays(closes: list[float], n: int) -> Optional[float]:
    """Percent change of the last close vs the close n sessions ago."""
    if len(closes) < n + 1 or closes[-(n + 1)] == 0:
        return None
    return round((closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)] * 100.0, 2)


def prior_high(highs: list[float], n: int) -> Optional[float]:
    """Highest high over the n sessions *before* today (excludes the last bar),
    so a close above it is a genuine breakout to a new n-day high."""
    if len(highs) < n + 1:
        return None
    return max(highs[-(n + 1):-1])


def avg_volume(volumes: list[float], n: int) -> Optional[float]:
    """Average volume over the n sessions before today (excludes the last bar)."""
    if len(volumes) < n + 1:
        return None
    window = volumes[-(n + 1):-1]
    return sum(window) / len(window)


def compute_metrics(candles: list[Candle]) -> dict:
    """Derive the technical inputs the scorer needs from a candle list.

    Returns a dict with: close, volume_today, today_change_pct, change_3d_pct,
    rsi14, high_20d, avg_volume_20d, high_52w. Missing values are None when there
    is not enough history (the scorer treats None as "signal unavailable").
    high_52w is the max high across the supplied candles, so feeding ~1 year of
    daily candles makes it a true 52-week high.
    """
    if not candles:
        return {
            "close": None, "volume_today": None, "today_change_pct": None,
            "change_3d_pct": None, "rsi14": None, "high_20d": None,
            "avg_volume_20d": None, "high_52w": None,
        }

    closes = _col(candles, _CLOSE)
    highs = _col(candles, _HIGH)
    volumes = _col(candles, _VOL)

    return {
        "close": closes[-1],
        "volume_today": volumes[-1],
        "today_change_pct": pct_change_today(closes),
        "change_3d_pct": pct_change_ndays(closes, 3),
        "rsi14": rsi(closes, 14),
        "high_20d": prior_high(highs, 20),
        "avg_volume_20d": avg_volume(volumes, 20),
        "high_52w": max(highs),
    }
