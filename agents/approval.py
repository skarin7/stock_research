"""Human-approval transport for live trades.

When a live run suspends at the trading node's ``interrupt()``, the orchestrator
calls ``send_approval_request`` to message the human the pending proposals. The
human replies ``/approve <id>`` or ``/reject <id>`` (Telegram), or an operator
runs ``run_agents.py --resume <run_id> --approve <id> ...``.

``resume_run`` rebuilds the graph and resumes the *exact* suspended run via
``Command(resume=decisions)``. This requires a **persistent checkpointer**
(Postgres / DATABASE_URL) for the suspended state to survive across processes;
with the in-memory fallback, resume only works within the same process.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from config import SETTINGS

logger = logging.getLogger("agents.approval")


def send_approval_request(payload: dict) -> bool:
    """Message the human the proposals awaiting approval. Returns True if sent."""
    if not (getattr(SETTINGS, "TELEGRAM_BOT_TOKEN", "") and getattr(SETTINGS, "TELEGRAM_CHAT_ID", "")):
        logger.warning("approval: Telegram not configured — cannot send approval request")
        return False
    from notifications.telegram_notifier import _send_text

    lines = ["<b>⚠️ Trade approval required</b>", f"run: <code>{payload.get('run_id', '')}</code>", ""]
    for p in payload.get("proposals", []):
        lines.append(
            f"<b>{p['ticker']}</b> {p['side']} x{p['qty']} @ {p.get('limit_price', 'MKT')} "
            f"(conv {p.get('conviction', 0):.2f})\n"
            f"  approve: <code>/approve {p['proposal_id']}</code>\n"
            f"  reject:  <code>/reject {p['proposal_id']}</code>"
        )
    lines.append("\nUnapproved proposals expire automatically.")
    return _send_text(SETTINGS.TELEGRAM_CHAT_ID, "\n".join(lines))


def decisions_from_lists(approve_ids, reject_ids) -> dict:
    """Build the resume map {proposal_id: 'approve'|'reject'}."""
    decisions = {pid: "reject" for pid in (reject_ids or [])}
    decisions.update({pid: "approve" for pid in (approve_ids or [])})
    return decisions


def resume_run(run_id: str, decisions: dict):
    """Resume a suspended run with the human's decisions. Returns the final state."""
    from langgraph.types import Command

    from agents.graph import build_graph

    graph = build_graph()
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": getattr(SETTINGS, "MAX_GRAPH_STEPS", 50)}
    logger.info("Resuming run %s with %d decisions", run_id, len(decisions))
    return graph.invoke(Command(resume=decisions), cfg)


def parse_telegram_decisions(updates: dict) -> dict:
    """Extract {proposal_id: 'approve'|'reject'} from a Telegram getUpdates payload."""
    decisions: dict[str, str] = {}
    for upd in updates.get("result", []):
        text = (upd.get("message", {}) or {}).get("text", "") or ""
        parts = text.strip().split()
        if len(parts) == 2 and parts[0] in ("/approve", "/reject"):
            decisions[parts[1]] = parts[0].lstrip("/")
    return decisions


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
