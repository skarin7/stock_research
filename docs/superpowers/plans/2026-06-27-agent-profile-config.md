# Agent Profile Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 9+ individual `ENABLE_*` env flags with a single `AGENT_PROFILE=research|paper|live` that expands to the right flag set at load time.

**Architecture:** Profile map lives in `settings.py`; `from_env()` reads `AGENT_PROFILE`, validates it, then calls `replace()` (already imported) to stamp all `ENABLE_*` and `AGENT_MODE` flags onto the frozen `Settings` object. Agent code is untouched — it still reads individual flags. `.env.example` drops to two pipeline knobs.

**Tech Stack:** Python dataclasses, existing `replace()` from `dataclasses`, pytest

---

### Task 1: Add profile map + `_apply_profile` + `AGENT_PROFILE` field to `settings.py`

**Files:**
- Modify: `settings.py`

- [ ] **Step 1: Add `_PROFILE_FLAGS` dict and `_apply_profile()` after the `_csv_set` helper (around line 20)**

Insert this block after the `_csv_set` function definition and before the `_default_signal_weights` function:

```python
_PROFILE_FLAGS: dict[str, dict] = {
    "research": {
        "AGENT_MODE": "research",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": False,
        "ENABLE_RISK_AGENT": False,
        "ENABLE_PORTFOLIO_AGENT": False,
        "ENABLE_TRADING_AGENT": False,
        "ENABLE_MONITORING_AGENT": False,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": False,
        "GROWW_TRADING_ENABLED": False,
        "ENABLE_AUTO_EXIT": False,
    },
    "paper": {
        "AGENT_MODE": "paper",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": True,
        "ENABLE_RISK_AGENT": True,
        "ENABLE_PORTFOLIO_AGENT": True,
        "ENABLE_TRADING_AGENT": True,
        "ENABLE_MONITORING_AGENT": True,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": False,
        "GROWW_TRADING_ENABLED": False,
        "ENABLE_AUTO_EXIT": False,
    },
    "live": {
        "AGENT_MODE": "live",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": True,
        "ENABLE_RISK_AGENT": True,
        "ENABLE_PORTFOLIO_AGENT": True,
        "ENABLE_TRADING_AGENT": True,
        "ENABLE_MONITORING_AGENT": True,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": True,
        "GROWW_TRADING_ENABLED": True,
        "ENABLE_AUTO_EXIT": True,
    },
}


def _apply_profile(settings: "Settings") -> "Settings":
    """Expand AGENT_PROFILE into individual flags. Raises ValueError for unknown profiles."""
    import logging
    profile = settings.AGENT_PROFILE.strip().lower()
    if profile not in _PROFILE_FLAGS:
        raise ValueError(
            f"Unknown AGENT_PROFILE={profile!r}. Valid values: {', '.join(_PROFILE_FLAGS)}"
        )
    flags = _PROFILE_FLAGS[profile]
    result = replace(settings, **flags)
    agents_on = [k.removeprefix("ENABLE_").lower() for k, v in flags.items() if k.startswith("ENABLE_") and v]
    logging.getLogger("agents.settings").info(
        "profile=%s → %s agents ON", profile, "+".join(agents_on) if agents_on else "none"
    )
    return result
```

- [ ] **Step 2: Add `AGENT_PROFILE` field to the `Settings` dataclass**

In the `# --- Multi-agent system ---` section of the `Settings` dataclass, replace the existing block:

```python
    # --- Multi-agent system ---
    AGENT_MODE: str = "research"
    ENABLE_RESEARCH_AGENT: bool = True
    ENABLE_ANALYST_AGENT: bool = True
    ENABLE_DEBATE_AGENT: bool = False
    ENABLE_RISK_AGENT: bool = False
    ENABLE_PORTFOLIO_AGENT: bool = False
    ENABLE_TRADING_AGENT: bool = False
    ENABLE_MONITORING_AGENT: bool = False
    ENABLE_MEMORY_AGENT: bool = False
```

with:

```python
    # --- Multi-agent system ---
    AGENT_PROFILE: str = "research"   # research | paper | live
    AGENT_MODE: str = "research"      # set by profile — do not set directly
    ENABLE_RESEARCH_AGENT: bool = True
    ENABLE_ANALYST_AGENT: bool = True
    ENABLE_DEBATE_AGENT: bool = False
    ENABLE_RISK_AGENT: bool = False
    ENABLE_PORTFOLIO_AGENT: bool = False
    ENABLE_TRADING_AGENT: bool = False
    ENABLE_MONITORING_AGENT: bool = False
    ENABLE_MEMORY_AGENT: bool = False
```

- [ ] **Step 3: Update `from_env()` to read `AGENT_PROFILE` and apply it**

In `from_env()`, replace the block that reads individual agent flags:

```python
            AGENT_MODE=os.environ.get("AGENT_MODE", "research").strip().lower(),
            ENABLE_RESEARCH_AGENT=_flag("ENABLE_RESEARCH_AGENT", "true"),
            ENABLE_ANALYST_AGENT=_flag("ENABLE_ANALYST_AGENT", "true"),
            ENABLE_DEBATE_AGENT=_flag("ENABLE_DEBATE_AGENT", "false"),
            ENABLE_RISK_AGENT=_flag("ENABLE_RISK_AGENT", "false"),
            ENABLE_PORTFOLIO_AGENT=_flag("ENABLE_PORTFOLIO_AGENT", "false"),
            ENABLE_TRADING_AGENT=_flag("ENABLE_TRADING_AGENT", "false"),
            ENABLE_MONITORING_AGENT=_flag("ENABLE_MONITORING_AGENT", "false"),
            ENABLE_MEMORY_AGENT=_flag("ENABLE_MEMORY_AGENT", "false"),
            ENABLE_LIVE_TRADING=_flag("ENABLE_LIVE_TRADING", "false"),
            GROWW_TRADING_ENABLED=_flag("GROWW_TRADING_ENABLED", "false"),
```

with:

```python
            AGENT_PROFILE=os.environ.get("AGENT_PROFILE", "research").strip().lower(),
```

Then at the end of `from_env()`, before `return base`, add:

```python
        return _apply_profile(base)
```

And remove the bare `return base` line that was there.

- [ ] **Step 4: Verify settings module imports cleanly**

```bash
python -c "from settings import Settings; s = Settings.from_env(); print(s.AGENT_PROFILE, s.ENABLE_DEBATE_AGENT)"
```

Expected output (with no `.env`): `research False`

---

### Task 2: Write unit tests for profile expansion

**Files:**
- Create: `tests/test_profile_config.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for AGENT_PROFILE → flag expansion in settings.py."""
import pytest
from dataclasses import replace
from settings import Settings, _apply_profile, _PROFILE_FLAGS


def _base(profile: str) -> Settings:
    return Settings(AGENT_PROFILE=profile)


class TestProfileResearch:
    def test_agents_on(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_RESEARCH_AGENT is True
        assert s.ENABLE_ANALYST_AGENT is True
        assert s.ENABLE_MEMORY_AGENT is True

    def test_agents_off(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_DEBATE_AGENT is False
        assert s.ENABLE_RISK_AGENT is False
        assert s.ENABLE_PORTFOLIO_AGENT is False
        assert s.ENABLE_TRADING_AGENT is False
        assert s.ENABLE_MONITORING_AGENT is False

    def test_no_live_trading(self):
        s = _apply_profile(_base("research"))
        assert s.ENABLE_LIVE_TRADING is False
        assert s.GROWW_TRADING_ENABLED is False
        assert s.ENABLE_AUTO_EXIT is False

    def test_agent_mode(self):
        s = _apply_profile(_base("research"))
        assert s.AGENT_MODE == "research"


class TestProfilePaper:
    def test_all_pipeline_agents_on(self):
        s = _apply_profile(_base("paper"))
        for flag in [
            "ENABLE_RESEARCH_AGENT", "ENABLE_ANALYST_AGENT", "ENABLE_DEBATE_AGENT",
            "ENABLE_RISK_AGENT", "ENABLE_PORTFOLIO_AGENT", "ENABLE_TRADING_AGENT",
            "ENABLE_MONITORING_AGENT", "ENABLE_MEMORY_AGENT",
        ]:
            assert getattr(s, flag) is True, f"{flag} should be True for paper"

    def test_no_live_trading(self):
        s = _apply_profile(_base("paper"))
        assert s.ENABLE_LIVE_TRADING is False
        assert s.GROWW_TRADING_ENABLED is False
        assert s.ENABLE_AUTO_EXIT is False

    def test_agent_mode(self):
        s = _apply_profile(_base("paper"))
        assert s.AGENT_MODE == "paper"


class TestProfileLive:
    def test_all_agents_on(self):
        s = _apply_profile(_base("live"))
        for flag in [
            "ENABLE_RESEARCH_AGENT", "ENABLE_ANALYST_AGENT", "ENABLE_DEBATE_AGENT",
            "ENABLE_RISK_AGENT", "ENABLE_PORTFOLIO_AGENT", "ENABLE_TRADING_AGENT",
            "ENABLE_MONITORING_AGENT", "ENABLE_MEMORY_AGENT",
            "ENABLE_LIVE_TRADING", "GROWW_TRADING_ENABLED", "ENABLE_AUTO_EXIT",
        ]:
            assert getattr(s, flag) is True, f"{flag} should be True for live"

    def test_agent_mode(self):
        s = _apply_profile(_base("live"))
        assert s.AGENT_MODE == "live"


class TestProfileValidation:
    def test_invalid_profile_raises(self):
        with pytest.raises(ValueError, match="Unknown AGENT_PROFILE"):
            _apply_profile(_base("yolo"))

    def test_case_insensitive(self):
        s = _apply_profile(Settings(AGENT_PROFILE="PAPER"))
        assert s.AGENT_MODE == "paper"

    def test_chat_agent_not_touched_by_profile(self):
        """ENABLE_CHAT_AGENT is orthogonal — profile must not override it."""
        s = _apply_profile(Settings(AGENT_PROFILE="live", ENABLE_CHAT_AGENT=False))
        assert s.ENABLE_CHAT_AGENT is False
        s2 = _apply_profile(Settings(AGENT_PROFILE="research", ENABLE_CHAT_AGENT=True))
        assert s2.ENABLE_CHAT_AGENT is True
```

- [ ] **Step 2: Run tests — expect FAIL (profile map not added yet if doing TDD, or PASS if Task 1 already done)**

```bash
python -m pytest tests/test_profile_config.py -v
```

Expected: all 12 tests PASS (Task 1 already complete).

- [ ] **Step 3: Run full test suite — check no regressions**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: same pass count as before + 12 new passes.

- [ ] **Step 4: Commit**

```bash
git add settings.py tests/test_profile_config.py
git commit -m "feat: replace ENABLE_* flags with AGENT_PROFILE=research|paper|live"
```

---

### Task 3: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Replace the multi-agent block in `.env.example`**

Remove this entire section:

```
# ─────────────────────────────────────────────────────────────────────────────
# Multi-agent flow (run_agents.py). Two kinds of flags below — keep them distinct:
#   (1) CAPABILITY flags decide which nodes run (graph shape).
#   (2) SAFETY INTERLOCKS are default-deny guards on irreversible actions.
# ─────────────────────────────────────────────────────────────────────────────

# Run mode: research | paper | live  (state["mode"]; live = real orders via broker)
AGENT_MODE=research

# (1) Capability flags — off by default = research-only path
#     (research → analyst → finalize → memory). Turn ON the whole entry chain to
#     get a Telegram trade proposal you approve to place an order:
ENABLE_DEBATE_AGENT=false       # bull/bear conviction; REQUIRED to reach risk/portfolio/trading
ENABLE_RISK_AGENT=false         # emits the TradeProposal (rationale + risk checks)
ENABLE_PORTFOLIO_AGENT=false    # position sizing → qty + limit_price (the ₹ budget)
ENABLE_TRADING_AGENT=false      # live: interrupt() for human approval; paper: simulate fill
ENABLE_MONITORING_AGENT=false   # stop-loss watch (run via --mode monitor)
ENABLE_MEMORY_AGENT=false       # record each call to long-term memory

# (2) Safety interlocks — ALL must be true for a real order to leave the broker
#     (default-deny, checked again inside the broker layer). Independent of (1).
ENABLE_LIVE_TRADING=false       # master live-order switch
GROWW_TRADING_ENABLED=false     # broker-level gate
KILL_SWITCH=false               # true OR the flag file present → halts everything
```

Replace with:

```
# ─────────────────────────────────────────────────────────────────────────────
# Multi-agent flow (run_agents.py)
# ─────────────────────────────────────────────────────────────────────────────

# Active profile — controls which agents run and whether live trading is enabled.
#   research  →  score + report + Telegram alert only (safe, default)
#   paper     →  full pipeline with simulated fills + stop-loss monitoring
#   live      →  real broker orders via Groww (requires Groww API keys + human approval)
AGENT_PROFILE=research

KILL_SWITCH=false               # true OR the flag file present → halts everything
```

- [ ] **Step 2: Verify `.env.example` still has `ENABLE_CHAT_AGENT`**

The chat-agent block must remain unchanged:

```
# ─── Conversational chat agent (Telegram webhook service) ──────────────────────
ENABLE_CHAT_AGENT=false
```

Confirm it's still present after the edit.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: simplify .env.example — AGENT_PROFILE replaces 9 ENABLE_* flags"
```
