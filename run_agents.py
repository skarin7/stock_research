"""Multi-agent entrypoint (parallel to main.py).

    python run_agents.py --mode research [--dry-run] [--date YYYY-MM-DD]
    python run_agents.py --kill        # create the kill-switch flag
    python run_agents.py --unkill      # remove it

In research mode this reproduces main.py's report + Telegram output, but routed
through the LangGraph orchestrator (research → analyst → finalize).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_agents")


def parse_args():
    p = argparse.ArgumentParser(description="Stock Intelligence — multi-agent runner")
    p.add_argument("--mode", choices=["research", "paper", "live"], default=None,
                   help="Override AGENT_MODE")
    p.add_argument("--date", help="Trading date (YYYY-MM-DD); defaults to today")
    p.add_argument("--dry-run", action="store_true", help="Limit to DRY_RUN_STOCK_COUNT stocks")
    p.add_argument("--kill", action="store_true", help="Engage the kill-switch and exit")
    p.add_argument("--unkill", action="store_true", help="Clear the kill-switch and exit")
    # Resume a run suspended at the trade-approval interrupt (needs DATABASE_URL).
    p.add_argument("--resume", metavar="RUN_ID", help="Resume a run awaiting trade approval")
    p.add_argument("--approve", action="append", default=[], metavar="PROPOSAL_ID",
                   help="Approve a proposal id (repeatable); used with --resume")
    p.add_argument("--reject", action="append", default=[], metavar="PROPOSAL_ID",
                   help="Reject a proposal id (repeatable); used with --resume")
    return p.parse_args()


def _toggle_kill(engage: bool) -> None:
    import config

    flag = Path(config.KILL_SWITCH_FILE)
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
        _resume(args.resume, args.approve, args.reject)
        return

    if args.mode:
        os.environ["AGENT_MODE"] = args.mode
    import config

    from agents.graph import build_graph
    from agents.state import RunStatus
    from observability.metrics import start_metrics_server

    report_date = date.fromisoformat(args.date) if args.date else date.today()
    run_id = f"{report_date.isoformat()}-{uuid.uuid4().hex[:8]}"
    logger.info("=== Agent run %s | mode=%s dry_run=%s ===", run_id, config.AGENT_MODE, args.dry_run)

    start_metrics_server()

    graph = build_graph()
    initial = {
        "run_id": run_id,
        "report_date": report_date.isoformat(),
        "mode": config.AGENT_MODE,
        "dry_run": args.dry_run,
        "status": RunStatus.RUNNING,
        "cost_usd": 0.0,
        "tokens": 0,
        "debate_rounds": 0,
    }
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": config.MAX_GRAPH_STEPS}

    final = graph.invoke(initial, cfg)

    # Suspended at the trade-approval interrupt? Notify the human and stop here.
    interrupts = final.get("__interrupt__")
    if interrupts:
        payload = getattr(interrupts[0], "value", interrupts[0])
        from agents.approval import send_approval_request

        send_approval_request(payload)
        logger.warning("=== Run %s AWAITING APPROVAL — %d proposal(s) ===",
                       run_id, len(payload.get("proposals", [])))
        if not config.DATABASE_URL:
            logger.warning("DATABASE_URL unset: this suspended run is in-memory only and cannot be "
                           "resumed from a separate process. Set DATABASE_URL for live approvals.")
        print(f"\nAwaiting approval. Resume with:\n"
              f"  python run_agents.py --resume {run_id} --approve <proposal_id> [--reject <id>]")
        return

    status = final.get("status")
    logger.info("=== Run %s finished → %s ===", run_id, getattr(status, "value", status))
    if final.get("report_path"):
        print(f"\nReport ready: {final['report_path']}")
    if status not in (RunStatus.COMPLETED, RunStatus.AWAITING_APPROVAL):
        sys.exit(1)


def _resume(run_id: str, approve: list, reject: list) -> None:
    import config  # noqa: F401  (ensures env loaded)

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
