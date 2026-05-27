"""Data fetchers for the intraday scorer.

Reliable sources first: daily OHLCV and per-stock PCR / Call-OI come from the
official Groww API (via enrichment.market_data, which falls back to yfinance for
history when Groww historical data isn't subscribed). 52-week high is derived
from the candles. The only remaining fragile NSE-web scrapes are next-day board
meetings (S1) and the ASM/GSM surveillance list (N4) — there is no Groww endpoint
for those. Every fetcher returns an empty/None result on failure and logs — a
dead endpoint must never abort the run (the scorer treats missing data as a
0-point signal).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import requests

from scrapers.http_client import nse_session

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"


# ── Reliable: Groww OHLCV + option chain (yfinance fallback for history) ──────

_provider = None


def _get_provider():
    global _provider
    if _provider is None:
        from enrichment.market_data import get_default_provider
        _provider = get_default_provider()
    return _provider


def fetch_history(symbol: str, days: int = 400, to_date: Optional[date] = None) -> list:
    """Daily OHLCV candles ``[(date_str, o, h, l, c, volume), ...]`` ascending.

    Delegates to the default market-data provider (Groww historical → yfinance
    fallback). Defaults to ~400 days so the 52-week high is derivable from the
    same candles. Empty list on failure.
    """
    return _get_provider().get_ohlcv(symbol, days=days, to_date=to_date)


def option_chain_signals(symbol: str) -> dict:
    """Per-stock PCR and unusual-Call-OI flag from the Groww option chain.
    Returns ``{"pcr": float|None, "unusual_call_oi": bool}``."""
    return _get_provider().get_option_chain(symbol)


def nifty_change_pct(to_date: Optional[date] = None) -> Optional[float]:
    """Nifty 50 (^NSEI) percent change on the latest session. None on failure.

    ^NSEI is an index ticker (no .NS suffix), so it's fetched directly rather
    than through fetch_history."""
    import yfinance as yf

    end = to_date or date.today()
    start = end - timedelta(days=10)  # buffer for weekends/holidays
    try:
        df = yf.download("^NSEI", start=start.strftime("%Y-%m-%d"),
                         end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df.empty:
            return None
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        closes = [float(c) for c in df["Close"].tolist()]
    except Exception as e:
        logger.warning("Nifty fetch failed: %s", e)
        return None

    if len(closes) < 2 or closes[-2] == 0:
        return None
    return round((closes[-1] - closes[-2]) / closes[-2] * 100.0, 2)


# ── Fragile NSE web endpoints (cookie-gated, best-effort: S1 + N4 only) ───────

def _nse_session() -> Optional[requests.Session]:
    """Prime an NSE session with cookies (NSE rejects API calls without them)."""
    s = nse_session()
    try:
        s.get(_NSE_BASE, timeout=10)
        return s
    except Exception as e:
        logger.warning("NSE session priming failed: %s", e)
        return None


def board_meetings_tomorrow(for_date: Optional[date] = None) -> set[str]:
    """Symbols with a board meeting on the next calendar day (NSE corporate
    actions). Best-effort: empty set on any failure. The endpoint returns a
    window; we filter to from_date == to_date == tomorrow."""
    ref = for_date or date.today()
    tomorrow = (ref + timedelta(days=1)).strftime("%d-%m-%Y")
    s = _nse_session()
    if s is None:
        return set()
    url = f"{_NSE_BASE}/api/corporate-board-meetings"
    try:
        resp = s.get(url, params={"index": "equities", "from_date": tomorrow, "to_date": tomorrow}, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, dict):
            rows = rows.get("data", [])
        out = set()
        for r in rows or []:
            sym = (r.get("symbol") or r.get("bm_symbol") or "").strip().upper()
            if sym:
                out.add(sym)
        logger.info("Board meetings tomorrow: %d symbols", len(out))
        return out
    except Exception as e:
        logger.warning("Board-meetings fetch failed: %s", e)
        return set()


def asm_gsm_symbols() -> set[str]:
    """Symbols under NSE ASM or GSM surveillance. Best-effort; empty on failure."""
    s = _nse_session()
    if s is None:
        return set()
    out: set[str] = set()
    for url in (f"{_NSE_BASE}/api/reportASM", f"{_NSE_BASE}/api/reportGSM"):
        try:
            resp = s.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("data", data) if isinstance(data, dict) else data
            for r in rows or []:
                sym = (r.get("symbol") or "").strip().upper()
                if sym:
                    out.add(sym)
        except Exception as e:
            logger.warning("ASM/GSM fetch failed (%s): %s", url, e)
    logger.info("ASM/GSM surveillance: %d symbols", len(out))
    return out
