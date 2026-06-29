"""Tests for propose_trade and intraday_watchlist chat tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock


def _settings(trading_mode="paper"):
    from settings import Settings
    return Settings(
        TRADING_MODE=trading_mode,
        TRADING_CAPITAL_INR=100000.0,
        MIN_CONVICTION_TO_TRADE=0.6,
        BLOCK_NEAR_EARNINGS=True,
        EARNINGS_PROXIMITY_DAYS=5,
        MAX_OPEN_POSITIONS=5,
        MAX_POSITION_PCT=0.10,
        MAX_SECTOR_PCT=0.30,
        STOP_LOSS_PCT=0.05,
        APPROVAL_TIMEOUT_SEC=900,
    )


def test_propose_trade_off_mode_returns_error(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _settings("off"))

    with patch("agents.chat.tools.trading_enabled", return_value=False):
        from agents.chat.tools import propose_trade
        result = propose_trade.invoke({"ticker": "INFY", "action": "BUY", "qty": 10, "rationale": "looks good"})
    assert "error" in result


def test_propose_trade_paper_fills_immediately(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _settings("paper"))

    from agents.contracts import ProposalStatus, PortfolioState

    proposal = MagicMock()
    proposal.status = ProposalStatus.APPROVED
    proposal.qty = 5
    proposal.limit_price = 1500.0
    proposal.ticker = "INFY"
    proposal.proposal_id = "chat:INFY"
    proposal.risk_checks = []

    with patch("agents.chat.tools.trading_enabled", return_value=True), \
         patch("agents.chat.tools.live_trading", return_value=False), \
         patch("agents.chat.tools.run_risk_checks", return_value=[proposal]), \
         patch("agents.chat.tools.size_proposals", return_value=[proposal]), \
         patch("agents.chat.tools._fetch_live_price", return_value=1500.0), \
         patch("agents.chat.tools.load_portfolio", return_value=PortfolioState(cash=100000.0)), \
         patch("agents.chat.tools.save_portfolio"), \
         patch("agents.chat.tools.recompute", side_effect=lambda b: b):
        from agents.chat.tools import propose_trade
        result = propose_trade.invoke({"ticker": "INFY", "action": "BUY", "qty": 5, "rationale": "looks good"})

    assert result.get("status") == "filled"
    assert result.get("ticker") == "INFY"
    assert result.get("mode") == "paper"


def test_propose_trade_live_returns_pending(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _settings("live"))

    from agents.contracts import ProposalStatus, PortfolioState

    proposal = MagicMock()
    proposal.status = ProposalStatus.APPROVED
    proposal.qty = 5
    proposal.limit_price = 1500.0
    proposal.ticker = "INFY"
    proposal.proposal_id = "chat:INFY"
    proposal.risk_checks = []

    with patch("agents.chat.tools.trading_enabled", return_value=True), \
         patch("agents.chat.tools.live_trading", return_value=True), \
         patch("agents.chat.tools.run_risk_checks", return_value=[proposal]), \
         patch("agents.chat.tools.size_proposals", return_value=[proposal]), \
         patch("agents.chat.tools._fetch_live_price", return_value=1500.0), \
         patch("agents.chat.tools.load_portfolio", return_value=PortfolioState(cash=100000.0)), \
         patch("agents.chat.tools.save_proposals"), \
         patch("agents.chat.tools.send_approval_request"):
        from agents.chat.tools import propose_trade
        result = propose_trade.invoke({"ticker": "INFY", "action": "BUY", "qty": 5, "rationale": "thesis"})

    assert result.get("status") == "pending_approval"
    assert result.get("mode") == "live"


def test_intraday_watchlist_returns_items():
    watchlist_item = {
        "symbol": "TCS", "score": 8, "conviction": "HIGH",
        "signals": {}, "company": "TCS Ltd", "sector": "IT", "close": 3500.0,
    }
    with patch("intraday.pipeline.run_pipeline", return_value=[watchlist_item]):
        from agents.chat.tools import intraday_watchlist
        result = intraday_watchlist.invoke({})

    assert result.get("count") == 1
    assert result["items"][0]["symbol"] == "TCS"


def test_intraday_watchlist_handles_error():
    with patch("intraday.pipeline.run_pipeline", side_effect=RuntimeError("no data")):
        from agents.chat.tools import intraday_watchlist
        result = intraday_watchlist.invoke({})
    assert "error" in result
