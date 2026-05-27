"""yfinance market-data adapter (free fallback)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import yfinance as yf

from enrichment.market_data.provider import Candle, OptionSignals, Quote

logger = logging.getLogger(__name__)


class YFinanceProvider:
    """Daily OHLCV from yfinance. Quote/option-chain are unavailable here
    (no reliable free NSE source), so they return empty/None."""

    def get_ohlcv(self, symbol: str, days: int = 400,
                  to_date: Optional[date] = None) -> list[Candle]:
        end = to_date or date.today()
        start = end - timedelta(days=days)
        try:
            df = yf.download(f"{symbol.upper()}.NS",
                             start=start.strftime("%Y-%m-%d"),
                             end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                             progress=False, auto_adjust=True)
            if df.empty:
                return []
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            return sorted(
                [(str(dt.date()), float(r["Open"]), float(r["High"]),
                  float(r["Low"]), float(r["Close"]), float(r["Volume"]))
                 for dt, r in df.iterrows()],
                key=lambda c: c[0],
            )
        except Exception as e:
            logger.warning("yfinance OHLCV failed for %s: %s", symbol, e)
            return []

    def get_quote(self, symbol: str) -> Quote:
        return {}

    def get_option_chain(self, symbol: str) -> OptionSignals:
        return {"pcr": None, "unusual_call_oi": False}
