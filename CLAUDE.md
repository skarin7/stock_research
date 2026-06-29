# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The project lives at the **repo root** — an NSE/BSE daily stock-scoring pipeline (there is no `stock-intelligence/` subdirectory; do not `cd` into one).

```
.
  main.py               # legacy pipeline orchestrator (7 stages) — still runnable
  run_agents.py         # multi-agent (LangGraph) entrypoint — parallel to main.py
  run_intraday.py       # intraday next-day watchlist entrypoint — parallel to main.py
  intraday/             # day-before signal scorer (S1–S10 / N1–N7) → watchlist
  config.py             # all settings loaded from .env
  scrapers/             # Stage 1–2: stock universe + NSE bhavcopy/bulk deals
  enrichment/           # Stage 3–4: Groww API (quotes/OHLC) + news + Gemini macro
  scoring/              # Stage 5–6: Claude Haiku batch scoring + weighted ranker
  backtest/             # Stage 7: T+1/T+3/T+5 backtest vs Nifty 50
  reports/              # HTML report (Jinja2) + Claude Sonnet narrative
  notifications/        # Telegram delivery
  agents/               # LangGraph multi-agent layer (wraps the modules above)
  agents/chat/          # Conversational chat agent (tools.py + agent.py)
  server/               # FastAPI webhook server for the chat agent (server/app.py)
  scripts/              # run_chat_local.py (long-poll dev loop), set_webhook.py
  persistence/          # Postgres ORM (runs, proposals, positions, orders, audit)
  observability/        # Langfuse callback + Prometheus metrics
  deploy/               # docker-compose.obs.yml (postgres + langfuse + prometheus + grafana)
  tests/                # pytest unit tests (no API keys needed)
  output/               # YYYY-MM-DD/scores.json + report.html, snapshot.json, backtest_log.json
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
3. **Groww enrichment + fundamentals** — live quotes via `growwapi` SDK; OHLC candles via `yfinance`. Then `enrichment/fundamentals.py` adds PE / forward PE / market cap / sector / volume ratio + earnings dates from yfinance `.info` (free), and computes `sector_pe` as the per-sector median PE. **PIT contamination guard:** yfinance `.info` (and news) are *not* point-in-time — they return today's values regardless of `ref_date`, so a historical re-run (`--date <past>`) is look-ahead-biased. `enrich_fundamentals` detects `ref_date < today`, logs a loud warning, and stamps every stock (and its snapshot row) `pit_safe=false` so contaminated runs self-document. Only the deterministic intraday scorer + dated bhavcopy/OHLC are PIT-safe; an honest walk-forward backtest over history is the deferred `docs/plans/eval-harness.md` (guard shipped from `docs/plans/pit-contamination-guard.md`). TOTP auth preferred over legacy JWT (TOTP avoids per-request OTP but, per Groww docs, still needs daily key approval — it is not exempt from the daily re-approval). Groww live/historical data endpoints require an **active paid Trading API subscription**; without it every data call returns 403 (`_log_data_error` flags this) and the FallbackChain drops to yfinance.
   After fundamentals, `enrichment/technical_signals.py` (`attach_technicals`) computes per-stock **MACD(12,26,9) + 20-day breakout + RSI** from the already-fetched `ohlc_10d` candles (pure helpers in `intraday/technicals.py`) into `stock["technicals"]`. These are **surfaced only** (Telegram card line + `daily_snapshot.technicals` JSON + chat tools) — not fed into the composite score (that needs backtest validation). `OHLC_LOOKBACK_DAYS` is 60 to give MACD enough warmup; the scorer still consumes only `ohlc_5d`.
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
- **LLM provider switch** (`llm_router.py`): `config.LLM_PROVIDER` is `anthropic` (default) or `openrouter`. OpenRouter is OpenAI-compatible and hosts cheap reasoning models (DeepSeek/Qwen/Kimi); `OPENROUTER_SCORING_MODEL`/`OPENROUTER_REPORT_MODEL` pick the model. The scorer (`claude_scorer.py`) and narrative (`daily_report.py`) and the agent LLM factory (`agents/llm.py`) all route through it. Note: the **Anthropic Batch API (50% off) only applies to the anthropic provider** — OpenRouter uses one sync call per stock. Validate cheaper models against the backtest before trusting them for trades.

## Intraday prediction system

`run_intraday.py` is a third, standalone entrypoint (parallel to `main.py`/`run_agents.py`) implementing the **day-before next-day watchlist** strategy: run it in the evening after NSE close to score the universe on a fixed signal framework and push a ranked watchlist to Telegram. It does **not** call the LLM — it's a deterministic integer scorer.

```bash
python run_intraday.py [--date YYYY-MM-DD] [--dry-run] [--no-telegram]
```

- **`intraday/signals.py`** — pure `score_stock(ctx)` port of the spec's S1–S10 (bullish, +1..+3) and N1–N7 (risk reducers, −1..−2) rules. Each rule is guarded: a missing/None input contributes 0 points (an unavailable data source never crashes the run). Per-signal cut-offs (1.5x volume, RSI 55–68, etc.) are **spec constants in this file**, not config. `conviction(score)` maps to HIGH (≥7) / MODERATE (5–6) / LOW (3–4) / IGNORE.
- **`intraday/technicals.py`** — pure indicator helpers over OHLCV candle lists (RSI(14) Wilder, prior-N-day high for breakout, N-day % change, N-day avg volume). No I/O — trivially testable.
- **`intraday/data_sources.py`** — reliable sources first. Daily OHLCV and per-stock PCR / Call-OI come from the **`enrichment.market_data` provider abstraction**: `get_default_provider()` returns a `FallbackChain([GrowwProvider(), YFinanceProvider()])`, so the official Groww historical endpoint is primary and yfinance is the fallback when Groww historical data isn't subscribed. `data_sources.fetch_history`/`option_chain_signals` are thin shims over `provider.get_ohlcv`/`get_option_chain`. 52-week high is derived from the candles (max high over ~1y), so `INTRADAY_HISTORY_DAYS` defaults to 400. The only remaining **fragile NSE-web scrapes are S1 (next-day board meetings) and N4 (ASM/GSM list)** — no Groww endpoint exists for those; each returns empty on failure. **Tier C (S4 peer read-across, S7/N6 sector-wise FII, N7 legal) is omitted** — no free data source, so the scorer carries no branch for them. The Groww daily-candle params (in `GrowwProvider`) are an **unverified seam** (like the broker): the yfinance fallback keeps it working today; verify against the installed SDK before relying on Groww for history.
- **`intraday/pipeline.py`** — `run_pipeline()` fetches bulk data once, loops per-stock (history + option chain), scores, filters ≥ `INTRADAY_SCORE_THRESHOLD` (5), sorts desc, caps at `INTRADAY_TOP_N` (10).
- **`intraday/report.py`** — renders the spec's watchlist alert (HIGH/MODERATE bands) and writes `output/YYYY-MM-DD/intraday_watchlist.{json,txt}`. Telegram delivery via `notifications.telegram_notifier.send_intraday_watchlist`.
- Tunables in `config.py`: `INTRADAY_SCORE_THRESHOLD`, `INTRADAY_HIGH_CONVICTION`, `INTRADAY_TOP_N`, `INTRADAY_HISTORY_DAYS`. Tests: `tests/test_intraday.py` (pure signals/technicals/report/pipeline, fully mocked).

## Multi-agent architecture & conventions

The `agents/` package re-platforms the 7-stage pipeline onto **LangGraph** as a multi-agent system, **without rewriting** the existing modules — agents wrap them. `main.py` (legacy) stays runnable; `run_agents.py` is the parallel entrypoint.

```bash
python run_agents.py --mode research [--dry-run] [--date YYYY-MM-DD]
python run_agents.py --kill        # engage kill-switch   (creates output/kill_switch.flag)
python run_agents.py --unkill      # clear it
```

**Graph** (`agents/graph.py`): `research → analyst → [debate → risk → portfolio → trading] → finalize → memory → END`. The trading chain is gated OFF by default, so research mode is `research → analyst → finalize → memory`. Any terminal status short-circuits to `finalize`. A separate `build_monitor_graph` (`START → monitor → END`) backs `--mode monitor`.

- **research** (`agents/nodes/research.py`) — Stages 1–4 (deterministic), skip-list moved in.
- **analyst** (`agents/nodes/analyst.py`) — Stage 5 scoring + Stage 6 ranking (Haiku).
- **finalize** (`agents/nodes/finalize.py`) — backtest + `write_report` (writes `scores.json`/`report.html`) + Telegram.
- **debate** (`agents/nodes/debate.py`) — a **bounded bull↔bear→synthesize subgraph** run per top-`DEBATE_TOP_N` candidate. `bull → bear` alternate up to `MAX_DEBATE_ROUNDS` (hard turn cap), then a judge step emits `{direction, conviction}` → one `ConvictionView` each. Provider-aware LLM via `agents/llm.py`; LLM turns isolated behind `_chat` (monkeypatched in tests). Gated by `ENABLE_DEBATE_AGENT` (off by default).
- **risk** (`agents/nodes/risk.py`) — deterministic gate over the debate's convictions: long-only, `MIN_CONVICTION_TO_TRADE`, earnings-proximity block, no-duplicate-of-held → emits `TradeProposal`s as PROPOSED/BLOCKED with `RiskCheck`s.
- **portfolio** (`agents/nodes/portfolio.py`) — sizes PROPOSED proposals by `capital × MAX_POSITION_PCT × conviction`, accepts highest-conviction first under `MAX_OPEN_POSITIONS` + `MAX_SECTOR_PCT` → APPROVED (qty + limit_price) / REJECTED.
- **trading** (`agents/nodes/trading.py`) — **paper** mode simulates fills (appends positions + stop-loss, debits cash, persists the book via `persistence/store.py`); **live** mode marks proposals AWAITING_APPROVAL, persists them, and calls LangGraph `interrupt()` to suspend for human approval — on resume, approved proposals go through the gated broker. Gated by `ENABLE_TRADING_AGENT`.
- **broker** (`agents/broker/groww_trader.py`) — the **only** order-placement seam; `place_order` is default-deny (re-checks `mode=="live"` AND `ENABLE_LIVE_TRADING` AND `GROWW_TRADING_ENABLED` AND no kill-switch) and idempotent on `broker_order_id`. Reuses the TOTP client from `enrichment/market_data/groww.py` (`default_client()`). Verify the exact `growwapi` params before enabling live.
- **approval** (`agents/approval.py`) — sends the Telegram approval request and `resume_run(run_id, decisions)` resumes a suspended run via `Command(resume=...)`. `run_agents.py --resume <run_id> --approve/--reject <id>` is the CLI path. **Cross-process resume requires `DATABASE_URL`** (the suspended state must be in the Postgres checkpointer; MemorySaver only resumes in-process).
- `proposals` in `AgentState` evolve through their lifecycle (risk → portfolio → trading), so it uses **replace** semantics (not an additive reducer).

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

## Deployment (VM)

Single VM (GCP e2-small or Hetzner CAX11). Full guide: `docs/deployment.md`.

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars  # fill in keys + enable_vm=true
terraform apply
# then: python scripts/set_webhook.py https://<vm-ip>/telegram/webhook
```

Schedules (research/intraday/watch) are stored in the `schedules` DB table — update rows to change timing, no SSH needed.

**Terraform** (`deploy/terraform/`): provisions Artifact Registry, Cloud Run service (chat webhook option), and optionally a GCP Compute Engine VM (`enable_vm=true`). `bash deploy/deploy.sh` builds the Docker image. Secrets injected as plain env vars (no Secret Manager).

- **Memory agent** (`agents/nodes/memory.py`): runs after `finalize`; records each ranked call (score/conviction/rationale + a coarse regime label) into long-term memory (`persistence/store.py` append-only jsonl) and stores a per-signal accuracy self-evaluation from the backtest log. Agents read it back via `store.recent_calls(ticker)` / `store.latest_signal_perf()` (feeding it into scoring weights is a future tuning step). Gated by `ENABLE_MEMORY_AGENT`.
- **Monitoring agent** (`agents/nodes/monitoring.py`, `build_monitor_graph`): a standalone `START → monitor → END` graph run via `run_agents.py --mode monitor`. It loads the book, fetches a live price per position (`_current_price`, monkeypatchable), evaluates stop-losses → `Alert`s, and notifies on critical ones. Paper book auto-exits stopped positions; under `ENABLE_LIVE_TRADING` it alerts only (real exits must go through the broker). Runs as a scheduled job every few minutes during market hours, NOT a 24/7 service.
- **Market-pulse agent** (`agents/nodes/pulse.py`, `build_pulse_graph`, `run_agents.py --mode pulse`): a proactive intraday **shock watcher** that ALERTS only (no broker action) on four trigger families — (1) NIFTY drop, (2) India VIX spike, (3) news/geopolitical shock (tiered cheap Haiku classifier), (4) **global cross-asset** shock (`PULSE_GLOBAL_TICKERS`: Kospi/Nikkei/Hang Seng/crude/USD-INR/US-futures, via `enrichment/market_pulse.py`). Session-only triggers (1,2 + per-holding drop) are gated by `_market_open()` (09:15–15:30 IST); global + news run pre-open too (the early read on overnight moves). Debounce is **once-per-episode** (fires on threshold cross, re-arms after the metric normalises; `PULSE_ALERT_COOLDOWN_MIN` is a secondary floor) with state in `store.load/save_pulse_state`. Global breaches map to Indian sectors (`PULSE_GLOBAL_SECTOR_MAP`) → names your exposed holdings. Gated by `ENABLE_PULSE_AGENT` (env, not profile). Runs on a **tight 1–2 min schedule incl. pre-open** (default `*/2 2-10 UTC`), still scale-to-zero.

## Conversational chat agent (Telegram)

A second, parallel entrypoint: the user asks free-form trade questions on Telegram and the agent researches and answers using the existing pipeline modules as tools.

```bash
# Local dev (long-poll, no public URL needed):
python scripts/run_chat_local.py

# Production: deploy the Cloud Run service, then register the webhook once:
python scripts/set_webhook.py https://<service-url>/telegram/webhook
```

**Architecture**: `Telegram → server/app.py (FastAPI webhook) → agents/chat/agent.py (ReAct agent) → tools → reply`.

The daily scheduled run's output is now also written to `output/<date>/snapshot.json` (and the `daily_snapshot` Postgres table when `DATABASE_URL` is set). The chat agent's `screen_snapshot` tool filters this cache; live top-up tools are called only on the shortlist.

**`agents/chat/tools.py`** — nine tools, all returning error-dicts on failure:
- `screen_snapshot(filters)` — filters the daily snapshot (PE, sector, score, has_news); flags staleness.
- `live_quote(symbols)` — live prices from the market-data provider chain.
- `fetch_news(symbols)` — fresh headlines from Google News RSS.
- `score_subset(symbols)` — re-scores a shortlist with fresh news + live price (Haiku).
- `deep_dive(ticker)` — runs the debate bull↔bear subgraph → `ConvictionView`; capped at 1 per turn.
- `get_portfolio()` — current paper-trading book.
- `macro_search(query)` — Tavily web search (gated on `TAVILY_API_KEY`, free tier) to ground event/geopolitical questions; agent maps the event → sectors → `screen_snapshot`.
- `timing(ticker)` — deterministic technicals (RSI14, 52w position, 20d breakout, momentum, support/resistance) from `intraday/technicals.py` over provider OHLCV; the agent composes the buy-zone/stop/target verdict (no nested LLM).
- `recall(ticker)` — past calls the agent recorded on a stock via `store.recent_calls`.

**`agents/chat/agent.py`** — `build_chat_agent()` builds a `create_react_agent` with the Sonnet model (`CHAT_MODEL`, default = `REPORT_MODEL`), per-chat checkpointer (thread_id = chat_id), and the system prompt. `run_turn(chat_id, text)` is the single entrypoint — honours kill-switch, bounds the loop via `MAX_CHAT_TOOL_CALLS`, and never raises.

**Intent router** (`agents/chat/intent.py`, `embedder.py`, `intent_exemplars.py`) — a core tiered front door before the ReAct loop (always on; self-gating — the semantic tier is skipped when no embedding backend is configured). (1) **HITL approval** is deterministic (no LLM/ReAct): `agents/approval.route_approval` (called first in `server/app.py`) handles `/approve|/reject <id>` AND a bare `approve`/`reject` reply when a proposal is pending for the chat (single → resolved, multiple → asks which) — so an approval response to a suspended trading flow never reaches the agent. (2) **Semantic tier (primary, no chat-LLM):** a curated exemplar bank (`EXEMPLARS`) is embedded **once via OpenRouter** (`CHAT_EMBED_MODEL`=`openai/text-embedding-3-small`) and cached in **Postgres** (`intent_embeddings` via `store.load/save_intent_bank`); each message embeds the query then cosine-matches → intent when top sim ≥ `CHAT_SEMANTIC_THRESHOLD`. (3) **LLM fallback** only below threshold (one cheap `CHAT_INTENT_MODEL`/Haiku call; low `CHAT_INTENT_MIN_CONFIDENCE` → `ambiguous`). Canned intents (greeting/out_of_scope/trade_intent) short-circuit with a reply; `ambiguous` asks a clarifying question; research-class intents fall through to the ReAct agent with an `(intent: x)` hint. **Fail-open**: any embedder/LLM error → straight to the agent. Intents logged via `store.record_memory("chat_intent", …)`.

**`server/app.py`** — FastAPI webhook: secret-token auth (`TELEGRAM_WEBHOOK_SECRET`), chat-ID allowlist, `update_id` dedup ring, always returns 200 to prevent Telegram retry storms.

**Config**: `ENABLE_CHAT_AGENT` (false), `CHAT_MODEL` (""), `TELEGRAM_WEBHOOK_SECRET` (""), `MAX_CHAT_TOOL_CALLS` (8), `MAX_CHAT_TURN_COST_USD` (0.25), `SNAPSHOT_STALE_DAYS` (3), `TAVILY_API_KEY` ("") + `MACRO_SEARCH_MAX_RESULTS` (5) for `macro_search`. Intent router (core, no enable flag): `CHAT_SEMANTIC_THRESHOLD` (0.55), `CHAT_EMBED_MODEL` (`openai/text-embedding-3-small`), `CHAT_INTENT_MODEL` ("" → `SCORING_MODEL`), `CHAT_INTENT_MIN_CONFIDENCE` (0.6). Semantic tier needs `OPENROUTER_API_KEY`; without it the router self-gates to the LLM classifier.

**Terraform** (`deploy/terraform/`): `enable_chat_agent=true` + `telegram_webhook_secret=<token>` provisions a Cloud Run **service** (not a job) that receives webhook updates (min-instances=0, request-timeout=300 s, public invoker). The `chat_webhook_url` output is the URL to register. Order placement from chat is out of scope for v1; the seam is `propose_trade` → the existing risk → portfolio → approval chain.
