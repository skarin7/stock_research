"""Market-data source contract shared by all providers."""
from __future__ import annotations

from datetime import date
from typing import Optional, Protocol, TypedDict, runtime_checkable

# Unified daily bar: (date_str, open, high, low, close, volume)
Candle = tuple[str, float, float, float, float, float]


class Quote(TypedDict, total=False):
    ltp: Optional[float]
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    week52_high: Optional[float]
    week52_low: Optional[float]


class OptionSignals(TypedDict):
    pcr: Optional[float]
    unusual_call_oi: bool


@runtime_checkable
class MarketDataProvider(Protocol):
    """A swappable source of NSE market data. All methods degrade to
    empty/None on failure — a dead source must never abort a run."""

    def get_ohlcv(self, symbol: str, days: int = 400,
                  to_date: Optional[date] = None) -> list[Candle]: ...

    def get_quote(self, symbol: str) -> Quote: ...

    def get_option_chain(self, symbol: str) -> OptionSignals: ...
