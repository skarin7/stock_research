"""Tiered intent router for the chat agent.

  tier 1.5 regex     — structural pattern match (query shape, not stock name)
  tier 2  semantic   — cosine match vs the embedded exemplar bank (no LLM)
  tier 3  llm         — one cheap Haiku call, only when semantic sim < threshold

(Tier 1 — the /approve|/reject prefix rule — lives in server/app.py.)

``route_intent`` is fail-open: any embedder/LLM error degrades to the safest
outcome (let the ReAct agent handle it), so the chat never breaks here.
"""

from __future__ import annotations

import json
import logging
import re

from config import SETTINGS

from agents.chat.intent_exemplars import ALL_INTENTS, RESEARCH_INTENTS

logger = logging.getLogger("agents.chat.intent")

from observability.chat_tracing import trace_intent

# Structural patterns that identify an intent regardless of company/stock name.
# Ordered: first match wins. Each tuple is (compiled_regex, intent).
_REGEX_RULES: list[tuple[re.Pattern, str]] = [
    # ticker / symbol / code lookups
    (re.compile(
        r'\b(nse|bse)\s+(code|ticker|symbol|scrip|listing)\b'
        r'|\b(ticker|symbol|scrip)\s+(for|of)\b'
        r'|\bstock\s+(code|symbol|ticker)\b'
        r'|\blisted\s+on\s+(nse|bse)\b'
        r'|\bwhich\s+exchange\s+(is|are)\b'
        r'|\bfind\s+(ticker|symbol|code)\b',
        re.IGNORECASE,
    ), "research"),
    # greetings
    (re.compile(
        r'^\s*(hi+|hello+|hey+|good\s+(morning|evening|afternoon)|thanks?|thank\s+you)\s*[!.]*\s*$',
        re.IGNORECASE,
    ), "greeting"),
    # portfolio
    (re.compile(
        r'\b(my\s+(portfolio|holdings|positions|book)|show\s+portfolio|p&l|pnl)\b',
        re.IGNORECASE,
    ), "portfolio"),
    # trade intent
    (re.compile(
        r'\b(buy|sell|place\s+order|execute\s+trade|square\s+off)\s+\d',
        re.IGNORECASE,
    ), "trade_intent"),
]


def _regex_classify(text: str) -> str | None:
    """Return intent if a structural regex matches, else None."""
    for pattern, intent in _REGEX_RULES:
        if pattern.search(text):
            return intent
    return None

# Canned replies for intents that never need the ReAct loop.
CANNED: dict[str, str] = {
    "greeting": (
        "Hi! I'm your NSE/BSE equity research assistant. Ask me to screen stocks, "
        "check entry/exit timing, explain a market event's sector impact, or review your book."
    ),
    "trade_intent": (
        "I can research and suggest trades, but <b>order placement isn't enabled from chat yet</b>. "
        "I can give you an entry zone, stop and target — want that instead?"
    ),
    "out_of_scope": (
        "I only cover Indian (NSE/BSE) equity research — screening, timing, macro impact, your book. "
        "Ask me something in that space."
    ),
}

_CLARIFY = (
    "I'm not sure what you're asking. Do you want a <b>stock shortlist</b>, "
    "<b>buy/sell timing</b> for a specific stock, or the <b>market impact of an event</b>?"
)

_LLM_PROMPT = (
    "Classify this Indian-stock chat message into exactly one intent.\n"
    "Intents: greeting, research, entry_exit, macro, recall, portfolio, trade_intent, out_of_scope.\n"
    'Reply ONLY compact JSON: {{"intent": "<one>", "confidence": 0.0-1.0}}.\n\n'
    "Message: {text}"
)


def is_research_intent(intent: str) -> bool:
    return intent in RESEARCH_INTENTS


def clarify_reply() -> str:
    return _CLARIFY


def classify_intent_llm(text: str) -> dict:
    """Fallback classifier — one cheap LLM call. Never raises; low confidence → ambiguous."""
    try:
        from agents.llm import get_chat_model

        model = get_chat_model(
            model=getattr(SETTINGS, "CHAT_INTENT_MODEL", "") or SETTINGS.SCORING_MODEL,
            max_tokens=120, temperature=0.0,
        )
        resp = model.invoke(_LLM_PROMPT.format(text=text))
        raw = getattr(resp, "content", resp)
        if isinstance(raw, list):
            raw = "".join(getattr(b, "text", str(b)) for b in raw)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        intent = str(data.get("intent", "")).strip()
        conf = float(data.get("confidence", 0.0) or 0.0)
    except Exception as e:
        logger.warning("intent LLM classify failed: %s", e)
        return {"intent": "ambiguous", "confidence": 0.0, "route": "llm"}

    min_conf = float(getattr(SETTINGS, "CHAT_INTENT_MIN_CONFIDENCE", 0.6))
    if intent not in ALL_INTENTS or conf < min_conf:
        intent = "ambiguous"
    return {"intent": intent, "confidence": conf, "route": "llm"}


def route_intent(text: str) -> dict:
    """Return {intent, confidence, route}.

    Tier 1.5 regex → tier 2 semantic → tier 3 LLM fallback.
    """
    # Tier 1.5: structural regex — zero cost, zero latency, company-name-agnostic
    regex_intent = _regex_classify(text)
    if regex_intent:
        verdict = {"intent": regex_intent, "confidence": 1.0, "route": "regex"}
        trace_intent(route="regex", intent=regex_intent, score=1.0)
        return verdict

    threshold = float(getattr(SETTINGS, "CHAT_SEMANTIC_THRESHOLD", 0.55))
    from agents.chat import embedder

    if embedder.available():
        try:
            intent, sim = embedder.nearest_intent(text)
            if sim >= threshold:
                verdict = {"intent": intent, "confidence": round(sim, 3), "route": "semantic"}
                trace_intent(route="semantic", intent=intent, score=sim)
                return verdict
            logger.debug("semantic sim %.3f < %.2f → LLM fallback", sim, threshold)
            trace_intent(route="llm_fallback", intent="", score=sim)
        except Exception as e:
            logger.warning("semantic router error (%s) — LLM fallback", e)

    verdict = classify_intent_llm(text)
    trace_intent(
        route=verdict.get("route", "llm"),
        intent=verdict.get("intent", ""),
        score=0.0,
        confidence=verdict.get("confidence"),
    )
    return verdict
