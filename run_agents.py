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

    status = final.get("status")
    logger.info("=== Run %s finished → %s ===", run_id, getattr(status, "value", status))
    if final.get("report_path"):
        print(f"\nReport ready: {final['report_path']}")
    if status not in (RunStatus.COMPLETED, RunStatus.AWAITING_APPROVAL):
        sys.exit(1)


if __name__ == "__main__":
    main()
