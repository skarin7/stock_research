"""Debate agent — a bounded bull vs bear subgraph that sharpens conviction.

For each top-ranked candidate the inner LangGraph subgraph runs:

    bull → bear → (loop while rounds < MAX_DEBATE_ROUNDS) → synthesize → END

A Bull researcher argues the long case, a Bear argues the short/risk case, they
alternate for a hard-capped number of rounds (no open-ended iteration), then a
synthesis step emits a direction + conviction (0..1). The outer ``debate_node``
runs this subgraph per candidate and emits one ``ConvictionView`` each.

LLM calls go through ``_chat`` (provider-aware via agents.llm.get_chat_model);
tests monkeypatch ``_chat`` so no network/keys are needed.
"""

from __future__ import annotations

import json
import logging
import operator
from typing import Annotated, Any, Optional, TypedDict

import config

from agents.contracts import ConvictionView, DebateTurn
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from agents.supervisor import budget_exceeded

logger = logging.getLogger("agents.debate")


# ── inner subgraph state ──────────────────────────────────────────────────────

class DebateState(TypedDict, total=False):
    ticker: str
    context: str                                   # static facts both sides see
    rounds: int
    max_rounds: int
    bull_case: str                                 # latest bull argument
    bear_case: str                                 # latest bear argument
    transcript: Annotated[list[dict], operator.add]
    tokens: int
    conviction: dict[str, Any]                     # {direction, conviction}


def _chat(messages: list[tuple[str, str]], *, max_tokens: int = 400, temperature: float = 0.4) -> tuple[str, int]:
    """Single LLM turn → (text, tokens). Isolated so tests can monkeypatch it."""
    from agents.llm import get_chat_model, usage_from_response

    llm = get_chat_model(max_tokens=max_tokens, temperature=temperature)
    resp = llm.invoke(messages)
    content = getattr(resp, "content", resp)
    text = content if isinstance(content, str) else str(content)
    tokens, _ = usage_from_response(resp)
    return text.strip(), tokens


# ── inner nodes ───────────────────────────────────────────────────────────────

def _bull_node(state: DebateState) -> dict:
    prior = f"\nBear's last point:\n{state.get('bear_case')}" if state.get("bear_case") else ""
    text, tok = _chat([
        ("system", "You are a Bull equity researcher. Argue the LONG case for the stock in 2-3 "
                   "sharp sentences. Engage with the bear's points if any; cite the data given."),
        ("human", f"{state['context']}{prior}"),
    ])
    return {
        "bull_case": text,
        "transcript": [{"side": "bull", "argument": text}],
        "tokens": (state.get("tokens", 0) or 0) + tok,
    }


def _bear_node(state: DebateState) -> dict:
    prior = f"\nBull's last point:\n{state.get('bull_case')}" if state.get("bull_case") else ""
    text, tok = _chat([
        ("system", "You are a Bear equity researcher. Argue the SHORT/risk case for the stock in "
                   "2-3 sharp sentences. Rebut the bull's points; cite the data given."),
        ("human", f"{state['context']}{prior}"),
    ])
    return {
        "bear_case": text,
        "transcript": [{"side": "bear", "argument": text}],
        "rounds": (state.get("rounds", 0) or 0) + 1,   # one round = one bull+bear exchange
        "tokens": (state.get("tokens", 0) or 0) + tok,
    }


def _synthesize_node(state: DebateState) -> dict:
    text, tok = _chat([
        ("system", "You are the debate judge. Weigh the bull and bear cases and output ONLY JSON: "
                   '{"direction": "long|short|neutral", "conviction": 0.0-1.0}. '
                   "Conviction reflects how decisively one side won."),
        ("human", f"{state['context']}\n\nBULL:\n{state.get('bull_case')}\n\nBEAR:\n{state.get('bear_case')}"),
    ], max_tokens=120, temperature=0.0)
    conviction = _parse_conviction(text)
    return {"conviction": conviction, "tokens": (state.get("tokens", 0) or 0) + tok}


def _parse_conviction(text: str) -> dict:
    try:
        start, end = text.find("{"), text.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(text[start:end])
            direction = str(data.get("direction", "neutral")).lower()
            if direction not in ("long", "short", "neutral"):
                direction = "neutral"
            conv = max(0.0, min(1.0, float(data.get("conviction", 0.0))))
            return {"direction": direction, "conviction": conv}
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Could not parse conviction from synthesis: %s | %.120s", e, text)
    return {"direction": "neutral", "conviction": 0.0}


def _continue_or_synthesize(state: DebateState) -> str:
    return "bull" if state.get("rounds", 0) < state.get("max_rounds", 1) else "synthesize"


def build_debate_subgraph():
    """Compile the bounded bull↔bear→synthesize subgraph (no checkpointer needed)."""
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(DebateState)
    g.add_node("bull", _bull_node)
    g.add_node("bear", _bear_node)
    g.add_node("synthesize", _synthesize_node)
    g.add_edge(START, "bull")
    g.add_edge("bull", "bear")
    g.add_conditional_edges("bear", _continue_or_synthesize,
                            {"bull": "bull", "synthesize": "synthesize"})
    g.add_edge("synthesize", END)
    return g.compile()


# ── prompt context ────────────────────────────────────────────────────────────

def _build_context(scorecard, stock, macro: str) -> str:
    facts = {
        "ticker": scorecard.ticker,
        "composite_score": scorecard.composite_score,
        "signals": {k: {"score": v.score, "reason": v.reason} for k, v in scorecard.signals.items()},
        "investment_rationale": scorecard.investment_rationale,
        "risk_flags": scorecard.risk_flags,
    }
    if stock is not None:
        facts["fundamentals"] = {
            "sector": stock.sector,
            "pe_ratio": stock.pe_ratio,
            "sector_pe": stock.sector_pe,
            "ltp": stock.ltp,
            "week52_high": stock.week52_high,
            "week52_low": stock.week52_low,
            "delivery_pct": stock.delivery_pct,
            "volume_ratio": stock.volume_ratio,
            "days_to_earnings": stock.days_to_earnings,
        }
    macro_block = f"\nMacro context:\n{macro}\n" if macro else ""
    return f"Stock under debate:\n{json.dumps(facts, indent=2)}{macro_block}"


# ── outer node ────────────────────────────────────────────────────────────────

@agent_node("debate", enabled_flag="ENABLE_DEBATE_AGENT")
def debate_node(state: AgentState) -> dict:
    ranking = state.get("ranking")
    if ranking is None or not ranking.top:
        logger.info("debate: no ranked candidates — skipping")
        return {}

    enriched = state.get("enriched")
    stock_by_ticker = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    macro = enriched.macro_context if enriched else ""

    top_n = max(1, int(getattr(config, "DEBATE_TOP_N", 5)))
    max_rounds = max(1, int(getattr(config, "MAX_DEBATE_ROUNDS", 3)))
    candidates = ranking.top[:top_n]

    subgraph = build_debate_subgraph()
    convictions: list[ConvictionView] = []
    total_tokens = 0

    for sc in candidates:
        # Stop debating more names if the run budget is already blown.
        if budget_exceeded({**state, "tokens": (state.get("tokens", 0) or 0) + total_tokens}):
            logger.warning("debate: budget reached after %d/%d candidates", len(convictions), len(candidates))
            break
        context = _build_context(sc, stock_by_ticker.get(sc.ticker), macro)
        try:
            result = subgraph.invoke(
                {"ticker": sc.ticker, "context": context, "rounds": 0,
                 "max_rounds": max_rounds, "tokens": 0, "transcript": []},
                {"recursion_limit": max_rounds * 3 + 5},
            )
        except Exception as e:
            logger.error("debate failed for %s: %s", sc.ticker, e)
            continue

        conv = result.get("conviction") or {}
        total_tokens += int(result.get("tokens", 0) or 0)
        convictions.append(ConvictionView(
            ticker=sc.ticker,
            direction=conv.get("direction", "neutral"),
            conviction=float(conv.get("conviction", 0.0)),
            bull_case=result.get("bull_case", ""),
            bear_case=result.get("bear_case", ""),
            transcript=[DebateTurn(**t) for t in result.get("transcript", [])],
            composite_score=sc.composite_score,
        ))

    logger.info("debate complete: %d convictions (%d tokens)", len(convictions), total_tokens)
    return {
        "status": RunStatus.RUNNING,
        "convictions": convictions,
        "tokens": (state.get("tokens", 0) or 0) + total_tokens,
    }
