"""Stub nodes for the trading half of the graph.

These are wired into the graph but gated OFF by default. The ``agent_node``
decorator turns a disabled flag into a clean pass-through (audit-only), so in
research mode they never execute. Real implementations land in later iterations
(see the plan: debate → risk → portfolio → trading → broker/approval).
"""

from __future__ import annotations

import logging

from agents.nodes.base import agent_node
from agents.state import AgentState

logger = logging.getLogger("agents.stub")


@agent_node("debate", enabled_flag="ENABLE_DEBATE_AGENT")
def debate_node(state: AgentState) -> dict:
    # TODO(iter-3): bounded bull/bear subgraph → ConvictionView (MAX_DEBATE_ROUNDS).
    logger.info("debate stub — no-op")
    return {}


@agent_node("risk", enabled_flag="ENABLE_RISK_AGENT")
def risk_node(state: AgentState) -> dict:
    # TODO(iter-4): position/sector caps, stop-loss, earnings block → RiskCheck gate.
    logger.info("risk stub — no-op")
    return {}


@agent_node("portfolio", enabled_flag="ENABLE_PORTFOLIO_AGENT")
def portfolio_node(state: AgentState) -> dict:
    # TODO(iter-4): hold the book, size positions, approve/reject proposals.
    logger.info("portfolio stub — no-op")
    return {}


@agent_node("trading", enabled_flag="ENABLE_TRADING_AGENT")
def trading_node(state: AgentState) -> dict:
    # TODO(iter-5): build TradeProposal → interrupt() for human approval → broker.
    logger.info("trading stub — no-op")
    return {}
