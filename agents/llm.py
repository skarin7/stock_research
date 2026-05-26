"""LLM factory for the reasoning agents.

Wraps ``ChatAnthropic`` (LangChain) and attaches the Langfuse callback when
configured. Imports are lazy so the rest of the agent layer (and the test
suite) can import ``agents.*`` without LangChain/Langfuse installed.
"""

from __future__ import annotations

from typing import Any, Optional

import config
import llm_router


def get_chat_model(model: Optional[str] = None, max_tokens: int = 1024, temperature: float = 0.0):
    """Return a configured LangChain chat model with the Langfuse callback attached.

    Provider-aware: ChatOpenAI pointed at OpenRouter when config.LLM_PROVIDER ==
    "openrouter", otherwise ChatAnthropic. ``model`` overrides the per-provider default.
    """
    from observability.langfuse_cb import get_callbacks

    if llm_router.is_openrouter():
        from langchain_openai import ChatOpenAI  # lazy

        return ChatOpenAI(
            model=model or llm_router.scoring_model(),
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL,
            max_tokens=max_tokens,
            temperature=temperature,
            callbacks=get_callbacks(),
        )

    from langchain_anthropic import ChatAnthropic  # lazy

    return ChatAnthropic(
        model=model or config.SCORING_MODEL,
        api_key=config.ANTHROPIC_API_KEY,
        max_tokens=max_tokens,
        temperature=temperature,
        callbacks=get_callbacks(),
    )


def accrue_cost(state: dict, *, tokens: int = 0, usd: float = 0.0) -> dict:
    """Return a state-update dict that accumulates token/cost usage."""
    return {
        "tokens": (state.get("tokens", 0) or 0) + int(tokens),
        "cost_usd": round((state.get("cost_usd", 0.0) or 0.0) + float(usd), 6),
    }


def usage_from_response(resp: Any) -> tuple[int, float]:
    """Best-effort (tokens, usd) extraction from a LangChain/Anthropic response.

    Cost is left to Langfuse for authoritative pricing; here we only sum tokens
    so the in-graph budget guard has something to act on.
    """
    meta = getattr(resp, "usage_metadata", None) or {}
    tokens = int(meta.get("total_tokens", 0)) if isinstance(meta, dict) else 0
    return tokens, 0.0
