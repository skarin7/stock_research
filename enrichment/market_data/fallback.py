"""Composite provider: try each source in order, return the first non-empty."""
from __future__ import annotations

from datetime import date
from typing import Optional

from enrichment.market_data.provider import (
    Candle,
    MarketDataProvider,
    OptionSignals,
    Quote,
)


class FallbackChain:
    def __init__(self, providers: list[MarketDataProvider]):
        self._providers = providers

    def get_ohlcv(self, symbol: str, days: int = 400,
                  to_date: Optional[date] = None) -> list[Candle]:
        for p in self._providers:
            out = p.get_ohlcv(symbol, days=days, to_date=to_date)
            if out:
                return out
        return []

    def get_quote(self, symbol: str) -> Quote:
        for p in self._providers:
            q = p.get_quote(symbol)
            if q.get("ltp") is not None:
                return q
        return {}

    def get_option_chain(self, symbol: str) -> OptionSignals:
        for p in self._providers:
            sig = p.get_option_chain(symbol)
            if sig.get("pcr") is not None or sig.get("unusual_call_oi"):
                return sig
        return {"pcr": None, "unusual_call_oi": False}
