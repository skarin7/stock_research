"""Analyst agent — Stage 5 (Claude Haiku scoring) + Stage 6 ranking.

Reuses scoring.claude_scorer.score_stocks (batch/sync logic untouched) and
scoring.ranker.rank_stocks. Emits typed Scorecards / RankingResult; the legacy
scores.json is written later in the finalize node so the backtest keeps working.
"""

from __future__ import annotations

import logging

from config import SETTINGS

from agents.contracts import RankingResult, Scorecard
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus

logger = logging.getLogger("agents.analyst")


@agent_node("analyst", enabled_flag="ENABLE_ANALYST_AGENT")
def analyst_node(state: AgentState) -> dict:
    enriched = state.get("enriched")
    if enriched is None or not enriched.stocks:
        logger.error("No enriched stocks to score")
        return {"status": RunStatus.FAILED}

    from scoring.claude_scorer import score_stocks
    from scoring.ranker import rank_stocks

    legacy_stocks = enriched.legacy_stocks()
    raw_scores = score_stocks(
        legacy_stocks, enriched.news_map, enriched.macro_context, enriched.sector_macro_map
    )
    if not raw_scores:
        logger.error("No stocks scored")
        return {"status": RunStatus.FAILED}

    top = rank_stocks(raw_scores)   # attaches composite_score, returns top-N

    scorecards = [Scorecard.from_legacy(s) for s in raw_scores]
    # rank_stocks copies dicts and sets composite_score; reflect that in all_scores
    by_ticker = {s["ticker"]: s for s in top}
    all_scored = [Scorecard.from_legacy(by_ticker.get(c.ticker, c.to_legacy_dict())) for c in scorecards]

    ranking = RankingResult(
        top=[Scorecard.from_legacy(s) for s in top],
        all_scores=all_scored,
    )
    logger.info("Analyst complete: %d scored, top %d ranked", len(all_scored), len(ranking.top))
    return {"status": RunStatus.RUNNING, "scorecards": scorecards, "ranking": ranking}
