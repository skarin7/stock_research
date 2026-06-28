"""Tests for the LLM provider switch (anthropic default / openrouter option).

No network or API keys — the OpenRouter client is faked.
"""

import sys
import types
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Minimal config mock (mirrors tests/test_scorer.py pattern)
mock_config = mock.MagicMock()
mock_config.ANTHROPIC_API_KEY = "test-key"
mock_config.SCORING_MODEL = "claude-haiku-4-5"
mock_config.REPORT_MODEL = "claude-sonnet-4-6"
mock_config.OPENROUTER_SCORING_MODEL = "deepseek/deepseek-chat"
mock_config.OPENROUTER_REPORT_MODEL = "deepseek/deepseek-chat"
mock_config.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
mock_config.OPENROUTER_API_KEY = "or-key"
mock_config.LLM_PROVIDER = "anthropic"
sys.modules["config"] = types.SimpleNamespace(SETTINGS=mock_config)

import llm_router  # noqa: E402


def test_default_provider_is_anthropic():
    mock_config.LLM_PROVIDER = "anthropic"
    assert not llm_router.is_openrouter()
    assert llm_router.scoring_model() == "claude-haiku-4-5"
    assert llm_router.report_model() == "claude-sonnet-4-6"


def test_openrouter_resolves_cheap_models():
    mock_config.LLM_PROVIDER = "openrouter"
    try:
        assert llm_router.is_openrouter()
        assert llm_router.scoring_model() == "deepseek/deepseek-chat"
        assert llm_router.report_model() == "deepseek/deepseek-chat"
    finally:
        mock_config.LLM_PROVIDER = "anthropic"


def _fake_openrouter_response(text):
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = text
    return resp


def test_chat_model_openrouter(monkeypatch):
    """chat_model() returns OPENROUTER_CHAT_MODEL when provider is openrouter."""
    mock_config.LLM_PROVIDER = "openrouter"
    mock_config.OPENROUTER_CHAT_MODEL = "deepseek/deepseek-chat"
    monkeypatch.setattr(llm_router, "SETTINGS", mock_config)
    assert llm_router.chat_model() == "deepseek/deepseek-chat"
    mock_config.LLM_PROVIDER = "anthropic"  # restore


def test_chat_model_anthropic(monkeypatch):
    """chat_model() returns REPORT_MODEL when provider is anthropic."""
    mock_config.LLM_PROVIDER = "anthropic"
    mock_config.REPORT_MODEL = "claude-sonnet-4-6"
    monkeypatch.setattr(llm_router, "SETTINGS", mock_config)
    assert llm_router.chat_model() == "claude-sonnet-4-6"


def test_score_stocks_routes_to_openrouter(monkeypatch):
    from scoring import claude_scorer

    fake_client = mock.MagicMock()
    fake_client.chat.completions.create.return_value = _fake_openrouter_response(
        '{"composite_score": 7.5, "signals": {}, "investment_rationale": "ok"}'
    )
    monkeypatch.setattr(claude_scorer.llm_router, "is_openrouter", lambda: True)
    monkeypatch.setattr(claude_scorer.llm_router, "scoring_model", lambda: "deepseek/deepseek-chat")
    monkeypatch.setattr(claude_scorer.llm_router, "openrouter_client", lambda: fake_client)

    stocks = [{"symbol": "HDFCBANK", "sector": "Banking"}]
    scores = claude_scorer.score_stocks(stocks, news_map={}, macro_context="")

    assert len(scores) == 1
    assert scores[0]["ticker"] == "HDFCBANK"
    assert scores[0]["composite_score"] == 7.5
    # OpenAI-compatible chat endpoint used, not the Anthropic Batch API
    fake_client.chat.completions.create.assert_called_once()
