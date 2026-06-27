"""Groww API market-data adapter — official source (primary).

Auth priority: (1) TOTP — set GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET.
(2) API key+secret — set GROWW_API_KEY + GROWW_API_SECRET.
NOTE: per Groww's docs all auth methods require a *daily approval* on the
Groww Cloud API Keys page — TOTP avoids per-request OTP entry but is NOT
exempt from the daily re-approval.

The growwapi SDK is imported lazily so this module imports cleanly without the
SDK installed (callers/tests inject a client_factory). Every fetch degrades to
empty/None on failure — a dead source must never abort a run.

Verified against the installed SDK: get_ohlcv uses the current (non-deprecated)
`get_historical_candles` (V2, /historical/candles) with candle_interval="1day";
`get_historical_candle_data` is the deprecated one — do not switch back to it.
The remaining unverified bits are the exact `groww_symbol` format ("NSE-<SYM>")
and the option-chain response shape, plus access itself: the Market Data /
historical endpoints require an *active paid Trading API subscription* — without
it every data call returns 403 (see _log_data_error). The yfinance fallback in
FallbackChain keeps the system working until the subscription is active.
"""
from __future__ import annotations

import collections
import logging
import random
import time
from datetime import date, datetime, timedelta
from typing import Callable, Deque, Optional, TypeVar

_T = TypeVar("_T")

from config import SETTINGS

from enrichment.market_data.provider import Candle, OptionSignals, Quote

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Sliding-window rate limiter for Groww Live Data: 10 req/s, 300 req/min.
    Operates below those ceilings by default (8/s, 250/min) to leave headroom.
    No-op when GROWW_RATE_LIMIT_DELAY_MS == 0 (tests / offline mode)."""

    def __init__(self, per_second: int = 8, per_minute: int = 250) -> None:
        self._per_second = per_second
        self._per_minute = per_minute
        self._window: Deque[float] = collections.deque()

    def wait(self) -> None:
        if SETTINGS.GROWW_RATE_LIMIT_DELAY_MS == 0:
            return  # bypass in tests / dry-run

        now = time.monotonic()

        # Evict entries outside the 60-second window
        while self._window and self._window[0] < now - 60.0:
            self._window.popleft()

        # Per-minute cap: pause until the oldest call falls out of the window
        if len(self._window) >= self._per_minute:
            sleep_for = (self._window[0] + 60.0) - now
            if sleep_for > 0:
                logger.info(
                    "Groww rate limit: %d calls/min cap reached — pausing %.1fs",
                    self._per_minute, sleep_for,
                )
                time.sleep(sleep_for)
            now = time.monotonic()
            while self._window and self._window[0] < now - 60.0:
                self._window.popleft()

        # Per-second cap: count calls in the last 1 s
        recent = sum(1 for t in self._window if t > now - 1.0)
        if recent >= self._per_second:
            time.sleep(1.0 / self._per_second)

        self._window.append(time.monotonic())


# Module-level singleton — shared across all GrowwProvider instances in process.
_rate_limiter = _RateLimiter(
    per_second=8,   # Groww limit: 10/s  (20% headroom)
    per_minute=250, # Groww limit: 300/m (17% headroom)
)

# Exception names that warrant a retry (by name to avoid hard SDK import).
_RETRYABLE_EXCEPTIONS = frozenset({"GrowwAPIRateLimitException", "GrowwAPITimeoutException"})


def _is_retryable(e: Exception) -> bool:
    return type(e).__name__ in _RETRYABLE_EXCEPTIONS


def _retry(fn: Callable[[], _T], max_retries: int = 3, base_delay: float = 2.0) -> _T:
    """Call fn(), retrying on rate-limit / timeout exceptions with exponential backoff + jitter.

    Non-retryable Groww errors (auth, authz, 404, bad-request) bubble up immediately.
    Retryable: GrowwAPIRateLimitException, GrowwAPITimeoutException.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e) or attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt) + random.uniform(0.0, 1.0), 30.0)
            logger.warning(
                "Groww %s (attempt %d/%d) — retrying in %.1fs",
                type(e).__name__, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


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
    """Generate a fresh Groww access token. Prefers TOTP (no per-request OTP);
    note Groww still requires daily key approval on the Cloud API Keys page."""
    # Pre-baked daily token — copy from Groww portal each morning; valid until 6 AM next day.
    if getattr(SETTINGS, "GROWW_ACCESS_TOKEN", None):
        logger.info("Groww: using pre-set GROWW_ACCESS_TOKEN (valid until 6 AM IST)")
        return SETTINGS.GROWW_ACCESS_TOKEN
    GrowwAPI = _sdk()
    if SETTINGS.GROWW_TOTP_TOKEN and SETTINGS.GROWW_TOTP_SECRET:
        try:
            import pyotp
            totp = pyotp.TOTP(SETTINGS.GROWW_TOTP_SECRET).now()
            token = GrowwAPI.get_access_token(api_key=SETTINGS.GROWW_TOTP_TOKEN, totp=totp)
            logger.info("Groww: authenticated via TOTP")
            return token
        except Exception as e:
            logger.warning("Groww TOTP auth failed: %s — trying API key+secret", e)
    if SETTINGS.GROWW_API_KEY and SETTINGS.GROWW_API_SECRET:
        try:
            token = GrowwAPI.get_access_token(api_key=SETTINGS.GROWW_API_KEY, secret=SETTINGS.GROWW_API_SECRET)
            logger.info("Groww: authenticated via API key+secret")
            return token
        except Exception as e:
            logger.warning("Groww API key+secret auth failed: %s", e)
    raise RuntimeError("No Groww credentials configured (set GROWW_TOTP_TOKEN/SECRET or GROWW_API_KEY/SECRET)")


_client_cache: dict = {"client": None, "expires_at": 0.0}


def _token_expiry_epoch() -> float:
    """Next 6:00 AM IST as a Unix timestamp (tokens expire at 6 AM IST daily)."""
    from datetime import timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    expiry_ist = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
    if now_ist >= expiry_ist:
        expiry_ist = expiry_ist + timedelta(days=1)
    return expiry_ist.timestamp()


def default_client():
    """Return an authenticated Groww client, refreshing automatically after 6 AM IST.

    TOTP credentials make this fully automatic — no daily manual token copy needed.
    Pre-baked GROWW_ACCESS_TOKEN overrides (manual fallback when TOTP isn't set up).
    Reused by the broker layer for order placement.
    """
    now = time.time()
    if _client_cache["client"] is None or now >= _client_cache["expires_at"]:
        token = _get_access_token()
        _client_cache["client"] = _sdk()(token)
        _client_cache["expires_at"] = _token_expiry_epoch()
        logger.info(
            "Groww client (re)initialised — valid until %s IST",
            datetime.fromtimestamp(_client_cache["expires_at"]).strftime("%H:%M"),
        )
    return _client_cache["client"]


def _log_data_error(op: str, symbol: str, e: Exception) -> None:
    """Log a Groww data-fetch failure, calling out 403 = missing data permission.

    A 403 here is authorisation, not authentication: TOTP login succeeded but the
    API key/account is not entitled to the Market Data API. It will not fix itself
    on retry — the account needs the live/historical data permission enabled. The
    FallbackChain drops to yfinance, so the run continues regardless.
    """
    if str(getattr(e, "code", "")) == "403":
        logger.warning(
            "Groww %s for %s: 403 — API token lacks Market Data permission "
            "(auth OK, account not entitled to live/historical data; falling back). %s",
            op, symbol, e,
        )
    else:
        logger.warning("Groww %s for %s failed: %s", op, symbol, e)


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
            try:
                interval = getattr(_sdk(), "CANDLE_INTERVAL_DAY", "1day")
            except Exception:
                interval = "1day"

            def _call():
                _rate_limiter.wait()
                return client.get_historical_candles(
                    exchange=self._exchange,
                    segment=self._segment,
                    groww_symbol=f"NSE-{symbol.upper()}",
                    start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                    candle_interval=interval,
                )

            resp = _retry(_call)
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
            _log_data_error("historical candles", symbol, e)
            return []

    def get_quote(self, symbol: str) -> Quote:
        try:
            client = self._client()

            def _call():
                _rate_limiter.wait()
                return client.get_quote(
                    trading_symbol=symbol.upper(),
                    exchange=self._exchange,
                    segment=self._segment,
                )

            resp = _retry(_call)
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
            _log_data_error("quote fetch", symbol, e)
            return {}

    def get_option_chain(self, symbol: str) -> OptionSignals:
        try:
            client = self._client()

            def _call():
                _rate_limiter.wait()
                return client.get_option_chain(trading_symbol=symbol.upper(), exchange=self._exchange)

            resp = _retry(_call)
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
            _log_data_error("option chain", symbol, e)
            return {"pcr": None, "unusual_call_oi": False}
