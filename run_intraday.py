"""Intraday prediction entrypoint — day-before next-day watchlist.

    python run_intraday.py [--date YYYY-MM-DD] [--dry-run] [--no-telegram]

Run in the evening after NSE close. Scores the universe on the S1–S10 / N1–N7
signal framework, writes output/YYYY-MM-DD/intraday_watchlist.{json,txt}, and
pushes the watchlist to Telegram. Parallel to main.py / run_agents.py.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import config  # noqa: F401  (import builds SETTINGS / loads .env)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_intraday")


def parse_args():
    p = argparse.ArgumentParser(description="Intraday prediction — next-day watchlist")
    p.add_argument("--date", help="Reference trading date (YYYY-MM-DD); defaults to today")
    p.add_argument("--dry-run", action="store_true", help="Limit to DRY_RUN_STOCK_COUNT stocks")
    p.add_argument("--no-telegram", action="store_true", help="Skip the Telegram push")
    return p.parse_args()


def main():
    args = parse_args()

    from intraday.pipeline import run_pipeline
    from intraday.report import build_alert, write_watchlist
    from intraday import data_sources

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info("=== Intraday run | date=%s dry_run=%s ===", report_date.isoformat(), args.dry_run)

    watchlist = run_pipeline(report_date, dry_run=args.dry_run)
    json_path = write_watchlist(watchlist, report_date)

    nifty_chg = data_sources.nifty_change_pct(report_date)
    alert = build_alert(watchlist, report_date, nifty_chg)
    plain = alert.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    logger.info("Intraday alert:\n%s", plain)

    if not args.no_telegram:
        from notifications.telegram_notifier import send_intraday_watchlist
        send_intraday_watchlist(alert)

    logger.info("=== Intraday run done → %d picks (%s) ===", len(watchlist), json_path)


if __name__ == "__main__":
    main()
