"""Portfolio Manager — sizes risk-passed proposals and enforces book-level caps.

Reads the PROPOSED proposals from the Risk Manager, sizes each by
capital × MAX_POSITION_PCT × conviction, and accepts them highest-conviction
first subject to MAX_OPEN_POSITIONS and MAX_SECTOR_PCT (measured against the
current book + already-accepted names this run). Accepted → APPROVED with qty +
limit_price; the rest → REJECTED with the binding RiskCheck. Execution (fills)
is the Trading node's job; this node only decides size + approval.
"""

from __future__ import annotations

import logging

from config import SETTINGS

from agents.contracts import ProposalStatus, RiskCheck
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from persistence.store import load_portfolio

logger = logging.getLogger("agents.portfolio")


def size_proposals(
    proposals: list,
    stock_by: dict,
    book,
    capital: float,
    max_open: int,
    max_pos_pct: float,
    max_sector_pct: float,
) -> list:
    """Pure portfolio sizer. Mutates proposal status in-place, returns same list."""
    open_count = len(book.positions)
    sector_value = dict(book.sector_exposure)

    candidates = sorted(
        (p for p in proposals if p.status == ProposalStatus.PROPOSED),
        key=lambda p: p.conviction, reverse=True,
    )

    for p in candidates:
        if open_count >= max_open:
            _reject(p, "max_open", f"{open_count}/{max_open} positions")
            continue

        stock = stock_by.get(p.ticker)
        price = stock.ltp if stock and stock.ltp else None
        if not price or price <= 0:
            _reject(p, "price", "no live price")
            continue

        budget = capital * max_pos_pct * p.conviction
        qty = int(budget // price)
        if qty <= 0:
            _reject(p, "position_size", f"budget {budget:.0f} < 1 share @ {price:.2f}")
            continue

        value = qty * price
        sector = (stock.sector if stock else None) or "Unknown"
        if sector_value.get(sector, 0.0) + value > capital * max_sector_pct:
            _reject(p, "sector_cap", f"{sector} would exceed {max_sector_pct:.0%}")
            continue

        p.qty = qty
        p.limit_price = round(price, 2)
        p.status = ProposalStatus.APPROVED
        p.risk_checks.append(RiskCheck(rule="sized", passed=True,
                                       detail=f"{qty} @ {price:.2f}"))
        sector_value[sector] = sector_value.get(sector, 0.0) + value
        open_count += 1

    return proposals


@agent_node("portfolio", requires_trading=True)
def portfolio_node(state: AgentState) -> dict:
    proposals = list(state.get("proposals") or [])
    if not proposals:
        logger.info("portfolio: no proposals — skipping")
        return {}

    enriched = state.get("enriched")
    stock_by = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    book = state.get("book") or load_portfolio()

    proposals = size_proposals(
        proposals=proposals,
        stock_by=stock_by,
        book=book,
        capital=float(getattr(SETTINGS, "TRADING_CAPITAL_INR", 0.0)),
        max_open=int(getattr(SETTINGS, "MAX_OPEN_POSITIONS", 5)),
        max_pos_pct=float(getattr(SETTINGS, "MAX_POSITION_PCT", 0.10)),
        max_sector_pct=float(getattr(SETTINGS, "MAX_SECTOR_PCT", 0.30)),
    )
    approved = sum(p.status == ProposalStatus.APPROVED for p in proposals)
    logger.info("portfolio: %d approved, %d rejected",
                approved, sum(p.status == ProposalStatus.REJECTED for p in proposals))
    return {"status": RunStatus.RUNNING, "proposals": proposals, "book": book}


def _reject(proposal, rule: str, detail: str) -> None:
    proposal.status = ProposalStatus.REJECTED
    proposal.risk_checks.append(RiskCheck(rule=rule, passed=False, detail=detail))
