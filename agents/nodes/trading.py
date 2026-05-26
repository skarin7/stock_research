"""Trading node — turns APPROVED proposals into fills.

paper mode: simulate fills at the approved limit price, append positions to the
  book (with a stop-loss), mark proposals FILLED, and persist the book.
live mode: mark proposals AWAITING_APPROVAL, persist them, and suspend the run
  via LangGraph interrupt() for explicit human approval. On resume, approved
  proposals are placed through the gated broker (default-deny). Live execution
  additionally requires ENABLE_LIVE_TRADING (else no order is ever placed).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config

from agents.contracts import Position, ProposalStatus
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from agents.supervisor import kill_switch_active
from persistence.store import load_portfolio, recompute, save_portfolio, save_proposals

logger = logging.getLogger("agents.trading")


def _interrupt(payload: dict):
    """Suspend the graph for human input (isolated so tests can monkeypatch)."""
    from langgraph.types import interrupt
    return interrupt(payload)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@agent_node("trading", enabled_flag="ENABLE_TRADING_AGENT")
def trading_node(state: AgentState) -> dict:
    proposals = list(state.get("proposals") or [])
    approved = [p for p in proposals if p.status == ProposalStatus.APPROVED]
    if not approved:
        logger.info("trading: no approved proposals — skipping")
        return {}

    mode = state.get("mode", "research")
    if mode == "live":
        return _execute_live(state, proposals, approved)
    if mode != "paper":
        logger.info("trading: mode=%s — no execution", mode)
        return {}

    book = state.get("book") or load_portfolio()
    enriched = state.get("enriched")
    stock_by = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    stop_pct = float(getattr(config, "STOP_LOSS_PCT", 0.05))

    for p in approved:
        price = p.limit_price or 0.0
        stock = stock_by.get(p.ticker)
        book.positions.append(Position(
            ticker=p.ticker,
            qty=p.qty,
            avg_price=price,
            stop_price=round(price * (1 - stop_pct), 2) if price else None,
            sector=(stock.sector if stock else None),
        ))
        book.cash = round(book.cash - p.qty * price, 2)
        p.status = ProposalStatus.FILLED

    book = recompute(book)
    save_portfolio(book)
    logger.info("trading(paper): filled %d positions; cash=%.2f exposure=%.2f",
                len(approved), book.cash, book.total_exposure)
    return {"status": RunStatus.RUNNING, "proposals": proposals, "book": book}


def _execute_live(state: AgentState, proposals: list, approved: list) -> dict:
    """Live path: human approval via interrupt(), then place through the gated broker.

    Default-deny: if ENABLE_LIVE_TRADING is off we never suspend or place — the
    proposals are left APPROVED and no order is made.

    Idempotency note: everything before _interrupt() re-runs verbatim on resume,
    so it must be side-effect-idempotent (marking AWAITING + persisting an upsert
    both are). Order placement happens only AFTER the human decision returns.
    """
    if not getattr(config, "ENABLE_LIVE_TRADING", False):
        logger.warning("trading(live): ENABLE_LIVE_TRADING=false — default-deny, %d approved, no orders",
                       len(approved))
        return {}

    timeout = int(getattr(config, "APPROVAL_TIMEOUT_SEC", 900))
    expires_at = (_now() + timedelta(seconds=timeout)).isoformat()
    for p in approved:
        p.status = ProposalStatus.AWAITING_APPROVAL
        p.expires_at = expires_at
    save_proposals(approved)   # visibility for the out-of-process approver (idempotent upsert)

    # Suspend the run until a human approves/rejects. On resume `decisions` is the
    # {proposal_id: "approve"|"reject"} map passed via Command(resume=...).
    decisions = _interrupt({
        "type": "trade_approval",
        "run_id": state.get("run_id", ""),
        "proposals": [
            {"proposal_id": p.proposal_id, "ticker": p.ticker, "side": p.side,
             "qty": p.qty, "limit_price": p.limit_price, "conviction": p.conviction}
            for p in approved
        ],
        "instructions": "/approve <id> or /reject <id>",
    }) or {}

    from agents.broker import groww_trader

    for p in approved:
        if kill_switch_active():
            p.status = ProposalStatus.HALTED
            continue
        if p.expires_at and _now() > datetime.fromisoformat(p.expires_at):
            p.status = ProposalStatus.EXPIRED
            continue
        if decisions.get(p.proposal_id) != "approve":
            p.status = ProposalStatus.REJECTED
            continue
        p.approved_at = _now().isoformat()
        try:
            order_id, ostatus = groww_trader.place_order(p, mode="live")
            p.broker_order_id = order_id
            p.status = ProposalStatus.FILLED if ostatus == "filled" else ProposalStatus.PLACED
        except Exception as e:
            logger.error("trading(live): order failed for %s: %s", p.ticker, e)
            p.status = ProposalStatus.ERROR

    save_proposals(approved)
    placed = sum(p.status in (ProposalStatus.PLACED, ProposalStatus.FILLED) for p in approved)
    logger.info("trading(live): %d placed, %d rejected/expired/halted",
                placed, len(approved) - placed)
    return {"status": RunStatus.RUNNING, "proposals": proposals}
