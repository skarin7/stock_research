"""Chat intent router tests — semantic tier, LLM fallback, run_turn routing, PG bank cache.

Fully mocked: no real embeddings, no real LLM, no network, no DB.
"""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import approval as appr_mod  # noqa: E402
from agents.chat import embedder as emb_mod  # noqa: E402
from agents.chat import intent as intent_mod  # noqa: E402


# ── Tier-1 HITL approval routing (deterministic, no LLM) ────────────────────────
class TestApprovalRouting:
    def test_slash_command_delegates(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "handle_approval_command", lambda t: f"handled:{t}")
        assert appr_mod.route_approval("/approve p1") == "handled:/approve p1"

    def test_bare_approve_no_pending_falls_through(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "pending_proposals", lambda: [])
        assert appr_mod.route_approval("approve") is None

    def _capture(self, monkeypatch, seen):
        def _h(t):
            seen["cmd"] = t
            return "ok"
        monkeypatch.setattr(appr_mod, "handle_approval_command", _h)

    def test_bare_approve_single_pending_resolves(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "pending_proposals", lambda: [{"proposal_id": "p7"}])
        seen = {}
        self._capture(monkeypatch, seen)
        assert appr_mod.route_approval("yes") == "ok"
        assert seen["cmd"] == "/approve p7"

    def test_bare_reject_single_pending(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "pending_proposals", lambda: [{"proposal_id": "p7"}])
        seen = {}
        self._capture(monkeypatch, seen)
        appr_mod.route_approval("no")
        assert seen["cmd"] == "/reject p7"

    def test_multiple_pending_asks_which(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "pending_proposals",
                            lambda: [{"proposal_id": "p1"}, {"proposal_id": "p2"}])
        out = appr_mod.route_approval("approve")
        assert "p1" in out and "p2" in out

    def test_pending_but_unrelated_message_falls_through(self, monkeypatch):
        monkeypatch.setattr(appr_mod, "pending_proposals", lambda: [{"proposal_id": "p7"}])
        assert appr_mod.route_approval("what is the price of TCS") is None

    def test_callback_approve_delegates(self, monkeypatch):
        seen = {}

        def _h(t):
            seen["cmd"] = t
            return "ok"

        monkeypatch.setattr(appr_mod, "handle_approval_command", _h)
        assert appr_mod.handle_callback("approve:p9") == "ok"
        assert seen["cmd"] == "/approve p9"

    def test_callback_reject_delegates(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(appr_mod, "handle_approval_command",
                            lambda t: seen.update(cmd=t) or "ok")
        appr_mod.handle_callback("reject:p9")
        assert seen["cmd"] == "/reject p9"

    def test_callback_bad_data_returns_none(self):
        assert appr_mod.handle_callback("garbage") is None
        assert appr_mod.handle_callback("delete:p9") is None


# ── tier 2 → tier 3 routing in route_intent ────────────────────────────────────
class TestRouteIntent:
    def test_semantic_hit_skips_llm(self, monkeypatch):
        monkeypatch.setattr("agents.chat.embedder.nearest_intent", lambda t: ("research", 0.91))

        def _boom(text):
            raise AssertionError("LLM classifier must not run on a semantic hit")

        monkeypatch.setattr(intent_mod, "classify_intent_llm", _boom)
        out = intent_mod.route_intent("show me cheap IT stocks")
        assert out["intent"] == "research" and out["route"] == "semantic"

    def test_low_similarity_falls_back_to_llm(self, monkeypatch):
        monkeypatch.setattr("agents.chat.embedder.nearest_intent", lambda t: ("research", 0.10))
        monkeypatch.setattr(intent_mod, "classify_intent_llm",
                            lambda t: {"intent": "macro", "confidence": 0.8, "route": "llm"})
        out = intent_mod.route_intent("what happens if oil triples overnight")
        assert out["intent"] == "macro" and out["route"] == "llm"

    def test_embedder_error_falls_back_to_llm(self, monkeypatch):
        def _raise(t):
            raise RuntimeError("embed backend down")

        monkeypatch.setattr("agents.chat.embedder.nearest_intent", _raise)
        monkeypatch.setattr(intent_mod, "classify_intent_llm",
                            lambda t: {"intent": "ambiguous", "confidence": 0.0, "route": "llm"})
        assert intent_mod.route_intent("???")["route"] == "llm"


# ── tier 3 classifier ───────────────────────────────────────────────────────────
def _fake_model(content: str):
    return types.SimpleNamespace(invoke=lambda prompt: types.SimpleNamespace(content=content))


class TestClassifyLLM:
    def test_valid_high_confidence(self, monkeypatch):
        monkeypatch.setattr("agents.llm.get_chat_model",
                            lambda **k: _fake_model('{"intent": "entry_exit", "confidence": 0.9}'))
        assert intent_mod.classify_intent_llm("when to buy")["intent"] == "entry_exit"

    def test_low_confidence_becomes_ambiguous(self, monkeypatch):
        monkeypatch.setattr("agents.llm.get_chat_model",
                            lambda **k: _fake_model('{"intent": "research", "confidence": 0.2}'))
        assert intent_mod.classify_intent_llm("hmm")["intent"] == "ambiguous"

    def test_unknown_intent_becomes_ambiguous(self, monkeypatch):
        monkeypatch.setattr("agents.llm.get_chat_model",
                            lambda **k: _fake_model('{"intent": "banana", "confidence": 0.99}'))
        assert intent_mod.classify_intent_llm("x")["intent"] == "ambiguous"

    def test_llm_exception_becomes_ambiguous(self, monkeypatch):
        def _raise(**k):
            raise RuntimeError("no key")

        monkeypatch.setattr("agents.llm.get_chat_model", _raise)
        assert intent_mod.classify_intent_llm("x")["intent"] == "ambiguous"


# ── embedder: PG-cached bank ─────────────────────────────────────────────────────
class TestBankCache:
    def setup_method(self):
        emb_mod._bank = None

    def teardown_method(self):
        emb_mod._bank = None

    def test_loads_from_pg_without_embedding(self, monkeypatch):
        monkeypatch.setattr("persistence.store.load_intent_bank",
                            lambda h, m: (["research"], [[1.0, 0.0]]))

        def _no_embed(texts):
            raise AssertionError("must not embed when PG cache hits")

        monkeypatch.setattr(emb_mod, "embed", _no_embed)
        labels, vecs = emb_mod.bank_vectors()
        assert labels == ["research"] and vecs.shape == (1, 2)

    def test_embeds_and_saves_on_miss(self, monkeypatch):
        monkeypatch.setattr("persistence.store.load_intent_bank", lambda h, m: None)
        saved = {}
        monkeypatch.setattr("persistence.store.save_intent_bank",
                            lambda h, m, labels, vecs: saved.update({"labels": labels, "vecs": vecs}))
        monkeypatch.setattr(emb_mod, "embed",
                            lambda phrases: np.ones((len(phrases), 3), dtype=np.float32))
        labels, vecs = emb_mod.bank_vectors()
        assert len(labels) == vecs.shape[0] and saved["labels"] == labels


# ── run_turn integration ────────────────────────────────────────────────────────
@pytest.fixture
def wired(monkeypatch):
    from agents.chat import agent as agent_mod

    monkeypatch.setattr("agents.supervisor.kill_switch_active", lambda: False)
    monkeypatch.setattr(agent_mod, "_record_intent", lambda *a, **k: None)
    monkeypatch.setattr(agent_mod, "_record_turn", lambda *a, **k: None)
    monkeypatch.setattr(agent_mod, "SETTINGS",
                        types.SimpleNamespace(ENABLE_CHAT_INTENT_ROUTER=True, MAX_CHAT_TOOL_CALLS=8))

    called = {"agent": 0, "last_input": None}

    class _Agent:
        def invoke(self, payload, cfg):
            called["agent"] += 1
            called["last_input"] = payload["messages"][0][1]
            return {"messages": [types.SimpleNamespace(type="ai", content="answer", usage_metadata={})]}

    monkeypatch.setattr(agent_mod, "_get_agent", lambda: _Agent())
    monkeypatch.setattr("agents.chat.tools.reset_turn_state", lambda: None)
    return agent_mod, called


def _set_route(monkeypatch, intent, route="semantic"):
    monkeypatch.setattr("agents.chat.intent.route_intent",
                        lambda t: {"intent": intent, "confidence": 0.9, "route": route})


class TestRunTurnRouting:
    def test_greeting_is_canned_no_agent(self, wired, monkeypatch):
        agent_mod, called = wired
        _set_route(monkeypatch, "greeting")
        reply = agent_mod.run_turn("c1", "hi there")
        assert called["agent"] == 0 and "research assistant" in reply.lower()

    def test_trade_intent_is_canned_no_agent(self, wired, monkeypatch):
        agent_mod, called = wired
        _set_route(monkeypatch, "trade_intent")
        reply = agent_mod.run_turn("c1", "buy 10 TCS")
        assert called["agent"] == 0 and "isn't enabled" in reply.lower()

    def test_ambiguous_asks_clarifying(self, wired, monkeypatch):
        agent_mod, called = wired
        _set_route(monkeypatch, "ambiguous", route="llm")
        reply = agent_mod.run_turn("c1", "blorp")
        assert called["agent"] == 0 and "?" in reply

    def test_research_falls_through_with_hint(self, wired, monkeypatch):
        agent_mod, called = wired
        _set_route(monkeypatch, "entry_exit")
        reply = agent_mod.run_turn("c1", "when to buy INFY")
        assert called["agent"] == 1
        assert called["last_input"].startswith("(intent: entry_exit)")
        assert reply == "answer"

    def test_routing_error_fails_open_to_agent(self, wired, monkeypatch):
        agent_mod, called = wired

        def _raise(t):
            raise RuntimeError("router blew up")

        monkeypatch.setattr("agents.chat.intent.route_intent", _raise)
        reply = agent_mod.run_turn("c1", "anything")
        assert called["agent"] == 1 and reply == "answer"
