"""Stage functions for the legacy main.py pipeline (one responsibility each).

Each stage takes explicit inputs and returns explicit outputs — no reaching into
argparse namespaces or module globals — so main.py reads as a thin orchestrator.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

from config import SETTINGS

logger = logging.getLogger("pipeline")


def load_universe(report_date: date, dry_run: bool) -> tuple[list[dict], int, dict, Path]:
    """Stage 1: fetch the universe, apply the skip list, cap by market cap.

    Returns (stocks, total_screened, skip_list, skip_list_path). Exits the
    process if the universe is empty.
    """
    universe = SETTINGS.STOCK_UNIVERSE.lower()
    logger.info("STAGE 1: Stock universe fetch (source=%s)", universe)

    skip_list_path = Path(SETTINGS.OUTPUT_DIR) / "skip_list.json"
    skip_list_path.parent.mkdir(parents=True, exist_ok=True)
    skip_list: dict = {}
    if skip_list_path.exists():
        skip_list = json.loads(skip_list_path.read_text())
        logger.info("Skip list loaded: %d symbols will be excluded", len(skip_list))

    if universe == "screener":
        from scrapers.screener_scraper import ScreenerScraper
        stocks = ScreenerScraper().fetch_screen()
        before = len(stocks)
        stocks = [s for s in stocks if not s["symbol"].isdigit()]
        if len(stocks) < before:
            logger.info("Dropped %d BSE-only (numeric) symbols — %d remain", before - len(stocks), len(stocks))
    else:
        from scrapers.nse_index import fetch_index_stocks
        stocks = fetch_index_stocks(universe)

    if skip_list:
        before = len(stocks)
        stocks = [s for s in stocks if s["symbol"] not in skip_list]
        logger.info("Skipped %d symbols from skip list — %d remain", before - len(stocks), len(stocks))

    logger.info("Stage 1 complete: %d stocks in universe", len(stocks))
    total_screened = len(stocks)

    if len(stocks) > SETTINGS.MAX_STOCKS_TO_SCORE:
        stocks = sorted(
            stocks,
            key=lambda s: float(s.get("market_cap_cr") or 0),
            reverse=True,
        )[:SETTINGS.MAX_STOCKS_TO_SCORE]
        logger.info("Capped to top %d stocks by market cap (%d total passed screen)", len(stocks), total_screened)

    if dry_run:
        stocks = stocks[:SETTINGS.DRY_RUN_STOCK_COUNT]
        logger.info("DRY RUN: limiting to %d stocks", len(stocks))

    if not stocks:
        logger.error("No stocks returned from universe — aborting")
        sys.exit(1)

    return stocks, total_screened, skip_list, skip_list_path


def enrich_nse(stocks: list[dict], report_date: date) -> list[dict]:
    """Stage 2: merge NSE bhavcopy (delivery %, 52w range) + bulk deals."""
    logger.info("STAGE 2: NSE Bhavcopy + Bulk Deals")
    from scrapers.nse_bhavcopy import download_bhavcopy
    from scrapers.nse_bulk_deals import download_bulk_deals, group_bulk_deals_by_symbol

    try:
        bhavcopy = download_bhavcopy(report_date)
    except FileNotFoundError as e:
        logger.warning("Bhavcopy unavailable: %s — proceeding without delivery data", e)
        bhavcopy = None

    try:
        bulk_df = download_bulk_deals(report_date)
        bulk_map = group_bulk_deals_by_symbol(bulk_df)
    except Exception as e:
        logger.warning("Bulk deals fetch failed: %s — proceeding without", e)
        bulk_map = {}

    for stock in stocks:
        sym = stock["symbol"]
        if bhavcopy is not None and sym in bhavcopy.index:
            row = bhavcopy.loc[sym]
            stock.setdefault("delivery_pct", row.get("delivery_pct"))
            stock.setdefault("52w_high", row.get("52w_high"))
            stock.setdefault("52w_low", row.get("52w_low"))
            stock["bhavcopy_volume"] = row.get("volume")
            stock["bhavcopy_close"] = row.get("close")
        stock["bulk_deals"] = bulk_map.get(sym, [])

    logger.info("Stage 2 complete")
    return stocks


def enrich_market_and_fundamentals(stocks: list[dict], report_date: date,
                                    skip_list: dict, skip_list_path: Path) -> list[dict]:
    """Stage 3: market-data enrichment (provider) + skip-list update + fundamentals."""
    logger.info("STAGE 3: Market-data enrichment (%d stocks)", len(stocks))
    from enrichment.market_data import enrich_stocks
    stocks = enrich_stocks(stocks)

    no_data = [s["symbol"] for s in stocks if s.get("no_data")]
    if no_data:
        for sym in no_data:
            skip_list[sym] = str(report_date)
        skip_list_path.write_text(json.dumps(skip_list, indent=2))
        logger.warning("Added %d symbols to skip list (no price data): %s", len(no_data), no_data)

    stocks = [s for s in stocks if not s.get("no_data")]
    logger.info("Stage 3 complete: %d stocks with valid price data", len(stocks))

    from enrichment.fundamentals import enrich_fundamentals
    return enrich_fundamentals(stocks, ref_date=report_date)


def fetch_news_and_macro(stocks: list[dict]) -> tuple[dict, str, dict]:
    """Stage 4: per-stock news (RSS) + Gemini macro/sector context."""
    logger.info("STAGE 4: News fetch (RSS) + macro context (Gemini)")
    from enrichment.news_fetcher import fetch_macro_context, fetch_news_batch
    news_map = fetch_news_batch(stocks)
    sectors = sorted({s.get("sector") for s in stocks if s.get("sector")})
    macro_context, sector_macro_map = fetch_macro_context(sectors)
    logger.info("Stage 4 complete: news fetched for %d stocks", len(news_map))
    return news_map, macro_context, sector_macro_map


def score_and_rank(stocks: list[dict], news_map: dict, macro_context: str,
                   sector_map: dict) -> tuple[list[dict], list[dict]]:
    """Stages 5-6: Claude Haiku scoring + weighted ranking.

    Returns (top_stocks, all_scores). Exits if nothing scored.
    """
    logger.info("STAGE 5: Claude Haiku scoring (Batch API)")
    from scoring.claude_scorer import score_stocks
    all_scores = score_stocks(stocks, news_map, macro_context, sector_map)
    logger.info("Stage 5 complete: %d stocks scored", len(all_scores))

    if not all_scores:
        logger.error("No scored stocks — aborting report generation")
        sys.exit(1)

    logger.info("STAGE 6: Rank + report generation")
    from scoring.ranker import rank_stocks
    top_stocks = rank_stocks(all_scores)
    return top_stocks, all_scores


def run_backtest_stage(report_date: date) -> dict | None:
    """Stage 7: backtest the previous trading day's signals. None if no prior scores."""
    from datetime import timedelta

    logger.info("STAGE 7: Backtest calculation")
    from backtest.engine import append_backtest_log, is_trading_day, run_backtest
    from backtest.reporter import latest_backtest_summary

    prev_trading_day = report_date - timedelta(days=1)
    while not is_trading_day(prev_trading_day):
        prev_trading_day -= timedelta(days=1)

    bt_result = run_backtest(prev_trading_day)
    if bt_result:
        append_backtest_log(bt_result)
        logger.info("Stage 7 complete: backtest appended")
        return latest_backtest_summary()
    logger.info("Stage 7: no previous scores found for %s", prev_trading_day)
    return None
