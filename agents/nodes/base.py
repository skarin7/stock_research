"""``agent_node`` decorator — the common guard wrapper for every graph node.

Responsibilities (the "supervisor contract"):
  * honour the kill-switch (→ HALTED) and per-run budget (→ BUDGET_EXCEEDED)
  * skip cleanly when the node's feature flag is off (pass-through)
  * time the node and record an audit trail + Prometheus latency
  * turn unexpected exceptions into a terminal FAILED status (never crash the run)

Nodes stay small and pure: they return a partial state-update dict.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, Optional

from config import SETTINGS

from agents.state import AgentState, RunStatus
from agents.supervisor import audit_entry, budget_exceeded, kill_switch_active
from observability import metrics

logger = logging.getLogger("agents")


def agent_node(name: str, enabled_flag: Optional[str] = None) -> Callable:
    """Wrap a node fn with kill-switch / budget / flag / error guards."""

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

            if enabled_flag is not None and not getattr(SETTINGS, enabled_flag, False):
                logger.info("[%s] disabled (%s=False) — skipping", name, enabled_flag)
                return {"audit": [audit_entry(name, state.get("status"), state.get("status"), "skipped (disabled)")]}

            start = time.monotonic()
            try:
                update = fn(state) or {}
            except Exception as e:  # never crash the graph
                logger.exception("[%s] failed: %s", name, e)
                metrics.inc_node_error(name)
                return {"status": RunStatus.FAILED,
                        "audit": [audit_entry(name, state.get("status"), RunStatus.FAILED, str(e))]}
            finally:
                metrics.observe_node_latency(name, time.monotonic() - start)

            update.setdefault("audit", []).append(
                audit_entry(name, state.get("status"), update.get("status", state.get("status")), "ok")
            )
            return update

        return wrapper

    return decorator
