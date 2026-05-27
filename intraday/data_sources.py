"""Data fetchers for the intraday scorer.

Tier A (reliable): OHLC history + Nifty move via yfinance, 52-week high via NSE
bhavcopy. Tier B (best-effort, fragile NSE web endpoints behind a cookie wall):
next-day board meetings, ASM/GSM surveillance list, per-stock PCR / Call-OI from
the option chain. Every fetcher returns an empty/None result on failure and logs
— a dead endpoint must never abort the run (the scorer treats missing data as a
0-point signal).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_NSE_BASE = "https://www.nseindia.com"


# ── Tier A: yfinance OHLC + Nifty ─────────────────────────────────────────────

def fetch_history(symbol: str, days: int = 90, to_date: Optional[date] = None) -> list[list]:
    """Daily OHLCV candles for an NSE ticker via yfinance.

    Returns ``[[date_str, open, high, low, close, volume], ...]`` ascending.
    Empty list on failure (so the scorer scores the stock on whatever else it has).
    """
    import yfinance as yf

    end = to_date or date.today()
    start = end - timedelta(days=days + 10)  # buffer for weekends/holidays
    ticker = f"{symbol.upper()}.NS"
    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True,
        )
        if df.empty:
            return []
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        candles = [
            [str(dt.date()), float(r["Open"]), float(r["High"]),
             float(r["Low"]), float(r["Close"]), float(r["Volume"])]
            for dt, r in df.iterrows()
        ]
        return sorted(candles, key=lambda c: c[0])
    except Exception as e:
        logger.warning("History fetch failed for %s: %s", symbol, e)
        return []


def nifty_change_pct(to_date: Optional[date] = None) -> Optional[float]:
    """Nifty 50 (^NSEI) percent change on the latest session. None on failure."""
    candles = fetch_history("^NSEI", days=10, to_date=to_date)
    # ^NSEI is an index, not an .NS equity — fetch directly.
    if not candles:
        import yfinance as yf
        try:
            df = yf.download("^NSEI", period="5d", progress=False, auto_adjust=True)
            if df.empty:
                return None
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            closes = [float(c) for c in df["Close"].tolist()]
        except Exception as e:
            logger.warning("Nifty fetch failed: %s", e)
            return None
    else:
        closes = [c[4] for c in candles]
    if len(closes) < 2 or closes[-2] == 0:
        return None
    return round((closes[-1] - closes[-2]) / closes[-2] * 100.0, 2)


def fetch_52w_highs(for_date: Optional[date] = None) -> dict[str, float]:
    """52-week high per symbol from the NSE bhavcopy (one bulk download).
    Returns {} on failure."""
    try:
        from scrapers.nse_bhavcopy import download_bhavcopy
        df = download_bhavcopy(for_date)
        if "52w_high" not in df.columns:
            return {}
        return {sym: float(v) for sym, v in df["52w_high"].dropna().items()}
    except Exception as e:
        logger.warning("Bhavcopy 52w-high fetch failed: %s", e)
        return {}


# ── Tier B: NSE web endpoints (cookie-gated, best-effort) ─────────────────────

def _nse_session() -> Optional[requests.Session]:
    """Prime an NSE session with cookies (NSE rejects API calls without them)."""
    s = requests.Session()
    s.headers.update(_BROWSER_HEADERS)
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


def option_chain_signals(symbol: str, session: Optional[requests.Session] = None) -> dict:
    """Per-stock PCR and unusual-Call-OI flag from the NSE equity option chain.

    Returns {"pcr": float|None, "unusual_call_oi": bool}. PCR is total put OI /
    total call OI. F&O stocks only — non-F&O symbols return {pcr: None,
    unusual_call_oi: False}, which the scorer simply skips.
    """
    s = session or _nse_session()
    if s is None:
        return {"pcr": None, "unusual_call_oi": False}
    url = f"{_NSE_BASE}/api/option-chain-equities"
    try:
        resp = s.get(url, params={"symbol": symbol.upper()}, timeout=10)
        resp.raise_for_status()
        records = resp.json().get("records", {})
        data = records.get("data", [])
        tot_call_oi = tot_put_oi = 0
        call_oi_by_strike = []
        for row in data:
            ce, pe = row.get("CE"), row.get("PE")
            if ce:
                oi = ce.get("openInterest", 0) or 0
                tot_call_oi += oi
                call_oi_by_strike.append(oi)
            if pe:
                tot_put_oi += pe.get("openInterest", 0) or 0
        pcr = round(tot_put_oi / tot_call_oi, 2) if tot_call_oi else None
        # "Unusual" heuristic: the top-3 call strikes hold > 40% of all call OI.
        unusual = False
        if call_oi_by_strike and tot_call_oi:
            top3 = sum(sorted(call_oi_by_strike, reverse=True)[:3])
            unusual = top3 / tot_call_oi > 0.40
        return {"pcr": pcr, "unusual_call_oi": unusual}
    except Exception as e:
        logger.warning("Option-chain fetch failed for %s: %s", symbol, e)
        return {"pcr": None, "unusual_call_oi": False}
