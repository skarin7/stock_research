"""The chat agent loop: one LangGraph ReAct agent, one thread per Telegram chat.

``run_turn(chat_id, text)`` is the single entrypoint both the webhook server
and the local polling script call. It honours the kill-switch, bounds the tool
loop via recursion_limit, and records each turn in the runs table (mode="chat")
when a database is configured.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from config import SETTINGS

logger = logging.getLogger("agents.chat")

_SYSTEM_PROMPT = """You are an equity research assistant for Indian (NSE/BSE) markets, \
chatting with your user on Telegram.

Data discipline:
- Start with screen_snapshot (cached nightly scored universe) to find candidates; \
use live_quote / fetch_news only on the shortlist for freshness.
- Use score_subset only when fresh scores genuinely change the answer (it costs money). \
deep_dive is for one named stock the user wants examined closely — at most once per question.
- Always state the snapshot as-of date, and warn clearly when data is flagged stale.
- If a tool returns an error, say what data was unavailable and answer with what you have.

Answer style:
- Telegram HTML only: <b>bold</b>, <i>italic</i> — no markdown, no tables, no headers.
- Be concise: a ranked shortlist with one-line reasons beats an essay. Keep replies under \
3000 characters.
- You give research and analysis, not guaranteed outcomes. No "sure-shot"/"guaranteed" \
language; mention key risks when recommending.
- You cannot place orders. If asked to trade, say order placement is not enabled from chat yet."""

_agent = None


def build_chat_agent(checkpointer=None):
    """Compile the ReAct agent (model + tools + per-chat memory)."""
    from langgraph.prebuilt import create_react_agent

    from agents.chat.tools import CHAT_TOOLS
    from agents.graph import get_checkpointer
    from agents.llm import get_chat_model

    model = get_chat_model(
        model=getattr(SETTINGS, "CHAT_MODEL", "") or SETTINGS.REPORT_MODEL,
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


def _turn_tokens(result: dict) -> int:
    total = 0
    for msg in result.get("messages", []):
        meta = getattr(msg, "usage_metadata", None) or {}
        if isinstance(meta, dict):
            total += int(meta.get("total_tokens", 0) or 0)
    return total


def _record_turn(chat_id: str, tokens: int, status: str) -> None:
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
                cost_usd=0.0,
                tokens=tokens,
                finished_at=datetime.utcnow(),
            ))
    except Exception as e:
        logger.warning("could not record chat turn: %s", e)


def run_turn(chat_id: str, text: str) -> str:
    """One user message in → one reply out. Never raises."""
    from agents.supervisor import kill_switch_active

    if kill_switch_active():
        return "⛔ Kill-switch is engaged — the agent is halted. Clear it with run_agents.py --unkill."

    from agents.chat.tools import reset_turn_state

    reset_turn_state()
    cfg = {
        "configurable": {"thread_id": str(chat_id)},
        # one model step + one tool step per call → 2 graph steps per tool use
        "recursion_limit": 2 * int(getattr(SETTINGS, "MAX_CHAT_TOOL_CALLS", 8)) + 1,
    }
    try:
        result = _get_agent().invoke({"messages": [("user", text)]}, cfg)
    except Exception as e:
        if type(e).__name__ == "GraphRecursionError":
            logger.warning("chat turn hit the tool-call cap")
            _record_turn(chat_id, 0, "MAX_ROUNDS")
            return ("I hit my research-step limit on that one. "
                    "Try narrowing the question (fewer stocks or one specific filter).")
        logger.exception("chat turn failed")
        _record_turn(chat_id, 0, "FAILED")
        return "Something went wrong while researching that — please try again."

    tokens = _turn_tokens(result)
    _record_turn(chat_id, tokens, "COMPLETED")
    logger.info("chat turn done (chat=%s, tokens=%d)", chat_id, tokens)
    return _reply_from(result)
