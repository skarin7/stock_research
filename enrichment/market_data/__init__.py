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
    """Groww-primary with yfinance fallback (the production default)."""
    from enrichment.market_data.fallback import FallbackChain
    from enrichment.market_data.groww import GrowwProvider
    from enrichment.market_data.yfinance_source import YFinanceProvider
    return FallbackChain([GrowwProvider(), YFinanceProvider()])


def enrich_stocks(stocks, provider: MarketDataProvider | None = None):
    from enrichment.market_data.enrich import enrich_stocks as _impl
    return _impl(stocks, provider or get_default_provider())
