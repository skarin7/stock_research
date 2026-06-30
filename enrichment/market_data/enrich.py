"""Enrich stock dicts with OHLC candles + live quote from a market-data provider."""
from __future__ import annotations

import logging

from config import SETTINGS

from enrichment.market_data.provider import MarketDataProvider

logger = logging.getLogger(__name__)


def enrich_stocks(stocks: list[dict], provider: MarketDataProvider) -> list[dict]:
    """Enrich each stock with OHLC candles and a live quote.

    Each dict gains: ohlc_5d, ohlc_10d, ltp, 52w_high, 52w_low, groww_volume.
    Sets no_data=True when the provider returns neither candles nor a quote
    (likely delisted/suspended).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lookback = SETTINGS.OHLC_LOOKBACK_DAYS
    workers = getattr(SETTINGS, "ENRICH_WORKERS", 10)

    def _enrich_one(indexed_stock: tuple[int, dict]) -> tuple[int, dict]:
        idx, stock = indexed_stock
        sym = stock["symbol"]
        logger.info("Market-data enrichment %d/%d: %s", idx + 1, len(stocks), sym)
        stock = dict(stock)
        try:
            candles = provider.get_ohlcv(sym, days=lookback)
            quote = provider.get_quote(sym)
            stock["ohlc_5d"] = candles[-5:] if len(candles) >= 5 else candles
            stock["ohlc_10d"] = candles
            stock["ltp"] = quote.get("ltp") or stock.get("price")
            stock["52w_high"] = quote.get("week52_high") or stock.get("52w_high")
            stock["52w_low"] = quote.get("week52_low") or stock.get("52w_low")
            stock["groww_volume"] = quote.get("volume")
            if not candles and not quote.get("ltp"):
                logger.warning("%s — no price data; possibly delisted/suspended", sym)
                stock["no_data"] = True
            else:
                stock["no_data"] = False
        except Exception as e:
            logger.error("Enrichment failed for %s: %s — skipping", sym, e)
            stock.setdefault("ohlc_5d", [])
            stock.setdefault("ohlc_10d", [])
            stock["no_data"] = True
        return idx, stock

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, (i, s)): i for i, s in enumerate(stocks)}
        for fut in as_completed(futures):
            idx, stock = fut.result()
            results[idx] = stock

    return [results[i] for i in range(len(stocks))]
