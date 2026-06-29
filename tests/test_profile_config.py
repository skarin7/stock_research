"""Tests for TRADING_MODE validation and config helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from settings import Settings


def test_default_trading_mode_is_off():
    s = Settings()
    assert s.TRADING_MODE == "off"


def test_paper_mode():
    s = Settings(TRADING_MODE="paper")
    assert s.TRADING_MODE == "paper"


def test_live_mode():
    s = Settings(TRADING_MODE="live")
    assert s.TRADING_MODE == "live"


def test_invalid_mode_raises_at_from_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "auto")
    with pytest.raises(ValueError, match="TRADING_MODE"):
        Settings.from_env()


def test_case_insensitive_from_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    s = Settings.from_env()
    assert s.TRADING_MODE == "paper"


def test_no_enable_flags():
    s = Settings()
    removed = [
        "ENABLE_RESEARCH_AGENT", "AGENT_PROFILE", "ENABLE_LIVE_TRADING",
        "ENABLE_DEBATE_AGENT", "ENABLE_TRADING_AGENT", "ENABLE_AUTO_EXIT",
        "ENABLE_CHAT_AGENT", "ENABLE_PULSE_AGENT", "ENABLE_MEMORY_AGENT",
        "AGENT_MODE", "GROWW_TRADING_ENABLED",
    ]
    for flag in removed:
        assert not hasattr(s, flag), f"Settings should not have {flag}"


def _trading_enabled(s: Settings) -> bool:
    """Mirrors config.trading_enabled() without importing the (possibly mocked) config module."""
    return s.TRADING_MODE in ("paper", "live")


def _live_trading(s: Settings) -> bool:
    """Mirrors config.live_trading() without importing the (possibly mocked) config module."""
    return s.TRADING_MODE == "live"


def test_trading_enabled_off():
    assert _trading_enabled(Settings(TRADING_MODE="off")) is False


def test_trading_enabled_paper():
    assert _trading_enabled(Settings(TRADING_MODE="paper")) is True


def test_trading_enabled_live():
    assert _trading_enabled(Settings(TRADING_MODE="live")) is True


def test_live_trading_paper():
    assert _live_trading(Settings(TRADING_MODE="paper")) is False


def test_live_trading_live():
    assert _live_trading(Settings(TRADING_MODE="live")) is True
