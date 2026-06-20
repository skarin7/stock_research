"""Chat agent loop: kill-switch, exception handling, reply extraction. No network/keys."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    LLM_PROVIDER="anthropic",
    REPORT_MODEL="claude-sonnet-4-6",
    SCORING_MODEL="claude-haiku-4-5",
    CHAT_MODEL="",
    DATABASE_URL="",
    OUTPUT_DIR="output",
    SNAPSHOT_STALE_DAYS=3,
    POSITIONS_FILE="/tmp/__chat_agent_positions__.json",
    TRADING_CAPITAL_INR=100000.0,
    MAX_CHAT_TOOL_CALLS=8,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__no_kill__.flag",
    SIGNAL_WEIGHTS={"news_sentiment": 0.20, "bulk_deals": 0.20, "momentum": 0.15,
                    "value": 0.20, "delivery_pct": 0.10, "52w_position": 0.05,
                    "institutional_trend": 0.05, "sector_rotation": 0.05},
    TOP_N_STOCKS=15,
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)


def _fake_agent(messages):
    """Minimal agent-like namespace: invoke returns a messages dict."""
    def invoke(input_dict, cfg=None):
        return {"messages": messages}
    return types.SimpleNamespace(invoke=invoke)


# ── kill-switch ───────────────────────────────────────────────────────────────

def test_run_turn_halted_by_kill_switch(monkeypatch):
    from agents import supervisor as sup
    monkeypatch.setattr(sup, "kill_switch_active", lambda: True)
    import agents.chat.agent as agent_mod

    reply = agent_mod.run_turn("chat123", "hello")
    assert "kill" in reply.lower() or "halt" in reply.lower()


# ── basic turn ────────────────────────────────────────────────────────────────

def test_run_turn_returns_last_ai_content(monkeypatch):
    from agents import supervisor as sup
    monkeypatch.setattr(sup, "kill_switch_active", lambda: False)
    import agents.chat.agent as agent_mod

    msgs = [
        types.SimpleNamespace(type="human", content="question",
                               usage_metadata=None, tool_calls=[]),
        types.SimpleNamespace(type="ai", content="Best stock today is INFY.",
                               usage_metadata={"total_tokens": 50}, tool_calls=[]),
    ]
    monkeypatch.setattr(agent_mod, "_get_agent", lambda: _fake_agent(msgs))
    monkeypatch.setattr(agent_mod, "_record_turn", lambda *a, **kw: None)

    reply = agent_mod.run_turn("chat1", "what is best stock today?")
    assert "INFY" in reply


def test_run_turn_exception_returns_error_string(monkeypatch):
    from agents import supervisor as sup
    monkeypatch.setattr(sup, "kill_switch_active", lambda: False)
    import agents.chat.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_get_agent", lambda: types.SimpleNamespace(
        invoke=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db dead"))
    ))
    monkeypatch.setattr(agent_mod, "_record_turn", lambda *a, **kw: None)

    reply = agent_mod.run_turn("chat1", "hello")
    assert "went wrong" in reply.lower() or "error" in reply.lower()


def test_run_turn_recursion_error_gives_friendly_reply(monkeypatch):
    from langgraph.errors import GraphRecursionError

    from agents import supervisor as sup
    monkeypatch.setattr(sup, "kill_switch_active", lambda: False)
    import agents.chat.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_get_agent", lambda: types.SimpleNamespace(
        invoke=lambda *a, **kw: (_ for _ in ()).throw(GraphRecursionError("too many"))
    ))
    monkeypatch.setattr(agent_mod, "_record_turn", lambda *a, **kw: None)

    reply = agent_mod.run_turn("chat1", "very complex question")
    assert "limit" in reply.lower() or "narrow" in reply.lower()


# ── reply extraction ──────────────────────────────────────────────────────────

def test_reply_from_picks_last_non_empty_ai():
    import agents.chat.agent as agent_mod

    def _mk(type_, content):
        return types.SimpleNamespace(type=type_, content=content)

    result = {"messages": [
        _mk("human", "question"),
        _mk("tool", "tool output"),
        _mk("ai", ""),                       # empty — skip
        _mk("ai", "Final answer here."),
    ]}
    assert agent_mod._reply_from(result) == "Final answer here."


def test_reply_from_no_ai_returns_fallback():
    import agents.chat.agent as agent_mod

    result = {"messages": [types.SimpleNamespace(type="human", content="hi")]}
    assert agent_mod._reply_from(result)  # non-empty fallback


# ── token extraction ──────────────────────────────────────────────────────────

def test_turn_tokens_sums_all_messages():
    import agents.chat.agent as agent_mod

    def _mk(inp, out):
        return types.SimpleNamespace(usage_metadata={"input_tokens": inp, "output_tokens": out})

    result = {"messages": [_mk(100, 20), _mk(50, 10),
                           types.SimpleNamespace(usage_metadata=None)]}
    tokens, cost = agent_mod._turn_tokens(result)
    assert tokens == 180  # 100+20 + 50+10
    # Sonnet pricing: $3/M input, $15/M output
    assert cost == round(150 * 3e-6 + 30 * 15e-6, 6)
