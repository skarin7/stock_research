"""Per-stock technical signals (MACD + breakout) for the daily report.

Pure join over ``intraday.technicals`` using the OHLC candles the market-data
enrichment already attached to each stock (``ohlc_10d``). No I/O, no LLM — these
are surfaced in the Telegram report + snapshot to speed the user's manual read,
NOT fed into the composite score (that needs backtest validation first).

A candle is ``[date, open, high, low, close, volume]`` (oldest → newest), the
shape ``MarketDataProvider.get_ohlcv`` returns.
"""

from __future__ import annotations

import logging

from intraday.technicals import macd, macd_cross, prior_high, rsi

logger = logging.getLogger(__name__)

_CLOSE = 4
_HIGH = 2

_BREAKOUT_LOOKBACK = 20


def _col(candles: list, idx: int) -> list[float]:
    out = []
    for c in candles:
        try:
            out.append(float(c[idx]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def compute_technicals(stock: dict) -> dict:
    """Derive MACD + breakout signals from a stock's ``ohlc_10d`` candles.

    Returns a dict (all keys present; values None when there isn't enough
    history). Never raises — bad/short data yields Nones.
    """
    candles = stock.get("ohlc_10d") or []
    closes = _col(candles, _CLOSE)
    highs = _col(candles, _HIGH)

    m = macd(closes) if len(closes) >= 35 else None
    cross = macd_cross(closes) if len(closes) >= 35 else None
    rsi14 = rsi(closes, 14)

    breakout_20d = None
    ph = prior_high(highs, _BREAKOUT_LOOKBACK)
    if ph is not None and closes:
        breakout_20d = closes[-1] > ph

    pct_from_52w_high = None
    high_52w = stock.get("52w_high")
    if high_52w and closes:
        try:
            pct_from_52w_high = round((closes[-1] - float(high_52w)) / float(high_52w) * 100.0, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pct_from_52w_high = None

    return {
        "macd": (m or {}).get("macd"),
        "macd_signal": (m or {}).get("signal"),
        "macd_hist": (m or {}).get("histogram"),
        "macd_cross": cross,
        "breakout_20d": breakout_20d,
        "rsi14": rsi14,
        "pct_from_52w_high": pct_from_52w_high,
    }


def attach_technicals(stocks: list[dict]) -> list[dict]:
    """Set ``stock['technicals']`` on each stock in place; returns the list."""
    for stock in stocks:
        try:
            stock["technicals"] = compute_technicals(stock)
        except Exception as e:  # never break the run for a signal annotation
            logger.warning("technicals failed for %s: %s", stock.get("symbol"), e)
            stock["technicals"] = {}
    return stocks
