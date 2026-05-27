"""
NSE/BSE Stock Intelligence System — Pipeline Orchestrator
Run: python main.py [--dry-run] [--date YYYY-MM-DD] [--skip-backtest]
"""

import argparse
import logging
from datetime import date

import config
import pipeline_stages as stages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def parse_args():
    p = argparse.ArgumentParser(description="NSE/BSE Stock Intelligence Pipeline")
    p.add_argument("--dry-run", action="store_true",
                   help=f"Run on {config.DRY_RUN_STOCK_COUNT} stocks only")
    p.add_argument("--date", help="Override trading date (YYYY-MM-DD); defaults to today")
    p.add_argument("--skip-backtest", action="store_true", help="Skip backtest calculation")
    p.add_argument("--skip-narrative", action="store_true", help="Skip Sonnet narrative (saves cost)")
    return p.parse_args()


def main():
    args = parse_args()

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info("=== Stock Intelligence Pipeline starting | date=%s dry_run=%s ===", report_date, args.dry_run)

    stocks, total_screened, skip_list, skip_list_path = stages.load_universe(report_date, args.dry_run)
    stocks = stages.enrich_nse(stocks, report_date)
    stocks = stages.enrich_market_and_fundamentals(stocks, report_date, skip_list, skip_list_path)
    news_map, macro_context, sector_macro_map = stages.fetch_news_and_macro(stocks)
    top_stocks, all_scores = stages.score_and_rank(stocks, news_map, macro_context, sector_macro_map)

    backtest_summary = None
    if not args.skip_backtest:
        backtest_summary = stages.run_backtest_stage(report_date)

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

    from notifications.telegram_notifier import send_report
    send_report(
        top_stocks=top_stocks,
        report_path=report_path,
        report_date=report_date.strftime("%Y-%m-%d"),
        macro_context=macro_context,
    )


if __name__ == "__main__":
    main()
