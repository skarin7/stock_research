# User Flows & System Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 11+ ENABLE_* flags with a single `TRADING_MODE=off|paper|live` knob, extract pure risk/portfolio functions for the new `propose_trade` chat tool, add `watch` and `intraday` run modes, and migrate `pulse_state` to Postgres.

**Architecture:** `settings.py` loses all ENABLE_* fields; `config.py` gains `trading_enabled()` / `live_trading()` helpers. The `agent_node` decorator gains `requires_trading: bool` in place of `enabled_flag: str`. Chat gets two new tools reusing the extracted risk/portfolio functions. `run_agents.py watch` directly calls monitor + pulse logic without a LangGraph graph.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, Postgres (asyncpg via `persistence/db.py`), Telegram Bot API.

---

## File Map

**Modified**
- `settings.py` — drop `_PROFILE_FLAGS`, `_apply_profile`, 15 ENABLE_* fields; add `TRADING_MODE`
- `config.py` — add `trading_enabled()` / `live_trading()` helpers
- `agents/nodes/base.py` — `requires_trading: bool` replaces `enabled_flag: Optional[str]`
- `agents/nodes/research.py` — drop enabled_flag arg
- `agents/nodes/analyst.py` — drop enabled_flag arg
- `agents/nodes/debate.py` — `requires_trading=True`
- `agents/nodes/risk.py` — `requires_trading=True` + extract `run_risk_checks()`
- `agents/nodes/portfolio.py` — `requires_trading=True` + extract `size_proposals()`
- `agents/nodes/trading.py` — `requires_trading=True`, `ENABLE_LIVE_TRADING` → `live_trading()`
- `agents/nodes/monitoring.py` — drop enabled_flag, `ENABLE_LIVE_TRADING` → `live_trading()`
- `agents/nodes/memory.py` — drop enabled_flag
- `agents/nodes/pulse.py` — drop enabled_flag
- `agents/supervisor.py` — `ENABLE_DEBATE_AGENT` → `trading_enabled()`
- `agents/broker/auto_exit.py` — `ENABLE_AUTO_EXIT` → `AUTO_TRADE_ALLOWLIST` check
- `agents/broker/groww_trader.py` — `ENABLE_LIVE_TRADING` → `live_trading()`
- `agents/chat/tools.py` — add `propose_trade` + `intraday_watchlist`
- `run_agents.py` — add `watch` + `intraday` modes, remove `monitor`/`pulse` modes
- `run_intraday.py` — deprecation stub
- `persistence/store.py` — `load/save_pulse_state` gains Postgres path
- `tests/test_profile_config.py` — rewrite for TRADING_MODE
- `tests/test_risk_portfolio.py` — update config fixtures
- `tests/test_trading_live.py` — update live checks
- `tests/test_debate.py` — update config fixture

**Created**
- `deploy/vm/setup.sh` — one-shot VM provisioning
- `deploy/vm/nginx.conf` — reverse proxy for uvicorn
- `deploy/vm/stock-chat.service` — systemd unit
- `deploy/vm/crontab` — production schedule

---

## Task 1: settings.py + config.py — TRADING_MODE replaces 11 flags

**Files:**
- Modify: `settings.py`
- Modify: `config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_mode.py
import pytest
from settings import Settings


def test_trading_mode_defaults_off():
    s = Settings()
    assert s.TRADING_MODE == "off"


def test_trading_mode_paper():
    s = Settings(TRADING_MODE="paper")
    assert s.TRADING_MODE == "paper"


def test_trading_mode_invalid_raises():
    with pytest.raises(ValueError, match="TRADING_MODE"):
        Settings.from_env.__func__(Settings)  # can't easily test from_env, test _validate directly


def test_no_enable_flags_on_settings():
    s = Settings()
    assert not hasattr(s, "ENABLE_RESEARCH_AGENT")
    assert not hasattr(s, "AGENT_PROFILE")
    assert not hasattr(s, "ENABLE_LIVE_TRADING")
```

- [ ] **Step 2: Run to verify it fails**

```
cd /home/shankar/Work/AI_Projects/AI-Agents/stock_research/.claude/worktrees/query-filter-extraction
python -m pytest tests/test_trading_mode.py -v
```

Expected: `AttributeError` or `AssertionError` (Settings still has the old fields).

- [ ] **Step 3: Rewrite settings.py**

Replace the entire file. Key changes:
1. Remove `_PROFILE_FLAGS` dict and `_apply_profile()` function
2. Remove 15 ENABLE_* fields + `AGENT_PROFILE` + `AGENT_MODE` from the `Settings` dataclass
3. Add `TRADING_MODE: str = "off"`
4. Add `_validate_trading_mode()` called at end of `from_env()`
5. Remove the `_apply_profile(base)` call from `from_env()`

Removed fields (delete these lines from the dataclass and from `from_env()`):
```
AGENT_PROFILE, AGENT_MODE,
ENABLE_RESEARCH_AGENT, ENABLE_ANALYST_AGENT, ENABLE_DEBATE_AGENT,
ENABLE_RISK_AGENT, ENABLE_PORTFOLIO_AGENT, ENABLE_TRADING_AGENT,
ENABLE_MONITORING_AGENT, ENABLE_MEMORY_AGENT, ENABLE_PULSE_AGENT,
ENABLE_LIVE_TRADING, GROWW_TRADING_ENABLED,
ENABLE_CHAT_AGENT, ENABLE_AUTO_EXIT,
```

Add to the dataclass (in the Multi-agent system section):
```python
# --- Trading mode (replaces all ENABLE_* flags) ---
TRADING_MODE: str = "off"       # off | paper | live
```

Add to `from_env()` (replace the AGENT_PROFILE line):
```python
TRADING_MODE=os.environ.get("TRADING_MODE", "off").strip().lower(),
```

Add after the `base = cls(...)` construction in `from_env()`:
```python
valid = ("off", "paper", "live")
if base.TRADING_MODE not in valid:
    raise ValueError(
        f"TRADING_MODE={base.TRADING_MODE!r} invalid. Valid values: {', '.join(valid)}"
    )
return base
```

Remove the `return _apply_profile(base)` line and replace with `return base`.

Update `__all__` to remove `_PROFILE_FLAGS` and `_apply_profile`:
```python
__all__ = ["Settings", "replace"]
```

- [ ] **Step 4: Update config.py**

```python
"""Process-wide configuration: the single typed Settings instance."""
from dotenv import load_dotenv

from settings import Settings

load_dotenv()

SETTINGS = Settings.from_env()


def trading_enabled() -> bool:
    """True when TRADING_MODE is paper or live."""
    return SETTINGS.TRADING_MODE in ("paper", "live")


def live_trading() -> bool:
    """True when TRADING_MODE is live."""
    return SETTINGS.TRADING_MODE == "live"


__all__ = ["SETTINGS", "trading_enabled", "live_trading"]
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/test_trading_mode.py -v
```

Expected: all pass.

Also run the existing profile test to confirm it fails (we'll replace it in Task 9):
```
python -m pytest tests/test_profile_config.py -v
```

Expected: all fail (these tests test the old API — expected).

- [ ] **Step 6: Commit**

```bash
git add settings.py config.py tests/test_trading_mode.py
git commit -m "feat(config): replace 11 ENABLE_* flags with TRADING_MODE=off|paper|live"
```

---

## Task 2: agents/nodes/base.py — requires_trading replaces enabled_flag

**Files:**
- Modify: `agents/nodes/base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_node.py
import pytest
from agents.state import AgentState, RunStatus


def test_requires_trading_skips_when_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", config.SETTINGS.__class__(TRADING_MODE="off"))

    from agents.nodes.base import agent_node

    @agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert "executed" not in result


def test_requires_trading_runs_when_paper(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", config.SETTINGS.__class__(TRADING_MODE="paper"))

    from agents.nodes.base import agent_node

    @agent_node("test_trade", requires_trading=True)
    def my_node(state):
        return {"executed": True}

    result = my_node({"run_id": "r1", "status": RunStatus.RUNNING})
    assert result.get("executed") is True
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/test_base_node.py -v
```

Expected: `TypeError: agent_node() got unexpected keyword argument 'requires_trading'`

- [ ] **Step 3: Update base.py**

Replace `enabled_flag: Optional[str] = None` with `requires_trading: bool = False` and update the check:

```python
"""``agent_node`` decorator — the common guard wrapper for every graph node."""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable

from config import SETTINGS, trading_enabled

from agents.state import AgentState, RunStatus
from agents.supervisor import audit_entry, budget_exceeded, kill_switch_active
from observability import metrics
from observability.chat_tracing import trace_node

logger = logging.getLogger("agents")


def agent_node(name: str, requires_trading: bool = False) -> Callable:
    """Wrap a node fn with kill-switch / budget / trading-gate / error guards."""

    def decorator(fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
        @functools.wraps(fn)
        def wrapper(state: AgentState) -> dict:
            if kill_switch_active():
                logger.error("[%s] kill-switch active — halting run", name)
                metrics.inc_node_error(name)
                return {"status": RunStatus.HALTED,
                        "audit": [audit_entry(name, state.get("status"), RunStatus.HALTED, "kill-switch")]}

            if budget_exceeded(state):
                logger.error("[%s] run budget exceeded — halting", name)
                metrics.inc_budget_exceeded()
                return {"status": RunStatus.BUDGET_EXCEEDED,
                        "audit": [audit_entry(name, state.get("status"), RunStatus.BUDGET_EXCEEDED, "budget")]}

            if requires_trading and not trading_enabled():
                logger.info("[%s] requires TRADING_MODE=paper|live — skipping", name)
                return {"audit": [audit_entry(name, state.get("status"), state.get("status"), "skipped (trading off)")]}

            run_id = state.get("run_id", "unknown")
            input_summary = {
                "stocks": len(state.get("stocks") or []),
                "proposals": len(state.get("proposals") or []),
            }
            start = time.monotonic()
            with trace_node(name, run_id, input_summary) as span:
                try:
                    update = fn(state) or {}
                except Exception as e:
                    logger.exception("[%s] failed: %s", name, e)
                    metrics.inc_node_error(name)
                    return {"status": RunStatus.FAILED,
                            "audit": [audit_entry(name, state.get("status"), RunStatus.FAILED, str(e))]}
                finally:
                    metrics.observe_node_latency(name, time.monotonic() - start)

                span.set_output({
                    "status": str(update.get("status", state.get("status", ""))),
                    "stocks_out": len(update.get("stocks") or []),
                    "proposals_out": len(update.get("proposals") or []),
                    "scores_out": len(update.get("scores") or []),
                })

            update.setdefault("audit", []).append(
                audit_entry(name, state.get("status"), update.get("status", state.get("status")), "ok")
            )
            return update

        return wrapper

    return decorator
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_base_node.py -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add agents/nodes/base.py tests/test_base_node.py
git commit -m "feat(base): replace enabled_flag with requires_trading bool in agent_node decorator"
```

---

## Task 3: Update all node decorators + broker flag refs

**Files:**
- Modify: `agents/nodes/research.py`, `agents/nodes/analyst.py`, `agents/nodes/debate.py`
- Modify: `agents/nodes/risk.py`, `agents/nodes/portfolio.py`, `agents/nodes/trading.py`
- Modify: `agents/nodes/monitoring.py`, `agents/nodes/memory.py`, `agents/nodes/pulse.py`
- Modify: `agents/supervisor.py`, `agents/broker/auto_exit.py`, `agents/broker/groww_trader.py`

- [ ] **Step 1: Update node decorator lines**

In each file, replace the `@agent_node(...)` line:

**research.py** (line 30):
```python
# Before:
@agent_node("research", enabled_flag="ENABLE_RESEARCH_AGENT")
# After:
@agent_node("research")
```

**analyst.py** (line 21):
```python
# Before:
@agent_node("analyst", enabled_flag="ENABLE_ANALYST_AGENT")
# After:
@agent_node("analyst")
```

**debate.py** (line 164):
```python
# Before:
@agent_node("debate", enabled_flag="ENABLE_DEBATE_AGENT")
# After:
@agent_node("debate", requires_trading=True)
```

**risk.py** (line 24):
```python
# Before:
@agent_node("risk", enabled_flag="ENABLE_RISK_AGENT")
# After:
@agent_node("risk", requires_trading=True)
```

**portfolio.py** (line 25):
```python
# Before:
@agent_node("portfolio", enabled_flag="ENABLE_PORTFOLIO_AGENT")
# After:
@agent_node("portfolio", requires_trading=True)
```

**trading.py** (line 37):
```python
# Before:
@agent_node("trading", enabled_flag="ENABLE_TRADING_AGENT")
# After:
@agent_node("trading", requires_trading=True)
```

**monitoring.py** (line 59):
```python
# Before:
@agent_node("monitor", enabled_flag="ENABLE_MONITORING_AGENT")
# After:
@agent_node("monitor")
```

**memory.py** (line 52):
```python
# Before:
@agent_node("memory", enabled_flag="ENABLE_MEMORY_AGENT")
# After:
@agent_node("memory")
```

**pulse.py** (line 99):
```python
# Before:
@agent_node("pulse", enabled_flag="ENABLE_PULSE_AGENT")
# After:
@agent_node("pulse")
```

- [ ] **Step 2: Update broker flag refs**

**agents/supervisor.py** (line 60) — replace `ENABLE_DEBATE_AGENT` with `trading_enabled()`:
```python
# Before:
from config import SETTINGS
...
if getattr(SETTINGS, "ENABLE_DEBATE_AGENT", False):

# After — add import at top:
from config import SETTINGS, trading_enabled
...
if trading_enabled():
```

**agents/nodes/trading.py** (line 87) — replace `ENABLE_LIVE_TRADING`:
```python
# Add to imports at top of file:
from config import SETTINGS, live_trading

# Replace line 87:
# Before:
if not getattr(SETTINGS, "ENABLE_LIVE_TRADING", False):
    logger.warning("trading(live): ENABLE_LIVE_TRADING=false — default-deny, %d approved, no orders",

# After:
if not live_trading():
    logger.warning("trading(live): TRADING_MODE!=live — default-deny, %d approved, no orders",
```

**agents/nodes/monitoring.py** (line 66) — replace `ENABLE_LIVE_TRADING`:
```python
# Add to imports at top:
from config import SETTINGS, live_trading

# Replace line 66:
# Before:
live = bool(getattr(SETTINGS, "ENABLE_LIVE_TRADING", False))
# After:
live = live_trading()
```

**agents/broker/auto_exit.py** (lines 101-102) — replace `ENABLE_AUTO_EXIT`:
```python
# Before:
if not getattr(g, "ENABLE_AUTO_EXIT", False):
    raise ExitRefused("ENABLE_AUTO_EXIT off")

# After (AUTO_TRADE_ALLOWLIST empty = auto-exit disabled):
if not getattr(g, "AUTO_TRADE_ALLOWLIST", frozenset()):
    raise ExitRefused("AUTO_TRADE_ALLOWLIST empty — auto-exit disabled")
```

**agents/broker/groww_trader.py** (line 38-39) — replace `ENABLE_LIVE_TRADING`:
```python
# Add import at top:
from config import SETTINGS, live_trading

# Replace lines 38-39:
# Before:
if not getattr(SETTINGS, "ENABLE_LIVE_TRADING", False):
    raise BrokerRefused("ENABLE_LIVE_TRADING is false")
# After:
if not live_trading():
    raise BrokerRefused("TRADING_MODE != live")
```

- [ ] **Step 3: Run existing tests to confirm nothing is broken**

```
python -m pytest tests/ -v --ignore=tests/test_profile_config.py -x
```

Expected: all pass (test_profile_config.py is excluded — it tests the old API, replaced in Task 9).

- [ ] **Step 4: Commit**

```bash
git add agents/nodes/research.py agents/nodes/analyst.py agents/nodes/debate.py \
        agents/nodes/risk.py agents/nodes/portfolio.py agents/nodes/trading.py \
        agents/nodes/monitoring.py agents/nodes/memory.py agents/nodes/pulse.py \
        agents/supervisor.py agents/broker/auto_exit.py agents/broker/groww_trader.py
git commit -m "feat(nodes): replace enabled_flag/ENABLE_* refs with requires_trading + live_trading()"
```

---

## Task 4: Extract pure functions from risk.py and portfolio.py

**Why:** The `propose_trade` chat tool (Task 5) needs to run risk + portfolio logic without a LangGraph state dict.

**Files:**
- Modify: `agents/nodes/risk.py`
- Modify: `agents/nodes/portfolio.py`

- [ ] **Step 1: Write the failing tests for pure functions**

```python
# tests/test_risk_portfolio_pure.py
import pytest
from agents.contracts import ConvictionView, ProposalStatus
from agents.nodes.risk import run_risk_checks
from agents.nodes.portfolio import size_proposals
from settings import Settings


def _cv(ticker, conviction=0.8, direction="long"):
    return ConvictionView(
        ticker=ticker, direction=direction, conviction=conviction,
        bull_case="bull", bear_case="bear", rationale="ok",
    )


def test_run_risk_checks_blocks_low_conviction():
    cv = _cv("INFY", conviction=0.3)
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held=set(),
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1"
    )
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_run_risk_checks_blocks_duplicate():
    cv = _cv("INFY")
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held={"INFY"},
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1"
    )
    assert proposals[0].status == ProposalStatus.BLOCKED


def test_run_risk_checks_passes():
    cv = _cv("TCS")
    proposals = run_risk_checks(
        convictions=[cv], stock_by={}, held=set(),
        min_conv=0.6, block_earnings=False, earnings_days=5, run_id="r1"
    )
    assert proposals[0].status == ProposalStatus.PROPOSED


def test_size_proposals_approves():
    from agents.contracts import TradeProposal, ProposalStatus, EnrichedStock
    from agents.contracts import PortfolioBook

    stock = EnrichedStock(symbol="TCS", ltp=1000.0, sector="IT")
    stock_by = {"TCS": stock}
    book = PortfolioBook(cash=100000.0)

    p = TradeProposal(
        proposal_id="r1:TCS", run_id="r1", ticker="TCS",
        side="BUY", qty=0, rationale="bull", conviction=0.8,
        status=ProposalStatus.PROPOSED,
    )
    result = size_proposals(
        proposals=[p], stock_by=stock_by, book=book,
        capital=100000.0, max_open=5, max_pos_pct=0.10, max_sector_pct=0.30,
    )
    assert result[0].status == ProposalStatus.APPROVED
    assert result[0].qty > 0
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/test_risk_portfolio_pure.py -v
```

Expected: `ImportError` — `run_risk_checks` and `size_proposals` don't exist yet.

- [ ] **Step 3: Extract pure function from risk.py**

Add `run_risk_checks()` as a module-level function and have `risk_node` delegate to it:

```python
# agents/nodes/risk.py — add this function above risk_node

def run_risk_checks(
    convictions: list,
    stock_by: dict,
    held: set,
    min_conv: float,
    block_earnings: bool,
    earnings_days: int,
    run_id: str,
) -> list:
    """Pure risk gate: returns a list of TradeProposal (PROPOSED or BLOCKED)."""
    from agents.contracts import ProposalStatus, RiskCheck, TradeProposal

    proposals: list = []
    for cv in convictions:
        checks: list = []
        passed = True

        if cv.direction != "long":
            checks.append(RiskCheck(rule="direction", passed=False,
                                    detail=f"{cv.direction} not tradable (long-only)"))
            passed = False

        ok_conv = cv.conviction >= min_conv
        checks.append(RiskCheck(rule="min_conviction", passed=ok_conv,
                                detail=f"{cv.conviction:.2f} vs {min_conv:.2f}"))
        passed = passed and ok_conv

        stock = stock_by.get(cv.ticker)
        if block_earnings and stock is not None and stock.days_to_earnings is not None:
            near = stock.days_to_earnings <= earnings_days
            checks.append(RiskCheck(rule="earnings_block", passed=not near,
                                    detail=f"{stock.days_to_earnings}d to earnings"))
            passed = passed and not near

        if cv.ticker in held:
            checks.append(RiskCheck(rule="duplicate_position", passed=False, detail="already held"))
            passed = False

        proposals.append(TradeProposal(
            proposal_id=f"{run_id}:{cv.ticker}",
            run_id=run_id,
            ticker=cv.ticker,
            side="BUY",
            qty=0,
            rationale=cv.bull_case[:500],
            conviction=cv.conviction,
            status=ProposalStatus.PROPOSED if passed else ProposalStatus.BLOCKED,
            risk_checks=checks,
        ))
    return proposals
```

Replace the body of `risk_node` to delegate:

```python
@agent_node("risk", requires_trading=True)
def risk_node(state: AgentState) -> dict:
    convictions = state.get("convictions") or []
    if not convictions:
        logger.info("risk: no convictions — skipping")
        return {}

    enriched = state.get("enriched")
    stock_by = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    book = state.get("book") or load_portfolio()
    held = {p.ticker for p in book.positions}

    proposals = run_risk_checks(
        convictions=convictions,
        stock_by=stock_by,
        held=held,
        min_conv=float(getattr(SETTINGS, "MIN_CONVICTION_TO_TRADE", 0.6)),
        block_earnings=bool(getattr(SETTINGS, "BLOCK_NEAR_EARNINGS", True)),
        earnings_days=int(getattr(SETTINGS, "EARNINGS_PROXIMITY_DAYS", 5)),
        run_id=state.get("run_id", ""),
    )

    n_pass = sum(p.status == ProposalStatus.PROPOSED for p in proposals)
    logger.info("risk: %d/%d candidates passed", n_pass, len(proposals))
    return {"status": RunStatus.RUNNING, "proposals": proposals, "book": book}
```

- [ ] **Step 4: Extract pure function from portfolio.py**

Add `size_proposals()` as a module-level function and have `portfolio_node` delegate:

```python
# agents/nodes/portfolio.py — add above portfolio_node

def size_proposals(
    proposals: list,
    stock_by: dict,
    book,
    capital: float,
    max_open: int,
    max_pos_pct: float,
    max_sector_pct: float,
) -> list:
    """Pure portfolio sizer: mutates proposal status in-place, returns same list."""
    open_count = len(book.positions)
    sector_value = dict(book.sector_exposure)

    candidates = sorted(
        (p for p in proposals if p.status == ProposalStatus.PROPOSED),
        key=lambda p: p.conviction, reverse=True,
    )

    for p in candidates:
        if open_count >= max_open:
            _reject(p, "max_open", f"{open_count}/{max_open} positions")
            continue

        stock = stock_by.get(p.ticker)
        price = stock.ltp if stock and stock.ltp else None
        if not price or price <= 0:
            _reject(p, "price", "no live price")
            continue

        budget = capital * max_pos_pct * p.conviction
        qty = int(budget // price)
        if qty <= 0:
            _reject(p, "position_size", f"budget {budget:.0f} < 1 share @ {price:.2f}")
            continue

        value = qty * price
        sector = (stock.sector if stock else None) or "Unknown"
        if sector_value.get(sector, 0.0) + value > capital * max_sector_pct:
            _reject(p, "sector_cap", f"{sector} would exceed {max_sector_pct:.0%}")
            continue

        p.qty = qty
        p.limit_price = round(price, 2)
        p.status = ProposalStatus.APPROVED
        p.risk_checks.append(RiskCheck(rule="sized", passed=True, detail=f"{qty} @ {price:.2f}"))
        sector_value[sector] = sector_value.get(sector, 0.0) + value
        open_count += 1

    return proposals
```

Replace `portfolio_node` body to delegate:

```python
@agent_node("portfolio", requires_trading=True)
def portfolio_node(state: AgentState) -> dict:
    proposals = list(state.get("proposals") or [])
    if not proposals:
        logger.info("portfolio: no proposals — skipping")
        return {}

    enriched = state.get("enriched")
    stock_by = {s.symbol: s for s in (enriched.stocks if enriched else [])}
    book = state.get("book") or load_portfolio()

    proposals = size_proposals(
        proposals=proposals,
        stock_by=stock_by,
        book=book,
        capital=float(getattr(SETTINGS, "TRADING_CAPITAL_INR", 0.0)),
        max_open=int(getattr(SETTINGS, "MAX_OPEN_POSITIONS", 5)),
        max_pos_pct=float(getattr(SETTINGS, "MAX_POSITION_PCT", 0.10)),
        max_sector_pct=float(getattr(SETTINGS, "MAX_SECTOR_PCT", 0.30)),
    )

    approved = sum(p.status == ProposalStatus.APPROVED for p in proposals)
    logger.info("portfolio: %d approved, %d rejected",
                approved, sum(p.status == ProposalStatus.REJECTED for p in proposals))
    return {"status": RunStatus.RUNNING, "proposals": proposals, "book": book}
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/test_risk_portfolio_pure.py tests/test_risk_portfolio.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agents/nodes/risk.py agents/nodes/portfolio.py tests/test_risk_portfolio_pure.py
git commit -m "refactor(risk,portfolio): extract run_risk_checks() + size_proposals() pure functions"
```

---

## Task 5: chat/tools.py — propose_trade + intraday_watchlist

**Files:**
- Modify: `agents/chat/tools.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_trade_tools.py
import pytest
from unittest.mock import patch, MagicMock


def _make_tool_test_settings(trading_mode="paper"):
    from settings import Settings
    return Settings(TRADING_MODE=trading_mode, TRADING_CAPITAL_INR=100000.0,
                    MIN_CONVICTION_TO_TRADE=0.6, BLOCK_NEAR_EARNINGS=True,
                    EARNINGS_PROXIMITY_DAYS=5, MAX_OPEN_POSITIONS=5,
                    MAX_POSITION_PCT=0.10, MAX_SECTOR_PCT=0.30, STOP_LOSS_PCT=0.05)


def test_propose_trade_off_mode_returns_error(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _make_tool_test_settings("off"))
    monkeypatch.setattr(config, "trading_enabled", lambda: False)

    from agents.chat.tools import propose_trade
    result = propose_trade("INFY", "BUY", 10, "looks good")
    assert "error" in result


def test_propose_trade_paper_returns_confirmation(monkeypatch):
    import config
    monkeypatch.setattr(config, "SETTINGS", _make_tool_test_settings("paper"))
    monkeypatch.setattr(config, "trading_enabled", lambda: True)
    monkeypatch.setattr(config, "live_trading", lambda: False)

    from agents.contracts import ConvictionView, PortfolioBook, EnrichedStock
    from agents.nodes.risk import run_risk_checks
    from agents.nodes.portfolio import size_proposals

    with patch("agents.chat.tools.run_risk_checks") as mock_risk, \
         patch("agents.chat.tools.size_proposals") as mock_size, \
         patch("agents.chat.tools._fetch_live_price", return_value=1500.0), \
         patch("agents.chat.tools.load_portfolio", return_value=PortfolioBook(cash=100000.0)), \
         patch("agents.chat.tools.save_portfolio"):
        from agents.contracts import TradeProposal, ProposalStatus
        proposal = TradeProposal(
            proposal_id="test:INFY", run_id="test", ticker="INFY",
            side="BUY", qty=5, limit_price=1500.0, rationale="bull",
            conviction=0.8, status=ProposalStatus.APPROVED,
        )
        mock_risk.return_value = [proposal]
        mock_size.return_value = [proposal]

        from agents.chat.tools import propose_trade
        result = propose_trade("INFY", "BUY", 5, "looks good")
    assert result.get("status") == "filled"
    assert result.get("ticker") == "INFY"


def test_intraday_watchlist_returns_items(monkeypatch):
    with patch("agents.chat.tools.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = [
            {"symbol": "TCS", "score": 8, "conviction": "HIGH",
             "signals": {}, "company": "TCS Ltd", "sector": "IT", "close": 3500.0}
        ]
        from agents.chat.tools import intraday_watchlist
        result = intraday_watchlist()
    assert result.get("count") == 1
    assert result["items"][0]["symbol"] == "TCS"
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/test_chat_trade_tools.py -v
```

Expected: `ImportError` — `propose_trade` and `intraday_watchlist` not defined yet.

- [ ] **Step 3: Add helper + propose_trade to tools.py**

Add these imports near the top of `agents/chat/tools.py` (after existing imports):
```python
from agents.nodes.risk import run_risk_checks
from agents.nodes.portfolio import size_proposals
from config import SETTINGS, trading_enabled, live_trading
from intraday.pipeline import run_pipeline
```

Add `_fetch_live_price` helper (used by propose_trade, also monkeypatchable in tests):
```python
def _fetch_live_price(ticker: str) -> float | None:
    """Fetch a single live price via the market-data provider chain."""
    try:
        from enrichment.market_data import get_default_provider
        provider = get_default_provider()
        quote = provider.get_quote(ticker)
        return float(quote.get("ltp") or quote.get("last_price") or 0) or None
    except Exception:
        return None
```

Add `propose_trade` tool:
```python
def propose_trade(ticker: str, action: str, qty: int, rationale: str) -> dict:
    """Propose a trade from chat. Runs risk + sizing; paper fills immediately, live awaits HITL."""
    if not trading_enabled():
        return {"error": "Trading is disabled (TRADING_MODE=off). Set TRADING_MODE=paper or live."}
    if action.upper() != "BUY":
        return {"error": f"Only BUY supported; got {action!r}"}
    if qty <= 0:
        return {"error": f"qty must be > 0; got {qty}"}

    from agents.contracts import ConvictionView, EnrichedStock, PortfolioBook
    from persistence.store import load_portfolio, save_portfolio

    price = _fetch_live_price(ticker)
    if not price:
        return {"error": f"Could not fetch live price for {ticker}"}

    stock = EnrichedStock(symbol=ticker, ltp=price, sector=None)
    stock_by = {ticker: stock}

    book: PortfolioBook = load_portfolio()
    held = {p.ticker for p in book.positions}

    cv = ConvictionView(
        ticker=ticker, direction="long",
        conviction=max(SETTINGS.MIN_CONVICTION_TO_TRADE, 0.75),
        bull_case=rationale, bear_case="", rationale=rationale,
    )

    proposals = run_risk_checks(
        convictions=[cv], stock_by=stock_by, held=held,
        min_conv=SETTINGS.MIN_CONVICTION_TO_TRADE,
        block_earnings=SETTINGS.BLOCK_NEAR_EARNINGS,
        earnings_days=SETTINGS.EARNINGS_PROXIMITY_DAYS,
        run_id="chat",
    )
    if proposals[0].status.value == "BLOCKED":
        failing = [c.detail for c in proposals[0].risk_checks if not c.passed]
        return {"error": f"Risk check failed: {'; '.join(failing)}"}

    proposals = size_proposals(
        proposals=proposals, stock_by=stock_by, book=book,
        capital=SETTINGS.TRADING_CAPITAL_INR,
        max_open=SETTINGS.MAX_OPEN_POSITIONS,
        max_pos_pct=SETTINGS.MAX_POSITION_PCT,
        max_sector_pct=SETTINGS.MAX_SECTOR_PCT,
    )
    p = proposals[0]
    if p.status.value != "APPROVED":
        failing = [c.detail for c in p.risk_checks if not c.passed]
        return {"error": f"Portfolio sizing rejected: {'; '.join(failing)}"}

    effective_qty = qty if qty > 0 else p.qty

    if not live_trading():
        # Paper: simulate fill
        from agents.contracts import Position, ProposalStatus
        from persistence.store import recompute
        stop = round(p.limit_price * (1 - SETTINGS.STOP_LOSS_PCT), 2)
        book.positions.append(Position(
            ticker=ticker, qty=effective_qty, avg_price=p.limit_price,
            stop_price=stop, sector=stock.sector,
        ))
        book.cash = round(book.cash - effective_qty * p.limit_price, 2)
        book = recompute(book)
        save_portfolio(book)
        return {
            "status": "filled",
            "ticker": ticker,
            "qty": effective_qty,
            "price": p.limit_price,
            "stop": stop,
            "cash_remaining": book.cash,
            "mode": "paper",
        }
    else:
        # Live: persist and request HITL via Telegram
        from agents.contracts import ProposalStatus
        from persistence.store import save_proposals
        from datetime import datetime, timedelta, timezone
        from agents.approval import send_approval_request

        p.qty = effective_qty
        p.status = ProposalStatus.AWAITING_APPROVAL
        p.expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SETTINGS.APPROVAL_TIMEOUT_SEC)).isoformat()
        save_proposals([p])
        send_approval_request({
            "type": "trade_approval",
            "run_id": "chat",
            "proposals": [{"proposal_id": p.proposal_id, "ticker": p.ticker,
                           "side": p.side, "qty": p.qty, "limit_price": p.limit_price,
                           "conviction": p.conviction}],
            "instructions": "/approve or /reject",
        })
        return {
            "status": "pending_approval",
            "ticker": ticker,
            "qty": effective_qty,
            "price": p.limit_price,
            "proposal_id": p.proposal_id,
            "mode": "live",
        }
```

Add `intraday_watchlist` tool:
```python
def intraday_watchlist(as_of_date: str | None = None) -> dict:
    """Run the intraday signal scorer and return the ranked next-day watchlist."""
    try:
        from datetime import date
        ref = date.fromisoformat(as_of_date) if as_of_date else None
        items = run_pipeline(report_date=ref)
        return {
            "count": len(items),
            "date": (ref or date.today()).isoformat(),
            "items": [
                {
                    "symbol": w["symbol"],
                    "score": w["score"],
                    "conviction": w.get("conviction", ""),
                    "company": w.get("company", ""),
                    "sector": w.get("sector", ""),
                    "close": w.get("close"),
                }
                for w in items
            ],
        }
    except Exception as e:
        return {"error": str(e)}
```

Also register both tools in `_build_tools()` (or however tools.py exposes its tool list to the agent). Look for the list of tools returned to `create_react_agent` and append:
```python
propose_trade,
intraday_watchlist,
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_chat_trade_tools.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agents/chat/tools.py tests/test_chat_trade_tools.py
git commit -m "feat(chat): add propose_trade + intraday_watchlist tools"
```

---

## Task 6: run_agents.py — watch + intraday modes; retire run_intraday.py

**Files:**
- Modify: `run_agents.py`
- Modify: `run_intraday.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_modes.py
import sys
from unittest.mock import patch, MagicMock


def test_watch_mode_exits_outside_market_hours(monkeypatch):
    """_watch() should exit cleanly (no alerts) outside 09:15-15:30 IST."""
    # Force a time that's outside market hours (e.g. 3am IST = 21:30 UTC prior day)
    import datetime
    fake_now = datetime.datetime(2026, 6, 29, 21, 30, tzinfo=datetime.timezone.utc)

    with patch("run_agents._market_open_ist", return_value=False), \
         patch("run_agents._pre_open_ist", return_value=False):
        # Import and call _watch; it should return without errors
        import run_agents
        # _watch needs a run_id; it should just exit cleanly
        run_agents._watch("test-run", MagicMock())
        # No assertion needed — just confirm no exception raised


def test_intraday_mode_calls_pipeline(monkeypatch):
    with patch("intraday.pipeline.run_pipeline", return_value=[]) as mock_pipe, \
         patch("intraday.report.send_watchlist_telegram"), \
         patch("intraday.report.write_watchlist"):
        import run_agents
        run_agents._intraday("test-run", MagicMock(), MagicMock())
        mock_pipe.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/test_run_modes.py -v
```

Expected: `AttributeError: module 'run_agents' has no attribute '_watch'`

- [ ] **Step 3: Update run_agents.py**

Replace the `--mode` choices and add `watch`/`intraday` modes. Key changes:

Update `parse_args()`:
```python
p.add_argument("--mode", choices=["research", "paper", "live", "watch", "intraday"], default=None,
               help="Run mode (overrides TRADING_MODE for entrypoint selection)")
```

Add routing in `main()` after `if args.kill / args.resume` blocks — replace the old `AGENT_MODE == "monitor"` and `AGENT_MODE == "pulse"` branches:
```python
    if args.mode == "watch":
        _watch(run_id, report_date)
        return

    if args.mode == "intraday":
        _intraday(run_id, report_date, args)
        return
```

Remove the old:
```python
    if SETTINGS.AGENT_MODE == "monitor":
        _monitor(run_id, report_date, RunStatus)
        return

    if SETTINGS.AGENT_MODE == "pulse":
        _pulse(run_id, report_date, RunStatus)
        return
```

Add the `_watch()` function (replaces `_monitor` + `_pulse`):
```python
import datetime as _dt
import pytz as _pytz

_IST = _pytz.timezone("Asia/Kolkata")


def _market_open_ist() -> bool:
    now = _dt.datetime.now(_IST).time()
    return _dt.time(9, 15) <= now <= _dt.time(15, 30)


def _pre_open_ist() -> bool:
    now = _dt.datetime.now(_IST).time()
    return now < _dt.time(9, 15)


def _watch(run_id: str, report_date) -> None:
    """Merged monitor + pulse watcher. Calls node logic directly — no LangGraph graph."""
    from config import SETTINGS
    from agents.state import RunStatus

    logger.info("=== Watch run %s ===", run_id)
    state = {
        "run_id": run_id,
        "report_date": str(report_date),
        "mode": "watch",
        "status": RunStatus.RUNNING,
        "cost_usd": 0.0,
        "tokens": 0,
    }

    if not _market_open_ist() and not _pre_open_ist():
        logger.info("watch: outside market hours — no-op")
        return

    if _market_open_ist():
        # Stop-loss / position monitoring
        from agents.nodes.monitoring import monitor_node
        result = monitor_node(state)
        state.update(result)
        alerts = state.get("alerts") or []
        logger.info("watch(monitor): %d alert(s)", len(alerts))

    # Pulse runs pre-open (global tickers) and market hours (all triggers)
    from agents.nodes.pulse import pulse_node
    result = pulse_node(state)
    state.update(result)
    alerts = state.get("alerts") or []
    logger.info("watch(pulse): %d alert(s)", len(alerts))

    from observability.metrics import push_metrics
    push_metrics(job="stock-intelligence-watch")
```

Add the `_intraday()` function:
```python
def _intraday(run_id: str, report_date, args) -> None:
    """Evening intraday scorer — next-day watchlist."""
    from config import SETTINGS
    from intraday.pipeline import run_pipeline
    from intraday.report import write_watchlist, send_watchlist_telegram

    logger.info("=== Intraday run %s ===", run_id)
    items = run_pipeline(report_date=report_date, dry_run=getattr(args, "dry_run", False))
    write_watchlist(items, report_date)
    if not getattr(args, "no_telegram", False):
        send_watchlist_telegram(items, report_date)
    logger.info("=== Intraday done: %d items ===", len(items))
```

Remove the old `_monitor()` and `_pulse()` functions entirely.

- [ ] **Step 4: Retire run_intraday.py**

Replace the entire file with a deprecation stub:
```python
"""run_intraday.py — RETIRED. Use: python run_agents.py intraday"""
import subprocess
import sys
import warnings

warnings.warn(
    "run_intraday.py is retired. Use: python run_agents.py intraday",
    DeprecationWarning,
    stacklevel=1,
)
# Forward to the new entrypoint, passing all args
sys.exit(subprocess.call([sys.executable, "run_agents.py", "intraday"] + sys.argv[1:]))
```

- [ ] **Step 5: Run tests**

```
python -m pytest tests/test_run_modes.py -v
```

Expected: all pass.

Run smoke check:
```
cd /home/shankar/Work/AI_Projects/AI-Agents/stock_research/.claude/worktrees/query-filter-extraction
python run_agents.py --help
```

Expected: shows `watch` and `intraday` in choices; no `monitor`/`pulse`.

- [ ] **Step 6: Commit**

```bash
git add run_agents.py run_intraday.py tests/test_run_modes.py
git commit -m "feat(runner): add watch+intraday modes; retire run_intraday.py; remove monitor/pulse modes"
```

---

## Task 7: persistence/store.py — pulse_state Postgres table

**Why:** `pulse_state.json` resets on every Cloud Run cold start. Postgres survives restarts and is shared across processes (chat agent + watcher).

**Files:**
- Modify: `persistence/store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pulse_state_postgres.py
import json
import pytest
from unittest.mock import patch, MagicMock


def test_save_load_pulse_state_postgres(monkeypatch, tmp_path):
    """When DATABASE_URL is set, load/save use the DB; JSON file is not touched."""
    import config
    from settings import Settings
    # Ensure DATABASE_URL is "mock://" to trigger DB path without real DB
    monkeypatch.setattr(config, "SETTINGS", Settings(DATABASE_URL="mock://", PULSE_STATE_FILE=str(tmp_path / "pulse.json")))

    fake_state = {"last_nifty_alert": "2026-06-29T10:00:00", "armed": True}

    with patch("persistence.store._db_save_pulse_state") as mock_db_save, \
         patch("persistence.store._db_load_pulse_state", return_value=fake_state) as mock_db_load:
        from persistence.store import save_pulse_state, load_pulse_state
        save_pulse_state(fake_state)
        mock_db_save.assert_called_once_with(fake_state)

        result = load_pulse_state()
        mock_db_load.assert_called_once()
    assert result == fake_state


def test_save_load_pulse_state_json_fallback(monkeypatch, tmp_path):
    """Without DATABASE_URL, fall back to JSON file."""
    import config
    from settings import Settings
    state_file = tmp_path / "pulse.json"
    monkeypatch.setattr(config, "SETTINGS", Settings(DATABASE_URL="", PULSE_STATE_FILE=str(state_file)))

    fake_state = {"last_vix_alert": "2026-06-29T11:00:00"}

    from persistence.store import save_pulse_state, load_pulse_state
    save_pulse_state(fake_state)
    assert state_file.exists()
    result = load_pulse_state()
    assert result == fake_state
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/test_pulse_state_postgres.py -v
```

Expected: ImportError or AttributeError (`_db_save_pulse_state` doesn't exist).

- [ ] **Step 3: Update persistence/store.py**

Find `load_pulse_state()` and `save_pulse_state()` (around lines 209-230) and replace with:

```python
def _db_load_pulse_state() -> dict:
    """Load pulse state from Postgres `pulse_state` table (upsert-single-row pattern)."""
    from persistence.db import get_sync_connection
    with get_sync_connection() as conn:
        row = conn.execute(
            "SELECT state_json FROM pulse_state WHERE id = 1"
        ).fetchone()
        if row:
            return json.loads(row[0])
        return {}


def _db_save_pulse_state(state: dict) -> None:
    """Upsert pulse state into Postgres `pulse_state` table."""
    from persistence.db import get_sync_connection
    with get_sync_connection() as conn:
        conn.execute(
            "INSERT INTO pulse_state (id, state_json, updated_at) VALUES (1, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT (id) DO UPDATE SET state_json = excluded.state_json, updated_at = CURRENT_TIMESTAMP",
            (json.dumps(state),)
        )
        conn.commit()


def _pulse_state_path() -> Path:
    return Path(getattr(SETTINGS, "PULSE_STATE_FILE", "output/pulse_state.json"))


def load_pulse_state() -> dict:
    if SETTINGS.DATABASE_URL:
        try:
            return _db_load_pulse_state()
        except Exception as e:
            logger.warning("pulse_state DB load failed (%s) — falling back to JSON", e)
    p = _pulse_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_pulse_state(state: dict) -> None:
    if SETTINGS.DATABASE_URL:
        try:
            _db_save_pulse_state(state)
            return
        except Exception as e:
            logger.warning("pulse_state DB save failed (%s) — falling back to JSON", e)
    p = _pulse_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))
```

Add the `pulse_state` table migration to `persistence/db.py` (look for the `_CREATE_TABLES` SQL or equivalent):
```sql
CREATE TABLE IF NOT EXISTS pulse_state (
    id INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Add this statement to the list of table creation SQL in `init_db()`.

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_pulse_state_postgres.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add persistence/store.py persistence/db.py tests/test_pulse_state_postgres.py
git commit -m "feat(store): pulse_state load/save gains Postgres path with JSON fallback"
```

---

## Task 8: Deploy scripts — VM setup

**Files (all new):**
- Create: `deploy/vm/setup.sh`
- Create: `deploy/vm/nginx.conf`
- Create: `deploy/vm/stock-chat.service`
- Create: `deploy/vm/crontab`

No tests for deploy scripts — verify by inspection.

- [ ] **Step 1: Create deploy/vm/setup.sh**

```bash
#!/usr/bin/env bash
# One-shot VM provisioning. Run as root on a fresh Debian/Ubuntu VM.
# Usage: sudo bash setup.sh <your-domain-or-ip>
set -euo pipefail

DOMAIN=${1:-""}
APP_DIR="/opt/stock-research"
APP_USER="stock"
VENV="$APP_DIR/venv"

# System deps
apt-get update -q
apt-get install -y -q python3.12 python3.12-venv git nginx certbot python3-certbot-nginx \
    gcc libxml2-dev libxslt-dev

# App user
useradd -r -m -s /bin/bash "$APP_USER" || true

# Clone / update repo
if [ -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
    git clone https://github.com/skarin7/stock-research "$APP_DIR"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# Virtualenv
sudo -u "$APP_USER" python3.12 -m venv "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# systemd service
cp "$APP_DIR/deploy/vm/stock-chat.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stock-chat
systemctl restart stock-chat

# nginx
cp "$APP_DIR/deploy/vm/nginx.conf" /etc/nginx/sites-available/stock-research
ln -sf /etc/nginx/sites-available/stock-research /etc/nginx/sites-enabled/stock-research
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# TLS (skip if no domain)
if [ -n "$DOMAIN" ]; then
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN"
fi

# Crontab
crontab -u "$APP_USER" "$APP_DIR/deploy/vm/crontab"

echo "Setup complete. Copy .env to $APP_DIR/.env and restart the service."
```

- [ ] **Step 2: Create deploy/vm/nginx.conf**

```nginx
server {
    listen 80;
    server_name _;

    location /telegram/webhook {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }

    location / {
        return 404;
    }
}
```

- [ ] **Step 3: Create deploy/vm/stock-chat.service**

```ini
[Unit]
Description=Stock Research Chat Agent (Telegram webhook)
After=network.target

[Service]
Type=simple
User=stock
WorkingDirectory=/opt/stock-research
EnvironmentFile=/opt/stock-research/.env
ExecStart=/opt/stock-research/venv/bin/uvicorn server.app:app \
    --host 127.0.0.1 --port 8080 --workers 1
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Create deploy/vm/crontab**

```cron
# Stock Research — production schedule (TZ=Asia/Kolkata)
SHELL=/bin/bash
PATH=/opt/stock-research/venv/bin:/usr/bin:/bin
PYTHONPATH=/opt/stock-research

# Daily research run (06:30 IST, Mon-Fri)
30 6 * * 1-5  cd /opt/stock-research && python run_agents.py research >> /var/log/stock-research.log 2>&1

# Evening intraday watchlist (18:30 IST, Mon-Fri)
30 18 * * 1-5  cd /opt/stock-research && python run_agents.py intraday >> /var/log/stock-intraday.log 2>&1

# Merged watcher — every 3 min (self-skips outside 09:15-15:30 IST)
*/3 * * * 1-5  cd /opt/stock-research && python run_agents.py watch >> /var/log/stock-watch.log 2>&1
```

- [ ] **Step 5: Commit**

```bash
mkdir -p deploy/vm
git add deploy/vm/setup.sh deploy/vm/nginx.conf deploy/vm/stock-chat.service deploy/vm/crontab
git commit -m "feat(deploy): add VM setup scripts — nginx, systemd, crontab"
```

---

## Task 9: Tests — Replace profile tests, update all ENABLE_* fixtures

**Files:**
- Modify: `tests/test_profile_config.py`
- Modify: `tests/test_risk_portfolio.py`
- Modify: `tests/test_trading_live.py`
- Modify: `tests/test_debate.py`

- [ ] **Step 1: Rewrite test_profile_config.py**

Replace the entire file:

```python
"""Tests for TRADING_MODE validation in settings.py."""
import pytest
from settings import Settings


def test_default_trading_mode_is_off():
    s = Settings()
    assert s.TRADING_MODE == "off"


def test_paper_mode():
    s = Settings(TRADING_MODE="paper")
    assert s.TRADING_MODE == "paper"


def test_live_mode():
    s = Settings(TRADING_MODE="live")
    assert s.TRADING_MODE == "live"


def test_invalid_mode_raises_at_from_env(monkeypatch):
    import os
    monkeypatch.setenv("TRADING_MODE", "auto")
    with pytest.raises(ValueError, match="TRADING_MODE"):
        Settings.from_env()


def test_trading_enabled_helper():
    import config
    from unittest.mock import patch
    with patch.object(config, "SETTINGS", Settings(TRADING_MODE="paper")):
        from importlib import reload
        # trading_enabled reads from module-level SETTINGS
        assert config.trading_enabled() is True

    with patch.object(config, "SETTINGS", Settings(TRADING_MODE="off")):
        assert config.trading_enabled() is False


def test_live_trading_helper():
    import config
    from unittest.mock import patch
    with patch.object(config, "SETTINGS", Settings(TRADING_MODE="live")):
        assert config.live_trading() is True

    with patch.object(config, "SETTINGS", Settings(TRADING_MODE="paper")):
        assert config.live_trading() is False


def test_no_enable_flags():
    s = Settings()
    for flag in ["ENABLE_RESEARCH_AGENT", "AGENT_PROFILE", "ENABLE_LIVE_TRADING",
                 "ENABLE_DEBATE_AGENT", "ENABLE_TRADING_AGENT"]:
        assert not hasattr(s, flag), f"Settings should not have {flag}"
```

- [ ] **Step 2: Update test_risk_portfolio.py**

Find the config fixture (around lines 13-15):
```python
# Before:
ENABLE_RISK_AGENT=True,
ENABLE_PORTFOLIO_AGENT=True,
ENABLE_TRADING_AGENT=True,

# After — replace all three lines with:
TRADING_MODE="paper",
```

- [ ] **Step 3: Update test_trading_live.py**

Replace all `ENABLE_LIVE_TRADING` references:

```python
# Replace at fixture setup (line 16-17):
# Before:
ENABLE_TRADING_AGENT=True,
ENABLE_LIVE_TRADING=False,
# After:
TRADING_MODE="paper",

# Replace each inline mutation:
# Before:
_cfg.ENABLE_LIVE_TRADING = False
# After:
object.__setattr__(_cfg, "TRADING_MODE", "paper")

# Before:
_cfg.ENABLE_LIVE_TRADING = True
# After:
object.__setattr__(_cfg, "TRADING_MODE", "live")
```

(Settings is a frozen dataclass, so use `object.__setattr__` for mutations in tests, or reconstruct with `replace(_cfg, TRADING_MODE="live")`.)

Better approach for all `_cfg.ENABLE_LIVE_TRADING = X` lines — use `replace`:
```python
from dataclasses import replace as _replace
# Each test that mutates:
_cfg = _replace(_cfg, TRADING_MODE="live")   # was: _cfg.ENABLE_LIVE_TRADING = True
_cfg = _replace(_cfg, TRADING_MODE="paper")  # was: _cfg.ENABLE_LIVE_TRADING = False
```

Also update `groww_trader.py` assertions — the error message changed from `"ENABLE_LIVE_TRADING is false"` to `"TRADING_MODE != live"`:
```python
# Before:
assert "ENABLE_LIVE_TRADING" in str(exc.value)
# After:
assert "TRADING_MODE" in str(exc.value)
```

- [ ] **Step 4: Update test_debate.py**

Find the config fixture (line 13):
```python
# Before:
ENABLE_DEBATE_AGENT=True,
# After:
TRADING_MODE="paper",
```

- [ ] **Step 5: Run all tests**

```
python -m pytest tests/ -v
```

Expected: all pass. Note: the old `test_profile_config.py` is replaced, so no failures from it.

- [ ] **Step 6: Commit**

```bash
git add tests/test_profile_config.py tests/test_risk_portfolio.py \
        tests/test_trading_live.py tests/test_debate.py
git commit -m "test: update all tests for TRADING_MODE — remove ENABLE_* fixtures"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| `TRADING_MODE=off\|paper\|live` replaces 11 flags | Task 1 |
| `_apply_profile()` + `_PROFILE_FLAGS` deleted | Task 1 |
| `trading_enabled()` / `live_trading()` helpers | Task 1 |
| `agent_node` decorator — no more `enabled_flag` | Task 2 |
| Debate/risk/portfolio/trading gates → `requires_trading=True` | Task 3 |
| `ENABLE_AUTO_EXIT` → allowlist check | Task 3 |
| `ENABLE_LIVE_TRADING` → `live_trading()` throughout | Tasks 3 + 9 |
| `run_risk_checks()` pure function | Task 4 |
| `size_proposals()` pure function | Task 4 |
| `propose_trade` chat tool | Task 5 |
| `intraday_watchlist` chat tool | Task 5 |
| `run_agents.py watch` mode (merged monitor + pulse) | Task 6 |
| `run_agents.py intraday` mode | Task 6 |
| `run_intraday.py` retired | Task 6 |
| `pulse_state` → Postgres with JSON fallback | Task 7 |
| VM deploy scripts (nginx, systemd, crontab) | Task 8 |
| Tests updated for all new flags | Tasks 1-6 + 9 |

All spec requirements are covered.

### What is NOT in scope (confirmed excluded)
- Multi-user support
- Web UI
- Backtesting from chat
- Chat-initiated stop-loss modification
- Terraform changes (Cloud Run → VM is a deploy decision, not code)
