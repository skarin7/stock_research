"""Master data for the semantic intent router.

Curated example phrasings per intent. These are embedded once (see
``agents/chat/embedder.py``) and the incoming message is matched against them by
cosine similarity — no LLM call on the common path. Extend freely: add phrasings
to improve coverage; the on-disk embedding cache re-builds when this set changes.

Intent vocabulary (must stay in sync with ``intent.py``):
  greeting       — hi/thanks/smalltalk
  research       — find/screen/compare stocks
  entry_exit     — when to buy/sell, levels, timing
  macro          — events/geopolitics/sector impact
  recall         — what did you say about X before
  portfolio      — my holdings / positions / P&L
  trade_intent   — place/modify an order (not supported from chat)
  out_of_scope   — unrelated to Indian equities research
"""

from __future__ import annotations

EXEMPLARS: dict[str, list[str]] = {
    "greeting": [
        "hi", "hello", "hey there", "good morning", "thanks", "thank you",
        "ok cool", "got it", "how are you",
    ],
    "research": [
        "best stocks to buy now", "show me undervalued IT stocks",
        "screen for high delivery percentage stocks", "top picks today",
        "which pharma stocks look good", "compare TCS and Infosys",
        "find stocks with strong momentum", "any good largecap value stocks",
        "what's scoring well in the snapshot", "give me a shortlist of banks",
        "tell me about RELIANCE fundamentals",
        "what sector is HDFC in", "PE ratio of Infosys",
    ],
    "entry_exit": [
        "when should I buy TCS", "good entry price for INFY",
        "is it a good time to sell RELIANCE", "what's the stop loss for HDFC",
        "target price for SBIN", "should I book profit now",
        "entry and exit levels for ITC", "is this a good buy zone",
        "where to add more of this stock",
    ],
    "macro": [
        "impact of the Iran war on markets", "how will crude oil spike affect stocks",
        "what does the RBI rate decision mean", "effect of US tariffs on Indian IT",
        "which sectors benefit from a weak rupee", "global market crash impact on Nifty",
        "budget impact on auto stocks", "geopolitical risk to the market",
    ],
    "recall": [
        "what did you think of TCS last time", "your previous view on INFY",
        "did you recommend RELIANCE before", "what was your call on HDFC earlier",
        "remind me what you said about this stock",
    ],
    "portfolio": [
        "show my portfolio", "what are my holdings", "how is my book doing",
        "my current positions", "what's my P&L", "how much cash do I have",
    ],
    "trade_intent": [
        "buy 10 shares of TCS", "place an order for INFY", "sell my RELIANCE",
        "execute a trade", "buy 50 SBIN at market", "square off my position",
    ],
    "out_of_scope": [
        "what's the weather today", "tell me a joke", "who won the cricket match",
        "write me a poem", "what is the capital of France", "book me a flight",
    ],
}

# Intents that proceed to the research ReAct agent (vs. canned/clarify).
RESEARCH_INTENTS = frozenset({"research", "entry_exit", "macro", "recall", "portfolio"})

# All known intents (+ the ambiguous sentinel produced by the LLM fallback).
ALL_INTENTS = frozenset(EXEMPLARS) | {"ambiguous"}
