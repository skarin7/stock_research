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

    def test_hit_returns_response(self, monkeypatch):
        from persistence import store as st
        from persistence.models import ChatResponseCache

        row = MagicMock(spec=ChatResponseCache)
        row.response = "cached answer"

        mock_session = self._make_session([row])

        with patch("persistence.store.SETTINGS") as mock_settings, \
             patch("persistence.db.session_scope") as mock_ss:
            mock_settings.DATABASE_URL = "postgres://x"
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = st.lookup_chat_cache_exact("abc123")
        assert result == "cached answer"

    def test_miss_returns_none(self, monkeypatch):
        from persistence import store as st

        mock_session = self._make_session([])

        with patch("persistence.store.SETTINGS") as mock_settings, \
             patch("persistence.db.session_scope") as mock_ss:
            mock_settings.DATABASE_URL = "postgres://x"
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            result = st.lookup_chat_cache_exact("nohash")
        assert result is None

    def test_no_db_returns_none(self):
        from persistence import store as st

        with patch("persistence.store.SETTINGS") as mock_settings:
            mock_settings.DATABASE_URL = ""
            result = st.lookup_chat_cache_exact("abc123")
        assert result is None

    def test_store_function_exists(self):
        from persistence import store as st
        assert hasattr(st, "store_chat_cache")


class TestStoreLookupSemantic:
    def test_hit_above_threshold(self):
        from persistence import store as st

        row = MagicMock()
        row.query_embedding = [1.0, 0.0]
        row.response = "semantic cached answer"

        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q
        mock_q.limit.return_value = mock_q
        mock_q.all.return_value = [row]
        mock_s = MagicMock()
        mock_s.query.return_value = mock_q

        with patch("persistence.store.SETTINGS") as mock_settings, \
             patch("persistence.db.session_scope") as mock_ss:
            mock_settings.DATABASE_URL = "postgres://x"
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_s)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            # Query embedding is identical → dot product = 1.0 ≥ threshold 0.95
            result = st.lookup_chat_cache_semantic([1.0, 0.0], threshold=0.95)
        assert result == "semantic cached answer"

    def test_miss_below_threshold(self):
        from persistence import store as st

        row = MagicMock()
        row.query_embedding = [1.0, 0.0]
        row.response = "some cached answer"

        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.order_by.return_value = mock_q
        mock_q.limit.return_value = mock_q
        mock_q.all.return_value = [row]
        mock_s = MagicMock()
        mock_s.query.return_value = mock_q

        with patch("persistence.store.SETTINGS") as mock_settings, \
             patch("persistence.db.session_scope") as mock_ss:
            mock_settings.DATABASE_URL = "postgres://x"
            mock_ss.return_value.__enter__ = MagicMock(return_value=mock_s)
            mock_ss.return_value.__exit__ = MagicMock(return_value=False)
            # Orthogonal embedding → dot product = 0.0 < threshold 0.95
            result = st.lookup_chat_cache_semantic([0.0, 1.0], threshold=0.95)
        assert result is None

    def test_no_db_returns_none(self):
        from persistence import store as st

        with patch("persistence.store.SETTINGS") as mock_settings:
            mock_settings.DATABASE_URL = ""
            result = st.lookup_chat_cache_semantic([1.0, 0.0])
        assert result is None


class TestChatCacheModule:
    def test_check_returns_none_when_disabled(self, monkeypatch):
        import agents.chat.cache as cache_mod
        from unittest.mock import MagicMock

        fake_settings = MagicMock()
        fake_settings.CHAT_CACHE_ENABLED = False
        monkeypatch.setattr(cache_mod, "SETTINGS", fake_settings)
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
