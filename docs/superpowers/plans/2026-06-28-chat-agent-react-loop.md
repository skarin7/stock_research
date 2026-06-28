# Chat Agent: Multi-Step ReAct Loop + DeepSeek + Citation Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat agent reliably chain multiple tool calls per turn, route chat through DeepSeek-V3 (cheaper), and cite sources for every factual claim.

**Architecture:** Three independent areas — (1) add `OPENROUTER_CHAT_MODEL` config + `chat_model()` routing so the chat model is decoupled from scoring/report; (2) rewrite `_SYSTEM_PROMPT` with explicit ReAct planning, post-tool evaluation, hard sequencing constraints, and citation rules; (3) parse RSS `<link>` URLs in `news_fetcher` and surface them through the chat tools using a backward-compat dual-key pattern.

**Tech Stack:** Python, LangGraph `create_react_agent`, `settings.py` dataclass config, `xml.etree.ElementTree`, `langchain_core.tools`

---

## File Map

| File | What changes |
|---|---|
| `settings.py` | Add `OPENROUTER_CHAT_MODEL` field + `from_env()` entry |
| `llm_router.py` | Add `chat_model()` function |
| `agents/chat/agent.py` | Import `llm_router`; update model resolution; rewrite `_SYSTEM_PROMPT` |
| `enrichment/news_fetcher.py` | Parse `<link>` in `_rss_items`; emit `headline_items` in `_fetch_via_rss` |
| `agents/chat/tools.py` | `_trim()` and `fetch_news` tool return structured items with URLs |
| `tests/test_llm_router.py` | Add `test_chat_model_openrouter` and `test_chat_model_anthropic` |
| `tests/test_chat_tools.py` | Add `test_fetch_news_returns_headline_items_with_url` |

---

## Task 1: Add OPENROUTER_CHAT_MODEL config field

**Files:**
- Modify: `settings.py:171` (field block) and `settings.py:319` (`from_env()` block)
- Test: `tests/test_llm_router.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_router.py` after the existing `test_openrouter_resolves_cheap_models` test:

```python
def test_chat_model_openrouter(monkeypatch):
    """chat_model() returns OPENROUTER_CHAT_MODEL when provider is openrouter."""
    mock_config.LLM_PROVIDER = "openrouter"
    mock_config.OPENROUTER_CHAT_MODEL = "deepseek/deepseek-chat"
    monkeypatch.setattr(llm_router, "SETTINGS", mock_config)
    assert llm_router.chat_model() == "deepseek/deepseek-chat"
    mock_config.LLM_PROVIDER = "anthropic"  # restore


def test_chat_model_anthropic(monkeypatch):
    """chat_model() returns REPORT_MODEL when provider is anthropic."""
    mock_config.LLM_PROVIDER = "anthropic"
    mock_config.REPORT_MODEL = "claude-sonnet-4-6"
    monkeypatch.setattr(llm_router, "SETTINGS", mock_config)
    assert llm_router.chat_model() == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_llm_router.py::test_chat_model_openrouter tests/test_llm_router.py::test_chat_model_anthropic -v
```

Expected: `FAILED` — `AttributeError: module 'llm_router' has no attribute 'chat_model'`

- [ ] **Step 3: Add `OPENROUTER_CHAT_MODEL` to `settings.py`**

In `settings.py`, after line 171 (`OPENROUTER_REPORT_MODEL: str = "deepseek/deepseek-chat"`):

```python
    OPENROUTER_CHAT_MODEL: str = "deepseek/deepseek-chat"
```

In `settings.py` `from_env()`, after the `OPENROUTER_REPORT_MODEL=...` line (~line 319):

```python
            OPENROUTER_CHAT_MODEL=os.environ.get("OPENROUTER_CHAT_MODEL", "deepseek/deepseek-chat"),
```

- [ ] **Step 4: Add `chat_model()` to `llm_router.py`**

In `llm_router.py`, after the `report_model()` function:

```python
def chat_model() -> str:
    """Model for the conversational chat agent."""
    return getattr(SETTINGS, "OPENROUTER_CHAT_MODEL", "deepseek/deepseek-chat") \
        if is_openrouter() else getattr(SETTINGS, "REPORT_MODEL", "")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_llm_router.py -v
```

Expected: all pass including two new tests.

- [ ] **Step 6: Commit**

```bash
git add settings.py llm_router.py tests/test_llm_router.py
git commit -m "feat(config): add OPENROUTER_CHAT_MODEL setting and chat_model() router"
```

---

## Task 2: Wire chat_model() into build_chat_agent()

**Files:**
- Modify: `agents/chat/agent.py:72-76`

- [ ] **Step 1: Update model resolution in `build_chat_agent()`**

In `agents/chat/agent.py`, add `import llm_router` at the top of `build_chat_agent()` (or at module level alongside the other imports). Then change:

```python
# Before (around line 73):
    model = get_chat_model(
        model=getattr(SETTINGS, "CHAT_MODEL", "") or SETTINGS.REPORT_MODEL,
        max_tokens=2048,
        temperature=0.3,
    )
```

```python
# After:
    import llm_router as _llm_router
    model = get_chat_model(
        model=getattr(SETTINGS, "CHAT_MODEL", "") or _llm_router.chat_model(),
        max_tokens=2048,
        temperature=0.3,
    )
```

- [ ] **Step 2: Verify no import errors**

```bash
python -c "from agents.chat.agent import build_chat_agent; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add agents/chat/agent.py
git commit -m "feat(chat): route chat model through llm_router.chat_model()"
```

---

## Task 3: Rewrite _SYSTEM_PROMPT

**Files:**
- Modify: `agents/chat/agent.py:20-58`

The rewrite adds a **Research Protocol** block at the top. All existing content is preserved and reorganised below it.

- [ ] **Step 1: Replace `_SYSTEM_PROMPT` in `agents/chat/agent.py`**

Replace lines 20–58 (the entire `_SYSTEM_PROMPT` string) with:

```python
_SYSTEM_PROMPT = """You are an equity research assistant for Indian (NSE/BSE) markets, \
chatting with your user on Telegram.

## Research Protocol (follow for every question)

**Step 1 — Plan before acting:**
Before calling any tool, identify:
- What data do I need to fully answer this question?
- Which tools will I call, in what order?
Then execute that plan step by step. Never call a tool before planning.

**Step 2 — Evaluate after every tool result:**
After each tool result, ask:
- What did I learn?
- Do I now have everything needed to answer completely?
- If not, what is still missing and which tool provides it?
Only emit a final reply when no data gaps remain.

## Hard Constraints

**Symbol format — NEVER pass company names to live_quote or timing:**
Always use NSE ticker format (RELIANCE, not "Reliance Industries").
Resolution order:
1. Call screen_snapshot(name="<company name>") to get the NSE symbol.
2. If no match (stock outside scored universe): use the NSE symbol from training knowledge, \
call live_quote/timing directly, and tell the user: \
"Not in today's scored universe — from general knowledge: <symbol>. Please verify on NSE/BSE."
Note: BSE numeric codes are not in any tool — answer those from training knowledge with the disclaimer.

**Entry/exit questions — MUST call timing() for each shortlisted stock:**
For any question about buy zone, entry, stop loss, or target:
1. Call screen_snapshot to get the shortlist.
2. Call timing(<symbol>) for EACH shortlisted stock.
3. Only compose the final answer after ALL timing calls are complete.
Never answer an entry/exit question without timing data.

## Citation Rules

Every factual claim must cite its source:
- macro_search result → include URL and fetch date from the result
- fetch_news headline → include headline text, URL, and publication date
- screen_snapshot score/fundamentals → state "as of <as_of date>"
- timing technicals → state "from OHLCV data"
- Training knowledge fallback → state "from general knowledge — verify on NSE/BSE directly"

Never assert a factual claim without citing which tool result or data source it came from.

## Data Discipline

- Start with screen_snapshot (cached nightly scored universe) to find candidates; \
use live_quote / fetch_news only on the shortlist for freshness.
- Use score_subset only when fresh scores genuinely change the answer (it costs money). \
deep_dive is for one named stock the user wants examined closely — at most once per question.
- For growth/performance questions over a period ("which stocks grew last month", \
"Reliance return since Jun 20"), check the time_context hint in the message — \
if lookback_days or date_from/date_to are given, call \
historical_performance(symbols, from_date, to_date) with those dates. \
For a specific past date's snapshot, call screen_snapshot(as_of="YYYY-MM-DD").
- For current events / geopolitics / macro (e.g. "impact of the Iran war"), call \
macro_search(query) to get grounded facts, map the event to sectors, then screen_snapshot \
on those sectors to name the affected stocks.
- Use recall(ticker) when the user asks what you thought of a stock before.
- Always state the snapshot as-of date, and warn clearly when data is flagged stale.
- If a tool returns an error, say what data was unavailable and answer with what you have.

## Answer Style

- Telegram HTML only: <b>bold</b>, <i>italic</i> — no markdown, no tables, no headers.
- Be concise: a ranked shortlist with one-line reasons beats an essay. Keep replies under \
3000 characters.
- You give research and analysis, not guaranteed outcomes. No "sure-shot"/"guaranteed" \
language; mention key risks when recommending.
- You cannot place orders. If asked to trade, say order placement is not enabled from chat yet."""
```

- [ ] **Step 2: Verify the module loads**

```bash
python -c "from agents.chat.agent import _SYSTEM_PROMPT; print(len(_SYSTEM_PROMPT), 'chars')"
```

Expected: prints a char count > 2000, no errors.

- [ ] **Step 3: Commit**

```bash
git add agents/chat/agent.py
git commit -m "feat(chat): rewrite system prompt with ReAct planning gate, hard constraints, citation rules"
```

---

## Task 4: Parse RSS `<link>` URLs in news_fetcher

**Files:**
- Modify: `enrichment/news_fetcher.py:61-110`

- [ ] **Step 1: Update `_rss_items()` to capture `<link>`**

In `enrichment/news_fetcher.py`, replace the `items.append(...)` line inside `_rss_items()`:

```python
# Before (line 79):
        items.append({"title": title, "ts": _parse_pubdate(item.findtext("pubDate", ""))})
```

```python
# After:
        url = item.findtext("link", "").strip()
        items.append({"title": title, "url": url, "ts": _parse_pubdate(item.findtext("pubDate", ""))})
```

Also update the docstring on line 62-63:

```python
def _rss_items(query: str) -> list[dict]:
    """Fetch RSS items for a query, filtered to trusted sources. Each item is
    {"title": str, "url": str, "ts": datetime|None}."""
```

- [ ] **Step 2: Update `_fetch_via_rss()` to emit `headline_items`**

In `enrichment/news_fetcher.py`, replace the loop body and return in `_fetch_via_rss()`:

```python
# Before (lines 100-110):
    # results headlines take priority, then general coverage
    seen, headlines = set(), []
    for it in prep(results_items) + prep(general_items):
        norm = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if norm in seen:
            continue
        seen.add(norm)
        headlines.append(it["title"])
        if len(headlines) == _MAX_HEADLINES:
            break

    return {"headlines": headlines, "sentiment": "neutral"}
```

```python
# After:
    # results headlines take priority, then general coverage
    seen, headlines, headline_items = set(), [], []
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

Also update `_EMPTY` at line 27 to include the new key:

```python
# Before:
_EMPTY = {"headlines": [], "sentiment": "neutral"}

# After:
_EMPTY = {"headlines": [], "headline_items": [], "sentiment": "neutral"}
```

- [ ] **Step 3: Verify the module loads and existing tests pass**

```bash
python -m pytest tests/ -k "news or fetcher" -v 2>/dev/null || python -c "from enrichment.news_fetcher import fetch_news, _rss_items, _fetch_via_rss; print('ok')"
```

Expected: `ok` (or existing news-related tests pass)

- [ ] **Step 4: Commit**

```bash
git add enrichment/news_fetcher.py
git commit -m "feat(news): parse RSS <link> URLs; emit headline_items with text/url/date alongside headlines"
```

---

## Task 5: Surface headline_items through chat tools

**Files:**
- Modify: `agents/chat/tools.py` — `_trim()` and `fetch_news` tool

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chat_tools.py` after `test_fetch_news_uses_snapshot_company_names`:

```python
def test_fetch_news_returns_headline_items_with_url(monkeypatch):
    """fetch_news tool returns structured headline_items when news_fetcher provides them."""
    def fake_batch(stocks):
        sym = stocks[0]["symbol"]
        return {sym: {
            "headlines": ["HDFC profit rises"],
            "headline_items": [{"text": "HDFC profit rises", "url": "https://news.google.com/x", "date": "2026-06-28"}],
            "sentiment": "neutral",
        }}

    import enrichment.news_fetcher as nf
    monkeypatch.setattr(nf, "fetch_news_batch", fake_batch)

    # Patch snapshot so fetch_news can resolve company names
    monkeypatch.setattr(store_mod, "load_latest_snapshot", lambda: ("2026-06-27", [
        {"symbol": "HDFCBANK", "company": "HDFC Bank"}
    ]))

    result = tools_mod.fetch_news.invoke({"symbols": ["HDFCBANK"]})
    items = result["news"]["HDFCBANK"]
    assert isinstance(items, list)
    assert items[0]["text"] == "HDFC profit rises"
    assert items[0]["url"] == "https://news.google.com/x"
    assert items[0]["date"] == "2026-06-28"


def test_fetch_news_falls_back_to_headlines_when_no_headline_items(monkeypatch):
    """fetch_news tool degrades gracefully when headline_items key is absent."""
    def fake_batch(stocks):
        sym = stocks[0]["symbol"]
        return {sym: {"headlines": ["HDFC profit rises"], "sentiment": "neutral"}}

    import enrichment.news_fetcher as nf
    monkeypatch.setattr(nf, "fetch_news_batch", fake_batch)
    monkeypatch.setattr(store_mod, "load_latest_snapshot", lambda: ("2026-06-27", [
        {"symbol": "HDFCBANK", "company": "HDFC Bank"}
    ]))

    result = tools_mod.fetch_news.invoke({"symbols": ["HDFCBANK"]})
    items = result["news"]["HDFCBANK"]
    assert isinstance(items, list)
    assert items[0]["text"] == "HDFC profit rises"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_chat_tools.py::test_fetch_news_returns_headline_items_with_url tests/test_chat_tools.py::test_fetch_news_falls_back_to_headlines_when_no_headline_items -v
```

Expected: `FAILED` — `KeyError` or `AssertionError` on `items[0]["text"]`

- [ ] **Step 3: Update `_trim()` in `agents/chat/tools.py`**

Replace the `"news"` line in `_trim()`:

```python
# Before:
        "news": (row.get("news") or [])[:3],
```

```python
# After:
        "news": (row.get("headline_items") or row.get("news") or [])[:3],
```

- [ ] **Step 4: Update `fetch_news` tool return in `agents/chat/tools.py`**

Inside the `fetch_news` tool function, replace the `"news"` key in the result dict:

```python
# Before (around line 181):
            result = {
                "news": {sym: (v or {}).get("headlines", []) for sym, v in news.items()},
                "_source": "google_news_rss",
            }
```

```python
# After:
            result = {
                "news": {
                    sym: (v or {}).get("headline_items") or
                         [{"text": h} for h in (v or {}).get("headlines", [])]
                    for sym, v in news.items()
                },
                "_source": "google_news_rss",
            }
```

- [ ] **Step 5: Run new tests to verify they pass**

```bash
python -m pytest tests/test_chat_tools.py::test_fetch_news_returns_headline_items_with_url tests/test_chat_tools.py::test_fetch_news_falls_back_to_headlines_when_no_headline_items -v
```

Expected: both `PASSED`

- [ ] **Step 6: Run full test suite to verify no regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add agents/chat/tools.py tests/test_chat_tools.py
git commit -m "feat(chat): surface headline_items with URLs through fetch_news tool and _trim()"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| `OPENROUTER_CHAT_MODEL` config field | Task 1 |
| `chat_model()` in llm_router | Task 1 |
| `build_chat_agent()` uses `chat_model()` | Task 2 |
| Planning block in system prompt | Task 3 |
| Post-tool evaluation gate | Task 3 |
| Hard symbol constraint (company name → NSE ticker) | Task 3 |
| Hard timing constraint for entry/exit questions | Task 3 |
| Citation rules per data source | Task 3 |
| Parse `<link>` in `_rss_items` | Task 4 |
| Emit `headline_items` dual-key in `_fetch_via_rss` | Task 4 |
| `_EMPTY` updated | Task 4 |
| `_trim()` surfaces `headline_items` | Task 5 |
| `fetch_news` tool returns structured items | Task 5 |
| Backward compat fallback for old snapshots | Task 5 step 3+4 |

**Placeholder scan:** None found. All code blocks show exact implementations.

**Type consistency:**
- `_rss_items` returns `list[dict]` with keys `{title, url, ts}` — consistent across Task 4 steps.
- `headline_items` shape `{text: str, url: str, date: str|None}` — consistent between `_fetch_via_rss` (Task 4) and `fetch_news` tool (Task 5) and tests (Task 5 step 1).
- `chat_model()` returns `str` — consistent with `get_chat_model(model=...)` which accepts `str`.

---

## Manual Verification (after all tasks complete)

**DeepSeek routing:**
```bash
LLM_PROVIDER=openrouter OPENROUTER_CHAT_MODEL=deepseek/deepseek-chat \
  python -c "
import llm_router
print('chat model:', llm_router.chat_model())
"
```
Expected: `chat model: deepseek/deepseek-chat`

**Multi-step chain test (requires live Telegram setup or run_chat_local.py):**
Ask: `"top 3 banking stocks with entry zone and stop loss"`
Expected agent trace: `screen_snapshot` → `timing` × 3 → final reply with buy zones.

**Citation check:**
Ask: `"impact of crude oil spike on Indian markets"`
Expected reply: macro answer with source URLs from `macro_search`.
