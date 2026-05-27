"""Intraday pipeline — score the universe and build the next-day watchlist.

Run in the evening after NSE close. Shared/bulk data (board meetings, ASM/GSM,
Nifty move, 52-week highs) is fetched once; per-stock data (OHLC history, option
chain) is fetched in a loop. Each stock is scored by ``signals.score_stock``;
the watchlist is everything scoring ≥ threshold, sorted descending, capped at
INTRADAY_TOP_N.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config import SETTINGS

from . import data_sources, signals, technicals

logger = logging.getLogger(__name__)


def _load_universe() -> list[dict]:
    """Stock universe for scoring. Uses the configured NSE index; the Screener
    path isn't intraday-relevant, so it falls back to nifty200."""
    from scrapers.nse_index import fetch_index_stocks

    universe = SETTINGS.STOCK_UNIVERSE
    if universe not in ("nifty50", "nifty100", "nifty200", "nifty500"):
        logger.info("STOCK_UNIVERSE=%s not an index — using nifty200 for intraday", universe)
        universe = "nifty200"
    return fetch_index_stocks(universe)


def run_pipeline(report_date: Optional[date] = None, dry_run: bool = False) -> list[dict]:
    """Score the universe and return the ranked watchlist (score ≥ threshold)."""
    ref = report_date or date.today()
    stocks = _load_universe()
    if dry_run:
        stocks = stocks[: SETTINGS.DRY_RUN_STOCK_COUNT]
    logger.info("Intraday scoring %d stocks for %s", len(stocks), ref.isoformat())

    # ── Shared / bulk data (fetched once) ────────────────────────────────
    board_syms = data_sources.board_meetings_tomorrow(ref)
    asm_gsm = data_sources.asm_gsm_symbols()
    nifty_chg = data_sources.nifty_change_pct(ref)

    scored: list[dict] = []
    for i, stock in enumerate(stocks):
        sym = stock["symbol"]
        logger.info("Scoring %d/%d: %s", i + 1, len(stocks), sym)

        candles = data_sources.fetch_history(sym, days=SETTINGS.INTRADAY_HISTORY_DAYS, to_date=ref)
        metrics = technicals.compute_metrics(candles)
        oi = data_sources.option_chain_signals(sym)

        ctx = {
            "symbol": sym,
            "sector": stock.get("sector"),
            # Tier A — technicals
            "close": metrics["close"],
            "today_change_pct": metrics["today_change_pct"],
            "change_3d_pct": metrics["change_3d_pct"],
            "rsi14": metrics["rsi14"],
            "high_20d": metrics["high_20d"],
            "volume_today": metrics["volume_today"],
            "avg_volume_20d": metrics["avg_volume_20d"],
            "high_52w": metrics["high_52w"],
            "nifty_change_pct": nifty_chg,
            # Tier B — best-effort NSE web
            "has_board_meeting_tomorrow": sym in board_syms,
            "in_asm_gsm": sym in asm_gsm,
            "pcr": oi["pcr"],
            "unusual_call_oi": oi["unusual_call_oi"],
        }

        result = signals.score_stock(ctx)
        result["company"] = stock.get("company", "")
        result["sector"] = stock.get("sector")
        result["close"] = metrics["close"]
        result["conviction"] = signals.conviction(result["score"], SETTINGS.INTRADAY_HIGH_CONVICTION)
        scored.append(result)

    watchlist = [r for r in scored if r["score"] >= SETTINGS.INTRADAY_SCORE_THRESHOLD]
    watchlist.sort(key=lambda r: r["score"], reverse=True)
    watchlist = watchlist[: SETTINGS.INTRADAY_TOP_N]
    logger.info("Watchlist: %d stocks ≥ %d (from %d scored)",
                len(watchlist), SETTINGS.INTRADAY_SCORE_THRESHOLD, len(scored))
    return watchlist
