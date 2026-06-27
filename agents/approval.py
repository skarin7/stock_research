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
    """Message the human the proposals awaiting approval with tap-to-act inline
    buttons (typed ``/approve <id>`` / bare ``approve`` still work as fallbacks).
    Returns True if sent."""
    if not (getattr(SETTINGS, "TELEGRAM_BOT_TOKEN", "") and getattr(SETTINGS, "TELEGRAM_CHAT_ID", "")):
        logger.warning("approval: Telegram not configured — cannot send approval request")
        return False
    from notifications.telegram_notifier import send_buttons

    lines = ["<b>⚠️ Trade approval required</b>", f"run: <code>{payload.get('run_id', '')}</code>", ""]
    keyboard: list[list[dict]] = []
    for p in payload.get("proposals", []):
        price = p.get("limit_price")
        qty = p.get("qty", 0)
        budget = (qty * price) if price else None
        budget_str = f"  = ₹{budget:,.0f}" if budget else ""   # transaction budget = qty × limit
        lines.append(
            f"<b>{p['ticker']}</b> {p['side']} x{qty} @ {price or 'MKT'}{budget_str} "
            f"(conv {p.get('conviction', 0):.2f})"
        )
        pid = p["proposal_id"]
        keyboard.append([
            {"text": f"✅ Approve {p['ticker']}", "callback_data": f"approve:{pid}"},
            {"text": "❌ Reject", "callback_data": f"reject:{pid}"},
        ])
    lines.append("\nUnapproved proposals expire automatically.")
    return send_buttons(SETTINGS.TELEGRAM_CHAT_ID, "\n".join(lines), keyboard)


def handle_callback(data: str) -> str | None:
    """Process an inline-button tap (``approve:<id>`` / ``reject:<id>``).

    Reuses the same resume path as the typed command. Returns a human-readable
    reply, or ``None`` if ``data`` isn't an approval callback."""
    if not data or ":" not in data:
        return None
    action, pid = data.split(":", 1)
    if action not in ("approve", "reject"):
        return None
    return handle_approval_command(f"/{action} {pid}")


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


def handle_approval_command(text: str) -> str | None:
    """Process a Telegram ``/approve <id>`` | ``/reject <id>`` message end-to-end.

    Resolves the proposal's run_id from the proposal store, resumes the suspended
    run with the human's decision, and returns a human-readable reply. Returns
    ``None`` if ``text`` is not an approval command (caller routes elsewhere).

    Cross-process resume requires DATABASE_URL (Postgres checkpointer) — without
    it the suspended run isn't visible here and resume will fail.
    """
    parts = text.strip().split()
    if not parts or parts[0] not in ("/approve", "/reject"):
        return None
    if len(parts) < 2:
        return "Usage: <code>/approve &lt;proposal_id&gt;</code> or <code>/reject &lt;proposal_id&gt;</code>"

    action = parts[0].lstrip("/")            # "approve" | "reject"
    proposal_id = parts[1]

    from persistence.store import load_proposals

    prop = load_proposals().get(proposal_id)
    if not prop:
        return f"Unknown proposal <code>{proposal_id}</code>."
    status = prop.get("status")
    if status != "awaiting_approval":
        return f"Proposal <code>{proposal_id}</code> is already <b>{status}</b> — nothing to do."
    run_id = prop.get("run_id", "")
    if not run_id:
        return f"Proposal <code>{proposal_id}</code> has no run to resume."

    try:
        resume_run(run_id, {proposal_id: action})
    except Exception as e:
        logger.error("resume failed for run %s: %s", run_id, e)
        return f"⚠️ Resume failed: {e}"

    updated = load_proposals().get(proposal_id, {})
    final = updated.get("status", "?")
    oid = updated.get("broker_order_id")
    tail = f" — order <code>{oid}</code>" if oid else ""
    verb = {"approve": "Approved", "reject": "Rejected"}[action]
    return f"{verb} <b>{prop.get('ticker', proposal_id)}</b> → <b>{final}</b>{tail}."


_AFFIRM = {"approve", "approved", "yes", "y", "ok", "okay", "confirm", "go", "proceed", "accept"}
_REJECT = {"reject", "rejected", "no", "n", "cancel", "decline", "skip", "stop", "abort"}


def pending_proposals() -> list[dict]:
    """Proposals currently awaiting human approval (single-user allowlist → all the chat's)."""
    from persistence.store import load_proposals

    return [p for p in load_proposals().values() if p.get("status") == "awaiting_approval"]


def _decision_keyword(text: str) -> str | None:
    """Map a bare reply to 'approve'|'reject' — only on a clear one-word-ish decision."""
    t = text.strip().lower().rstrip(".!").split()
    if not t:
        return None
    head = t[0]
    if head in _AFFIRM:
        return "approve"
    if head in _REJECT:
        return "reject"
    return None


def route_approval(text: str) -> str | None:
    """Deterministic tier-1 approval router (NO LLM / NO ReAct).

    Handles both the explicit ``/approve <id>``|``/reject <id>`` command AND a
    bare ``approve``/``reject`` reply when an approval is pending for the chat.
    Returns a reply string when it handled the message, else ``None`` (the caller
    then routes to the chat agent).
    """
    t = (text or "").strip()
    if t.startswith("/approve") or t.startswith("/reject"):
        return handle_approval_command(t)

    pend = pending_proposals()
    if not pend:
        return None                       # no HITL pending → not an approval context

    decision = _decision_keyword(t)
    if decision is None:
        return None                       # pending, but not an approve/reject reply → chat agent
    if len(pend) == 1:
        return handle_approval_command(f"/{decision} {pend[0]['proposal_id']}")

    ids = ", ".join(f"<code>{p['proposal_id']}</code>" for p in pend)
    return (f"There are {len(pend)} pending approvals — reply "
            f"<code>/{decision} &lt;id&gt;</code>. Pending: {ids}")


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
