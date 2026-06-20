# Chat Agent — New Capabilities Design

Date: 2026-06-20

## Goal

Extend the Telegram conversational chat agent (`agents/chat/`) so the user can:

1. **Recall on stocks it knows** — ask about a stock and get the agent's prior verdicts, not just today's snapshot.
2. **Best time to buy/sell** — ask for entry/exit timing on a named stock and get a technical read plus a buy-zone/stop/target verdict.
3. **Event-driven macro/geopolitical impact** — ask "what's the impact of the Iran war on the Indian market?" and get a grounded answer naming the primarily impacted sectors and known stocks.

## Non-goals

- No order placement from chat (unchanged from v1).
- No backtest-performance tool (deferred — `latest_signal_perf()` stays unexposed for now).
- No graph or webhook-server changes.

## Architecture

Same single ReAct loop (`agents/chat/agent.py`, `create_react_agent`). The work is:

- Add **3 tools** to `agents/chat/tools.py`.
- Extend `_SYSTEM_PROMPT` with routing/discipline guidance for the new tools.
- Add config keys in `settings.py`.

Every new tool keeps the existing module contract: returns a JSON-serializable dict, converts any exception to `{"error": ...}`, degrades gracefully when a data source is missing, and is bounded by the existing `MAX_CHAT_TOOL_CALLS` cap. No new uncapped LLM loops are introduced.

## Tool 1 — `macro_search(query: str)`

Event-driven web search to ground macro/geopolitical questions.

- Implementation: Tavily REST via the already-present `requests` dependency. `POST https://api.tavily.com/search` with `api_key` from `SETTINGS.TAVILY_API_KEY`, `search_depth="basic"`, `max_results=SETTINGS.MACRO_SEARCH_MAX_RESULTS` (default 5).
- Gating: if `TAVILY_API_KEY` is empty, return `{"error": "macro search is not configured"}` — no crash.
- Returns: `{"answer": <tavily answer or "">, "results": [{"title", "url", "snippet"}], "fetched_at": <iso date>}`.
- Free-tier safety: `search_depth="basic"`, results capped; personal-volume usage stays well under the ~1,000 searches/month free tier.

Agent flow for a macro question: `macro_search(...)` → reason event → impacted sectors (e.g. crude → oil & gas, aviation, paints; defense; shipping) → `screen_snapshot(sector=...)` to name known/held stocks → reply citing the source URLs and the fetch date. Discipline: it is news-derived analysis, not a forecast.

## Tool 2 — `timing(ticker: str)`

Deterministic technical read for entry/exit timing.

- Implementation: fetch ~400 days OHLCV from `enrichment.market_data.get_default_provider().get_ohlcv(ticker, days=400)`, feed `intraday/technicals.py` `compute_metrics(candles)` (gives `close`, `rsi14`, `high_20d`, `change_3d_pct`, `high_52w`). `compute_metrics` has no low helper, so the tool derives `low_52w` and `support` directly from the candle low column (`min` over the full series / recent N).
- Returns: `{"ticker", "ltp", "rsi14", "pct_52w_position", "near_52w_high", "near_52w_low", "breakout_20d", "mom_5d", "mom_20d", "support", "resistance"}` where `resistance` = `high_20d` (prior-20d high), `support` = recent N-day low (min of recent lows), `pct_52w_position` = (ltp − low_52w) / (high_52w − low_52w).
- **No nested LLM call.** The tool returns raw numbers; the chat agent (the LLM) composes the buy-zone / stop / target verdict in its reply.
- Degradation: each field is computed under a guard — a missing/short candle series yields `null` for that field, never a crash. Empty candles → all-null payload with no exception.

## Tool 3 — `recall(ticker: str)`

Memory of the agent's prior calls on a stock.

- Implementation: wraps `persistence.store.recent_calls(ticker)`, which returns a list of stored call dicts (fields: `ticker`, `score`, `conviction`, `rationale`, `regime`, `outcome`, plus date).
- Returns: `{"ticker", "past_calls": [<the stored call dicts, trimmed to the fields above>]}`.
- Degradation: when no `DATABASE_URL` / no jsonl memory is populated, returns `{"ticker", "past_calls": []}` — the agent says it has no prior record.

## Prompt changes (`_SYSTEM_PROMPT`)

Add tool-routing and discipline guidance:

- Use `macro_search` for current events / geopolitics; always cite source URLs and the fetch date, and frame as news-derived, not a forecast.
- Use `timing` for entry/exit questions; always present key risks, never use "guaranteed"/"sure-shot" language, and phrase entries as zones with a stop.
- Use `recall` when the user asks what the agent thought before about a stock.
- Reinforce existing rules: not financial advice; cannot place orders from chat.

## Config (`settings.py`)

- `TAVILY_API_KEY: str = ""` (+ `os.environ.get` load in `from_env`).
- `MACRO_SEARCH_MAX_RESULTS: int = 5` (+ env load).

## Testing (`tests/test_chat_tools.py`)

- `macro_search`: mock `requests.post` → assert returned shape and result cap; empty key → error dict.
- `timing`: mock provider `get_ohlcv` with synthetic candles → assert `rsi14`, `breakout_20d`, `support`/`resistance`; empty candles → all-null, no crash.
- `recall`: mock `store.recent_calls` → assert shape; empty → `past_calls == []`.
- Register all 3 in `CHAT_TOOLS`; update the agent test that asserts the tool count (6 → 9).

## Cost / safety

- Tavily free tier (~1,000/month), `search_depth="basic"`.
- New tools add tool calls but no new uncapped LLM loops; the existing `MAX_CHAT_TOOL_CALLS` and per-turn cost recording in `run_turn` still bound a turn.
- All tools default-safe: missing key/data → error or null payload, never an exception that breaks the chat loop.
