"""The chat agent loop: one LangGraph ReAct agent, one thread per Telegram chat.

``run_turn(chat_id, text)`` is the single entrypoint both the webhook server
and the local polling script call. It honours the kill-switch, bounds the tool
loop via recursion_limit, and records each turn in the runs table (mode="chat")
when a database is configured.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime

from config import SETTINGS

logger = logging.getLogger("agents.chat")

_SYSTEM_PROMPT = """You are an equity research assistant for Indian (NSE/BSE) markets, \
chatting with your user on Telegram.

## Research Protocol (follow for every question)

**Step 1 — Plan before acting:**
Before calling any tool, identify:
- What data do I need to fully answer this question?
- Which tools will I call, in what order?
Then execute that plan step by step. Never call a tool before planning.

**Step 2 — Evaluate after every tool result:**
After each tool result, ask:
- What did I learn?
- Do I now have everything needed to answer completely?
- If not, what is still missing and which tool provides it?
Only emit a final reply when no data gaps remain.

## Hard Constraints

**Symbol format — NEVER pass company names to live_quote or timing:**
Always use NSE ticker format (RELIANCE, not "Reliance Industries").
Resolution order:
1. Call screen_snapshot(name="<company name>") to get the NSE symbol.
2. If no match (stock outside scored universe): use the NSE symbol from training knowledge, \
call live_quote/timing directly, and tell the user: \
"Not in today's scored universe — from general knowledge: <symbol>. Please verify on NSE/BSE."
Note: BSE numeric codes are not in any tool — answer those from training knowledge with the disclaimer.

**Entry/exit questions — MUST call timing() for each shortlisted stock:**
For any question about buy zone, entry, stop loss, or target:
1. Call screen_snapshot to get the shortlist.
2. Call timing(<symbol>) for EACH shortlisted stock.
3. Only compose the final answer after ALL timing calls are complete.
Never answer an entry/exit question without timing data.

## Citation Rules

Every factual claim must cite its source:
- macro_search result → include URL and fetch date from the result
- fetch_news headline → include headline text, URL, and publication date
- screen_snapshot score/fundamentals → state "as of <as_of date>"
- timing technicals → state "from OHLCV data"
- Training knowledge fallback → state "from general knowledge — verify on NSE/BSE directly"

Never assert a factual claim without citing which tool result or data source it came from.

## Data Discipline

- Start with screen_snapshot (cached nightly scored universe) to find candidates; \
use live_quote / fetch_news only on the shortlist for freshness.
- Use score_subset only when fresh scores genuinely change the answer (it costs money). \
deep_dive is for one named stock the user wants examined closely — at most once per question.
- For growth/performance questions over a period ("which stocks grew last month", \
"Reliance return since Jun 20"), check the time_context hint in the message — \
if lookback_days or date_from/date_to are given, call \
historical_performance(symbols, from_date, to_date) with those dates. \
For a specific past date's snapshot, call screen_snapshot(as_of="YYYY-MM-DD").
- For current events / geopolitics / macro (e.g. "impact of the Iran war"), call \
macro_search(query) to get grounded facts, map the event to sectors, then screen_snapshot \
on those sectors to name the affected stocks.
- Use recall(ticker) when the user asks what you thought of a stock before.
- Always state the snapshot as-of date, and warn clearly when data is flagged stale.
- If a tool returns an error, say what data was unavailable and answer with what you have.

## Answer Style

- Telegram HTML only: <b>bold</b>, <i>italic</i> — no markdown, no tables, no headers.
- Be concise: a ranked shortlist with one-line reasons beats an essay. Keep replies under \
3000 characters.
- You give research and analysis, not guaranteed outcomes. No "sure-shot"/"guaranteed" \
language; mention key risks when recommending.
- You cannot place orders. If asked to trade, say order placement is not enabled from chat yet."""

_agent = None
_agent_lock = threading.Lock()


def build_chat_agent(checkpointer=None):
    """Compile the ReAct agent (model + tools + per-chat memory)."""
    from langgraph.prebuilt import create_react_agent

    from agents.chat.tools import CHAT_TOOLS
    from agents.graph import get_checkpointer
    from agents.llm import get_chat_model
    import agents.llm_router as _llm_router

    model = get_chat_model(
        model=getattr(SETTINGS, "CHAT_MODEL", "") or _llm_router.chat_model(),
        max_tokens=2048,
        temperature=0.3,
    )
    return create_react_agent(
        model,
        CHAT_TOOLS,
        prompt=_SYSTEM_PROMPT,
        checkpointer=checkpointer or get_checkpointer(),
    )


def _get_agent():
    global _agent
    if _agent is None:
        _agent = build_chat_agent()
    return _agent


def _reply_from(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if getattr(msg, "type", "") == "ai" and isinstance(msg.content, str) and msg.content.strip():
            return msg.content.strip()
    return "I could not produce an answer — please try rephrasing."


def _turn_tokens(result: dict) -> tuple[int, float]:
    """Return (total_tokens, estimated_cost_usd) across all messages in the result."""
    total, cost = 0, 0.0
    for msg in result.get("messages", []):
        meta = getattr(msg, "usage_metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        inp = int(meta.get("input_tokens", 0) or 0)
        out = int(meta.get("output_tokens", 0) or 0)
        total += inp + out
        # Sonnet-class pricing: $3/M input, $15/M output (conservative estimate)
        cost += inp * 3e-6 + out * 15e-6
    return total, round(cost, 6)


def _record_turn(chat_id: str, tokens: int, cost_usd: float, status: str) -> None:
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    try:
        from persistence.db import session_scope
        from persistence.models import Run

        with session_scope() as s:
            s.add(Run(
                run_id=f"chat-{uuid.uuid4().hex[:12]}",
                report_date=date.today().isoformat(),
                mode="chat",
                status=status,
                cost_usd=cost_usd,
                tokens=tokens,
                finished_at=datetime.utcnow(),
            ))
    except Exception as e:
        logger.warning("could not record chat turn: %s", e)


def _record_intent(chat_id: str, verdict: dict) -> None:
    """Log the routed intent for analytics (best-effort, never raises)."""
    try:
        from persistence import store

        store.record_memory("chat_intent", str(chat_id), {
            "intent": verdict.get("intent"),
            "route": verdict.get("route"),
            "confidence": verdict.get("confidence"),
        })
    except Exception as e:
        logger.debug("could not record intent: %s", e)


def _resolved_model() -> str:
    return getattr(SETTINGS, "CHAT_MODEL", "") or getattr(SETTINGS, "REPORT_MODEL", "unknown")


def _resolved_provider() -> str:
    return getattr(SETTINGS, "LLM_PROVIDER", "unknown")


def run_turn(chat_id: str, text: str) -> str:
    """One user message in → one reply out. Never raises."""
    from agents.supervisor import kill_switch_active

    if kill_switch_active():
        return "⛔ Kill-switch is engaged — the agent is halted. Clear it with run_agents.py --unkill."

    text = (text or "").strip()
    model = _resolved_model()
    provider = _resolved_provider()

    # Extract time/date filters from query and append to agent hint
    from agents.chat.query_filters import extract_filters as _extract_filters
    _qfilters = _extract_filters(text)
    _filter_hint = _qfilters.as_hint()  # "" when no filter found

    # Tiered intent router (semantic cosine → LLM fallback) in front of the
    # expensive ReAct loop — a core front door, self-gating on embedding
    # availability. Fail-open: any error → fall through to the agent.
    hint = ""
    routed_intent = ""
    if text:
        try:
            from agents.chat.intent import (
                CANNED, clarify_reply, is_research_intent, route_intent,
            )

            verdict = route_intent(text)
            routed_intent = verdict["intent"]
            _record_intent(chat_id, verdict)

            if routed_intent in CANNED:
                _record_turn(chat_id, 0, 0.0, "COMPLETED")
                return CANNED[routed_intent]
            if routed_intent == "ambiguous":
                _record_turn(chat_id, 0, 0.0, "COMPLETED")
                return clarify_reply()
            if is_research_intent(routed_intent):
                hint = f"(intent: {routed_intent})\n"
                if _filter_hint:
                    hint += f"{_filter_hint}\n"
        except Exception:
            logger.exception("intent routing failed — proceeding to agent")

    # Append filter hint even if no intent was routed
    if _filter_hint and not hint:
        hint = _filter_hint + "\n"

    # Prompt response cache — check before agent, store after
    _cached_embedding: list[float] | None = None
    from agents.chat import cache as _cache
    try:
        from agents.chat import embedder as _emb
        if _emb.available() and text:
            # NOTE: route_intent() also embeds this text internally for semantic routing.
            # To avoid the double embed, route_intent would need to return the embedding
            # in its verdict dict. Deferred — cost is one extra embed call per turn.
            _cached_embedding = _emb.embed([text])[0].tolist()
    except Exception:
        pass  # embedding failure → skip semantic cache tier

    cached_response = _cache.check(
        text=text, intent=routed_intent, embedding=_cached_embedding
    )
    if cached_response is not None:
        _record_turn(chat_id, 0, 0.0, "CACHE_HIT")
        from observability.chat_tracing import trace_chat_turn
        trace_chat_turn(chat_id, model, provider, 0, 0.0, "CACHE_HIT", routed_intent)
        return cached_response

    from agents.chat.tools import reset_turn_state

    reset_turn_state()
    cfg = {
        "configurable": {"thread_id": str(chat_id)},
        # one model step + one tool step per call → 2 graph steps per tool use
        "recursion_limit": 2 * int(getattr(SETTINGS, "MAX_CHAT_TOOL_CALLS", 8)) + 1,
    }
    from langgraph.errors import GraphRecursionError

    try:
        result = _get_agent().invoke({"messages": [("user", hint + text)]}, cfg)
    except GraphRecursionError:
        logger.warning("chat turn hit the tool-call cap")
        _record_turn(chat_id, 0, 0.0, "MAX_ROUNDS")
        from observability.chat_tracing import trace_chat_turn
        trace_chat_turn(chat_id, model, provider, 0, 0.0, "MAX_ROUNDS", routed_intent)
        return ("I hit my research-step limit on that one. "
                "Try narrowing the question (fewer stocks or one specific filter).")
    except Exception:
        logger.exception("chat turn failed")
        _record_turn(chat_id, 0, 0.0, "FAILED")
        from observability.chat_tracing import trace_chat_turn
        trace_chat_turn(chat_id, model, provider, 0, 0.0, "FAILED", routed_intent)
        return "Something went wrong while researching that — please try again."

    tokens, cost_usd = _turn_tokens(result)
    _record_turn(chat_id, tokens, cost_usd, "COMPLETED")
    from observability.chat_tracing import trace_chat_turn
    trace_chat_turn(chat_id, model, provider, tokens, cost_usd, "COMPLETED", routed_intent)
    reply = _reply_from(result)
    _cache.put(text=text, intent=routed_intent, embedding=_cached_embedding, response=reply)
    return reply
