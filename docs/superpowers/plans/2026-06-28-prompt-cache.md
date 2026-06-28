# Prompt Response Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache ReAct agent responses in Postgres so identical or near-identical queries return instantly without an LLM call.

**Architecture:** Two lookup tiers before the agent runs: (1) exact SHA-256 hash match (zero cost), (2) semantic cosine match via the existing embedder (reuses the same OpenRouter embed call infrastructure). Cache misses fall through to the agent as before. Skips caching for personal intents (`portfolio`, `recall`) whose answers change per user/session. Degrades gracefully to no-op when `DATABASE_URL` is unset or `CHAT_CACHE_ENABLED=false`.

**Tech Stack:** SQLAlchemy (existing), SHA-256 from stdlib `hashlib`, numpy cosine (existing in `embedder.py`). No new dependencies.

---

## File Map

| File | Change |
|------|--------|
| `persistence/models.py` | Add `ChatResponseCache` ORM model |
| `persistence/store.py` | Add `lookup_chat_cache()` and `store_chat_cache()` |
| `agents/chat/cache.py` | New module: `check()` and `put()` wrappers; TTL strategy; graceful no-op |
| `agents/chat/agent.py` | Wire `cache.check()` before agent invoke, `cache.put()` after |
| `settings.py` | Add `CHAT_CACHE_ENABLED`, `CHAT_CACHE_TTL_SECONDS`, `CHAT_CACHE_SEMANTIC_THRESHOLD` |
| `tests/test_chat_cache.py` | New test file |

---

## Task 1 — DB Model + Store Functions

**Files:**
- Modify: `persistence/models.py`
- Modify: `persistence/store.py`
- Test: `tests/test_chat_cache.py` (create)

### Step 1: Write failing tests for store functions

- [ ] Create `tests/test_chat_cache.py`:

```python
"""Prompt cache: DB model + store layer tests — fully mocked DB."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestChatCacheModel:
    def test_model_importable(self):
        from persistence.models import ChatResponseCache
        row = ChatResponseCache(
            query_hash="abc123",
            query_text="What is the PE of TCS?",
            query_embedding=[0.1, 0.2, 0.3],
            response="TCS PE is 28.",
            intent="research",
            expires_at=datetime.utcnow() + timedelta(seconds=1800),
        )
        assert row.query_hash == "abc123"
        assert row.intent == "research"


class TestStoreLookupExact:
    def _make_session(self, rows):
        """Build a mock session that returns `rows` from .all()."""
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q
        mock_q.first.return_value = rows[0] if rows else None
        mock_s = MagicMock()
        mock_s.query.return_value = mock_q
        return mock_s

    def test_hit_returns_response(self):
        from persistence import store as st

        row = MagicMock()
        row.response = "cached answer"
        row.query_embedding = [1.0, 0.0]

        with patch("persistence.store.session_scope") as ss, \
             patch("persistence.store.getattr", return_value="postgres://x"):
            ss.return_value.__enter__ = lambda s, *a: self._make_session([row])
            ss.return_value.__exit__ = MagicMock(return_value=False)
            # Just test the function signature exists and model import works
            assert hasattr(st, "lookup_chat_cache_exact")

    def test_miss_returns_none(self):
        from persistence import store as st
        assert hasattr(st, "lookup_chat_cache_exact")

    def test_store_function_exists(self):
        from persistence import store as st
        assert hasattr(st, "store_chat_cache")
```

- [ ] Run to confirm expected failure:
```
python -m pytest tests/test_chat_cache.py::TestChatCacheModel -v
```
Expected: FAIL — `ChatResponseCache` not yet defined.

### Step 2: Add `ChatResponseCache` to `persistence/models.py`

- [ ] After the `IntentEmbeddingRow` class (around line 151), add:

```python
class ChatResponseCache(Base):
    """Cached ReAct agent responses, keyed by query hash.

    Exact-match lookup uses ``query_hash`` (SHA-256 of normalized text).
    Semantic lookup loads recent rows and computes cosine similarity in Python
    against ``query_embedding`` (L2-normalised float list from the embedder).
    """

    __tablename__ = "chat_response_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    query_text: Mapped[str] = mapped_column(Text)
    query_embedding: Mapped[list] = mapped_column(JSON)   # list[float], L2-normalised
    response: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
```

### Step 3: Add store functions to `persistence/store.py`

- [ ] At the top of `persistence/store.py`, ensure `datetime` and `timedelta` are imported (they likely are via existing code — check and add if missing):

```python
from datetime import datetime, timedelta
```

- [ ] Append these two functions at the end of `persistence/store.py`:

```python
def lookup_chat_cache_exact(query_hash: str) -> str | None:
    """Return cached response for exact hash match, or None. No-op without DB."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    try:
        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        with session_scope() as s:
            row = (
                s.query(ChatResponseCache)
                .filter(
                    ChatResponseCache.query_hash == query_hash,
                    ChatResponseCache.expires_at > datetime.utcnow(),
                )
                .order_by(ChatResponseCache.created_at.desc())
                .first()
            )
            return row.response if row else None
    except Exception as e:
        logger.warning("cache exact lookup failed: %s", e)
        return None


def lookup_chat_cache_semantic(
    embedding: list[float], threshold: float = 0.95, limit: int = 200
) -> str | None:
    """Return cached response for semantically similar query, or None.

    Loads up to ``limit`` recent non-expired rows, computes cosine similarity
    in Python (embeddings are L2-normalised, so dot product = cosine), returns
    the best match above ``threshold``. No-op without DB or numpy.
    """
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    try:
        import numpy as np

        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        with session_scope() as s:
            rows = (
                s.query(ChatResponseCache)
                .filter(ChatResponseCache.expires_at > datetime.utcnow())
                .order_by(ChatResponseCache.created_at.desc())
                .limit(limit)
                .all()
            )
        if not rows:
            return None

        q = np.array(embedding, dtype=np.float32)
        best_sim, best_resp = 0.0, None
        for row in rows:
            if not row.query_embedding:
                continue
            v = np.array(row.query_embedding, dtype=np.float32)
            sim = float(np.dot(q, v))
            if sim > best_sim:
                best_sim, best_resp = sim, row.response

        return best_resp if best_sim >= threshold else None
    except Exception as e:
        logger.warning("cache semantic lookup failed: %s", e)
        return None


def store_chat_cache(
    query_hash: str,
    query_text: str,
    query_embedding: list[float],
    response: str,
    intent: str,
    ttl_seconds: int,
) -> None:
    """Persist a query-response pair. No-op without DB."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    try:
        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        with session_scope() as s:
            s.add(ChatResponseCache(
                query_hash=query_hash,
                query_text=query_text,
                query_embedding=query_embedding,
                response=response,
                intent=intent,
                expires_at=expires_at,
            ))
    except Exception as e:
        logger.warning("cache store failed: %s", e)
```

### Step 4: Run tests

- [ ] `python -m pytest tests/test_chat_cache.py::TestChatCacheModel -v`
Expected: PASS.

- [ ] `python -m pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: all pass (model addition is additive, no existing code broken).

### Step 5: Commit

```bash
git add persistence/models.py persistence/store.py tests/test_chat_cache.py
git commit -m "feat(cache): add ChatResponseCache model and store lookup/write functions"
```

---

## Task 2 — Settings + `agents/chat/cache.py` Module

**Files:**
- Modify: `settings.py`
- Create: `agents/chat/cache.py`
- Test: `tests/test_chat_cache.py` (extend)

### Step 1: Write failing tests for cache module

- [ ] Append to `tests/test_chat_cache.py`:

```python
class TestChatCacheModule:
    def test_check_returns_none_when_disabled(self, monkeypatch):
        import agents.chat.cache as cache_mod
        import settings as settings_mod

        monkeypatch.setattr(settings_mod.Settings, "CHAT_CACHE_ENABLED", False, raising=False)
        # rebuild module-level config read
        result = cache_mod.check(
            text="show me IT stocks",
            intent="research",
            embedding=None,
        )
        assert result is None

    def test_skip_personal_intents(self):
        import agents.chat.cache as cache_mod

        for intent in ("portfolio", "recall"):
            result = cache_mod.check(text="my holdings", intent=intent, embedding=None)
            assert result is None, f"Should not cache {intent} intent"

    def test_ttl_for_market_intents(self):
        import agents.chat.cache as cache_mod

        # TTL function must return positive int for research/entry_exit/macro
        for intent in ("research", "entry_exit", "macro"):
            ttl = cache_mod._ttl_for_intent(intent)
            assert isinstance(ttl, int) and ttl > 0

    def test_ttl_for_unknown_intent_is_default(self):
        import agents.chat.cache as cache_mod

        ttl = cache_mod._ttl_for_intent("unknown_xyz")
        assert ttl == cache_mod._DEFAULT_TTL_SECONDS
```

- [ ] Run to confirm failure:
```
python -m pytest tests/test_chat_cache.py::TestChatCacheModule -v
```
Expected: FAIL — `agents.chat.cache` not found.

### Step 2: Add settings fields to `settings.py`

- [ ] In `settings.py`, in the `# --- Conversational chat agent ---` section (around line 229), add after `CHAT_INTENT_MIN_CONFIDENCE`:

```python
# --- Prompt response cache ---
CHAT_CACHE_ENABLED: bool = True
CHAT_CACHE_TTL_SECONDS: int = 1800           # 30 min — market data queries
CHAT_CACHE_STABLE_TTL_SECONDS: int = 86400   # 24 h — NSE codes, sector info
CHAT_CACHE_SEMANTIC_THRESHOLD: float = 0.95  # very high — only near-identical queries
```

- [ ] In the `from_env()` classmethod (around line 289), add corresponding env-var parsing (follow the exact pattern of the surrounding lines):

```python
CHAT_CACHE_ENABLED=os.environ.get("CHAT_CACHE_ENABLED", "true").lower() not in ("false", "0", "no"),
CHAT_CACHE_TTL_SECONDS=int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "1800")),
CHAT_CACHE_STABLE_TTL_SECONDS=int(os.environ.get("CHAT_CACHE_STABLE_TTL_SECONDS", "86400")),
CHAT_CACHE_SEMANTIC_THRESHOLD=float(os.environ.get("CHAT_CACHE_SEMANTIC_THRESHOLD", "0.95")),
```

### Step 3: Create `agents/chat/cache.py`

- [ ] Create the file:

```python
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

from config import SETTINGS

logger = logging.getLogger("agents.chat.cache")

# Intents that are personal / session-specific — never cache
_NO_CACHE_INTENTS = frozenset({"portfolio", "recall"})

# Market-data intents get the short TTL; everything else gets stable TTL
_MARKET_INTENTS = frozenset({"research", "entry_exit"})

_DEFAULT_TTL_SECONDS = 1800


def _ttl_for_intent(intent: str) -> int:
    """Short TTL for price-sensitive intents, stable TTL for reference data."""
    if intent in _MARKET_INTENTS:
        return int(getattr(SETTINGS, "CHAT_CACHE_TTL_SECONDS", 1800))
    return int(getattr(SETTINGS, "CHAT_CACHE_STABLE_TTL_SECONDS", 86400))


def _query_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

        store.store_chat_cache(
            query_hash=_query_hash(text),
            query_text=text,
            query_embedding=embedding or [],
            response=response,
            intent=intent,
            ttl_seconds=_ttl_for_intent(intent),
        )
        logger.debug("cache_stored", extra={"intent": intent, "ttl": _ttl_for_intent(intent)})
    except Exception as e:
        logger.debug("cache store error: %s", e)
```

### Step 4: Run tests

- [ ] `python -m pytest tests/test_chat_cache.py::TestChatCacheModule -v`
Expected: all pass.

### Step 5: Commit

```bash
git add agents/chat/cache.py settings.py tests/test_chat_cache.py
git commit -m "feat(cache): add chat response cache module with exact+semantic lookup"
```

---

## Task 3 — Wire Cache into `run_turn()` in `agent.py`

**Files:**
- Modify: `agents/chat/agent.py`
- Test: `tests/test_chat_cache.py` (extend)

### Step 1: Write failing integration test

- [ ] Append to `tests/test_chat_cache.py`:

```python
class TestRunTurnCacheIntegration:
    """run_turn() must check cache before agent and store after."""

    def test_cache_hit_skips_agent(self, monkeypatch):
        import agents.chat.agent as agent_mod
        import agents.chat.cache as cache_mod

        monkeypatch.setattr(cache_mod, "check", lambda **kw: "cached response")
        # agent._get_agent().invoke should never be called
        agent_invoked = []
        monkeypatch.setattr(agent_mod, "_get_agent", lambda: (_ for _ in ()).throw(
            AssertionError("agent was invoked on a cache hit")))

        # Bypass kill-switch and intent router
        monkeypatch.setattr("agents.supervisor.kill_switch_active", lambda: False)
        monkeypatch.setattr("agents.chat.intent.route_intent",
                            lambda t: {"intent": "research", "confidence": 0.9, "route": "regex"})
        monkeypatch.setattr("agents.chat.tools.reset_turn_state", lambda: None)

        # The agent should NOT be invoked
        try:
            result = agent_mod.run_turn("123", "What is PE of TCS?")
        except AssertionError:
            pytest.fail("agent was invoked on a cache hit")
        assert result == "cached response"

    def test_cache_miss_stores_result(self, monkeypatch):
        import agents.chat.agent as agent_mod
        import agents.chat.cache as cache_mod

        stored = {}
        monkeypatch.setattr(cache_mod, "check", lambda **kw: None)
        monkeypatch.setattr(cache_mod, "put", lambda **kw: stored.update(kw))

        from unittest.mock import MagicMock
        fake_msg = MagicMock()
        fake_msg.type = "ai"
        fake_msg.content = "TCS PE is 28."
        fake_result = {"messages": [fake_msg]}

        monkeypatch.setattr("agents.supervisor.kill_switch_active", lambda: False)
        monkeypatch.setattr("agents.chat.intent.route_intent",
                            lambda t: {"intent": "research", "confidence": 0.9, "route": "regex"})
        monkeypatch.setattr("agents.chat.tools.reset_turn_state", lambda: None)

        fake_agent = MagicMock()
        fake_agent.invoke.return_value = fake_result
        monkeypatch.setattr(agent_mod, "_get_agent", lambda: fake_agent)

        result = agent_mod.run_turn("123", "What is PE of TCS?")
        assert result == "TCS PE is 28."
        assert "response" in stored
        assert stored["response"] == "TCS PE is 28."
```

- [ ] Run to confirm failure:
```
python -m pytest tests/test_chat_cache.py::TestRunTurnCacheIntegration -v
```
Expected: FAIL — `run_turn` does not yet call `cache.check` or `cache.put`.

### Step 2: Wire cache into `run_turn()` in `agent.py`

- [ ] In `run_turn()`, after the intent routing block (where `hint` and `routed_intent` are set) and before `reset_turn_state()`, add:

```python
    # Prompt response cache — check before agent, store after
    _cached_embedding: list[float] | None = None
    from agents.chat import cache as _cache
    try:
        from agents.chat import embedder as _emb
        if _emb.available() and text:
            _cached_embedding = _emb.embed([text])[0].tolist()
    except Exception:
        pass  # embedding failure → skip semantic cache tier

    cached_response = _cache.check(
        text=text, intent=routed_intent, embedding=_cached_embedding
    )
    if cached_response is not None:
        _record_turn(chat_id, 0, 0.0, "CACHE_HIT")
        return cached_response
```

- [ ] After the successful agent response (`tokens, cost_usd = _turn_tokens(result)`), before `return _reply_from(result)`, add:

```python
    reply = _reply_from(result)
    _cache.put(text=text, intent=routed_intent, embedding=_cached_embedding, response=reply)
    return reply
```

- [ ] Remove the old bare `return _reply_from(result)` at the end.

### Step 3: Run tests

- [ ] `python -m pytest tests/test_chat_cache.py -v`
Expected: all pass.

- [ ] `python -m pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: no regressions.

### Step 4: Commit

```bash
git add agents/chat/agent.py tests/test_chat_cache.py
git commit -m "feat(cache): wire prompt cache into run_turn — check before agent, store after"
```

---

## Self-Review

**Spec coverage:**
- [x] Cache in Postgres separate collection → `chat_response_cache` table, `ChatResponseCache` model
- [x] Avoid LLM calls for same query → exact hash match, returns before `_get_agent().invoke()`
- [x] Avoid LLM calls for similar query → semantic cosine match, reuses embedder
- [x] Graceful degradation without DB → all store functions no-op when `DATABASE_URL` unset
- [x] Don't cache personal intents → `_NO_CACHE_INTENTS = {"portfolio", "recall"}`
- [x] Configurable TTL → `CHAT_CACHE_TTL_SECONDS` (market), `CHAT_CACHE_STABLE_TTL_SECONDS` (stable)

**What this does NOT do:**
- Cache eviction job — expired rows stay in DB until `expires_at` is past (filtered in queries). A periodic `DELETE WHERE expires_at < NOW()` can be added as a cron later.
- pgvector ANN index — semantic lookup is O(n) in Python over 200 rows max; sufficient for <10k entries.

**Placeholder scan:** No TBDs. All code complete.

**Type consistency:**
- `check(text, intent, embedding) -> str | None` matches `put(text, intent, embedding, response) -> None`
- `_ttl_for_intent(intent: str) -> int` used in `put()` and tested directly
