"""Live trading path: broker default-deny gates + interrupt/approve/reject.

No network/SDK — the broker call and the interrupt() are monkeypatched.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    ENABLE_TRADING_AGENT=True,
    ENABLE_LIVE_TRADING=False,
    GROWW_TRADING_ENABLED=True,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    APPROVAL_TIMEOUT_SEC=900,
    STOP_LOSS_PCT=0.05,
    TRADING_CAPITAL_INR=100_000.0,
    POSITIONS_FILE="/tmp/__pos__.json",
    PROPOSALS_FILE="/tmp/__prop__.json",
    OUTPUT_DIR="output",
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from agents.broker import groww_trader as broker_mod  # noqa: E402
from agents.contracts import ProposalStatus, TradeProposal  # noqa: E402
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import trading as trade_mod  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from persistence import store as store_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _bind_config(tmp_path):
    _cfg.PROPOSALS_FILE = str(tmp_path / "proposals.json")
    _cfg.POSITIONS_FILE = str(tmp_path / "positions.json")
    _cfg.ENABLE_LIVE_TRADING = False
    _cfg.KILL_SWITCH = False
    _cfg.APPROVAL_TIMEOUT_SEC = 900
    for mod in (_sup, _base, trade_mod, store_mod, broker_mod):
        mod.SETTINGS = _cfg
    yield


def _approved(pid="r1:AAA", ticker="AAA", qty=10):
    return TradeProposal(proposal_id=pid, run_id="r1", ticker=ticker, side="BUY", qty=qty,
                         order_type="LIMIT", limit_price=100.0, conviction=0.8,
                         status=ProposalStatus.APPROVED)


def _state(proposals):
    return {"run_id": "r1", "mode": "live", "proposals": proposals, "enriched": None}


# ── broker gates (default-deny) ─────────────────────────────────────────────────

def test_gate_check_blocks_when_flags_off():
    _cfg.ENABLE_LIVE_TRADING = False
    with pytest.raises(broker_mod.BrokerRefused):
        broker_mod._gate_check("live")
    _cfg.ENABLE_LIVE_TRADING = True
    with pytest.raises(broker_mod.BrokerRefused):
        broker_mod._gate_check("paper")          # wrong mode
    _cfg.GROWW_TRADING_ENABLED = False
    try:
        with pytest.raises(broker_mod.BrokerRefused):
            broker_mod._gate_check("live")       # broker disabled
    finally:
        _cfg.GROWW_TRADING_ENABLED = True


def test_gate_check_blocks_under_kill_switch():
    _cfg.ENABLE_LIVE_TRADING = True
    _cfg.KILL_SWITCH = True
    try:
        with pytest.raises(broker_mod.BrokerRefused):
            broker_mod._gate_check("live")
    finally:
        _cfg.KILL_SWITCH = False


def test_place_order_idempotent_on_existing_order_id():
    p = _approved()
    p.broker_order_id = "EXISTING"
    oid, status = broker_mod.place_order(p, mode="live")     # returns before any gate/SDK call
    assert (oid, status) == ("EXISTING", "already_placed")


# ── live trading node ───────────────────────────────────────────────────────────

def test_live_default_deny_places_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(broker_mod, "place_order", lambda *a, **k: called.append(1))
    _cfg.ENABLE_LIVE_TRADING = False
    p = _approved()
    out = trade_mod.trading_node(_state([p]))
    assert "proposals" not in out                 # no mutation emitted
    assert p.status == ProposalStatus.APPROVED     # left untouched
    assert not called                              # broker never called


def test_live_approve_places_order(monkeypatch):
    _cfg.ENABLE_LIVE_TRADING = True
    p = _approved()
    monkeypatch.setattr(trade_mod, "_interrupt", lambda payload: {p.proposal_id: "approve"})
    monkeypatch.setattr(broker_mod, "place_order", lambda prop, mode="live": ("OID1", "placed"))

    out = trade_mod.trading_node(_state([p]))
    done = out["proposals"][0]
    assert done.status == ProposalStatus.PLACED
    assert done.broker_order_id == "OID1"
    assert done.approved_at is not None
    # proposal persisted for approver visibility
    assert p.proposal_id in store_mod.load_proposals()


def test_live_reject_places_nothing(monkeypatch):
    _cfg.ENABLE_LIVE_TRADING = True
    p = _approved()
    called = []
    monkeypatch.setattr(trade_mod, "_interrupt", lambda payload: {p.proposal_id: "reject"})
    monkeypatch.setattr(broker_mod, "place_order", lambda *a, **k: called.append(1))

    out = trade_mod.trading_node(_state([p]))
    assert out["proposals"][0].status == ProposalStatus.REJECTED
    assert not called


def test_live_expired_before_decision(monkeypatch):
    _cfg.ENABLE_LIVE_TRADING = True
    _cfg.APPROVAL_TIMEOUT_SEC = -1                # already expired by the time we resume
    p = _approved()
    called = []
    monkeypatch.setattr(trade_mod, "_interrupt", lambda payload: {p.proposal_id: "approve"})
    monkeypatch.setattr(broker_mod, "place_order", lambda *a, **k: called.append(1))

    out = trade_mod.trading_node(_state([p]))
    assert out["proposals"][0].status == ProposalStatus.EXPIRED
    assert not called                             # expired approvals never reach the broker
