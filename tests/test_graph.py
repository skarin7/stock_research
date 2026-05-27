"""Orchestration tests: routing, guards (kill-switch / budget / disabled flag),
and an end-to-end research-path walk with fake nodes. Uses MemorySaver and a
lightweight stand-in config; no API keys / network.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Lightweight config installed BEFORE importing the agent layer so the
# `import config` inside agents.* binds to this object.
_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    AGENT_MODE="research",
    ENABLE_RESEARCH_AGENT=True,
    ENABLE_ANALYST_AGENT=True,
    ENABLE_DEBATE_AGENT=False,
    ENABLE_RISK_AGENT=False,
    ENABLE_PORTFOLIO_AGENT=False,
    ENABLE_TRADING_AGENT=False,
    ENABLE_MEMORY_AGENT=False,
    ENABLE_MONITORING_AGENT=False,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    MAX_GRAPH_STEPS=50,
    DATABASE_URL="",
    METRICS_PORT=9100,
    OUTPUT_DIR="output",
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

import agents.graph as _graph_mod  # noqa: E402
import agents.nodes.base as _base_mod  # noqa: E402
import agents.supervisor as _sup_mod  # noqa: E402
from agents.graph import build_graph  # noqa: E402
from agents.nodes.base import agent_node  # noqa: E402
from agents.state import RunStatus  # noqa: E402
from agents.supervisor import (  # noqa: E402
    budget_exceeded,
    kill_switch_active,
    next_or_finalize,
    route_after_analyst,
    route_after_research,
)


@pytest.fixture(autouse=True)
def _bind_config():
    """Bind the agent-layer modules to THIS file's config (order-independent)."""
    _graph_mod.SETTINGS = _cfg
    _sup_mod.SETTINGS = _cfg
    _base_mod.SETTINGS = _cfg
    yield


# ── routing ───────────────────────────────────────────────────────────────────

class TestRouting:
    def test_research_advances_to_analyst(self):
        assert route_after_research({"status": RunStatus.RUNNING}) == "analyst"

    def test_terminal_short_circuits_to_finalize(self):
        for st in (RunStatus.HALTED, RunStatus.FAILED, RunStatus.BUDGET_EXCEEDED):
            assert route_after_research({"status": st}) == "finalize"
            assert next_or_finalize("risk")({"status": st}) == "finalize"

    def test_analyst_skips_debate_when_disabled(self):
        _cfg.ENABLE_DEBATE_AGENT = False
        assert route_after_analyst({"status": RunStatus.RUNNING}) == "finalize"
        _cfg.ENABLE_DEBATE_AGENT = True
        assert route_after_analyst({"status": RunStatus.RUNNING}) == "debate"
        _cfg.ENABLE_DEBATE_AGENT = False


# ── guards ──────────────────────────────────────────────────────────────────--

class TestGuards:
    def test_kill_switch_via_flag(self):
        assert not kill_switch_active()
        _cfg.KILL_SWITCH = True
        try:
            assert kill_switch_active()
        finally:
            _cfg.KILL_SWITCH = False

    def test_kill_switch_via_file(self, tmp_path):
        flag = tmp_path / "kill.flag"
        _cfg.KILL_SWITCH_FILE = str(flag)
        assert not kill_switch_active()
        flag.write_text("engaged")
        try:
            assert kill_switch_active()
        finally:
            _cfg.KILL_SWITCH_FILE = "/tmp/__no_such_killswitch__.flag"

    def test_budget_exceeded(self):
        assert not budget_exceeded({"cost_usd": 1.0, "tokens": 10})
        assert budget_exceeded({"cost_usd": 99.0, "tokens": 10})
        assert budget_exceeded({"cost_usd": 0.0, "tokens": 10**9})


# ── decorator ───────────────────────────────────────────────────────────────--

class TestAgentNode:
    def test_disabled_flag_is_pass_through(self):
        @agent_node("x", enabled_flag="ENABLE_TRADING_AGENT")  # off
        def node(state):
            raise AssertionError("must not run when disabled")

        out = node({"status": RunStatus.RUNNING})
        assert "status" not in out          # no status change
        assert out["audit"][0]["detail"] == "skipped (disabled)"

    def test_kill_switch_halts(self):
        @agent_node("x")
        def node(state):
            raise AssertionError("must not run under kill-switch")

        _cfg.KILL_SWITCH = True
        try:
            out = node({"status": RunStatus.RUNNING})
        finally:
            _cfg.KILL_SWITCH = False
        assert out["status"] == RunStatus.HALTED

    def test_budget_halts(self):
        @agent_node("x")
        def node(state):
            raise AssertionError("must not run over budget")

        out = node({"status": RunStatus.RUNNING, "cost_usd": 999.0})
        assert out["status"] == RunStatus.BUDGET_EXCEEDED

    def test_exception_becomes_failed(self):
        @agent_node("x")
        def node(state):
            raise ValueError("boom")

        out = node({"status": RunStatus.RUNNING})
        assert out["status"] == RunStatus.FAILED
        assert "boom" in out["audit"][0]["detail"]


# ── end-to-end research path (fake nodes) ─────────────────────────────────────

class TestGraphWalk:
    def _fakes(self, calls):
        def research(state):
            calls.append("research")
            return {"status": RunStatus.RUNNING, "total_screened": 3}

        def analyst(state):
            calls.append("analyst")
            return {"status": RunStatus.RUNNING}

        def finalize(state):
            calls.append("finalize")
            from agents.state import TERMINAL_STATUSES

            cur = state.get("status")
            final = cur if cur in TERMINAL_STATUSES else RunStatus.COMPLETED
            return {"status": final, "report_path": "/tmp/report.html"}

        return {"research": research, "analyst": analyst, "finalize": finalize}

    def test_research_to_analyst_to_finalize(self):
        calls = []
        graph = build_graph(checkpointer=MemorySaver(), nodes=self._fakes(calls))
        final = graph.invoke(
            {"status": RunStatus.RUNNING},
            {"configurable": {"thread_id": "t1"}, "recursion_limit": 50},
        )
        assert calls == ["research", "analyst", "finalize"]
        assert final["status"] == RunStatus.COMPLETED
        assert final["report_path"] == "/tmp/report.html"

    def test_halted_research_skips_analyst(self):
        calls = []
        fakes = self._fakes(calls)

        def halted_research(state):
            calls.append("research")
            return {"status": RunStatus.HALTED}

        fakes["research"] = halted_research
        graph = build_graph(checkpointer=MemorySaver(), nodes=fakes)
        final = graph.invoke(
            {"status": RunStatus.RUNNING},
            {"configurable": {"thread_id": "t2"}, "recursion_limit": 50},
        )
        assert "analyst" not in calls          # routed straight to finalize
        assert calls == ["research", "finalize"]
        assert final["status"] == RunStatus.HALTED


# ── recursion cap (the runaway-loop guardrail) ────────────────────────────────

class TestRecursionCap:
    def test_recursion_limit_raises(self):
        from langgraph.errors import GraphRecursionError
        from langgraph.graph import START, StateGraph
        from typing import TypedDict

        class S(TypedDict, total=False):
            n: int

        g = StateGraph(S)
        g.add_node("ping", lambda s: {"n": s.get("n", 0) + 1})
        g.add_node("pong", lambda s: {"n": s.get("n", 0) + 1})
        g.add_edge(START, "ping")
        g.add_edge("ping", "pong")
        g.add_edge("pong", "ping")           # infinite loop
        compiled = g.compile()

        with pytest.raises(GraphRecursionError):
            compiled.invoke({"n": 0}, {"recursion_limit": 6})
