"""LangGraph run state (the shared blackboard) + terminal-status model.

Short-term working memory = this state, checkpointed per run (thread_id = run_id).
List fields use an additive reducer so worker nodes append compact results
instead of overwriting each other.
"""

from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Any, Optional, TypedDict

from agents.contracts import (
    Alert,
    ConvictionView,
    EnrichmentResult,
    PortfolioState,
    RankingResult,
    Scorecard,
    TradeProposal,
    UniverseResult,
)


class RunStatus(str, Enum):
    """Explicit agent-loop terminal states (Claude-Code-style)."""

    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    HALTED = "halted"                # kill-switch
    FAILED = "failed"
    MAX_ROUNDS = "max_rounds"        # recursion / loop cap hit
    BUDGET_EXCEEDED = "budget_exceeded"


# Statuses that mean "stop walking the graph, go straight to finalize".
TERMINAL_STATUSES = {
    RunStatus.HALTED,
    RunStatus.FAILED,
    RunStatus.MAX_ROUNDS,
    RunStatus.BUDGET_EXCEEDED,
}


class AgentState(TypedDict, total=False):
    # run identity / control
    run_id: str
    report_date: str            # ISO date string
    mode: str                   # research | paper | live
    dry_run: bool
    status: RunStatus

    # research
    total_screened: int
    universe: Optional[UniverseResult]
    enriched: Optional[EnrichmentResult]

    # analyst
    scorecards: Annotated[list[Scorecard], operator.add]
    ranking: Optional[RankingResult]

    # debate / trading
    convictions: Annotated[list[ConvictionView], operator.add]
    # proposals evolve through their lifecycle (risk → portfolio → trading), so the
    # latest full list replaces — NOT an additive reducer.
    proposals: list[TradeProposal]
    book: Optional[PortfolioState]
    alerts: Annotated[list[Alert], operator.add]
    debate_rounds: int

    # cost accounting (bounded spend)
    cost_usd: float
    tokens: int

    # observability
    audit: Annotated[list[dict[str, Any]], operator.add]

    # output
    report_path: Optional[str]
