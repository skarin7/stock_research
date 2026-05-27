"""Unit tests for the market-data provider abstraction. Fully mocked."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

sys.modules.setdefault("config", types.SimpleNamespace(
    GROWW_RATE_LIMIT_DELAY_MS=0, GROWW_TOTP_TOKEN="", GROWW_TOTP_SECRET="",
    GROWW_API_KEY="", OHLC_LOOKBACK_DAYS=10,
))

from enrichment.market_data.provider import Candle, MarketDataProvider  # noqa: E402


def test_candle_is_six_columns():
    c: Candle = ("2026-01-01", 1.0, 2.0, 0.5, 1.5, 1000.0)
    assert len(c) == 6


def test_provider_protocol_runtime_checkable():
    class Dummy:
        def get_ohlcv(self, symbol, days=400, to_date=None):
            return []

        def get_quote(self, symbol):
            return {}

        def get_option_chain(self, symbol):
            return {"pcr": None, "unusual_call_oi": False}

    assert isinstance(Dummy(), MarketDataProvider)


def test_yfinance_ohlcv_six_col(monkeypatch):
    import pandas as pd

    from enrichment.market_data import yfinance_source as yfs
    idx = pd.to_datetime(["2026-01-01", "2026-01-02"])
    df = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0.5, 1],
                       "Close": [1.5, 2.5], "Volume": [100, 200]}, index=idx)
    monkeypatch.setattr(yfs.yf, "download", lambda *a, **k: df)
    out = yfs.YFinanceProvider().get_ohlcv("INFY", days=5)
    assert out[0] == ("2026-01-01", 1.0, 2.0, 0.5, 1.5, 100.0)
    assert len(out[-1]) == 6


def test_groww_option_chain_pcr():
    from enrichment.market_data.groww import GrowwProvider
    fake_resp = {"data": {"option_chain": [
        {"call": {"oi": 100}, "put": {"oi": 150}},
        {"call": {"oi": 50}, "put": {"oi": 60}},
    ]}}

    class FakeClient:
        def get_option_chain(self, **k):
            return fake_resp

    p = GrowwProvider(client_factory=lambda: FakeClient())
    sig = p.get_option_chain("INFY")
    assert sig["pcr"] == round(210 / 150, 2)


def test_groww_is_market_data_provider():
    from enrichment.market_data.groww import GrowwProvider
    assert isinstance(GrowwProvider(client_factory=lambda: None), MarketDataProvider)


def test_fallback_uses_second_when_first_empty():
    from enrichment.market_data.fallback import FallbackChain

    class Empty:
        def get_ohlcv(self, *a, **k):
            return []

        def get_quote(self, *a, **k):
            return {}

        def get_option_chain(self, *a, **k):
            return {"pcr": None, "unusual_call_oi": False}

    class Has:
        def get_ohlcv(self, *a, **k):
            return [("2026-01-01", 1.0, 2.0, 0.5, 1.5, 9.0)]

        def get_quote(self, *a, **k):
            return {"ltp": 10.0}

        def get_option_chain(self, *a, **k):
            return {"pcr": 1.3, "unusual_call_oi": True}

    chain = FallbackChain([Empty(), Has()])
    assert chain.get_ohlcv("X")[0][5] == 9.0
    assert chain.get_quote("X")["ltp"] == 10.0
    assert chain.get_option_chain("X")["pcr"] == 1.3


def test_enrich_stocks_populates_keys():
    from enrichment.market_data.enrich import enrich_stocks

    class FakeProvider:
        def get_ohlcv(self, symbol, days=400, to_date=None):
            return [("2026-01-0%d" % i, 1.0, 2.0, 0.5, float(i), 100.0)
                    for i in range(1, 7)]

        def get_quote(self, symbol):
            return {"ltp": 42.0, "week52_high": 99.0, "week52_low": 10.0, "volume": 500.0}

        def get_option_chain(self, symbol):
            return {"pcr": None, "unusual_call_oi": False}

    out = enrich_stocks([{"symbol": "INFY"}], FakeProvider())
    s = out[0]
    assert s["ltp"] == 42.0
    assert s["52w_high"] == 99.0
    assert s["52w_low"] == 10.0
    assert len(s["ohlc_5d"]) == 5
    assert len(s["ohlc_10d"]) == 6
    assert s["no_data"] is False


def test_enrich_stocks_flags_no_data():
    from enrichment.market_data.enrich import enrich_stocks

    class Dead:
        def get_ohlcv(self, *a, **k): return []
        def get_quote(self, *a, **k): return {}
        def get_option_chain(self, *a, **k): return {"pcr": None, "unusual_call_oi": False}

    out = enrich_stocks([{"symbol": "DEAD"}], Dead())
    assert out[0]["no_data"] is True
