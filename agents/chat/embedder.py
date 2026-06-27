"""Query embedder + Postgres-cached exemplar bank for the semantic router.

Embeddings come from an **OpenAI-compatible endpoint via OpenRouter** (model
`CHAT_EMBED_MODEL`, default `openai/text-embedding-3-small`) using the existing
`openai` client + `OPENROUTER_*` config.

The master exemplar bank (``intent_exemplars.EXEMPLARS``) is embedded **once** and
cached in Postgres (`intent_embeddings`, keyed by an exemplar-set hash + model);
since the master intents rarely change, this is a one-time cost — edits bump the
hash and trigger a single re-embed. Per user message: **one** query embedding,
then cosine on CPU against the PG-loaded bank.

No DB? Falls back to embedding the bank once per process (in-memory memo). Embed
backend error? ``nearest_intent`` raises and the router falls back to the LLM
classifier — the chat never breaks here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading

import numpy as np

from config import SETTINGS

from agents.chat.intent_exemplars import EXEMPLARS

logger = logging.getLogger("agents.chat.embedder")

_client = None
_client_lock = threading.Lock()
_bank = None  # (labels: list[str], vectors: np.ndarray)


def _model_name() -> str:
    return getattr(SETTINGS, "CHAT_EMBED_MODEL", "") or "openai/text-embedding-3-small"


def _get_client():
    """Lazily build an OpenAI-compatible client pointed at OpenRouter."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI  # lazy; already a dependency

            _client = OpenAI(
                api_key=getattr(SETTINGS, "OPENROUTER_API_KEY", "") or "",
                base_url=getattr(SETTINGS, "OPENROUTER_BASE_URL", "")
                or "https://openrouter.ai/api/v1",
            )
    return _client


def embed(texts: list[str]) -> np.ndarray:
    """Embed texts → L2-normalised float32 matrix (n, dim). Raises on backend failure."""
    resp = _get_client().embeddings.create(model=_model_name(), input=list(texts))
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def _exemplar_hash() -> str:
    payload = json.dumps(EXEMPLARS, sort_keys=True) + "|" + _model_name()
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _flatten() -> tuple[list[str], list[str]]:
    """(labels, phrases) flattened from EXEMPLARS in a stable order."""
    labels, phrases = [], []
    for intent in sorted(EXEMPLARS):
        for phrase in EXEMPLARS[intent]:
            labels.append(intent)
            phrases.append(phrase)
    return labels, phrases


def bank_vectors() -> tuple[list[str], np.ndarray]:
    """(labels, vectors) for the exemplar bank — memoized; PG-cached when DATABASE_URL is set."""
    global _bank
    if _bank is not None:
        return _bank

    from persistence import store

    labels, phrases = _flatten()
    h, model = _exemplar_hash(), _model_name()

    cached = store.load_intent_bank(h, model)   # None when absent / no DB
    if cached is not None:
        clabels, cvecs = cached
        _bank = (clabels, np.array(cvecs, dtype=np.float32))
        logger.info("intent bank loaded from PG (%d exemplars)", len(clabels))
        return _bank

    vectors = embed(phrases)                     # one-time embed; may raise → LLM fallback
    store.save_intent_bank(h, model, labels, vectors.tolist())  # no-op without DB
    _bank = (labels, vectors)
    logger.info("intent bank embedded (%d exemplars, model=%s)", len(labels), model)
    return _bank


def nearest_intent(text: str) -> tuple[str, float]:
    """Best-matching intent + cosine similarity for one message. Raises on backend failure."""
    labels, vectors = bank_vectors()
    q = embed([text])[0]
    sims = vectors @ q  # both L2-normalised → cosine
    idx = int(np.argmax(sims))
    return labels[idx], float(sims[idx])
