"""
Fundamentals enrichment via yfinance (free, NSE .NS suffix). No LLM calls.

Per stock it adds (only when not already populated, so the Screener path keeps
precedence): pe_ratio, forward_pe, market_cap_cr, sector, volume_ratio, and
earnings dates (next_earnings_date / last_earnings_date / days_to_earnings) so
earnings_proximity can be data-driven instead of guessed from headlines.

After the per-stock pass it computes sector_pe as the median trailing PE of the
stocks within each sector in the current universe.
"""

import logging
import statistics
import time
from datetime import date
from typing import Optional

import yfinance as yf
from config import SETTINGS

logger = logging.getLogger(__name__)

_DELAY = 0.2  # seconds between yfinance calls (politeness; yfinance is free)


def _to_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _earnings_dates(tk, ref: date) -> dict:
    """Next/last earnings dates (ISO) and signed days_to_earnings (negative = days
    since a just-reported result, positive = days until the next one)."""
    out = {"next_earnings_date": None, "last_earnings_date": None, "days_to_earnings": None}

    dates: list[date] = []
    try:
        df = tk.get_earnings_dates(limit=8)
        if df is not None and not df.empty:
            for ts in df.index:
                try:
                    dates.append(ts.date())
                except Exception:
                    continue
    except Exception:
        pass

    if not dates:
        try:
            cal = tk.calendar
            raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if raw:
                for d in (raw if isinstance(raw, (list, tuple)) else [raw]):
                    try:
                        dates.append(d if isinstance(d, date) else d.date())
                    except Exception:
                        continue
        except Exception:
            pass

    if not dates:
        return out

    past = [d for d in dates if d <= ref]
    future = [d for d in dates if d > ref]
    if future:
        out["next_earnings_date"] = min(future).isoformat()
    if past:
        out["last_earnings_date"] = max(past).isoformat()
    nearest = min(dates, key=lambda d: abs((d - ref).days))
    out["days_to_earnings"] = (nearest - ref).days
    return out


def _assign_sector_pe(stocks: list[dict]) -> None:
    """Fill sector_pe with the median trailing PE within each sector."""
    by_sector: dict[str, list[float]] = {}
    for s in stocks:
        sec = s.get("sector")
        pe = _to_float(s.get("pe_ratio"))
        if sec and pe and pe > 0:
            by_sector.setdefault(sec, []).append(pe)
    medians = {sec: round(statistics.median(vals), 2) for sec, vals in by_sector.items()}
    for s in stocks:
        sec = s.get("sector")
        if s.get("sector_pe") is None and sec in medians:
            s["sector_pe"] = medians[sec]


def enrich_fundamentals(stocks: list[dict], ref_date: Optional[date] = None) -> list[dict]:
    """Enrich stocks with yfinance fundamentals + earnings dates, then sector PE.

    PIT contamination guard: yfinance ``.info`` (PE/forwardPE/marketCap/sector) is
    NOT point-in-time — it returns *today's* values regardless of ``ref_date``. So a
    historical re-run (``--date <past>``) scores the past with future knowledge
    (look-ahead bias). When ``ref_date`` is in the past we log a loud warning and
    stamp every stock ``pit_safe=False`` so the output self-documents as
    not-for-validation. See docs/plans/pit-contamination-guard.md.
    """
    ref = ref_date or date.today()
    pit_safe = ref >= date.today()
    if not pit_safe:
        logger.warning(
            "Historical re-run (ref_date=%s < today): fundamentals/news are NOT "
            "point-in-time — scores are look-ahead-biased, do NOT use for validation",
            ref.isoformat(),
        )
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = getattr(SETTINGS, "FUNDAMENTALS_WORKERS", 8)

    def _enrich_one(indexed_stock: tuple[int, dict]) -> tuple[int, dict]:
        idx, stock = indexed_stock
        sym = stock["symbol"]
        logger.info("Fundamentals %d/%d: %s", idx + 1, len(stocks), sym)
        stock = dict(stock)

        info, tk = {}, None
        try:
            tk = yf.Ticker(f"{sym.upper()}.NS")
            info = tk.info or {}
        except Exception as e:
            logger.warning("Fundamentals fetch failed for %s: %s", sym, e)

        pe = _to_float(info.get("trailingPE"))
        fpe = _to_float(info.get("forwardPE"))
        mcap = _to_float(info.get("marketCap"))
        sector = info.get("sector") or stock.get("sector")
        avg_vol = _to_float(info.get("averageVolume")) or _to_float(info.get("averageVolume10days"))

        if stock.get("pe_ratio") is None and pe is not None:
            stock["pe_ratio"] = round(pe, 2)
        if fpe is not None:
            stock["forward_pe"] = round(fpe, 2)
        if stock.get("market_cap_cr") is None and mcap is not None:
            stock["market_cap_cr"] = round(mcap / 1e7, 2)
        if sector:
            stock["sector"] = sector

        cur_vol = _to_float(stock.get("groww_volume") or stock.get("bhavcopy_volume"))
        if stock.get("volume_ratio") is None and cur_vol and avg_vol:
            stock["volume_ratio"] = round(cur_vol / avg_vol, 2)

        if tk is not None:
            stock.update(_earnings_dates(tk, ref))

        stock["pit_safe"] = pit_safe
        return idx, stock

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, (i, s)): i for i, s in enumerate(stocks)}
        for fut in as_completed(futures):
            idx, stock = fut.result()
            results[idx] = stock

    enriched = [results[i] for i in range(len(stocks))]
    _assign_sector_pe(enriched)
    return enriched
