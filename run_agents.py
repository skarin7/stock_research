"""Multi-agent entrypoint (parallel to main.py).

    python run_agents.py --mode research|paper|live [--dry-run] [--date YYYY-MM-DD]
    python run_agents.py --mode watch               # merged monitor + pulse watcher
    python run_agents.py --mode intraday            # evening next-day watchlist scorer
    python run_agents.py --resume <run_id> --approve <id> [--reject <id>]
    python run_agents.py --kill | --unkill          # toggle the kill-switch flag

In research mode this reproduces main.py's report + Telegram output, but routed
through the LangGraph orchestrator (research → analyst → finalize → memory).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

from observability.logging_config import setup_logging
setup_logging()
logger = logging.getLogger("run_agents")


def parse_args():
    p = argparse.ArgumentParser(description="Stock Intelligence — multi-agent runner")
    p.add_argument("--mode", choices=["research", "paper", "live", "watch", "intraday"], default=None,
                   help="Run mode")
    p.add_argument("--date", help="Trading date (YYYY-MM-DD); defaults to today")
    p.add_argument("--dry-run", action="store_true", help="Limit to DRY_RUN_STOCK_COUNT stocks")
    p.add_argument("--no-telegram", action="store_true", help="Skip Telegram notification")
    p.add_argument("--kill", action="store_true", help="Engage the kill-switch and exit")
    p.add_argument("--unkill", action="store_true", help="Clear the kill-switch and exit")
    # Resume a run suspended at the trade-approval interrupt (needs DATABASE_URL).
    p.add_argument("--resume", metavar="RUN_ID", help="Resume a run awaiting trade approval")
    p.add_argument("--approve", action="append", default=None, metavar="PROPOSAL_ID",
                   help="Approve a proposal id (repeatable); used with --resume")
    p.add_argument("--reject", action="append", default=None, metavar="PROPOSAL_ID",
                   help="Reject a proposal id (repeatable); used with --resume")
    return p.parse_args()


def _toggle_kill(engage: bool) -> None:
    from config import SETTINGS

    flag = Path(SETTINGS.KILL_SWITCH_FILE)
    flag.parent.mkdir(parents=True, exist_ok=True)
    if engage:
        flag.write_text("engaged\n")
        logger.warning("Kill-switch ENGAGED → %s", flag)
    elif flag.exists():
        flag.unlink()
        logger.warning("Kill-switch cleared")
    else:
        logger.info("Kill-switch was not set")


def main():
    args = parse_args()

    if args.kill or args.unkill:
        _toggle_kill(args.kill)
        return

    if args.resume:
        _resume(args.resume, args.approve or [], args.reject or [])
        return

    from config import SETTINGS

    from agents.state import RunStatus
    from observability.metrics import start_metrics_server

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    run_id = f"{report_date.isoformat()}-{uuid.uuid4().hex[:8]}"

    # watch and intraday are short, scheduled flows — not the full pipeline.
    if args.mode == "watch":
        _watch(run_id, report_date)
        return

    if args.mode == "intraday":
        _intraday(run_id, report_date, args)
        return

    from persistence.db import init_db
    init_db()  # creates app tables (daily_snapshot, runs, etc.) if DATABASE_URL is set

    from agents.graph import build_graph
    logger.info("=== Agent run %s | trading_mode=%s dry_run=%s ===", run_id, SETTINGS.TRADING_MODE, args.dry_run)

    start_metrics_server()

    graph = build_graph()
    initial = {
        "run_id": run_id,
        "report_date": report_date.isoformat(),
        "mode": SETTINGS.TRADING_MODE,
        "dry_run": args.dry_run,
        "status": RunStatus.RUNNING,
        "cost_usd": 0.0,
        "tokens": 0,
        "debate_rounds": 0,
    }
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": SETTINGS.MAX_GRAPH_STEPS}

    final = graph.invoke(initial, cfg)

    # Suspended at the trade-approval interrupt? Notify the human and stop here.
    interrupts = final.get("__interrupt__")
    if interrupts:
        payload = getattr(interrupts[0], "value", interrupts[0])
        from agents.approval import send_approval_request

        send_approval_request(payload)
        logger.warning("=== Run %s AWAITING APPROVAL — %d proposal(s) ===",
                       run_id, len(payload.get("proposals", [])))
        if not SETTINGS.DATABASE_URL:
            logger.warning("DATABASE_URL unset: this suspended run is in-memory only and cannot be "
                           "resumed from a separate process. Set DATABASE_URL for live approvals.")
        logger.info("Awaiting approval. Resume with:\n"
                    "  python run_agents.py --resume %s --approve <proposal_id> [--reject <id>]", run_id)
        return

    import observability.metrics as metrics
    metrics.set_run_cost(final.get("cost_usd", 0.0) or 0.0, final.get("tokens", 0) or 0)
    metrics.push_metrics()

    status = final.get("status")
    logger.info("=== Run %s finished → %s ===", run_id, getattr(status, "value", status))
    if final.get("report_path"):
        logger.info("Report ready: %s", final["report_path"])
    if status not in (RunStatus.COMPLETED, RunStatus.AWAITING_APPROVAL):
        sys.exit(1)


def _market_open_ist() -> bool:
    """Return True if current IST time is within NSE market hours (09:15–15:30)."""
    try:
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now = _dt.datetime.now(ist).time()
    except ImportError:
        utc_now = _dt.datetime.now(_dt.timezone.utc)
        now = (utc_now + _dt.timedelta(hours=5, minutes=30)).time()
    return _dt.time(9, 15) <= now <= _dt.time(15, 30)


def _pre_open_ist() -> bool:
    """Return True if current IST time is before market open (i.e. before 09:15)."""
    try:
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now = _dt.datetime.now(ist).time()
    except ImportError:
        utc_now = _dt.datetime.now(_dt.timezone.utc)
        now = (utc_now + _dt.timedelta(hours=5, minutes=30)).time()
    return now < _dt.time(9, 15)


def _watch(run_id: str, report_date) -> None:
    """Merged monitor + pulse watcher. Calls node logic directly — no LangGraph graph."""
    from agents.state import RunStatus
    logger.info("=== Watch run %s ===", run_id)

    if not _market_open_ist() and not _pre_open_ist():
        logger.info("watch: outside market hours — no-op")
        return

    state = {
        "run_id": run_id,
        "report_date": str(report_date),
        "mode": "watch",
        "status": RunStatus.RUNNING,
        "cost_usd": 0.0,
        "tokens": 0,
    }

    if _market_open_ist():
        from agents.nodes.monitoring import monitoring_node
        result = monitoring_node(state)
        state.update(result)
        alerts = state.get("alerts") or []
        logger.info("watch(monitor): %d alert(s)", len(alerts))

    from agents.nodes.pulse import pulse_node
    result = pulse_node(state)
    state.update(result)
    alerts = state.get("alerts") or []
    logger.info("watch(pulse): %d alert(s)", len(alerts))

    from observability.metrics import push_metrics
    push_metrics(job="stock-intelligence-watch")


def _intraday(run_id: str, report_date, args) -> None:
    """Evening intraday scorer — builds and delivers next-day watchlist."""
    from intraday.pipeline import run_pipeline
    from intraday import data_sources
    import intraday.report as _report

    logger.info("=== Intraday run %s ===", run_id)
    from datetime import date as _date
    ref = _date.fromisoformat(str(report_date)) if isinstance(report_date, str) else report_date
    dry_run = getattr(args, "dry_run", False)
    watchlist = run_pipeline(report_date=ref, dry_run=dry_run)

    json_path = _report.write_watchlist(watchlist, ref)

    nifty_chg = data_sources.nifty_change_pct(ref)
    alert = _report.build_alert(watchlist, ref, nifty_chg)
    plain = alert.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    logger.info("Intraday alert:\n%s", plain)

    if not getattr(args, "no_telegram", False):
        from notifications.telegram_notifier import send_intraday_watchlist
        send_intraday_watchlist(alert)

    logger.info("=== Intraday done: %d items (%s) ===", len(watchlist), json_path)


def _resume(run_id: str, approve: list, reject: list) -> None:
    from config import SETTINGS

    from agents.approval import decisions_from_lists, resume_run
    from agents.state import RunStatus

    decisions = decisions_from_lists(approve, reject)
    if not decisions:
        logger.error("No --approve/--reject ids given — nothing to resume")
        sys.exit(1)
    final = resume_run(run_id, decisions)
    status = final.get("status")
    logger.info("=== Run %s resumed → %s ===", run_id, getattr(status, "value", status))
    if status not in (RunStatus.COMPLETED, RunStatus.AWAITING_APPROVAL):
        sys.exit(1)


if __name__ == "__main__":
    main()
