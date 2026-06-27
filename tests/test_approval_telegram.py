"""Telegram approval: budget line in the request + /approve|/reject → resume.
No network, no DB — resume_run and the proposal store are monkeypatched.

Run: python -m pytest tests/test_approval_telegram.py -v
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    TELEGRAM_BOT_TOKEN="x",
    TELEGRAM_CHAT_ID="123",
    MAX_GRAPH_STEPS=50,
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from agents import approval  # noqa: E402


@pytest.fixture(autouse=True)
def _bind():
    approval.SETTINGS = _cfg
    yield


# ── (a) budget line in the approval request ─────────────────────────────────────

def test_request_shows_rupee_budget(monkeypatch):
    sent = {}
    import notifications.telegram_notifier as tn
    monkeypatch.setattr(tn, "send_buttons",
                        lambda chat, body, kb: sent.update(body=body, kb=kb) or True)

    payload = {"run_id": "R1", "proposals": [
        {"proposal_id": "p1", "ticker": "RELIANCE", "side": "BUY",
         "qty": 12, "limit_price": 2840.5, "conviction": 0.72},
    ]}
    ok = approval.send_approval_request(payload)
    assert ok
    # 12 * 2840.5 = 34086 → rendered as ₹34,086
    assert "₹34,086" in sent["body"]
    assert "RELIANCE</b> BUY x12" in sent["body"]
    # inline buttons carry the decision as callback_data
    flat = [b for row in sent["kb"] for b in row]
    assert {"text": "❌ Reject", "callback_data": "reject:p1"} in flat
    assert any(b["callback_data"] == "approve:p1" for b in flat)


def test_request_market_order_has_no_budget(monkeypatch):
    sent = {}
    import notifications.telegram_notifier as tn
    monkeypatch.setattr(tn, "send_buttons",
                        lambda chat, body, kb: sent.update(body=body, kb=kb) or True)

    payload = {"run_id": "R1", "proposals": [
        {"proposal_id": "p1", "ticker": "TCS", "side": "BUY", "qty": 5,
         "limit_price": None, "conviction": 0.6},
    ]}
    approval.send_approval_request(payload)
    assert "MKT" in sent["body"]
    assert "₹" not in sent["body"]   # no limit price → no computable budget


# ── (b) /approve|/reject command handling ───────────────────────────────────────

def _stub_store(monkeypatch, props):
    """Patch persistence.store.load_proposals to a fixed dict and capture mutation."""
    import persistence.store as store
    state = {"props": props}
    monkeypatch.setattr(store, "load_proposals", lambda: state["props"])
    return state


def test_non_command_returns_none():
    assert approval.handle_approval_command("what's hot today?") is None


def test_missing_id_returns_usage():
    out = approval.handle_approval_command("/approve")
    assert "Usage" in out


def test_unknown_proposal(monkeypatch):
    _stub_store(monkeypatch, {})
    out = approval.handle_approval_command("/approve p999")
    assert "Unknown proposal" in out


def test_already_processed_proposal(monkeypatch):
    _stub_store(monkeypatch, {"p1": {"status": "placed", "run_id": "R1", "ticker": "AAA"}})
    out = approval.handle_approval_command("/approve p1")
    assert "already" in out and "placed" in out


def test_approve_resumes_and_reports_order(monkeypatch):
    state = _stub_store(monkeypatch, {
        "p1": {"status": "awaiting_approval", "run_id": "R1", "ticker": "RELIANCE"},
    })
    captured = {}

    def fake_resume(run_id, decisions):
        captured["run_id"] = run_id
        captured["decisions"] = decisions
        # simulate the trading node placing the order on resume
        state["props"]["p1"] = {"status": "placed", "run_id": "R1",
                                "ticker": "RELIANCE", "broker_order_id": "OID-9"}
        return {}

    monkeypatch.setattr(approval, "resume_run", fake_resume)
    out = approval.handle_approval_command("/approve p1")

    assert captured["run_id"] == "R1"
    assert captured["decisions"] == {"p1": "approve"}
    assert "Approved" in out and "RELIANCE" in out and "OID-9" in out


def test_reject_resumes_with_reject_decision(monkeypatch):
    state = _stub_store(monkeypatch, {
        "p1": {"status": "awaiting_approval", "run_id": "R1", "ticker": "TCS"},
    })

    def fake_resume(run_id, decisions):
        state["props"]["p1"] = {"status": "rejected", "run_id": "R1", "ticker": "TCS"}
        return {}

    monkeypatch.setattr(approval, "resume_run", fake_resume)
    out = approval.handle_approval_command("/reject p1")
    assert "Rejected" in out and "rejected" in out


def test_resume_failure_reported(monkeypatch):
    _stub_store(monkeypatch, {
        "p1": {"status": "awaiting_approval", "run_id": "R1", "ticker": "AAA"},
    })

    def boom(run_id, decisions):
        raise RuntimeError("no checkpointer")

    monkeypatch.setattr(approval, "resume_run", boom)
    out = approval.handle_approval_command("/approve p1")
    assert "Resume failed" in out and "no checkpointer" in out
