"""Prompt response cache: check before the ReAct agent, put after.

Two-tier lookup:
  1. Exact SHA-256 hash of NFKC-normalised text → instant DB read
  2. Semantic cosine similarity against recent cache embeddings (reuses embedder)

Never raises — all errors degrade to a cache miss (None).
Skips caching for personal intents whose answers are session-specific.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata

from config import SETTINGS

logger = logging.getLogger("agents.chat.cache")

# Intents that are personal / session-specific — never cache
_NO_CACHE_INTENTS = frozenset({"portfolio", "recall"})

# Market-data intents get the short TTL; everything else gets stable TTL
_MARKET_INTENTS = frozenset({"research", "entry_exit", "macro"})

_DEFAULT_TTL_SECONDS = 1800


def _ttl_for_intent(intent: str) -> int:
    """Short TTL for price-sensitive intents, stable TTL for reference data."""
    if intent in _MARKET_INTENTS:
        return int(getattr(SETTINGS, "CHAT_CACHE_TTL_SECONDS", 1800))
    return int(getattr(SETTINGS, "CHAT_CACHE_STABLE_TTL_SECONDS", 86400))


def _query_hash(text: str) -> str:
    normalised = unicodedata.normalize("NFKC", text)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def check(text: str, intent: str, embedding: list[float] | None) -> str | None:
    """Return cached response string or None (cache miss / disabled / error)."""
    if not getattr(SETTINGS, "CHAT_CACHE_ENABLED", True):
        return None
    if intent in _NO_CACHE_INTENTS:
        return None
    if not text:
        return None

    try:
        from persistence import store

        # Tier 1: exact match
        hit = store.lookup_chat_cache_exact(_query_hash(text))
        if hit is not None:
            logger.info("cache_hit", extra={"tier": "exact", "intent": intent})
            return hit

        # Tier 2: semantic match (only when embedder ran for this turn)
        if embedding is not None:
            threshold = float(getattr(SETTINGS, "CHAT_CACHE_SEMANTIC_THRESHOLD", 0.95))
            hit = store.lookup_chat_cache_semantic(embedding, threshold=threshold)
            if hit is not None:
                logger.info("cache_hit", extra={"tier": "semantic", "intent": intent})
                return hit

    except Exception as e:
        logger.debug("cache check error (miss): %s", e)

    return None


def put(
    text: str,
    intent: str,
    embedding: list[float] | None,
    response: str,
) -> None:
    """Store a response in the cache. Never raises."""
    if not getattr(SETTINGS, "CHAT_CACHE_ENABLED", True):
        return
    if intent in _NO_CACHE_INTENTS:
        return
    if not text or not response:
        return

    try:
        from persistence import store

        ttl = _ttl_for_intent(intent)
        store.store_chat_cache(
            query_hash=_query_hash(text),
            query_text=text,
            query_embedding=embedding or [],
            response=response,
            intent=intent,
            ttl_seconds=ttl,
        )
        logger.debug("cache_stored", extra={"intent": intent, "ttl": ttl})
    except Exception as e:
        logger.debug("cache store error: %s", e)
