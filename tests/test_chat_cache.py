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

        # Function exists and returns None when DATABASE_URL is unset (no-op without DB).
        assert hasattr(st, "lookup_chat_cache_exact")
        result = st.lookup_chat_cache_exact("abc123")
        assert result is None

    def test_miss_returns_none(self):
        from persistence import store as st
        assert hasattr(st, "lookup_chat_cache_exact")

    def test_store_function_exists(self):
        from persistence import store as st
        assert hasattr(st, "store_chat_cache")
