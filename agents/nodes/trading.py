"""Trading node — turns APPROVED proposals into fills.

paper mode: simulate fills at the approved limit price, append positions to the
  book (with a stop-loss), mark proposals FILLED, and persist the book.
live mode: deferred — real Groww orders require the broker layer + LangGraph
  interrupt() human approval (next iteration). Until then live leaves approved
  proposals untouched (no order is ever placed).
"""

from __future__ import annotations

import logging

import config

from agents.contracts import Position, ProposalStatus
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from persistence.store import load_portfolio, recompute, save_portfolio

logger = logging.getLogger("agents.trading")


@agent_node("trading", enabled_flag="ENABLE_TRADING_AGENT")
def trading_node(state: AgentState) -> dict:
    proposals = list(state.get("proposals") or [])
    approved = [p for p in proposals if p.status == ProposalStatus.APPROVED]
    if not approved:
        logger.info("trading: no approved proposals — skipping")
        return {}

    mode = state.get("mode", "research")
    if mode != "paper":
        # live execution (broker + interrupt approval) lands in the next iteration.
        logger.warning("trading: mode=%s — live execution not wired yet; leaving %d approved, no orders placed",
                       mode, len(approved))
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
