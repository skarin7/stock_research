"""Tests for AGENT_PROFILE → flag expansion in settings.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from settings import Settings, _apply_profile, _PROFILE_FLAGS


def _base(profile: str) -> Settings:
    return Settings(AGENT_PROFILE=profile)


class TestProfileResearch:
    def test_agents_on(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_RESEARCH_AGENT is True
        assert s.ENABLE_ANALYST_AGENT is True
        assert s.ENABLE_MEMORY_AGENT is True

    def test_agents_off(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_DEBATE_AGENT is False
        assert s.ENABLE_RISK_AGENT is False
        assert s.ENABLE_PORTFOLIO_AGENT is False
        assert s.ENABLE_TRADING_AGENT is False
        assert s.ENABLE_MONITORING_AGENT is False

    def test_no_live_trading(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_LIVE_TRADING is False
        assert s.GROWW_TRADING_ENABLED is False
        assert s.ENABLE_AUTO_EXIT is False

    def test_agent_mode(self):
        s = _apply_profile(_base("research"))
        assert s.AGENT_MODE == "research"


class TestProfilePaper:
    def test_all_pipeline_agents_on(self):
        s = _apply_profile(_base("paper"))
        for flag in [
            "ENABLE_RESEARCH_AGENT", "ENABLE_ANALYST_AGENT", "ENABLE_DEBATE_AGENT",
            "ENABLE_RISK_AGENT", "ENABLE_PORTFOLIO_AGENT", "ENABLE_TRADING_AGENT",
            "ENABLE_MONITORING_AGENT", "ENABLE_MEMORY_AGENT",
        ]:
            assert getattr(s, flag) is True, f"{flag} should be True for paper"

    def test_no_live_trading(self):
        s = _apply_profile(_base("paper"))
        assert s.ENABLE_LIVE_TRADING is False
        assert s.GROWW_TRADING_ENABLED is False
        assert s.ENABLE_AUTO_EXIT is False

    def test_agent_mode(self):
        s = _apply_profile(_base("paper"))
        assert s.AGENT_MODE == "paper"


class TestProfileLive:
    def test_all_agents_on(self):
        s = _apply_profile(_base("live"))
        for flag in [
            "ENABLE_RESEARCH_AGENT", "ENABLE_ANALYST_AGENT", "ENABLE_DEBATE_AGENT",
            "ENABLE_RISK_AGENT", "ENABLE_PORTFOLIO_AGENT", "ENABLE_TRADING_AGENT",
            "ENABLE_MONITORING_AGENT", "ENABLE_MEMORY_AGENT",
            "ENABLE_LIVE_TRADING", "GROWW_TRADING_ENABLED", "ENABLE_AUTO_EXIT",
        ]:
            assert getattr(s, flag) is True, f"{flag} should be True for live"

    def test_agent_mode(self):
        s = _apply_profile(_base("live"))
        assert s.AGENT_MODE == "live"


class TestProfileValidation:
    def test_invalid_profile_raises(self):
        with pytest.raises(ValueError, match="Unknown AGENT_PROFILE"):
            _apply_profile(_base("yolo"))

    def test_case_insensitive(self):
        s = _apply_profile(Settings(AGENT_PROFILE="PAPER"))
        assert s.AGENT_MODE == "paper"

    def test_chat_agent_not_touched_by_profile(self):
        s = _apply_profile(Settings(AGENT_PROFILE="live", ENABLE_CHAT_AGENT=False))
        assert s.ENABLE_CHAT_AGENT is False
        s2 = _apply_profile(Settings(AGENT_PROFILE="research", ENABLE_CHAT_AGENT=True))
        assert s2.ENABLE_CHAT_AGENT is True
