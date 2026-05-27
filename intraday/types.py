"""Shared TypedDicts for the intraday scorer."""
from __future__ import annotations

from typing import Optional, TypedDict

from enrichment.market_data.provider import Candle

__all__ = ["Candle", "MetricsDict", "SignalResult", "WatchlistItem"]


class MetricsDict(TypedDict):
    close: Optional[float]
    volume_today: Optional[float]
    today_change_pct: Optional[float]
    change_3d_pct: Optional[float]
    rsi14: Optional[float]
    high_20d: Optional[float]
    avg_volume_20d: Optional[float]
    high_52w: Optional[float]


class SignalResult(TypedDict):
    symbol: str
    score: int
    reasons: list[str]


class WatchlistItem(TypedDict, total=False):
    symbol: str
    score: int
    reasons: list[str]
    conviction: str
    company: str
    sector: Optional[str]
    close: Optional[float]
