"""Finalize — Stage 7 backtest + report (scores.json/report.html) + Telegram.

Mirrors the tail of main.py so ``run_agents.py --mode research`` produces the
same artifacts as ``python main.py``. Always runs (even after a terminal status)
so a halted/failed run still emits whatever it has.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from config import SETTINGS

from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus, TERMINAL_STATUSES
from observability import metrics

logger = logging.getLogger("agents.finalize")


def _run_backtest(report_date: date):
    from backtest.engine import append_backtest_log, is_trading_day, run_backtest
    from backtest.reporter import latest_backtest_summary

    prev = report_date - timedelta(days=1)
    while not is_trading_day(prev):
        prev -= timedelta(days=1)
    result = run_backtest(prev)
    if result:
        append_backtest_log(result)
        return latest_backtest_summary()
    return None


@agent_node("finalize")
def finalize_node(state: AgentState) -> dict:
    ranking = state.get("ranking")
    if ranking is None:
        logger.warning("No ranking — nothing to report")
        status = state.get("status")
        return {"status": status if status in TERMINAL_STATUSES else RunStatus.COMPLETED}

    report_date = date.fromisoformat(state["report_date"])
    skip_narrative = state.get("dry_run", False)

    backtest_summary = None
    try:
        backtest_summary = _run_backtest(report_date)
    except Exception as e:
        logger.warning("Backtest skipped: %s", e)

    from reports.daily_report import write_report

    report_path = write_report(
        top_stocks=ranking.legacy_top(),
        all_scores=ranking.legacy_all(),
        report_date=report_date,
        total_screened=state.get("total_screened", 0),
        backtest_summary=backtest_summary,
        generate_narrative=not skip_narrative,
        macro_context=(state.get("enriched").macro_context if state.get("enriched") else ""),
    )

    if getattr(SETTINGS, "ENABLE_CHAT_AGENT", False):
        try:
            from persistence import store

            enriched = state.get("enriched")
            rows = store.build_snapshot_rows(
                enriched.legacy_stocks() if enriched else [],
                ranking.legacy_all(),
                enriched.news_map if enriched else {},
            )
            store.save_daily_snapshot(state["report_date"], rows)
        except Exception as e:
            logger.warning("Snapshot save failed: %s", e)

    try:
        from notifications.telegram_notifier import send_report

        send_report(
            top_stocks=ranking.legacy_top(),
            report_path=Path(report_path),
            report_date=report_date.strftime("%Y-%m-%d"),
            macro_context=(state.get("enriched").macro_context if state.get("enriched") else ""),
        )
    except Exception as e:
        logger.warning("Telegram delivery failed: %s", e)

    metrics.set_run_cost(state.get("cost_usd", 0.0), state.get("tokens", 0))

    final = state.get("status")
    final = final if final in TERMINAL_STATUSES else RunStatus.COMPLETED
    logger.info("Finalize complete → %s | report=%s", getattr(final, "value", final), report_path)
    return {"status": final, "report_path": str(report_path)}
