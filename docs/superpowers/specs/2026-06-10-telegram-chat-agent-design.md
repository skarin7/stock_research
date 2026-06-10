# Telegram Conversational Trading Agent — Design Spec

Date: 2026-06-10
Status: Approved

## Problem

The system today is a scheduled batch job: the daily pipeline scores the stock
universe and pushes a fixed report to Telegram. The user wants an interactive,
agentic experience — ask free-form trade questions on Telegram (e.g. "what is
the best stock for today with low PE and some news related to it?") and get a
researched, ranked recommendation with reasoning.

## Decisions

| Question | Decision |
|---|---|
| Interface | Telegram bot (reuse existing bot token / notifier) |
| Inbound transport | Webhook on a Cloud Run **service**, min-instances 0 (keeps scale-to-zero cost model) |
| Data strategy | Daily run output cached in Postgres (`daily_snapshot`); agent filters the snapshot, then does live top-up (quotes/news) only for top candidates |
| Agent core | Single LangGraph tool-calling (ReAct) agent per chat thread; debate bull↔bear subgraph exposed as a `deep_dive` tool |
| Scope v1 | Research + recommendations only; clean seam for order placement later via existing risk→portfolio→trading→approval chain |
| Users | Single user — hard allowlist on `TELEGRAM_CHAT_ID` |
| Memory | Conversational — checkpointer keyed by `thread_id = chat_id` (PostgresSaver; MemorySaver fallback) |

## Architecture

```
Telegram ──POST──► server/app.py (FastAPI, Cloud Run service, min=0)
                      │  secret-token check, chat-ID allowlist, update_id dedupe, ACK 200
                      ▼
              agents/chat/agent.py  — create_react_agent(Sonnet, tools, checkpointer)
                      │ thread_id = chat_id
        ┌─────────────┼──────────────────────────────┐
        ▼             ▼                              ▼
 screen_snapshot   live_quote / fetch_news /     deep_dive(ticker)
 (Postgres          score_subset                 (existing debate
  daily_snapshot)   (existing modules, live)      bull↔bear subgraph)
                      │
                      ▼
              reply via notifications/telegram_notifier
```

The daily scheduled job is unchanged as the data producer; it gains one write:
the enriched + scored universe into `daily_snapshot`.

## Components

### Snapshot persistence (`persistence/`)

- New ORM model `DailySnapshot` (composite PK `run_date` + `symbol`): sector,
  pe, sector_pe, market_cap, ltp, delivery_pct, week52_high/low,
  composite_score, signals (JSON), news headlines (JSON), rationale.
- `store.save_daily_snapshot(report_date, ranked_stocks)` (upsert) and
  `store.load_latest_snapshot() -> (run_date, rows)`.
- No-DB fallback: read the newest `output/*/scores.json`.
- Write hooked into `agents/nodes/finalize.py` after `write_report`.

### Chat agent (`agents/chat/`)

Tools are thin wrappers over existing modules. Every tool returns a
JSON-serializable dict and catches all exceptions into `{"error": ...}` so the
LLM can explain failures gracefully instead of the loop crashing.

| Tool | Wraps | Freshness |
|---|---|---|
| `screen_snapshot(filters)` | `daily_snapshot` query | daily; flags `stale` if > `SNAPSHOT_STALE_DAYS` |
| `live_quote(symbols)` | `enrichment.market_data` provider chain | live |
| `fetch_news(symbols)` | `news_fetcher.fetch_news_batch` (cap 5) | live |
| `score_subset(symbols)` | enrich + `claude_scorer` sync + ranker (cap 10) | live |
| `deep_dive(ticker)` | debate bull↔bear subgraph → ConvictionView | live; max 1/turn |
| `get_portfolio()` | `persistence.store` book | current |

`build_chat_agent()` uses `create_react_agent` with the model from
`agents/llm.py` (`CHAT_MODEL`, default = `REPORT_MODEL`, Sonnet; OpenRouter
routing comes free) and the checkpointer from `agents/graph.py`.
`run_turn(chat_id, text) -> reply` invokes with
`{"configurable": {"thread_id": chat_id}, "recursion_limit": MAX_CHAT_TOOL_CALLS}`.

System prompt: Indian-market analyst persona; snapshot-first then live top-up
for finalists; always state the data as-of date; flag staleness; no
guaranteed-return language; concise Telegram-friendly HTML.

Guardrails: kill-switch honored (replies "halted"); per-turn cost recorded in
the `runs` table (mode=`chat`); per-turn budget `MAX_CHAT_TURN_COST_USD`;
tool-call cap via recursion limit.

### Webhook server (`server/app.py`)

- `POST /telegram/webhook`: reject unless
  `X-Telegram-Bot-Api-Secret-Token == TELEGRAM_WEBHOOK_SECRET`; drop messages
  from chat IDs other than `TELEGRAM_CHAT_ID`; dedupe on `update_id`
  (Telegram retries on non-200); send "🔎 researching…" placeholder
  immediately; run the agent turn; reply chunked ≤4000 chars; always 200.
- `GET /healthz` for Cloud Run.
- Local dev: `scripts/run_chat_local.py` long-polls `getUpdates` and drives the
  same `run_turn` — no public URL needed.

### Config

New settings with safe defaults: `TELEGRAM_WEBHOOK_SECRET` (""),
`ENABLE_CHAT_AGENT` (false), `CHAT_MODEL` (= `REPORT_MODEL`),
`MAX_CHAT_TOOL_CALLS` (8), `MAX_CHAT_TURN_COST_USD` (0.25),
`SNAPSHOT_STALE_DAYS` (3).

### Deploy

`fastapi` + `uvicorn` added to requirements. Terraform adds a Cloud Run
service (same image, `uvicorn server.app:app`), min-instances 0, request
timeout 300 s. One-time webhook registration via `scripts/set_webhook.py`
(`setWebhook` with `secret_token`).

## Error handling

- Tool failures → error dicts, never exceptions into the agent loop.
- Stale snapshot → flagged in tool output; agent must mention it.
- Webhook always returns 200 to prevent Telegram retry storms; duplicates
  filtered by `update_id`.
- No `DATABASE_URL`: snapshot falls back to `scores.json` files; memory falls
  back to `MemorySaver` (per-process only).

## Testing

- `test_chat_tools.py` — per-tool unit tests, mocked modules, error-dict and
  staleness cases (no API keys, existing pattern).
- `test_chat_agent.py` — scripted fake LLM emitting tool calls, MemorySaver;
  asserts routing, final reply, recursion cap.
- `test_webhook.py` — FastAPI TestClient: bad secret 403, foreign chat dropped,
  duplicate update runs once, happy path.
- `test_snapshot_store.py` — save/load round-trip + file fallback.

## Out of scope (v1)

Trading from chat (future `propose_trade` tool → existing
risk→portfolio→approval chain), multi-user support, intraday-watchlist
queries (future tool), streaming/voice.
