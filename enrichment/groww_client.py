"""
Groww API client — live quotes via official growwapi SDK.
Historical OHLC candles fetched via yfinance (free, NSE .NS suffix).

Auth: TOTP flow (preferred, no daily expiry) — set GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET.
      Falls back to legacy JWT (GROWW_API_KEY) if TOTP vars are absent.
Groww historical candles require a paid data subscription; yfinance covers
the same data for free for our backtesting / momentum signal needs.
"""

import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import yfinance as yf
from growwapi import GrowwAPI

import config

logger = logging.getLogger(__name__)

_DELAY = config.GROWW_RATE_LIMIT_DELAY_MS / 1000.0  # ms → seconds
_EXCHANGE = GrowwAPI.EXCHANGE_NSE
_SEGMENT = GrowwAPI.SEGMENT_CASH

_client: Optional[GrowwAPI] = None
_ticker_to_trading: dict[str, str] = {}  # NSE ticker → trading_symbol (for quote)


def _get_access_token() -> str:
    """Generate a fresh Groww access token. Prefers TOTP (no daily expiry)."""
    if config.GROWW_TOTP_TOKEN and config.GROWW_TOTP_SECRET:
        try:
            import pyotp
            totp = pyotp.TOTP(config.GROWW_TOTP_SECRET).now()
            token = GrowwAPI.get_access_token(api_key=config.GROWW_TOTP_TOKEN, totp=totp)
            logger.info("Groww: authenticated via TOTP")
            return token
        except Exception as e:
            logger.warning("Groww TOTP auth failed: %s — trying legacy JWT", e)
    # Legacy: use JWT directly
    if config.GROWW_API_KEY:
        logger.info("Groww: using legacy JWT token")
        return config.GROWW_API_KEY
    raise RuntimeError("No Groww credentials configured (set GROWW_TOTP_TOKEN/SECRET or GROWW_API_KEY)")


def _get_client() -> GrowwAPI:
    global _client
    if _client is None:
        token = _get_access_token()
        _client = GrowwAPI(token)
    return _client


def _load_instruments():
    """Download instruments CSV to confirm ticker exists on NSE (called once per session)."""
    global _ticker_to_trading
    if _ticker_to_trading:
        return
    try:
        logger.info("Loading Groww instruments CSV...")
        client = _get_client()
        df = client.get_all_instruments()
        nse_eq = df[(df["exchange"] == _EXCHANGE) & (df["segment"] == _SEGMENT)]
        for _, row in nse_eq.iterrows():
            ts = str(row.get("trading_symbol", "")).strip().upper()
            if ts:
                _ticker_to_trading[ts] = ts
        logger.info("Instruments loaded: %d NSE equity symbols", len(_ticker_to_trading))
    except Exception as e:
        logger.warning("Groww instruments load failed: %s — quotes will be skipped", e)


def get_candles(
    symbol: str,
    lookback_days: int = config.OHLC_LOOKBACK_DAYS,
    to_date: Optional[date] = None,
) -> list[list]:
    """
    Fetch daily OHLC candles for an NSE ticker via yfinance.
    Returns list of [date_str, open, high, low, close] sorted ascending.
    """
    end = to_date or date.today()
    start = end - timedelta(days=lookback_days + 7)  # buffer for weekends/holidays

    ticker = f"{symbol.upper()}.NS"
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df.empty:
            logger.warning("No yfinance data for %s", ticker)
            return []

        # Flatten MultiIndex columns if present (yfinance ≥0.2 with single ticker)
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)

        candles = []
        for dt, row in df.iterrows():
            candles.append([
                str(dt.date()),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
            ])
        return sorted(candles, key=lambda x: x[0])[-lookback_days:]
    except Exception as e:
        logger.warning("Candle fetch failed for %s: %s", symbol, e)
        return []


def get_ohlcv(
    symbol: str,
    days: int = 400,
    to_date: Optional[date] = None,
) -> list[list]:
    """Daily OHLCV candles for an NSE ticker: ``[date_str, o, h, l, c, volume]``
    ascending. Prefers Groww's official historical endpoint; falls back to
    yfinance if Groww is unavailable (e.g. no historical-data subscription) or
    returns nothing — so callers get reliable data when subscribed and still
    work for free otherwise.

    NOTE: the Groww daily-candle params/constant are unverified against the
    installed SDK (mirrors the broker seam). The yfinance fallback guarantees
    the function works today; verify the Groww path before relying on it.
    """
    end = to_date or date.today()
    start = end - timedelta(days=days)

    groww = _groww_ohlcv(symbol, start, end)
    if groww:
        return groww
    return _yfinance_ohlcv(symbol, start, end)


def _groww_ohlcv(symbol: str, start: date, end: date) -> list[list]:
    try:
        client = _get_client()
        time.sleep(_DELAY)
        # Daily interval; growwapi exposes named constants (e.g. CANDLE_INTERVAL_DAY).
        interval = getattr(GrowwAPI, "CANDLE_INTERVAL_DAY", "1440")
        resp = client.get_historical_candles(
            exchange=_EXCHANGE,
            segment=_SEGMENT,
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
        candles = []
        for r in rows:  # Groww candle: [epoch_seconds, open, high, low, close, volume]
            ts = datetime.fromtimestamp(int(r[0])).date()
            candles.append([str(ts), float(r[1]), float(r[2]),
                            float(r[3]), float(r[4]), float(r[5])])
        return sorted(candles, key=lambda c: c[0])
    except Exception as e:
        logger.warning("Groww historical candles failed for %s: %s — using yfinance", symbol, e)
        return []


def _yfinance_ohlcv(symbol: str, start: date, end: date) -> list[list]:
    try:
        df = yf.download(f"{symbol.upper()}.NS", start=start.strftime("%Y-%m-%d"),
                         end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df.empty:
            return []
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        return sorted(
            [[str(dt.date()), float(r["Open"]), float(r["High"]),
              float(r["Low"]), float(r["Close"]), float(r["Volume"])]
             for dt, r in df.iterrows()],
            key=lambda c: c[0],
        )
    except Exception as e:
        logger.warning("yfinance OHLCV failed for %s: %s", symbol, e)
        return []


def get_option_chain_pcr(symbol: str) -> dict:
    """Put-Call Ratio and an unusual-Call-OI flag from the Groww option chain.

    Returns ``{"pcr": float|None, "unusual_call_oi": bool}``. PCR is total put OI
    / total call OI across strikes. Non-F&O symbols (or no F&O data access)
    return ``{pcr: None, unusual_call_oi: False}`` and the scorer skips S8/S9.
    """
    time.sleep(_DELAY)
    try:
        client = _get_client()
        resp = client.get_option_chain(trading_symbol=symbol.upper(), exchange=_EXCHANGE)
        payload = resp.get("data", resp) if isinstance(resp, dict) else {}
        chain = payload.get("option_chain") or payload.get("optionChain") or payload.get("chains") or []
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
            unusual = top3 / tot_call_oi > 0.40
        return {"pcr": pcr, "unusual_call_oi": unusual}
    except Exception as e:
        logger.warning("Groww option chain failed for %s: %s", symbol, e)
        return {"pcr": None, "unusual_call_oi": False}


def get_quote(symbol: str) -> dict:
    """
    Fetch live/last quote for an NSE ticker.
    Returns dict with: ltp, open, high, low, close, volume, 52w_high, 52w_low
    """
    time.sleep(_DELAY)
    try:
        client = _get_client()
        resp = client.get_quote(
            trading_symbol=symbol.upper(),
            exchange=_EXCHANGE,
            segment=_SEGMENT,
        )
        payload = resp.get("data", resp)
        return {
            "ltp":      _safe_float(payload, ["ltp", "lastPrice", "last_price", "close"]),
            "open":     _safe_float(payload, ["open", "openPrice"]),
            "high":     _safe_float(payload, ["high", "dayHigh", "highPrice"]),
            "low":      _safe_float(payload, ["low", "dayLow", "lowPrice"]),
            "close":    _safe_float(payload, ["close", "previousClose", "prevClose"]),
            "volume":   _safe_float(payload, ["volume", "totalVolume", "tradedVolume"]),
            "52w_high": _safe_float(payload, ["52WeekHigh", "yearHigh", "week52High"]),
            "52w_low":  _safe_float(payload, ["52WeekLow", "yearLow", "week52Low"]),
        }
    except Exception as e:
        logger.warning("Quote fetch failed for %s: %s", symbol, e)
        return {}


def enrich_stocks(stocks: list[dict]) -> list[dict]:
    """
    Enrich a list of stock dicts with OHLC candles (yfinance) and live quote (Groww).
    Each dict gains: ohlc_5d, ohlc_10d, ltp, 52w_high, 52w_low.
    Sets no_data=True when both yfinance and Groww return nothing (likely delisted/suspended).
    """
    _load_instruments()
    enriched = []
    for i, stock in enumerate(stocks):
        sym = stock["symbol"]
        logger.info("Groww enrichment %d/%d: %s", i + 1, len(stocks), sym)
        stock = dict(stock)
        try:
            candles = get_candles(sym)
            quote = get_quote(sym)

            stock["ohlc_5d"] = candles[-5:] if len(candles) >= 5 else candles
            stock["ohlc_10d"] = candles
            stock["ltp"] = quote.get("ltp") or stock.get("price")
            stock["52w_high"] = quote.get("52w_high") or stock.get("52w_high")
            stock["52w_low"] = quote.get("52w_low") or stock.get("52w_low")
            stock["groww_volume"] = quote.get("volume")

            if not candles and not quote.get("ltp"):
                logger.warning("%s — no price data from yfinance or Groww; possibly delisted/suspended", sym)
                stock["no_data"] = True
            else:
                stock["no_data"] = False
        except Exception as e:
            logger.error("Enrichment failed for %s: %s — skipping", sym, e)
            stock.setdefault("ohlc_5d", [])
            stock.setdefault("ohlc_10d", [])
            stock["no_data"] = True

        enriched.append(stock)
    return enriched


def _safe_float(payload: dict, keys: list[str]) -> Optional[float]:
    for k in keys:
        v = payload.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None
