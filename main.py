"""
NSE/BSE Stock Intelligence System — Pipeline Orchestrator
Run: python main.py [--dry-run] [--date YYYY-MM-DD] [--skip-backtest]
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def parse_args():
    p = argparse.ArgumentParser(description="NSE/BSE Stock Intelligence Pipeline")
    p.add_argument("--dry-run", action="store_true", help=f"Run on {__import__('config').DRY_RUN_STOCK_COUNT} stocks only")
    p.add_argument("--date", help="Override trading date (YYYY-MM-DD); defaults to today")
    p.add_argument("--skip-backtest", action="store_true", help="Skip backtest calculation")
    p.add_argument("--skip-narrative", action="store_true", help="Skip Sonnet narrative (saves cost)")
    return p.parse_args()


def main():
    args = parse_args()
    import config

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info("=== Stock Intelligence Pipeline starting | date=%s dry_run=%s ===", report_date, args.dry_run)

    # ── Stage 1: Stock universe fetch ─────────────────────────────────────────
    universe = config.STOCK_UNIVERSE.lower()
    logger.info("STAGE 1: Stock universe fetch (source=%s)", universe)

    # Load persistent skip list (stocks with no price data on previous runs)
    import json as _json
    skip_list_path = Path(config.OUTPUT_DIR) / "skip_list.json"
    skip_list_path.parent.mkdir(parents=True, exist_ok=True)
    skip_list: dict = {}
    if skip_list_path.exists():
        skip_list = _json.loads(skip_list_path.read_text())
        logger.info("Skip list loaded: %d symbols will be excluded", len(skip_list))

    if universe == "screener":
        from scrapers.screener_scraper import ScreenerScraper
        stocks = ScreenerScraper().fetch_screen()
        # Drop BSE-only stocks (purely numeric symbols e.g. 530929)
        before = len(stocks)
        stocks = [s for s in stocks if not s["symbol"].isdigit()]
        if len(stocks) < before:
            logger.info("Dropped %d BSE-only (numeric) symbols — %d remain", before - len(stocks), len(stocks))
    else:
        from scrapers.nse_index import fetch_index_stocks
        stocks = fetch_index_stocks(universe)

    # Apply skip list
    if skip_list:
        before = len(stocks)
        stocks = [s for s in stocks if s["symbol"] not in skip_list]
        logger.info("Skipped %d symbols from skip list — %d remain", before - len(stocks), len(stocks))

    logger.info("Stage 1 complete: %d stocks in universe", len(stocks))

    total_screened = len(stocks)

    # Cap to top MAX_STOCKS_TO_SCORE by market cap before expensive enrichment
    if len(stocks) > config.MAX_STOCKS_TO_SCORE:
        stocks = sorted(
            stocks,
            key=lambda s: float(s.get("market_cap_cr") or 0),
            reverse=True,
        )[:config.MAX_STOCKS_TO_SCORE]
        logger.info("Capped to top %d stocks by market cap (%d total passed screen)", len(stocks), total_screened)

    if args.dry_run:
        stocks = stocks[:config.DRY_RUN_STOCK_COUNT]
        logger.info("DRY RUN: limiting to %d stocks", len(stocks))

    if not stocks:
        logger.error("No stocks returned from Screener.in — aborting")
        sys.exit(1)

    # ── Stage 2: NSE Bhavcopy + Bulk Deals ───────────────────────────────────
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

    # Merge bhavcopy data into stock dicts
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

    # ── Stage 3: Groww API enrichment ────────────────────────────────────────
    logger.info("STAGE 3: Groww API enrichment (%d stocks)", len(stocks))
    from enrichment.groww_client import enrich_stocks
    stocks = enrich_stocks(stocks)

    # Save stocks with no price data to skip list for future runs
    no_data = [s["symbol"] for s in stocks if s.get("no_data")]
    if no_data:
        for sym in no_data:
            skip_list[sym] = str(report_date)
        skip_list_path.write_text(_json.dumps(skip_list, indent=2))
        logger.warning("Added %d symbols to skip list (no price data): %s", len(no_data), no_data)

    stocks = [s for s in stocks if not s.get("no_data")]
    logger.info("Stage 3 complete: %d stocks with valid price data", len(stocks))

    # Fundamentals (PE / sector / market cap / volume ratio / earnings dates) via yfinance — free
    from enrichment.fundamentals import enrich_fundamentals
    stocks = enrich_fundamentals(stocks, ref_date=report_date)

    # ── Stage 4: News fetch + macro context ──────────────────────────────────
    logger.info("STAGE 4: News fetch (RSS) + macro context (Gemini)")
    from enrichment.news_fetcher import fetch_news_batch, fetch_macro_context
    news_map = fetch_news_batch(stocks)
    sectors = sorted({s.get("sector") for s in stocks if s.get("sector")})
    macro_context, sector_macro_map = fetch_macro_context(sectors)
    logger.info("Stage 4 complete: news fetched for %d stocks", len(news_map))

    # ── Stage 5: Claude Haiku Batch API scoring ───────────────────────────────
    logger.info("STAGE 5: Claude Haiku scoring (Batch API)")
    from scoring.claude_scorer import score_stocks
    all_scores = score_stocks(stocks, news_map, macro_context, sector_macro_map)
    logger.info("Stage 5 complete: %d stocks scored", len(all_scores))

    if not all_scores:
        logger.error("No scored stocks — aborting report generation")
        sys.exit(1)

    # ── Stage 6: Rank + report ────────────────────────────────────────────────
    logger.info("STAGE 6: Rank + report generation")
    from scoring.ranker import rank_stocks
    top_stocks = rank_stocks(all_scores)

    backtest_summary = None
    if not args.skip_backtest:
        # ── Stage 7: Backtest previous day's signals ──────────────────────────
        logger.info("STAGE 7: Backtest calculation")
        from backtest.engine import run_backtest, append_backtest_log, nth_trading_day, is_trading_day
        from backtest.reporter import latest_backtest_summary

        prev_trading_day = report_date - __import__("datetime").timedelta(days=1)
        while not is_trading_day(prev_trading_day):
            prev_trading_day -= __import__("datetime").timedelta(days=1)

        bt_result = run_backtest(prev_trading_day)
        if bt_result:
            append_backtest_log(bt_result)
            backtest_summary = latest_backtest_summary()
            logger.info("Stage 7 complete: backtest appended")
        else:
            logger.info("Stage 7: no previous scores found for %s", prev_trading_day)

    from reports.daily_report import write_report
    report_path = write_report(
        top_stocks=top_stocks,
        all_scores=all_scores,
        report_date=report_date,
        total_screened=total_screened,
        backtest_summary=backtest_summary,
        generate_narrative=not args.skip_narrative,
        macro_context=macro_context,
    )

    logger.info("=== Pipeline complete | report → %s ===", report_path)
    print(f"\nReport ready: {report_path}")

    # ── Telegram notification ─────────────────────────────────────────────────
    from notifications.telegram_notifier import send_report
    send_report(
        top_stocks=top_stocks,
        report_path=report_path,
        report_date=report_date.strftime("%Y-%m-%d"),
        macro_context=macro_context,
    )


if __name__ == "__main__":
    main()
