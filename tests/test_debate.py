"""Debate subgraph + node tests. No network/keys — the LLM turn (`_chat`) is faked."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    ENABLE_DEBATE_AGENT=True,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_such_killswitch__.flag",
    MAX_RUN_COST_USD=5.0,
    MAX_RUN_TOKENS=1_000_000,
    MAX_DEBATE_ROUNDS=2,
    DEBATE_TOP_N=5,
    OUTPUT_DIR="output",
)
sys.modules["config"] = _cfg

from agents.contracts import (  # noqa: E402
    EnrichedStock,
    EnrichmentResult,
    RankingResult,
    Scorecard,
    SignalScore,
)
from agents.nodes import base as _base  # noqa: E402
from agents.nodes import debate as debate_mod  # noqa: E402
from agents import supervisor as _sup  # noqa: E402
from agents.state import RunStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _bind_config():
    """Bind the agent-layer modules to THIS file's config (order-independent)."""
    _sup.config = _cfg
    _base.config = _cfg
    debate_mod.config = _cfg
    yield


def _fake_chat(messages, *, max_tokens=400, temperature=0.4):
    system = messages[0][1]
    if "judge" in system:
        return '{"direction": "long", "conviction": 0.8}', 10
    if "Bull" in system:
        return "Bull: strong delivery and cheap vs sector.", 12
    return "Bear: near earnings, momentum stretched.", 11


def _scorecard(ticker):
    return Scorecard(
        ticker=ticker,
        composite_score=7.5,
        signals={"value": SignalScore(score=8, reason="cheap")},
        investment_rationale="solid",
        risk_flags=["earnings_soon"],
    )


# ── inner subgraph ─────────────────────────────────────────────────────────────

def test_subgraph_is_bounded_and_synthesizes(monkeypatch):
    monkeypatch.setattr(debate_mod, "_chat", _fake_chat)
    sub = debate_mod.build_debate_subgraph()
    result = sub.invoke(
        {"ticker": "ABC", "context": "facts", "rounds": 0, "max_rounds": 2,
         "tokens": 0, "transcript": []},
        {"recursion_limit": 20},
    )
    assert result["rounds"] == 2                       # hard turn cap honoured
    assert len(result["transcript"]) == 4              # 2 bull + 2 bear
    assert [t["side"] for t in result["transcript"]] == ["bull", "bear", "bull", "bear"]
    assert result["conviction"] == {"direction": "long", "conviction": 0.8}
    assert result["tokens"] > 0


def test_parse_conviction_handles_garbage():
    assert debate_mod._parse_conviction("not json") == {"direction": "neutral", "conviction": 0.0}
    assert debate_mod._parse_conviction('{"direction":"short","conviction":1.7}')["conviction"] == 1.0


# ── outer node ──────────────────────────────────────────────────────────────────

def test_debate_node_emits_convictions(monkeypatch):
    monkeypatch.setattr(debate_mod, "_chat", _fake_chat)
    ranking = RankingResult(top=[_scorecard("AAA"), _scorecard("BBB")])
    enriched = EnrichmentResult(
        stocks=[EnrichedStock(symbol="AAA", sector="Banking"),
                EnrichedStock(symbol="BBB", sector="IT")],
        macro_context="rates steady",
    )
    state = {"status": RunStatus.RUNNING, "ranking": ranking, "enriched": enriched, "tokens": 0}

    out = debate_mod.debate_node(state)

    assert out["status"] == RunStatus.RUNNING
    convs = out["convictions"]
    assert {c.ticker for c in convs} == {"AAA", "BBB"}
    for c in convs:
        assert c.direction == "long"
        assert c.conviction == 0.8
        assert c.composite_score == 7.5
        assert len(c.transcript) == 4
    assert out["tokens"] > 0


def test_debate_node_respects_top_n(monkeypatch):
    monkeypatch.setattr(debate_mod, "_chat", _fake_chat)
    monkeypatch.setattr(_cfg, "DEBATE_TOP_N", 1)
    ranking = RankingResult(top=[_scorecard("AAA"), _scorecard("BBB"), _scorecard("CCC")])
    state = {"status": RunStatus.RUNNING, "ranking": ranking,
             "enriched": EnrichmentResult(stocks=[]), "tokens": 0}

    out = debate_mod.debate_node(state)
    assert len(out["convictions"]) == 1                # only the top-1 debated
    monkeypatch.setattr(_cfg, "DEBATE_TOP_N", 5)


def test_debate_node_skips_when_no_ranking(monkeypatch):
    monkeypatch.setattr(debate_mod, "_chat", _fake_chat)
    out = debate_mod.debate_node({"status": RunStatus.RUNNING, "ranking": None})
    assert "convictions" not in out                    # clean skip
