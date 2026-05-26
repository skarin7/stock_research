"""Risk + Portfolio gates and the paper-mode trading fill. No network/DB."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    ENABLE_RISK_AGENT=True,
    ENABLE_PORTFOLIO_AGENT=True,
    ENABLE_TRADING_AGENT=True,
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
    STOP_LOSS_PCT=0.05,
    POSITIONS_FILE="/tmp/__positions_test__.json",
    OUTPUT_DIR="output",
)
sys.modules["config"] = _cfg

from agents.contracts import (  # noqa: E402
    ConvictionView,
    EnrichedStock,
    EnrichmentResult,
    PortfolioState,
    Position,
    ProposalStatus,
)
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import portfolio as port_mod  # noqa: E402
from agents.nodes import risk as risk_mod  # noqa: E402
from agents.nodes import trading as trade_mod  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from agents.state import RunStatus  # noqa: E402
from persistence import store as store_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _bind_config(tmp_path):
    _cfg.POSITIONS_FILE = str(tmp_path / "positions.json")
    for mod in (_sup, _base, risk_mod, port_mod, trade_mod, store_mod):
        mod.config = _cfg
    yield


def _conv(ticker, conviction=0.8, direction="long"):
    return ConvictionView(ticker=ticker, direction=direction, conviction=conviction,
                          bull_case="cheap + momentum", composite_score=7.5)


def _enriched(stocks):
    return EnrichmentResult(stocks=stocks)


def _stock(symbol, ltp=100.0, sector="IT", days_to_earnings=None):
    return EnrichedStock(symbol=symbol, sector=sector, ltp=ltp, days_to_earnings=days_to_earnings)


# ── risk gate ─────────────────────────────────────────────────────────────────

def test_risk_blocks_low_conviction_earnings_short_and_duplicate():
    convs = [_conv("LOWC", conviction=0.3), _conv("ERN"), _conv("SHORT", direction="short"),
             _conv("HELD")]
    enriched = _enriched([_stock("LOWC"), _stock("ERN", days_to_earnings=2), _stock("SHORT"),
                          _stock("HELD")])
    book = PortfolioState(cash=100_000, positions=[Position(ticker="HELD", qty=1, avg_price=10, sector="IT")])
    out = risk_mod.risk_node({"run_id": "r1", "convictions": convs, "enriched": enriched, "book": book})

    by = {p.ticker: p for p in out["proposals"]}
    assert by["LOWC"].status == ProposalStatus.BLOCKED
    assert by["ERN"].status == ProposalStatus.BLOCKED       # within earnings window
    assert by["SHORT"].status == ProposalStatus.BLOCKED     # long-only
    assert by["HELD"].status == ProposalStatus.BLOCKED      # duplicate of existing position


def test_risk_passes_clean_long():
    out = risk_mod.risk_node({
        "run_id": "r1", "convictions": [_conv("GOOD", conviction=0.9)],
        "enriched": _enriched([_stock("GOOD", days_to_earnings=30)]),
        "book": PortfolioState(cash=100_000),
    })
    assert out["proposals"][0].status == ProposalStatus.PROPOSED


# ── portfolio sizing + caps ─────────────────────────────────────────────────────

def test_portfolio_sizes_by_capital_and_conviction():
    risk_out = risk_mod.risk_node({
        "run_id": "r1", "convictions": [_conv("AAA", conviction=0.8)],
        "enriched": _enriched([_stock("AAA", ltp=100.0, days_to_earnings=30)]),
        "book": PortfolioState(cash=100_000),
    })
    state = {"run_id": "r1", **risk_out, "enriched": _enriched([_stock("AAA", ltp=100.0)])}
    out = port_mod.portfolio_node(state)
    p = out["proposals"][0]
    assert p.status == ProposalStatus.APPROVED
    assert p.qty == 80           # 100000 * 0.10 * 0.8 / 100
    assert p.limit_price == 100.0


def test_portfolio_respects_max_open():
    _cfg.MAX_OPEN_POSITIONS = 1
    try:
        convs = [_conv("AAA", 0.9), _conv("BBB", 0.8)]
        stocks = [_stock("AAA", ltp=100), _stock("BBB", ltp=100)]
        risk_out = risk_mod.risk_node({"run_id": "r1", "convictions": convs,
                                       "enriched": _enriched(stocks), "book": PortfolioState(cash=100_000)})
        out = port_mod.portfolio_node({"run_id": "r1", **risk_out, "enriched": _enriched(stocks)})
        statuses = {p.ticker: p.status for p in out["proposals"]}
        assert statuses["AAA"] == ProposalStatus.APPROVED        # higher conviction wins the single slot
        assert statuses["BBB"] == ProposalStatus.REJECTED
    finally:
        _cfg.MAX_OPEN_POSITIONS = 5


def test_portfolio_enforces_sector_cap():
    # Two IT names; capital small so the second breaches the 30% sector cap.
    _cfg.MAX_POSITION_PCT = 1.0
    try:
        convs = [_conv("AAA", 0.9), _conv("BBB", 0.9)]
        stocks = [_stock("AAA", ltp=100, sector="IT"), _stock("BBB", ltp=100, sector="IT")]
        risk_out = risk_mod.risk_node({"run_id": "r1", "convictions": convs,
                                       "enriched": _enriched(stocks), "book": PortfolioState(cash=100_000)})
        out = port_mod.portfolio_node({"run_id": "r1", **risk_out, "enriched": _enriched(stocks)})
        rejected = [p for p in out["proposals"] if p.status == ProposalStatus.REJECTED]
        assert any(c.rule == "sector_cap" and not c.passed for p in rejected for c in p.risk_checks)
    finally:
        _cfg.MAX_POSITION_PCT = 0.10


# ── paper-mode end-to-end ────────────────────────────────────────────────────────

def test_paper_flow_fills_and_persists_book():
    stocks = [_stock("AAA", ltp=100.0, sector="IT", days_to_earnings=30)]
    state = {"run_id": "r1", "mode": "paper", "convictions": [_conv("AAA", 0.8)],
             "enriched": _enriched(stocks), "book": PortfolioState(cash=100_000)}

    state = {**state, **risk_mod.risk_node(state)}
    state = {**state, **port_mod.portfolio_node(state)}
    out = trade_mod.trading_node(state)

    assert out["status"] == RunStatus.RUNNING
    p = out["proposals"][0]
    assert p.status == ProposalStatus.FILLED
    book = out["book"]
    assert [pos.ticker for pos in book.positions] == ["AAA"]
    pos = book.positions[0]
    assert pos.qty == 80
    assert pos.stop_price == 95.0                 # 100 * (1 - 0.05)
    assert book.cash == 100_000 - 80 * 100        # cash debited
    assert book.sector_exposure["IT"] == 80 * 100
    # persisted to the temp positions file
    assert store_mod.load_portfolio().positions[0].ticker == "AAA"


def test_live_mode_places_no_orders():
    stocks = [_stock("AAA", ltp=100.0, days_to_earnings=30)]
    state = {"run_id": "r1", "mode": "live", "convictions": [_conv("AAA", 0.8)],
             "enriched": _enriched(stocks), "book": PortfolioState(cash=100_000)}
    state = {**state, **risk_mod.risk_node(state)}
    state = {**state, **port_mod.portfolio_node(state)}
    out = trade_mod.trading_node(state)
    assert out == {} or "book" not in out          # no fills, no book mutation in live (deferred)
