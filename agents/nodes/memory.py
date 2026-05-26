"""Memory + Backtest self-evaluation.

Runs after finalize (which has already appended the day's backtest). This node:
  1. records each of today's ranked calls into long-term memory (compact: score,
     conviction, rationale, regime) so future runs can query a ticker's history,
  2. computes the signal-accuracy self-evaluation from the backtest log (which
     signals actually predicted winners) and stores it.

Long-term memory = append-only store (persistence.store); short-term = run state.
Other agents read it via persistence.store.recent_calls / latest_signal_perf
(feeding it back into scoring weights is a future tuning step). Gated by
ENABLE_MEMORY_AGENT.
"""

from __future__ import annotations

import logging

from agents.nodes.base import agent_node
from agents.state import AgentState
from persistence.store import record_memory

logger = logging.getLogger("agents.memory")


def _regime_label() -> str:
    """Coarse market-regime tag from the latest backtest's Nifty return."""
    try:
        from backtest.reporter import latest_backtest_summary
        r = (latest_backtest_summary() or {}).get("nifty50_return_pct")
        if r is None:
            return "unknown"
        if r > 1.0:
            return "bull"
        if r < -1.0:
            return "bear"
        return "range"
    except Exception:
        return "unknown"


def _signal_performance() -> dict:
    """Per-signal winner/loser score gap from the backtest log (self-evaluation)."""
    try:
        from backtest.reporter import signal_accuracy_report
        return signal_accuracy_report() or {}
    except Exception as e:
        logger.warning("memory: signal accuracy unavailable: %s", e)
        return {}


@agent_node("memory", enabled_flag="ENABLE_MEMORY_AGENT")
def memory_node(state: AgentState) -> dict:
    report_date = state.get("report_date", "")
    run_id = state.get("run_id", "")
    ranking = state.get("ranking")
    convictions_by = {c.ticker: c for c in (state.get("convictions") or [])}
    regime = _regime_label()

    recorded = 0
    if ranking and ranking.top:
        for sc in ranking.top:
            cv = convictions_by.get(sc.ticker)
            record_memory("calls", f"{sc.ticker}:{report_date}", {
                "ticker": sc.ticker,
                "date": report_date,
                "run_id": run_id,
                "composite_score": sc.composite_score,
                "conviction": cv.conviction if cv else None,
                "direction": cv.direction if cv else None,
                "rationale": (sc.investment_rationale or "")[:300],
                "regime": regime,
            })
            recorded += 1

    perf = _signal_performance()
    if perf:
        record_memory("signal_perf", report_date, perf)

    logger.info("memory: recorded %d call(s), regime=%s, signal_perf=%s",
                recorded, regime, "yes" if perf else "no")
    return {}
