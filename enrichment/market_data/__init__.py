"""Market-data providers: swappable NSE data sources with fallback."""
from enrichment.market_data.provider import (
    Candle,
    MarketDataProvider,
    OptionSignals,
    Quote,
)

__all__ = ["Candle", "Quote", "OptionSignals", "MarketDataProvider",
           "get_default_provider", "enrich_stocks"]


def get_default_provider() -> MarketDataProvider:
    """Provider chain — order depends on agent mode.

    research/paper: yfinance-primary (free, no API cost, sufficient for EOD data).
    live: Groww-primary (real-time quotes needed for order placement accuracy).
    """
    from enrichment.market_data.fallback import FallbackChain
    from enrichment.market_data.groww import GrowwProvider
    from enrichment.market_data.yfinance_source import YFinanceProvider

    try:
        from config import SETTINGS
        live_mode = getattr(SETTINGS, "AGENT_MODE", "research") == "live"
    except Exception:
        live_mode = False

    if live_mode:
        return FallbackChain([GrowwProvider(), YFinanceProvider()])
    return FallbackChain([YFinanceProvider(), GrowwProvider()])


def enrich_stocks(stocks, provider: MarketDataProvider | None = None):
    from enrichment.market_data.enrich import enrich_stocks as _impl
    return _impl(stocks, provider or get_default_provider())
