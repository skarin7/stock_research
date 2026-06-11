"""Memory store + memory node (records calls + signal self-eval). No network/DB."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    ENABLE_MEMORY_AGENT=True,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    MEMORY_FILE="/tmp/__mem_test__.jsonl",
    OUTPUT_DIR="output",
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from agents.contracts import ConvictionView, RankingResult, Scorecard  # noqa: E402
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import memory as mem  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from persistence import store as store_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _bind(tmp_path, monkeypatch):
    _cfg.MEMORY_FILE = str(tmp_path / "memory.jsonl")
    for m in (_sup, _base, mem, store_mod):
        m.SETTINGS = _cfg
    monkeypatch.setattr(mem, "_regime_label", lambda: "bull")
    monkeypatch.setattr(mem, "_signal_performance", lambda: {"value": {"score_diff": 1.2}})
    yield


# ── store ───────────────────────────────────────────────────────────────────────

def test_record_and_query_roundtrip():
    store_mod.record_memory("calls", "AAA:2026-05-26", {"ticker": "AAA", "composite_score": 7.0})
    store_mod.record_memory("calls", "BBB:2026-05-26", {"ticker": "BBB", "composite_score": 6.0})
    rows = store_mod.query_memory("calls")
    assert len(rows) == 2
    assert store_mod.query_memory("nonexistent") == []


def test_recent_calls_filters_by_ticker():
    store_mod.record_memory("calls", "AAA:d1", {"ticker": "AAA", "composite_score": 7.0})
    store_mod.record_memory("calls", "BBB:d1", {"ticker": "BBB", "composite_score": 6.0})
    store_mod.record_memory("calls", "AAA:d2", {"ticker": "AAA", "composite_score": 8.0})
    aaa = store_mod.recent_calls("AAA")
    assert [c["composite_score"] for c in aaa] == [7.0, 8.0]


# ── memory node ───────────────────────────────────────────────────────────────--

def test_memory_node_records_calls_and_signal_perf():
    ranking = RankingResult(top=[
        Scorecard(ticker="AAA", composite_score=7.5, investment_rationale="cheap"),
        Scorecard(ticker="BBB", composite_score=6.8, investment_rationale="momentum"),
    ])
    convictions = [ConvictionView(ticker="AAA", direction="long", conviction=0.82)]
    state = {"run_id": "r1", "report_date": "2026-05-26", "ranking": ranking, "convictions": convictions}

    mem.memory_node(state)

    calls = store_mod.query_memory("calls")
    assert {c["value"]["ticker"] for c in calls} == {"AAA", "BBB"}
    aaa = next(c["value"] for c in calls if c["value"]["ticker"] == "AAA")
    assert aaa["regime"] == "bull"
    assert aaa["conviction"] == 0.82          # carried from the matching conviction
    assert store_mod.latest_signal_perf() == {"value": {"score_diff": 1.2}}


def test_memory_node_no_ranking_records_nothing():
    mem.memory_node({"run_id": "r1", "report_date": "2026-05-26", "ranking": None})
    assert store_mod.query_memory("calls") == []
