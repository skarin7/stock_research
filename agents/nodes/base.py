"""``agent_node`` decorator — the common guard wrapper for every graph node.

Responsibilities (the "supervisor contract"):
  * honour the kill-switch (→ HALTED) and per-run budget (→ BUDGET_EXCEEDED)
  * skip cleanly when requires_trading=True but TRADING_MODE=off
  * time the node and record an audit trail + Prometheus latency
  * turn unexpected exceptions into a terminal FAILED status (never crash the run)

Nodes stay small and pure: they return a partial state-update dict.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable

from config import trading_enabled

from agents.state import AgentState, RunStatus
from agents.supervisor import audit_entry, budget_exceeded, kill_switch_active
from observability import metrics
from observability.chat_tracing import trace_node

logger = logging.getLogger("agents")


def agent_node(name: str, requires_trading: bool = False) -> Callable:
    """Wrap a node fn with kill-switch / budget / trading-gate / error guards."""

    def decorator(fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
        @functools.wraps(fn)
        def wrapper(state: AgentState) -> dict:
            if kill_switch_active():
                logger.error("[%s] kill-switch active — halting run", name)
                metrics.inc_node_error(name)
                return {"status": RunStatus.HALTED,
                        "audit": [audit_entry(name, state.get("status"), RunStatus.HALTED, "kill-switch")]}

            if budget_exceeded(state):
                logger.error("[%s] run budget exceeded — halting", name)
                metrics.inc_budget_exceeded()
                return {"status": RunStatus.BUDGET_EXCEEDED,
                        "audit": [audit_entry(name, state.get("status"), RunStatus.BUDGET_EXCEEDED, "budget")]}

            if requires_trading and not trading_enabled():
                logger.info("[%s] requires TRADING_MODE=paper|live — skipping", name)
                return {"audit": [audit_entry(name, state.get("status"), state.get("status"), "skipped (trading off)")]}

            run_id = state.get("run_id", "unknown")
            input_summary = {
                "stocks": len(state.get("stocks") or []),
                "proposals": len(state.get("proposals") or []),
            }
            start = time.monotonic()
            with trace_node(name, run_id, input_summary) as span:
                try:
                    update = fn(state) or {}
                except Exception as e:
                    logger.exception("[%s] failed: %s", name, e)
                    metrics.inc_node_error(name)
                    return {"status": RunStatus.FAILED,
                            "audit": [audit_entry(name, state.get("status"), RunStatus.FAILED, str(e))]}
                finally:
                    metrics.observe_node_latency(name, time.monotonic() - start)

                span.set_output({
                    "status": str(update.get("status", state.get("status", ""))),
                    "stocks_out": len(update.get("stocks") or []),
                    "proposals_out": len(update.get("proposals") or []),
                    "scores_out": len(update.get("scores") or []),
                })

            update.setdefault("audit", []).append(
                audit_entry(name, state.get("status"), update.get("status", state.get("status")), "ok")
            )
            return update

        return wrapper

    return decorator
