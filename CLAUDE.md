# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The project lives at the **repo root** — an NSE/BSE daily stock-scoring pipeline (there is no `stock-intelligence/` subdirectory; do not `cd` into one).

```
.
  main.py               # legacy pipeline orchestrator (7 stages) — still runnable
  run_agents.py         # multi-agent (LangGraph) entrypoint — parallel to main.py
  config.py             # all settings loaded from .env
  scrapers/             # Stage 1–2: stock universe + NSE bhavcopy/bulk deals
  enrichment/           # Stage 3–4: Groww API (quotes/OHLC) + news + Gemini macro
  scoring/              # Stage 5–6: Claude Haiku batch scoring + weighted ranker
  backtest/             # Stage 7: T+1/T+3/T+5 backtest vs Nifty 50
  reports/              # HTML report (Jinja2) + Claude Sonnet narrative
  notifications/        # Telegram delivery
  agents/               # LangGraph multi-agent layer (wraps the modules above)
  persistence/          # Postgres ORM (runs, proposals, positions, orders, audit)
  observability/        # Langfuse callback + Prometheus metrics
  deploy/               # docker-compose.obs.yml (postgres + langfuse + prometheus + grafana)
  tests/                # pytest unit tests (no API keys needed)
  output/               # YYYY-MM-DD/scores.json + report.html, backtest_log.json
  scheduler/cron.sh     # cron wrapper for production scheduling
```

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
```

Required env vars: `ANTHROPIC_API_KEY`. All others are optional with graceful fallback (see `config.py`).

## Running

```bash
# Full run
bash run_local.sh

# Dry-run (5 stocks, fast)
bash run_local.sh --dry-run

# Skip backtest (saves time if no prior day's scores exist)
bash run_local.sh --skip-backtest

# Skip Sonnet narrative (saves cost)
bash run_local.sh --skip-narrative

# Specific date
python main.py --date 2026-05-14
```

## Tests

```bash
# Run all tests from the repo root
python -m pytest tests/ -v

# Single test
python -m pytest tests/test_scorer.py::TestRanker::test_composite_score_weighted -v
```

Tests mock `config` entirely — no `.env` needed. Coverage: prompts, ranker, backtest engine, Screener filters, yfinance fundamentals + earnings dates, news merge/dedup, sector-aware macro parsing, agent contract round-trips (`test_contracts.py`), and graph routing/guards (`test_graph.py`, using `MemorySaver`).

## Pipeline architecture

`main.py` runs 7 sequential stages:

1. **Stock universe** — either Screener.in custom screen (`STOCK_UNIVERSE=screener`) or NSE index (`nifty50`/`nifty100`/`nifty200`/`nifty500`). BSE-only (numeric) symbols are dropped. A persistent `output/skip_list.json` excludes stocks with no price data.
2. **NSE Bhavcopy + Bulk Deals** — delivery %, 52-week range, institutional bulk deals from NSE CSV dumps.
3. **Groww enrichment + fundamentals** — live quotes via `growwapi` SDK; OHLC candles via `yfinance`. Then `enrichment/fundamentals.py` adds PE / forward PE / market cap / sector / volume ratio + earnings dates from yfinance `.info` (free), and computes `sector_pe` as the per-sector median PE. TOTP auth preferred over legacy JWT.
4. **News + macro** — per stock, two Google News RSS queries (generic + results-focused), recency-filtered and deduped, results headlines first (cap 5). One Gemini call returns the macro summary **and** a per-sector impact map (`fetch_macro_context(sectors)`), so each stock is scored against its own sector's macro tailwinds/headwinds. Falls back gracefully if no key.
5. **Claude Haiku scoring** — each stock → JSON scorecard with 8 weighted signals (1–10). Uses synchronous API for <20 stocks, Batch API (50% cheaper) for ≥20.
6. **Rank + report** — `ranker.py` computes weighted composite score; `reports/daily_report.py` writes `output/YYYY-MM-DD/{scores.json, report.html}`. Claude Sonnet writes the narrative section.
7. **Backtest** — reads previous day's `scores.json`, fetches T+1/T+3/T+5 closes via yfinance, computes win rate + alpha vs Nifty 50, appends to `output/backtest_log.json`.

## Key design decisions

- **Signal weights** in `config.SIGNAL_WEIGHTS` must sum to 1.0. The ranker normalises against present signals only, so partial scorecards are handled gracefully.
- **Scorer threshold** `_SYNC_THRESHOLD = 20` in `claude_scorer.py` controls sync vs batch split.
- **Groww rate limit** is `GROWW_RATE_LIMIT_DELAY_MS` (default 200 ms); set to 0 in tests.
- **`MAX_STOCKS_TO_SCORE`** (default 100) caps the Groww/Claude expense: stocks are sorted by market cap and the tail is dropped before enrichment.
- Claude models are `claude-haiku-4-5` (scoring) and `claude-sonnet-4-6` (narrative); both are in `config.py`.

## Multi-agent architecture & conventions

The `agents/` package re-platforms the 7-stage pipeline onto **LangGraph** as a multi-agent system, **without rewriting** the existing modules — agents wrap them. `main.py` (legacy) stays runnable; `run_agents.py` is the parallel entrypoint.

```bash
python run_agents.py --mode research [--dry-run] [--date YYYY-MM-DD]
python run_agents.py --kill        # engage kill-switch   (creates output/kill_switch.flag)
python run_agents.py --unkill      # clear it
```

**Graph** (`agents/graph.py`): `research → analyst → [debate → risk → portfolio → trading] → finalize → END`. The trading chain is gated OFF by default, so research mode is `research → analyst → finalize`. Any terminal status short-circuits to `finalize`.

- **research** (`agents/nodes/research.py`) — Stages 1–4 (deterministic), skip-list moved in.
- **analyst** (`agents/nodes/analyst.py`) — Stage 5 scoring + Stage 6 ranking (Haiku).
- **finalize** (`agents/nodes/finalize.py`) — backtest + `write_report` (writes `scores.json`/`report.html`) + Telegram.
- **debate / risk / portfolio / trading** (`agents/nodes/stubs.py`) — stubs, gated off; real impls land in later iterations.

**State & contracts**: `agents/state.py` holds `AgentState` (LangGraph state, list fields use additive reducers) and the `RunStatus` terminal enum (`RUNNING | COMPLETED | AWAITING_APPROVAL | HALTED | FAILED | MAX_ROUNDS | BUDGET_EXCEEDED`). `agents/contracts.py` has Pydantic models with `from_legacy`/`to_legacy_dict` — the **seam** to the dict-based modules (e.g. `EnrichedStock` maps `52w_high`↔`week52_high` and preserves unknown keys in `extra`; `Scorecard` round-trips the exact `signals[k]["score"]` shape the ranker/telegram expect).

**Conventions** (borrowed from Claude Code / orchestrator-worker agent design):
- **Single responsibility per node**; nodes return a partial state-update dict and never reach into each other — they pass typed contracts.
- **`agent_node` decorator** (`agents/nodes/base.py`) is the supervisor contract on every node: honours the **kill-switch** (→ HALTED) and **per-run cost/token budget** (→ BUDGET_EXCEEDED), **skips** cleanly when the node's `ENABLE_*` flag is off, times + audits the node, and turns exceptions into a terminal `FAILED` (never crashes the run).
- **Explicit terminal states + bounded loops** — no open-ended iteration. The graph is invoked with `recursion_limit = MAX_GRAPH_STEPS`; the debate loop is bounded by `MAX_DEBATE_ROUNDS`.
- **Default-deny for irreversible actions** — live orders require `ENABLE_LIVE_TRADING` AND `AGENT_MODE=="live"` AND no kill-switch AND explicit human approval (LangGraph `interrupt()`), checked again inside the broker layer.
- **Dependency injection of `config`** — modules read `config` dynamically (mockable); tests use a stand-in config + `MemorySaver`.

**Persistence** (`persistence/`): Postgres owns agent + trading state — LangGraph checkpoints/store plus app tables (`runs`, `trade_proposals`, `positions`, `orders`, `agent_audit`, `memory`). Falls back to `MemorySaver` when `DATABASE_URL` is unset (so research mode + tests run without a DB). Research output stays as files.

**Observability** (`observability/`): Langfuse callback (LLM/agent traces + token/cost) and Prometheus metrics (run/node latency, cost, proposals); both no-op when their deps/keys are absent. `deploy/docker-compose.obs.yml` brings up postgres + langfuse + prometheus + grafana.

**Cost/iteration guardrails** live in `config.py`: `MAX_GRAPH_STEPS`, `MAX_DEBATE_ROUNDS`, `MAX_NODE_RETRIES`, `MAX_RUN_COST_USD`, `MAX_RUN_TOKENS`. Token-efficiency levers: prompt caching on the static scoring prefix, the Batch API for ≥20 stocks, model tiering (Haiku scoring / Sonnet debate), and pre-filtering before the LLM.

## Deployment (GCP Cloud Run)

```bash
# Build image (Cloud Build / Kaniko)
gcloud builds submit --config cloudbuild.yaml

# Run job (secrets injected as env vars — no .env in container)
```

The Dockerfile uses `python:3.12-slim` and needs `gcc`, `libxml2-dev`, `libxslt-dev` for lxml.
