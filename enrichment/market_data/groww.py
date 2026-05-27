"""Groww API market-data adapter — official source (primary).

Auth: TOTP flow (preferred, no daily expiry) — set GROWW_TOTP_TOKEN +
GROWW_TOTP_SECRET. Falls back to legacy JWT (GROWW_API_KEY) if TOTP vars absent.

The growwapi SDK is imported lazily so this module imports cleanly without the
SDK installed (callers/tests inject a client_factory). Every fetch degrades to
empty/None on failure — a dead source must never abort a run.

NOTE: the Groww daily-candle params/constant and option-chain response shape are
unverified against the installed SDK (mirrors the broker seam). The yfinance
fallback in FallbackChain keeps the system working today; verify the Groww path
against the SDK before relying on it.
"""
from __future__ import annotations

import functools
import logging
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import config

from enrichment.market_data.provider import Candle, OptionSignals, Quote

logger = logging.getLogger(__name__)


def _sdk():
    from growwapi import GrowwAPI
    return GrowwAPI


def _exchange() -> str:
    try:
        return _sdk().EXCHANGE_NSE
    except Exception:
        return "NSE"


def _segment() -> str:
    try:
        return _sdk().SEGMENT_CASH
    except Exception:
        return "CASH"


def _get_access_token() -> str:
    """Generate a fresh Groww access token. Prefers TOTP (no daily expiry)."""
    GrowwAPI = _sdk()
    if config.GROWW_TOTP_TOKEN and config.GROWW_TOTP_SECRET:
        try:
            import pyotp
            totp = pyotp.TOTP(config.GROWW_TOTP_SECRET).now()
            token = GrowwAPI.get_access_token(api_key=config.GROWW_TOTP_TOKEN, totp=totp)
            logger.info("Groww: authenticated via TOTP")
            return token
        except Exception as e:
            logger.warning("Groww TOTP auth failed: %s — trying legacy JWT", e)
    if config.GROWW_API_KEY:
        logger.info("Groww: using legacy JWT token")
        return config.GROWW_API_KEY
    raise RuntimeError("No Groww credentials configured (set GROWW_TOTP_TOKEN/SECRET or GROWW_API_KEY)")


@functools.lru_cache(maxsize=1)
def default_client():
    """Process-wide authenticated Groww client (one per process). Reused by the
    broker layer for order placement."""
    return _sdk()(_get_access_token())


def _delay() -> float:
    return config.GROWW_RATE_LIMIT_DELAY_MS / 1000.0


def _safe_float(payload: dict, keys: list[str]) -> Optional[float]:
    for k in keys:
        v = payload.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class GrowwProvider:
    UNUSUAL_OI_THRESHOLD = 0.40  # top-3 call strikes > 40% of total call OI → unusual

    def __init__(self, client_factory: Optional[Callable[[], object]] = None):
        self._client_factory = client_factory or default_client
        self._exchange = _exchange()
        self._segment = _segment()

    def _client(self):
        return self._client_factory()

    def get_ohlcv(self, symbol: str, days: int = 400,
                  to_date: Optional[date] = None) -> list[Candle]:
        end = to_date or date.today()
        start = end - timedelta(days=days)
        try:
            client = self._client()
            time.sleep(_delay())
            try:
                interval = getattr(_sdk(), "CANDLE_INTERVAL_DAY", "1440")
            except Exception:
                interval = "1440"
            resp = client.get_historical_candles(
                exchange=self._exchange,
                segment=self._segment,
                groww_symbol=f"NSE-{symbol.upper()}",
                start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval=interval,
            )
            rows = resp.get("candles") if isinstance(resp, dict) else None
            if rows is None and isinstance(resp, dict):
                rows = resp.get("data", {}).get("candles")
            if not rows:
                return []
            candles: list[Candle] = []
            for r in rows:  # Groww candle: [epoch_seconds, open, high, low, close, volume]
                ts = datetime.fromtimestamp(int(r[0])).date()
                candles.append((str(ts), float(r[1]), float(r[2]),
                                float(r[3]), float(r[4]), float(r[5])))
            return sorted(candles, key=lambda c: c[0])
        except Exception as e:
            logger.warning("Groww historical candles failed for %s: %s", symbol, e)
            return []

    def get_quote(self, symbol: str) -> Quote:
        try:
            client = self._client()
            time.sleep(_delay())
            resp = client.get_quote(
                trading_symbol=symbol.upper(),
                exchange=self._exchange,
                segment=self._segment,
            )
            payload = resp.get("data", resp)
            return {
                "ltp": _safe_float(payload, ["ltp", "lastPrice", "last_price", "close"]),
                "open": _safe_float(payload, ["open", "openPrice"]),
                "high": _safe_float(payload, ["high", "dayHigh", "highPrice"]),
                "low": _safe_float(payload, ["low", "dayLow", "lowPrice"]),
                "close": _safe_float(payload, ["close", "previousClose", "prevClose"]),
                "volume": _safe_float(payload, ["volume", "totalVolume", "tradedVolume"]),
                "week52_high": _safe_float(payload, ["52WeekHigh", "yearHigh", "week52High"]),
                "week52_low": _safe_float(payload, ["52WeekLow", "yearLow", "week52Low"]),
            }
        except Exception as e:
            logger.warning("Quote fetch failed for %s: %s", symbol, e)
            return {}

    def get_option_chain(self, symbol: str) -> OptionSignals:
        try:
            client = self._client()
            time.sleep(_delay())
            resp = client.get_option_chain(trading_symbol=symbol.upper(), exchange=self._exchange)
            payload = resp.get("data", resp) if isinstance(resp, dict) else {}
            chain = (payload.get("option_chain") or payload.get("optionChain")
                     or payload.get("chains") or [])
            tot_call_oi = tot_put_oi = 0.0
            call_oi_by_strike = []
            for row in chain:
                ce = row.get("call") or row.get("CE") or {}
                pe = row.get("put") or row.get("PE") or {}
                c_oi = _safe_float(ce, ["open_interest", "openInterest", "oi"]) or 0.0
                p_oi = _safe_float(pe, ["open_interest", "openInterest", "oi"]) or 0.0
                tot_call_oi += c_oi
                tot_put_oi += p_oi
                if c_oi:
                    call_oi_by_strike.append(c_oi)
            pcr = round(tot_put_oi / tot_call_oi, 2) if tot_call_oi else None
            unusual = False
            if call_oi_by_strike and tot_call_oi:
                top3 = sum(sorted(call_oi_by_strike, reverse=True)[:3])
                unusual = top3 / tot_call_oi > self.UNUSUAL_OI_THRESHOLD
            return {"pcr": pcr, "unusual_call_oi": unusual}
        except Exception as e:
            logger.warning("Groww option chain failed for %s: %s", symbol, e)
            return {"pcr": None, "unusual_call_oi": False}
