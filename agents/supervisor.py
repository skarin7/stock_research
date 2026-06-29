"""Master/Supervisor helpers: kill-switch + budget guards, audit, and the
conditional-edge routing functions the graph uses to sequence agents.

Routing rule of thumb: any terminal status short-circuits straight to
``finalize``; otherwise we advance to the next node (gated by its feature flag).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from config import SETTINGS, trading_enabled

from agents.state import TERMINAL_STATUSES, AgentState, RunStatus


def kill_switch_active() -> bool:
    """True if the kill-switch flag file exists or KILL_SWITCH is set."""
    if getattr(SETTINGS, "KILL_SWITCH", False):
        return True
    flag = getattr(SETTINGS, "KILL_SWITCH_FILE", "")
    return bool(flag) and os.path.exists(flag)


def budget_exceeded(state: AgentState) -> bool:
    cost = state.get("cost_usd", 0.0) or 0.0
    tokens = state.get("tokens", 0) or 0
    return cost > getattr(SETTINGS, "MAX_RUN_COST_USD", float("inf")) or tokens > getattr(
        SETTINGS, "MAX_RUN_TOKENS", float("inf")
    )


def audit_entry(node: str, old: Any, new: Any, detail: str = "") -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "node": node,
        "old_status": getattr(old, "value", old),
        "new_status": getattr(new, "value", new),
        "detail": detail,
    }


def is_terminal(state: AgentState) -> bool:
    return state.get("status") in TERMINAL_STATUSES


# ── conditional-edge routers ──────────────────────────────────────────────────

def route_after_research(state: AgentState) -> str:
    if is_terminal(state):
        return "finalize"
    return "analyst"


def route_after_analyst(state: AgentState) -> str:
    if is_terminal(state):
        return "finalize"
    if trading_enabled():
        return "debate"
    return "finalize"


def next_or_finalize(target: str):
    """Generic router: advance to ``target`` unless the run hit a terminal state."""

    def _router(state: AgentState) -> str:
        if is_terminal(state):
            return "finalize"
        return target

    return _router
