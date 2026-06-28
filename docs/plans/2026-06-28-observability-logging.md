# Observability & Logging Overhaul

**Date:** 2026-06-28  
**Scope:** Full system — chat agent webhook, intent router, pipeline nodes, batch scoring

## Context

The Telegram chat webhook (`server/app.py`) and the underlying agent stack produce almost no
observable output beyond error-only exception logs. Concrete problem: a user asks "ABB last
traded price" and gets an answer, but cannot tell whether it came from the stale Postgres
snapshot (`screen_snapshot` tool) or a live API call (`live_quote` tool). LLM call logs are
absent from Python stdout. Langfuse traces capture LLM I/O but miss tool results, intent
routing decisions, and pipeline node transitions entirely.

Root causes:
1. `logging.basicConfig()` / handler setup never called → `server.app` logger has no handler,
   logs silently dropped.
2. Only exceptions logged per tool — no pre/post logging of tool name, args, result, or
   data source.
3. Langfuse `CallbackHandler` captures LLM model I/O but not tool outputs (LangChain's
   `on_tool_end` result is not captured in the current setup).
4. Intent router logs nothing on semantic hits; cosine scores invisible.
5. Batch API scoring (≥20 stocks) bypasses LangChain callbacks entirely — invisible to Langfuse.
6. Pipeline node transitions (research → analyst → debate etc.) not in Langfuse; only
   Prometheus metrics cover node-level timing.

## Goals

- Every chat turn: full message text visible in Python logs + Langfuse turn-level trace.
- Every tool call: name, args, result summary, `_source` (cache vs live API), duration_ms —
  in Python JSON log AND as a Langfuse child span.
- Intent routing: semantic score + matched intent, or LLM fallback confidence — logged at INFO.
- Pipeline nodes: start/end Langfuse spans with key output metrics (stock count, proposals, etc.).
- Batch scoring: explicit Langfuse trace for each batch run.

## Architecture

```
Telegram message
  ↓
server/app.py          → LOG: full text, chat_id, update_id, turn_start
  ↓
intent.py              → LOG + SPAN: route (semantic/llm/fallback), score, confidence
  ↓
tools.py (9 tools)     → LOG + SPAN: tool name, args, result summary, _source, duration_ms
  ↓
agents/nodes/*.py      → SPAN: node name, input/output metrics, duration, status
  ↓
scoring/claude_scorer  → SPAN: batch_id, model, stock_count, est_cost_usd
```

**Python logs** → structured JSON to stdout (docker logs / Cloud Run).  
**Langfuse spans** → child spans under the existing turn/run trace.

## New Files

### `observability/logging_config.py`

`setup_logging(level: str = "INFO")` — configures root logger with a JSON formatter:

```json
{
  "ts": "2026-06-28T10:23:01.456Z",
  "level": "INFO",
  "logger": "agents.chat.tools",
  "msg": "tool_call",
  "tool": "live_quote",
  "args": {"symbols": ["ABB"]},
  "result_summary": "1 quote returned",
  "_source": "groww_api",
  "duration_ms": 45
}
```

Called once at `server/app.py` startup (`@app.on_event("startup")`) and at
`run_agents.py` / `run_intraday.py` entrypoints.

### `observability/chat_tracing.py`

`trace_tool(name, args)` — context manager built on Langfuse's `@observe` decorator pattern
(`langfuse.decorators.observe`). This automatically parents spans to the active trace when
called inside a LangChain ReAct loop (Langfuse SDK maintains trace context via thread-local
state). Outside ReAct (pipeline nodes), spans attach to the top-level `langfuse.trace()` for
that run. Falls back to Python-log-only when Langfuse keys are unset.

- On `__exit__`: records output dict + duration + `_source` to span; emits JSON log line.
- On exception: records error to span + logs at ERROR level; re-raises.

```python
with trace_tool("live_quote", {"symbols": ["ABB"]}) as span:
    result = _fetch(symbols)
    span.set_output(result)
return result
```

`trace_node(name, run_id, input_summary)` — same pattern for pipeline nodes.

`trace_intent(route, score, intent, confidence)` — logs + creates a Langfuse span for the
intent routing decision. Called from `route_intent()` before returning.

## Changes by File

### `server/app.py`

- Import and call `setup_logging()` inside `_startup()`.
- Replace line 144 truncated log with full structured log:
  ```python
  logger.info("msg_received", extra={"chat_id": chat_id, "text": text, "update_id": update_id})
  ```
- Log approval routing decision (approved / rejected / pass-through).
- Log agent turn start (with `chat_id`) and turn end (with `duration_ms`, `reply_length`,
  `chunk_count`).

### `agents/chat/tools.py`

Wrap each of the 9 tools with `trace_tool()`. Add `_source` key to every return dict:

| Tool | `_source` values |
|------|-----------------|
| `screen_snapshot` | `"postgres_snapshot"` / `"file_snapshot"` / `"no_snapshot"` |
| `live_quote` | `"groww_api"` / `"yfinance_fallback"` |
| `fetch_news` | `"google_news_rss"` |
| `score_subset` | `"claude_haiku_batch"` / `"claude_haiku_sync"` |
| `deep_dive` | `"debate_subgraph"` |
| `get_portfolio` | `"postgres"` / `"memory"` |
| `macro_search` | `"tavily_api"` / `"unavailable"` |
| `timing` | `"intraday_technicals"` |
| `recall` | `"memory_store"` |

`_source` is included in the result dict returned to the agent (the ReAct loop sees it; this
is intentional — the agent can reference provenance in its reply if relevant).

### `agents/chat/intent.py`

In `route_intent()`, after each routing decision, call `trace_intent(...)`:

- Semantic hit: `{"route": "semantic", "top_score": 0.87, "intent": "live_price"}`
- Semantic miss (LLM fallback): `{"route": "llm_fallback", "top_score": 0.43}`
- LLM decision: `{"route": "llm", "intent": "live_price", "confidence": 0.91}`
- Ambiguous: `{"route": "llm", "intent": "ambiguous", "confidence": 0.42}`

All at INFO level. Langfuse span wraps the full `route_intent()` call.

### `agents/nodes/base.py`

Extend `agent_node` decorator to open a Langfuse span at node entry and close it at exit.
Span metadata:

- Entry: `node_name`, `run_id`, `input_summary` (derived from state — e.g. stock count)
- Exit: `output_summary`, `duration_ms`, `status` (RUNNING / COMPLETED / FAILED / HALTED)

No change to the decorator signature — backward compatible.

### `agents/nodes/*.py` — key metrics per node

Passed via `span.set_output(...)` on exit:

| Node | Output metrics |
|------|---------------|
| `research` | `stocks_fetched`, `skip_list_hits` |
| `analyst` | `stocks_scored`, `top_score`, `model` |
| `debate` | `candidates`, `rounds`, `bull_conviction`, `bear_conviction` |
| `risk` | `proposed`, `blocked` |
| `portfolio` | `approved`, `rejected`, `capital_allocated_usd` |
| `finalize` | `report_written` (bool), `telegram_sent` (bool) |
| `memory` | `records_written` |
| `pulse` | `triggers_fired`, `alerts_sent` |

### `scoring/claude_scorer.py`

Batch API path (`_score_batch`): wrap with `langfuse.trace()` call:
```python
langfuse.trace(
    name="batch_scoring",
    metadata={
        "batch_id": batch_id,
        "model": model,
        "stock_count": len(stocks),
        "estimated_cost_usd": round(len(stocks) * COST_PER_STOCK * 0.5, 4),
    }
)
```

Sync path: already logged via LangChain callbacks per-call.

## Data Flow: "ABB LTP" Example (after this change)

```
Python log: {"msg": "msg_received", "chat_id": "123", "text": "what is ABB LTP", ...}
Python log: {"msg": "intent_routed", "route": "semantic", "intent": "live_price", "top_score": 0.91}
Python log: {"msg": "tool_call", "tool": "live_quote", "args": {"symbols": ["ABB"]},
             "result_summary": "1 quote", "_source": "groww_api", "duration_ms": 38}
Python log: {"msg": "turn_complete", "chat_id": "123", "duration_ms": 1240, "reply_length": 87}

Langfuse trace: turn → [intent_span, live_quote_span (input: ABB, output: {price: 1234.5, _source: groww_api})]
```

## Testing

1. `docker compose up` → run `python scripts/run_chat_local.py` → send "ABB LTP" → verify:
   - Python stdout shows JSON log lines for intent + tool + turn_complete
   - `_source` field present in tool log
   - Langfuse UI (localhost:3000) shows turn trace with `live_quote` child span containing full result

2. Trigger `screen_snapshot` path (send "show me top stocks") → verify `_source` is
   `"postgres_snapshot"` not `"groww_api"`.

3. Run `python run_agents.py --mode research --dry-run` → verify Langfuse shows node spans
   for research, analyst, finalize.

4. Run with ≥20 stocks → verify Langfuse shows `batch_scoring` trace.

5. Existing tests: `python -m pytest tests/ -v` must pass (no behavioral changes).

## Non-Goals

- No changes to Telegram message format sent to users.
- No changes to scoring weights, signal logic, or backtest.
- No new config flags — logging is always-on; Langfuse spans are no-ops when keys unset
  (existing `get_callbacks()` behavior preserved).
