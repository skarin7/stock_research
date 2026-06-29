"""Tests for TRADING_MODE validation in settings.py."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from settings import Settings


def test_trading_mode_defaults_off():
    s = Settings()
    assert s.TRADING_MODE == "off"


def test_trading_mode_paper():
    s = Settings(TRADING_MODE="paper")
    assert s.TRADING_MODE == "paper"


def test_trading_mode_live():
    s = Settings(TRADING_MODE="live")
    assert s.TRADING_MODE == "live"


def test_no_enable_flags_on_settings():
    s = Settings()
    for flag in ["ENABLE_RESEARCH_AGENT", "AGENT_PROFILE", "ENABLE_LIVE_TRADING",
                 "ENABLE_DEBATE_AGENT", "ENABLE_TRADING_AGENT", "ENABLE_AUTO_EXIT",
                 "ENABLE_CHAT_AGENT", "ENABLE_PULSE_AGENT", "ENABLE_MEMORY_AGENT",
                 "AGENT_MODE", "GROWW_TRADING_ENABLED"]:
        assert not hasattr(s, flag), f"Settings should not have {flag}"
