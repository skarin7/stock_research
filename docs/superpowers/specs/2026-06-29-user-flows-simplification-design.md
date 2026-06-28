# User Flows & System Simplification Design

**Date:** 2026-06-29  
**Status:** Approved  
**Scope:** Deployment model, config simplification, unified user flows, merged watcher, chat trading

---

## Problem Statement

The system has accumulated too many independent flags and entrypoints that obscure how to operate it as a personal trading tool:

- 11+ `ENABLE_*` flags controlled by `AGENT_PROFILE` — but pulse and chat were excluded
- 4 separate Cloud Run Jobs + 1 Cloud Run Service + 4 Cloud Scheduler triggers
- `pulse_state.json` reset on every Cloud Run cold start (latent debounce bug)
- Chat agent has no trading capability despite Telegram being the primary interface
- `run_intraday.py` is a separate entrypoint with no connection to chat
- Monitor and pulse ran as separate jobs despite complementary purposes

---

## Decisions

| Question | Decision |
|---|---|
| Who is the user? | Solo personal trader |
| Primary interface | Telegram only |
| Deployment | Single VM (Hetzner CAX11 or GCP e2-small) |
| Config simplification | `TRADING_MODE=off\|paper\|live` replaces 11 flags |
| Chat trading | Option C — passive daily proposals + chat-initiated trades |
| Monitor + Pulse | Merged into single `watch` mode |
| Intraday | Chat tool (on-demand) + scheduled cron |
| Cloud Run Jobs | Replaced by cron on persistent VM |

---

## Architecture

### VM Layout

```
Single VM (~€4-10/month)
│
├── nginx (port 443, TLS via certbot)
│     └── → uvicorn 127.0.0.1:8080   ← Telegram webhook
│
├── systemd service: stock-chat
│     └── server/app.py (FastAPI, always-on)
│
└── cron jobs (TZ=Asia/Kolkata in crontab):
      30 6  * * 1-5  →  python run_agents.py research
      30 18 * * 1-5  →  python run_agents.py intraday
      */3   * * * 1-5  →  python run_agents.py watch  (self-skips outside 09:15-15:30)
```

### What is removed

- Cloud Run Jobs: research, monitor, pulse (→ cron)
- Cloud Run Service for schedulers (→ VM cron)
- Cloud Scheduler (4 triggers → cron)
- `run_intraday.py` as standalone entrypoint (→ `run_agents.py intraday` + chat tool)
- Separate `build_monitor_graph` and `build_pulse_graph` graph entrypoints for scheduled use

### What stays

- Neon Postgres (cross-process state, HITL resume, pulse_state)
- `server/app.py` webhook (unchanged, runs as systemd service)
- `agents/nodes/monitoring.py` and `agents/nodes/pulse.py` logic (called directly, not via graph)
- Cloud Run Service option for chat webhook if preferred (nginx on VM is equally valid)

---

## Config Simplification

### Single knob replaces 11 flags

```bash
# .env
TRADING_MODE=paper        # off | paper | live
```

| Mode | Research | Chat Q&A | Watcher | Trade proposals | Fills | Real broker |
|---|---|---|---|---|---|---|
| `off` | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ |
| `paper` | ✓ | ✓ | ✓ | ✓ | Simulated | ✗ |
| `live` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ + HITL |

### Flags removed from settings.py

```
AGENT_PROFILE
AGENT_MODE
ENABLE_RESEARCH_AGENT
ENABLE_ANALYST_AGENT
ENABLE_DEBATE_AGENT
ENABLE_RISK_AGENT
ENABLE_PORTFOLIO_AGENT
ENABLE_TRADING_AGENT
ENABLE_MONITORING_AGENT
ENABLE_MEMORY_AGENT
ENABLE_PULSE_AGENT
ENABLE_CHAT_AGENT
ENABLE_AUTO_EXIT
ENABLE_LIVE_TRADING
GROWW_TRADING_ENABLED
```

`_PROFILE_FLAGS` dict and `_apply_profile()` in `settings.py` are deleted.

### Flags that stay

```bash
KILL_SWITCH=false                # emergency stop — kept, it is a runtime control
AUTO_TRADE_ALLOWLIST=            # opt-in per-symbol auto-exit; empty = never auto-exit
DATABASE_URL=                    # required for live HITL cross-process resume
```

### Internal expansion

Agents that previously read `ENABLE_*` flags now read `TRADING_MODE` directly via a single helper:

```python
def trading_enabled() -> bool:
    return SETTINGS.TRADING_MODE in ("paper", "live")

def live_trading() -> bool:
    return SETTINGS.TRADING_MODE == "live"
```

Invalid `TRADING_MODE` raises `ValueError` at startup (fail fast, same as current profile validation).

---

## User Flows

All interaction is Telegram. No CLI required post-deploy.

### Automated (no user action)

```
06:30 IST  research cron  →  score stocks → rank → Telegram report card
18:30 IST  intraday cron  →  S1-S10/N1-N7 scorer → Telegram "watch tomorrow" list
*/3 min    watch cron     →  merged watcher → Telegram alerts on threshold breach
```

### Chat-initiated

| User message | Agent action |
|---|---|
| "what's the outlook on HDFC?" | screen_snapshot → live_quote → score_subset → answer |
| "what should I watch tomorrow?" | intraday pipeline inline → ranked watchlist |
| "deep dive TCS" | deep_dive (bull↔bear debate) → conviction + rationale |
| "buy 100 RELIANCE" | risk check → size → paper: fill + confirm / live: HITL buttons |
| "any macro risks?" | macro_search + last watcher state → answer |
| "what's my portfolio?" | get_portfolio → positions, P&L, stop levels |
| "/kill" | engage kill switch → "Kill switch ON. All trading halted." |

### Trade approval flow (live mode)

```
Daily research identifies top candidates
→ Suspends (LangGraph interrupt)
→ Telegram: "Propose BUY 50 INFY @₹1820, conviction 0.78" [✓ Approve] [✗ Reject]
→ User taps button
→ Run resumes → Groww order placed → "Order ORD-XXXX confirmed"
```

Chat-initiated trade in live mode follows the same HITL path.

**Invariant:** Chat never executes a real order without HITL approval. Paper mode fills immediately and confirms.

---

## Merged Watcher

### New entrypoint

```bash
python run_agents.py watch
```

Replaces both `--mode monitor` and `--mode pulse`.

### Logic

```
pre-open  (before 09:15 IST):
  └── global tickers only (overnight Nikkei / crude / USD-INR)

market hours (09:15–15:30 IST):
  ├── NIFTY intraday drop ≥ PULSE_INDEX_DROP_PCT       → alert
  ├── India VIX spike ≥ PULSE_VIX_SPIKE_PCT            → alert
  ├── global ticker threshold breach                   → alert + sector impact map
  ├── per-holding intraday drop ≥ PULSE_HOLDING_DROP_PCT → alert
  ├── stop-loss breach on held position                → alert + auto-exit if live + allowlist
  └── news shock classification (Haiku, optional)      → alert

after 15:30 IST:
  └── skip (no-op, cron still fires but exits immediately)
```

### LLM usage

~95% deterministic. Only the news classifier (`PULSE_NEWS_ENABLED=true`) calls LLM (Haiku, ~$0.001/check). Set `PULSE_NEWS_ENABLED=false` for zero LLM cost in the watcher.

### State

`pulse_state.json` → `pulse_state` Postgres table (via `store.load/save_pulse_state`). Debounce survives VM restarts and is shared with the chat agent (so chat can answer "any macro risks?" from the same state).

### Implementation

Watcher calls `monitoring.py` and `pulse.py` node logic directly as functions — not via LangGraph graph. No graph overhead for a 3-minute cron that runs deterministic checks.

---

## New Chat Tool: `propose_trade`

Added to `agents/chat/tools.py`:

```python
def propose_trade(ticker: str, action: str, qty: int, rationale: str) -> dict:
    """
    Propose a trade from the chat agent.
    - Runs risk check (same logic as risk node, deterministic)
    - Runs portfolio sizing
    - paper:  simulate fill, update book, return confirmation
    - live:   persist TradeProposal, send Telegram HITL buttons, return "pending approval"
    - off:    return error "trading disabled"
    """
```

**Constraint added to chat system prompt:** Only call `propose_trade` after `deep_dive` confirms conviction ≥ MIN_CONVICTION_TO_TRADE, or after explicit user instruction with ticker + qty. Never infer qty from context — ask if not provided.

No new LangGraph graph for chat-initiated trades. Direct call through risk/portfolio logic in `agents/nodes/risk.py` and `agents/nodes/portfolio.py`.

**Prerequisite refactor:** The core risk-check and portfolio-sizing logic in those node files must be extracted as pure functions (currently entangled with LangGraph state dict access). The graph nodes become thin wrappers calling those functions. The `propose_trade` chat tool calls the same functions directly.

---

## Intraday as Chat Tool

`run_intraday.py` pipeline logic extracted into a callable function `run_intraday_pipeline(date)`.

Chat tool:

```python
def intraday_watchlist(as_of_date: str | None = None) -> dict:
    """Run the intraday signal scorer and return ranked watchlist."""
```

Scheduled cron at 18:30 calls the same function. Single implementation, two callers.

---

## Deployment

### VM setup

```bash
# 1. Provision VM (Hetzner CAX11 or GCP e2-small)
# 2. Clone repo, pip install -r requirements.txt
# 3. Copy .env (TRADING_MODE=paper, Telegram keys, Groww keys, DATABASE_URL)
# 4. certbot for TLS, register domain or use IP
# 5. nginx config → proxy_pass 127.0.0.1:8080
# 6. systemd service for uvicorn (server/app.py)
# 7. crontab entries (see Architecture section)
# 8. python scripts/set_webhook.py https://<vm-ip-or-domain>/telegram/webhook
```

### .env surface after simplification

```bash
# Required
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_WEBHOOK_SECRET=
TRADING_MODE=paper          # off | paper | live
DATABASE_URL=               # postgres — REQUIRED for TRADING_MODE=live (HITL cross-process resume)
                            # optional for off/paper (falls back to MemorySaver + local files)

# Optional but recommended
OPENROUTER_API_KEY=         # cheaper models for scoring

# Optional
GROWW_ACCESS_TOKEN=         # required only for TRADING_MODE=live
GEMINI_API_KEY=             # macro summaries
TAVILY_API_KEY=             # macro_search web grounding
KILL_SWITCH=false
AUTO_TRADE_ALLOWLIST=       # symbols eligible for auto-exit
```

---

## What is NOT in scope

- Multi-user / multi-account support
- Web UI (Telegram is the UI)
- Backtesting from chat
- Options / derivatives trading
- Chat-initiated stop-loss modification (read-only portfolio via `get_portfolio`)
