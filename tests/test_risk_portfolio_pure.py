"""Tests for the extracted pure functions run_risk_checks() and size_proposals()."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    ENABLE_RISK_AGENT=True,
    ENABLE_PORTFOLIO_AGENT=True,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    MIN_CONVICTION_TO_TRADE=0.6,
    BLOCK_NEAR_EARNINGS=True,
    EARNINGS_PROXIMITY_DAYS=5,
    TRADING_CAPITAL_INR=100_000.0,
    MAX_OPEN_POSITIONS=5,
    MAX_POSITION_PCT=0.10,
    MAX_SECTOR_PCT=0.30,
    POSITIONS_FILE="/tmp/__positions_test_pure__.json",
    OUTPUT_DIR="output",
    TRADING_MODE="paper",
)
sys.modules["config"] = types.SimpleNamespace(
    SETTINGS=_cfg,
    trading_enabled=lambda: True,
    live_trading=lambda: False,
)

from agents.contracts import (  # noqa: E402
    ConvictionView,
    EnrichedStock,
    PortfolioState,
    Position,
    ProposalStatus,
    TradeProposal,
)
from agents.nodes.risk import run_risk_checks  # noqa: E402
from agents.nodes.portfolio import size_proposals  # noqa: E402


def _cv(ticker, conviction=0.8, direction="long"):
    return ConvictionView(
        ticker=ticker,
        direction=direction,
        conviction=conviction,
        bull_case="bullish thesis",
        bear_case="bearish thesis",
    )


def test_run_risk_checks_blocks_low_conviction():
    cv = _cv("INFY", conviction=0.3)
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held=set(),
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1",
    )
    assert len(proposals) == 1
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_run_risk_checks_blocks_non_long():
    cv = _cv("INFY", direction="short")
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held=set(),
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1",
    )
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_run_risk_checks_blocks_duplicate():
    cv = _cv("INFY")
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held={"INFY"},
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1",
    )
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_run_risk_checks_passes():
    cv = _cv("TCS")
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held=set(),
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1",
    )
    assert proposals[0].status == ProposalStatus.PROPOSED
    assert proposals[0].ticker == "TCS"
    assert proposals[0].run_id == "r1"


def test_run_risk_checks_blocks_near_earnings():
    cv = _cv("HDFC")
    stock = EnrichedStock(symbol="HDFC", ltp=1800.0, days_to_earnings=3)
    proposals = run_risk_checks(
        convictions=[cv], stock_by={"HDFC": stock}, held=set(),
        min_conv=0.6, block_earnings=True, earnings_days=5, run_id="r1",
    )
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_size_proposals_approves():
    stock = EnrichedStock(symbol="TCS", ltp=1000.0, sector="IT")
    book = PortfolioState(cash=100000.0)
    p = TradeProposal(
        proposal_id="r1:TCS", run_id="r1", ticker="TCS",
        side="BUY", qty=0, rationale="bull", conviction=0.8,
        status=ProposalStatus.PROPOSED,
    )
    result = size_proposals(
        proposals=[p], stock_by={"TCS": stock}, book=book,
        capital=100000.0, max_open=5, max_pos_pct=0.10, max_sector_pct=0.30,
    )
    assert result[0].status == ProposalStatus.APPROVED
    assert result[0].qty > 0
    assert result[0].limit_price == 1000.0


def test_size_proposals_rejects_no_price():
    book = PortfolioState(cash=100000.0)
    stock = EnrichedStock(symbol="X", ltp=None)
    p = TradeProposal(
        proposal_id="r1:X", run_id="r1", ticker="X",
        side="BUY", qty=0, rationale="bull", conviction=0.8,
        status=ProposalStatus.PROPOSED,
    )
    result = size_proposals(
        proposals=[p], stock_by={"X": stock}, book=book,
        capital=100000.0, max_open=5, max_pos_pct=0.10, max_sector_pct=0.30,
    )
    assert result[0].status == ProposalStatus.REJECTED


def test_size_proposals_rejects_max_open():
    stock = EnrichedStock(symbol="TCS", ltp=1000.0, sector="IT")
    # Book already at max positions
    book = PortfolioState(
        cash=50000.0,
        positions=[Position(ticker=f"S{i}", qty=1, avg_price=100.0) for i in range(5)],
    )
    p = TradeProposal(
        proposal_id="r1:TCS", run_id="r1", ticker="TCS",
        side="BUY", qty=0, rationale="bull", conviction=0.8,
        status=ProposalStatus.PROPOSED,
    )
    result = size_proposals(
        proposals=[p], stock_by={"TCS": stock}, book=book,
        capital=100000.0, max_open=5, max_pos_pct=0.10, max_sector_pct=0.30,
    )
    assert result[0].status == ProposalStatus.REJECTED
