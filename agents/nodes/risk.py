"""Risk Manager — deterministic gate before any position is sized.

Consumes the debate's ConvictionViews and emits a TradeProposal per candidate:
PROPOSED (passed all checks) or BLOCKED (with the failing RiskCheck). Long-only
for now. Rules: minimum conviction, earnings-proximity block, no duplicate of an
existing position. Position sizing + portfolio-level caps live in the Portfolio
Manager; per-trade stop-loss is attached at fill time.
"""

from __future__ import annotations

import logging

import config

from agents.contracts import ProposalStatus, RiskCheck, TradeProposal
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from persistence.store import load_portfolio

logger = logging.getLogger("agents.risk")


@agent_node("risk", enabled_flag="ENABLE_RISK_AGENT")
def risk_node(state: AgentState) -> dict:
    convictions = state.get("convictions") or []
    if not convictions:
        logger.info("risk: no convictions — skipping")
        return {}

    enriched = state.get("enriched")
    stock_by = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    book = state.get("book") or load_portfolio()
    held = {p.ticker for p in book.positions}
    min_conv = float(getattr(config, "MIN_CONVICTION_TO_TRADE", 0.6))
    block_earnings = bool(getattr(config, "BLOCK_NEAR_EARNINGS", True))
    earnings_days = int(getattr(config, "EARNINGS_PROXIMITY_DAYS", 5))

    proposals: list[TradeProposal] = []
    for cv in convictions:
        checks: list[RiskCheck] = []
        passed = True

        if cv.direction != "long":
            checks.append(RiskCheck(rule="direction", passed=False,
                                    detail=f"{cv.direction} not tradable (long-only)"))
            passed = False

        ok_conv = cv.conviction >= min_conv
        checks.append(RiskCheck(rule="min_conviction", passed=ok_conv,
                                detail=f"{cv.conviction:.2f} vs {min_conv:.2f}"))
        passed = passed and ok_conv

        stock = stock_by.get(cv.ticker)
        if block_earnings and stock is not None and stock.days_to_earnings is not None:
            near = stock.days_to_earnings <= earnings_days
            checks.append(RiskCheck(rule="earnings_block", passed=not near,
                                    detail=f"{stock.days_to_earnings}d to earnings"))
            passed = passed and not near

        if cv.ticker in held:
            checks.append(RiskCheck(rule="duplicate_position", passed=False, detail="already held"))
            passed = False

        proposals.append(TradeProposal(
            proposal_id=f"{state.get('run_id', '')}:{cv.ticker}",
            run_id=state.get("run_id", ""),
            ticker=cv.ticker,
            side="BUY",
            qty=0,
            rationale=cv.bull_case[:500],
            conviction=cv.conviction,
            status=ProposalStatus.PROPOSED if passed else ProposalStatus.BLOCKED,
            risk_checks=checks,
        ))

    n_pass = sum(p.status == ProposalStatus.PROPOSED for p in proposals)
    logger.info("risk: %d/%d candidates passed", n_pass, len(proposals))
    return {"status": RunStatus.RUNNING, "proposals": proposals, "book": book}
