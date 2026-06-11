"""Provider-agnostic LLM routing.

Lets every LLM call site switch between Anthropic (default) and OpenRouter
(OpenAI-compatible, cheap reasoning models) via ``SETTINGS.LLM_PROVIDER`` without
each module re-implementing client setup or model resolution.

The OpenRouter client is the ``openai`` SDK pointed at OpenRouter's base URL;
it is imported lazily so the package only needs to be installed when actually
using the openrouter provider.
"""

from __future__ import annotations

from config import SETTINGS

_openrouter_client = None


def provider() -> str:
    """Active provider: 'anthropic' (default) or 'openrouter'."""
    return getattr(SETTINGS, "LLM_PROVIDER", "anthropic")


def is_openrouter() -> bool:
    return provider() == "openrouter"


def scoring_model() -> str:
    return SETTINGS.OPENROUTER_SCORING_MODEL if is_openrouter() else SETTINGS.SCORING_MODEL


def report_model() -> str:
    return SETTINGS.OPENROUTER_REPORT_MODEL if is_openrouter() else SETTINGS.REPORT_MODEL


def chat_model() -> str:
    return SETTINGS.OPENROUTER_CHAT_MODEL if is_openrouter() else SETTINGS.REPORT_MODEL


def openrouter_client():
    """Cached OpenAI-SDK client pointed at OpenRouter."""
    global _openrouter_client
    if _openrouter_client is None:
        from openai import OpenAI  # lazy

        _openrouter_client = OpenAI(
            base_url=SETTINGS.OPENROUTER_BASE_URL,
            api_key=SETTINGS.OPENROUTER_API_KEY,
        )
    return _openrouter_client
