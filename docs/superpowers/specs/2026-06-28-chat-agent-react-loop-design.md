# Chat Agent: Multi-Step ReAct Loop + DeepSeek Model + Citation Layer

**Date:** 2026-06-28
**Status:** Approved

## Problem

The chat agent (`agents/chat/agent.py`) uses LangGraph's `create_react_agent`, which supports up to 8 tool calls per turn (`recursion_limit=17`). In practice the model stops after one tool call because the system prompt describes *which* tools to call but never instructs the model to:

1. Plan the full tool sequence before acting
2. Evaluate result completeness after each tool call
3. Continue calling tools when the answer is still incomplete

Additionally:
- Chat uses `claude-sonnet-4-6` (expensive). DeepSeek-V3 via OpenRouter is cheaper and sufficient.
- News citations are missing: `fetch_news` drops RSS `<link>` URLs, so the agent cannot cite sources for stock-level news claims.

## Goals

1. Reliable multi-step ReAct chains (e.g. `screen_snapshot → timing × N → compose`)
2. Switch chat model to `deepseek/deepseek-chat` via OpenRouter, independent of scoring/report model
3. Source citations for all factual claims: macro (URL), stock news (URL + date), scores (snapshot date), technicals (OHLCV)

## Non-Goals

- Changing `create_react_agent` architecture (plan-execute, state_modifier, etc.)
- Changing intent router, cache, or other chat infrastructure
- Scoring or report pipeline model changes

---

## Design

### Area 1: Model Config

**Problem:** `CHAT_MODEL` is a single string with no provider distinction. No `chat_model()` exists in `llm_router.py`. When `LLM_PROVIDER=openrouter`, chat falls back to `scoring_model()` — a smaller model — unless `CHAT_MODEL` is explicitly set. Switching chat to DeepSeek today would also flip scoring to OpenRouter.

**Fix:** Add `OPENROUTER_CHAT_MODEL` config field and `chat_model()` routing function. Chat agent resolves its own model independently of scoring/report.

**`config.py`** — add field:
```python
OPENROUTER_CHAT_MODEL: str = "deepseek/deepseek-chat"
```

**`llm_router.py`** — add function:
```python
def chat_model() -> str:
    """Model for the conversational chat agent."""
    return getattr(SETTINGS, "OPENROUTER_CHAT_MODEL", "deepseek/deepseek-chat") \
        if is_openrouter() else getattr(SETTINGS, "REPORT_MODEL", "")
```

**`agents/chat/agent.py`** `build_chat_agent()` — update model resolution:
```python
# Before:
model=getattr(SETTINGS, "CHAT_MODEL", "") or SETTINGS.REPORT_MODEL,

# After:
model=getattr(SETTINGS, "CHAT_MODEL", "") or llm_router.chat_model(),
```

**`.env` for DeepSeek:**
```
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<key>
OPENROUTER_CHAT_MODEL=deepseek/deepseek-chat
# OPENROUTER_SCORING_MODEL and OPENROUTER_REPORT_MODEL set independently
```

---

### Area 2: System Prompt Rewrite

**File:** `agents/chat/agent.py` — `_SYSTEM_PROMPT`

The rewrite preserves all existing tool descriptions and answer-style rules. It adds five structural sections at the top under a **Research Protocol** header.

#### 2a — Planning block (before first tool call)

> Before calling any tool, identify:
> - What data do I need to fully answer this question?
> - Which tools will I call, in what order?
> State this plan mentally, then execute it step by step.

#### 2b — Post-tool evaluation gate (after every tool result)

> After each tool result, ask:
> - What did I learn?
> - Do I now have everything needed to answer completely?
> - If not, what is still missing — and which tool provides it?
> Only emit a final reply when no data gaps remain.

#### 2c — Hard constraint: symbol format

> NEVER pass a company name to `live_quote` or `timing`.
> Always use NSE ticker format (e.g. `RELIANCE`, not `Reliance Industries`).
>
> Resolution order:
> 1. Call `screen_snapshot(name="<company name>")` to get the NSE symbol.
> 2. If no match (stock outside scored universe): use the known NSE symbol from training knowledge, proceed with `live_quote`/`timing`, and tell the user "not in today's scored universe."

#### 2d — Hard constraint: entry/exit questions

> For any question about buy zone, entry, stop loss, or target:
> - First call `screen_snapshot` to get the shortlist.
> - Then call `timing(<symbol>)` for EACH shortlisted stock.
> - Only compose the final answer after all `timing` calls are complete.
> Never answer an entry/exit question without `timing` data.

#### 2e — Citation rules (per data source)

| Data source | Required citation |
|---|---|
| `macro_search` result | URL + fetch date from result |
| `fetch_news` headline | Headline text + URL + publication date |
| `screen_snapshot` score/fundamentals | "as of {as_of date}" |
| `timing` technicals | "from OHLCV data" |
| Training knowledge (fallback) | "from general knowledge — verify on NSE/BSE" |

> Never assert a factual claim without citing which tool result or data source it came from.

---

### Area 3: News URL Capture

**Problem:** `enrichment/news_fetcher._rss_items()` parses RSS `<title>` and `<pubDate>` but drops `<link>`. Headlines reach the chat agent as bare strings with no URL.

**Fix:** Dual-key backward-compat approach. Keeps `headlines: [str]` (consumed by scoring pipeline — no breaking change). Adds `headline_items: [{"text": str, "url": str, "date": str|None}]` (consumed by chat tools for citations).

#### `enrichment/news_fetcher.py`

`_rss_items()` — add `url` to each item:
```python
# Before:
items.append({"title": title, "ts": _parse_pubdate(...)})

# After:
url = item.findtext("link", "").strip()
items.append({"title": title, "url": url, "ts": _parse_pubdate(...)})
```

`_fetch_via_rss()` — emit both keys:
```python
# Build headline_items alongside headlines
headline_items = []
for it in prep(results_items) + prep(general_items):
    norm = re.sub(r"\W+", " ", it["title"].lower()).strip()
    if norm in seen:
        continue
    seen.add(norm)
    date_str = it["ts"].date().isoformat() if it.get("ts") else None
    headlines.append(it["title"])
    headline_items.append({"text": it["title"], "url": it.get("url", ""), "date": date_str})
    if len(headlines) == _MAX_HEADLINES:
        break

return {"headlines": headlines, "headline_items": headline_items, "sentiment": "neutral"}
```

#### `agents/chat/tools.py`

`_trim()` — surface `headline_items` from snapshot rows:
```python
"news": (row.get("headline_items") or row.get("news") or [])[:3],
```

`fetch_news` tool — return structured items:
```python
"news": {sym: (v or {}).get("headline_items") or
              [{"text": h} for h in (v or {}).get("headlines", [])]
         for sym, v in news.items()},
```

**Snapshot pipeline:** No explicit change needed. The daily enrichment pipeline calls `fetch_news_batch` → stores enriched stock dict including `headline_items`. Future daily snapshots automatically include URLs. Existing snapshots without `headline_items` fall back to bare `headlines` strings via the `or` chain in `_trim()`.

---

## Data Flow After Fix

```
User: "top 3 banking stocks to buy tomorrow"

Agent plan:  screen_snapshot → timing(x3) → compose

Step 1: screen_snapshot(sector="banking", min_score=6, limit=5)
        ↳ result: HDFCBANK (8.2), ICICIBANK (7.8), KOTAKBANK (7.1)
        ↳ eval: "have shortlist, need entry zones — call timing x3"

Step 2: timing("HDFCBANK")
        ↳ result: RSI=52, 52w_pos=68%, breakout=false, support=1680
        ↳ eval: "1 of 3 done, still need ICICI and KOTAK"

Step 3: timing("ICICIBANK")
        ↳ eval: "2 of 3, need KOTAK"

Step 4: timing("KOTAKBANK")
        ↳ eval: "have all timing data — compose final answer"

Step 5: Final reply with entry zone + stop + target per stock
        Citations: "scores as of 2026-06-27 | technicals from OHLCV data"
```

---

## Files Changed

| File | Change |
|---|---|
| `config.py` | Add `OPENROUTER_CHAT_MODEL` field |
| `llm_router.py` | Add `chat_model()` function |
| `agents/chat/agent.py` | Update model resolution + rewrite `_SYSTEM_PROMPT` |
| `enrichment/news_fetcher.py` | Parse `<link>` in `_rss_items`; emit `headline_items` in `_fetch_via_rss` |
| `agents/chat/tools.py` | `_trim()` surfaces `headline_items`; `fetch_news` returns structured items |

## Testing

- Unit: `tests/test_chat_tools.py` — verify `fetch_news` returns `headline_items` with url/date fields
- Unit: `tests/test_news_fetcher.py` (if exists) — verify `_rss_items` includes `url` key
- Manual: ask "top 3 banking stocks with entry zone" → verify agent calls `screen_snapshot` then `timing` × 3
- Manual: ask macro question → verify source URLs appear in reply
- Manual: set `LLM_PROVIDER=openrouter`, `OPENROUTER_CHAT_MODEL=deepseek/deepseek-chat` → verify chat uses DeepSeek

## Risks

- **DeepSeek tool-calling reliability:** DeepSeek-V3 supports OpenAI-compatible function calling but is less battle-tested than Sonnet for ReAct loops. If tool dispatch fails, fall back by setting `CHAT_MODEL=claude-sonnet-4-6` (Anthropic provider) or `OPENROUTER_CHAT_MODEL=anthropic/claude-sonnet-4-5`.
- **RSS `<link>` element format:** Google News RSS `<link>` is a redirect URL (`news.google.com/...`), not the original article URL. Citations will point to the Google News redirect, not the publisher directly. Acceptable for now.
- **Backward compat:** `headline_items` is additive. Existing code consuming `headlines: [str]` is unaffected.
