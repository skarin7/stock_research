# Observability & Logging Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON logging + Langfuse spans across the full agent stack so every chat turn, tool call, intent decision, pipeline node, and batch scoring run is fully observable.

**Architecture:** Two new `observability/` modules (`logging_config.py`, `chat_tracing.py`) provide a JSON formatter and a `trace_tool()` context manager. All 9 chat tools are wrapped with `trace_tool()` and return a `_source` field. The `agent_node` decorator is extended with `trace_node()` spans. Batch scoring gets an explicit `langfuse.trace()` call. `setup_logging()` is wired into all three entrypoints.

**Verification:** Run existing tests (no regressions) + manual end-to-end via docker logs and Langfuse UI.

**Tech Stack:** Python `logging` stdlib, `langfuse` SDK (already in requirements)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| **Create** | `observability/logging_config.py` | JSON formatter + `setup_logging()` |
| **Create** | `observability/chat_tracing.py` | `trace_tool()`, `trace_node()`, `trace_intent()` |
| **Modify** | `server/app.py` | Call `setup_logging()`, full-text log, turn timing |
| **Modify** | `agents/chat/intent.py` | Call `trace_intent()` on every routing outcome |
| **Modify** | `agents/chat/tools.py` | Wrap all 9 tools with `trace_tool()`, add `_source` |
| **Modify** | `agents/nodes/base.py` | Extend `agent_node` with `trace_node()` spans |
| **Modify** | `scoring/claude_scorer.py` | Add `langfuse.trace()` around batch scoring path |
| **Modify** | `run_agents.py` | Replace `logging.basicConfig()` with `setup_logging()` |
| **Modify** | `run_intraday.py` | Add `setup_logging()` call |

---

### Task 1: JSON Logging Infrastructure

**Files:**
- Create: `observability/logging_config.py`

- [ ] **Step 1: Create `observability/logging_config.py`**

```python
"""Structured JSON logging — call setup_logging() once at process start."""
from __future__ import annotations

import json
import logging
from typing import Any

# Standard LogRecord attributes — exclude from the JSON extra fields
_RECORD_ATTRS = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs", "msg",
    "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "taskName",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        out: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in _RECORD_ATTRS and not k.startswith("_"):
                out[k] = v
        return json.dumps(out, default=str)


_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output. Idempotent — subsequent calls are no-ops."""
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
```

- [ ] **Step 2: Commit**

```bash
git add observability/logging_config.py
git commit -m "feat(observability): add JSON logging formatter and setup_logging()"
```

---

### Task 2: Tracing Helpers Module

**Files:**
- Create: `observability/chat_tracing.py`

- [ ] **Step 1: Create `observability/chat_tracing.py`**

```python
"""Instrumentation helpers: trace_tool(), trace_node(), trace_intent().

All helpers are fail-safe — any internal error is swallowed so instrumentation
never breaks the agent flow. Langfuse spans are no-ops when keys are unset.
"""
from __future__ import annotations

import contextlib
import logging
import time

logger = logging.getLogger("observability.tracing")

_lf_client = None
_lf_resolved = False


def _get_langfuse():
    """Singleton Langfuse client, or None when not configured."""
    global _lf_client, _lf_resolved
    if _lf_resolved:
        return _lf_client
    _lf_resolved = True
    try:
        from config import SETTINGS
        if SETTINGS.LANGFUSE_PUBLIC_KEY and SETTINGS.LANGFUSE_SECRET_KEY:
            from langfuse import Langfuse
            _lf_client = Langfuse(
                public_key=SETTINGS.LANGFUSE_PUBLIC_KEY,
                secret_key=SETTINGS.LANGFUSE_SECRET_KEY,
                host=SETTINGS.LANGFUSE_HOST,
            )
    except Exception:
        pass
    return _lf_client


def _summarize(result: dict) -> str:
    if not isinstance(result, dict):
        return "ok"
    if "error" in result:
        return f"error: {result['error']}"
    if "stocks" in result:
        return f"{len(result['stocks'])} stocks"
    if "quotes" in result:
        return f"{len(result['quotes'])} quotes"
    if "news" in result:
        return f"{len(result['news'])} symbols"
    if "scores" in result:
        return f"{len(result['scores'])} scores"
    if "past_calls" in result:
        return f"{len(result['past_calls'])} past calls"
    return "ok"


class _Span:
    def __init__(self, real=None):
        self._real = real
        self.output: dict = {}

    def set_output(self, data: dict) -> None:
        self.output = data or {}
        if self._real is not None:
            try:
                self._real.update(output=data)
            except Exception:
                pass

    def _end(self, error: str | None = None) -> None:
        if self._real is not None:
            try:
                if error:
                    self._real.update(level="ERROR", status_message=error)
                self._real.end()
            except Exception:
                pass


@contextlib.contextmanager
def trace_tool(name: str, args: dict):
    """Context manager — wraps a single tool call with JSON log + Langfuse span.

    Usage::

        with trace_tool("live_quote", {"symbols": ["ABB"]}) as span:
            result = _fetch(symbols)
            span.set_output(result)
        return result
    """
    t0 = time.monotonic()
    real_span = None
    lf = _get_langfuse()
    if lf is not None:
        try:
            trace = lf.trace(name=f"tool:{name}")
            real_span = trace.span(name=name, input=args)
        except Exception:
            pass
    span = _Span(real_span)

    logger.info("tool_start", extra={"tool": name, "args": args})
    try:
        yield span
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.error(
            "tool_error",
            extra={"tool": name, "args": args, "error": str(exc), "duration_ms": duration_ms},
        )
        span._end(error=str(exc))
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "tool_call",
            extra={
                "tool": name,
                "args": args,
                "result_summary": _summarize(span.output),
                "_source": span.output.get("_source", "unknown"),
                "duration_ms": duration_ms,
            },
        )
        span._end()


@contextlib.contextmanager
def trace_node(name: str, run_id: str, input_summary: dict):
    """Context manager — wraps a pipeline node execution."""
    t0 = time.monotonic()
    real_span = None
    lf = _get_langfuse()
    if lf is not None:
        try:
            trace = lf.trace(name=f"run:{run_id}", id=run_id)
            real_span = trace.span(name=f"node:{name}", input=input_summary)
        except Exception:
            pass
    span = _Span(real_span)

    logger.info("node_start", extra={"node": name, "run_id": run_id, **input_summary})
    try:
        yield span
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.error(
            "node_error",
            extra={"node": name, "run_id": run_id, "error": str(exc), "duration_ms": duration_ms},
        )
        span._end(error=str(exc))
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "node_done",
            extra={"node": name, "run_id": run_id, "duration_ms": duration_ms, **span.output},
        )
        span._end()


def trace_intent(route: str, intent: str, score: float, confidence: float | None = None) -> None:
    """Log an intent routing decision. Never raises."""
    try:
        extra: dict = {"route": route, "intent": intent, "top_score": round(score, 3)}
        if confidence is not None:
            extra["confidence"] = round(confidence, 3)
        logger.info("intent_routed", extra=extra)
    except Exception:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add observability/chat_tracing.py
git commit -m "feat(observability): add trace_tool/trace_node/trace_intent helpers"
```

---

### Task 3: Fix Server Webhook Logging

**Files:**
- Modify: `server/app.py`

Current problem: `logging.basicConfig()` never called → all logs silently dropped. Line 144 truncates text to 80 chars.

- [ ] **Step 1: Replace `_startup()` and update webhook handler**

Replace the existing `_startup` function:

```python
@app.on_event("startup")
async def _startup():
    from observability.logging_config import setup_logging
    setup_logging()
    from persistence.db import init_db
    init_db()
```

Replace line 144 (`logger.info("Incoming message...")`):

```python
    chat_id = str(chat_id_raw)
    logger.info("msg_received", extra={"chat_id": chat_id, "text": text, "update_id": update_id})
```

Replace the approval routing + agent call block (from `from agents.approval import route_approval` to the end of `webhook()`):

```python
    from agents.approval import route_approval

    try:
        reply = route_approval(text)
    except Exception as e:
        logger.error("approval_routing_failed", extra={"error": str(e), "chat_id": chat_id})
        reply = f"⚠️ Could not process approval: {e}"
    if reply is not None:
        logger.info("approval_routed", extra={"chat_id": chat_id, "reply_len": len(reply)})
        _send(reply, chat_id)
        return {"ok": True}

    try:
        _send("🔎 Researching…", chat_id)
    except Exception as e:
        logger.warning("placeholder_send_failed", extra={"error": str(e)})

    import time as _time
    from agents.chat.agent import run_turn

    t0 = _time.monotonic()
    reply = run_turn(chat_id, text)
    duration_ms = round((_time.monotonic() - t0) * 1000)
    logger.info("turn_complete", extra={
        "chat_id": chat_id,
        "duration_ms": duration_ms,
        "reply_length": len(reply),
    })
    try:
        _send(reply, chat_id)
    except Exception as e:
        logger.error("reply_send_failed", extra={"error": str(e), "chat_id": chat_id})

    return {"ok": True}
```

- [ ] **Step 2: Run existing tests to verify no regressions**

```bash
python -m pytest tests/test_webhook.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add server/app.py
git commit -m "feat(server): structured logging — full text, turn timing, approval routing"
```

---

### Task 4: Intent Routing Logging

**Files:**
- Modify: `agents/chat/intent.py`

- [ ] **Step 1: Add `trace_intent` calls to `route_intent()`**

Add import at top (after existing imports):

```python
from observability.chat_tracing import trace_intent
```

Replace the body of `route_intent()`:

```python
def route_intent(text: str) -> dict:
    """Return {intent, confidence, route}. Tier 2 semantic → tier 3 LLM fallback."""
    threshold = float(getattr(SETTINGS, "CHAT_SEMANTIC_THRESHOLD", 0.55))
    from agents.chat import embedder

    if embedder.available():
        try:
            intent, sim = embedder.nearest_intent(text)
            if sim >= threshold:
                verdict = {"intent": intent, "confidence": round(sim, 3), "route": "semantic"}
                trace_intent(route="semantic", intent=intent, score=sim)
                return verdict
            logger.debug("semantic sim %.3f < %.2f → LLM fallback", sim, threshold)
            trace_intent(route="llm_fallback", intent="", score=sim)
        except Exception as e:
            logger.warning("semantic router error (%s) — LLM fallback", e)

    verdict = classify_intent_llm(text)
    trace_intent(
        route=verdict.get("route", "llm"),
        intent=verdict.get("intent", ""),
        score=0.0,
        confidence=verdict.get("confidence"),
    )
    return verdict
```

- [ ] **Step 2: Run existing tests**

```bash
python -m pytest tests/test_chat_intent.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add agents/chat/intent.py
git commit -m "feat(intent): log routing decisions via trace_intent"
```

---

### Task 5: Wrap All 9 Chat Tools + Add `_source`

**Files:**
- Modify: `agents/chat/tools.py`

The `_source` field answers "was this data from cache or a live API call?" and appears in every tool's return dict.

- [ ] **Step 1: Add import**

Add at the top of `agents/chat/tools.py` (after existing imports):

```python
from observability.chat_tracing import trace_tool
```

- [ ] **Step 2: Rewrite `screen_snapshot` body**

Replace the existing `try:` block inside `screen_snapshot`:

```python
    try:
        meta, rows = _snapshot_rows()
        _source = "snapshot_cache" if rows else "no_snapshot"
        if not rows:
            return {**meta, "_source": _source,
                    "error": "no snapshot available — the daily run has not produced data yet"}

        def keep(r: dict) -> bool:
            pe = r.get("pe_ratio")
            if pe_max is not None and (pe is None or pe > pe_max):
                return False
            if pe_min is not None and (pe is None or pe < pe_min):
                return False
            if sector and sector.lower() not in (r.get("sector") or "").lower():
                return False
            if min_score is not None and (r.get("composite_score") or 0) < min_score:
                return False
            if has_news and not r.get("news"):
                return False
            return True

        matched = [r for r in rows if keep(r)]
        if sort_by == "pe_ratio":
            matched.sort(key=lambda r: r.get("pe_ratio") if r.get("pe_ratio") is not None else 1e9)
        elif sort_by == "market_cap_cr":
            matched.sort(key=lambda r: r.get("market_cap_cr") or 0, reverse=True)
        else:
            matched.sort(key=lambda r: r.get("composite_score") or 0, reverse=True)

        limit = max(1, min(int(limit), 25))
        with trace_tool("screen_snapshot", {"sector": sector, "min_score": min_score, "limit": limit}) as span:
            result = {**meta, "_source": _source,
                      "total_matched": len(matched), "stocks": [_trim(r) for r in matched[:limit]]}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("screen_snapshot failed")
        return {"error": str(e), "_source": "snapshot_cache"}
```

- [ ] **Step 3: Rewrite `live_quote` body**

```python
    try:
        from enrichment.market_data import get_default_provider

        provider = get_default_provider()
        with trace_tool("live_quote", {"symbols": symbols[:_MAX_QUOTE_SYMBOLS]}) as span:
            out = {}
            for sym in symbols[:_MAX_QUOTE_SYMBOLS]:
                try:
                    out[sym.upper()] = provider.get_quote(sym.upper()) or {"error": "no data"}
                except Exception as e:
                    out[sym.upper()] = {"error": str(e)}
            result = {"quotes": out, "_source": "live_api"}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("live_quote failed")
        return {"error": str(e), "_source": "live_api"}
```

- [ ] **Step 4: Rewrite `fetch_news` body**

```python
    try:
        from enrichment.news_fetcher import fetch_news_batch

        meta, rows = _snapshot_rows(symbols)
        company = {r["symbol"]: r.get("company", "") for r in rows if r.get("symbol")}
        stocks = [{"symbol": s.upper(), "company": company.get(s.upper(), "")}
                  for s in symbols[:_MAX_NEWS_SYMBOLS]]
        with trace_tool("fetch_news", {"symbols": [s["symbol"] for s in stocks]}) as span:
            news = fetch_news_batch(stocks)
            result = {
                "news": {sym: (v or {}).get("headlines", []) for sym, v in news.items()},
                "_source": "google_news_rss",
            }
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("fetch_news failed")
        return {"error": str(e), "_source": "google_news_rss"}
```

- [ ] **Step 5: Rewrite `score_subset` body**

Replace only the return dict and exception return in the existing try/except — add `_source` and wrap the expensive section:

```python
        with trace_tool("score_subset", {"symbols": [s["symbol"] for s in stocks]}) as span:
            news_map = fetch_news_batch(stocks)
            scored = score_stocks(stocks, news_map)
            ranked = rank_stocks(scored, top_n=len(scored))
            result = {
                **meta,
                "_source": "claude_haiku_sync",
                "scores": [
                    {"ticker": c.get("ticker"), "composite_score": c.get("composite_score"),
                     "rationale": c.get("investment_rationale", "")[:300],
                     "risk_flags": (c.get("risk_flags") or [])[:3]}
                    for c in ranked
                ],
            }
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("score_subset failed")
        return {"error": str(e), "_source": "claude_haiku_sync"}
```

- [ ] **Step 6: Rewrite `deep_dive` return + add `_source`**

Replace only the `return` dict and exception return:

```python
        with trace_tool("deep_dive", {"ticker": ticker}) as span:
            result_dict = {
                **meta,
                "_source": "debate_subgraph",
                "ticker": ticker,
                "direction": conv.get("direction", "neutral"),
                "conviction": conv.get("conviction", 0.0),
                "bull_case": result.get("bull_case", ""),
                "bear_case": result.get("bear_case", ""),
            }
            span.set_output(result_dict)
        return result_dict
    except Exception as e:
        logger.exception("deep_dive failed")
        return {"error": str(e), "_source": "debate_subgraph"}
```

- [ ] **Step 7: Rewrite `get_portfolio` body**

```python
    try:
        from persistence import store

        with trace_tool("get_portfolio", {}) as span:
            book = store.recompute(store.load_portfolio())
            result = json.loads(book.model_dump_json())
            result["_source"] = "portfolio_store"
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("get_portfolio failed")
        return {"error": str(e), "_source": "portfolio_store"}
```

- [ ] **Step 8: Rewrite `macro_search` body**

```python
    api_key = getattr(SETTINGS, "TAVILY_API_KEY", "")
    if not api_key:
        return {"error": "macro search is not configured (set TAVILY_API_KEY)", "_source": "unavailable"}
    try:
        import requests

        max_results = int(getattr(SETTINGS, "MACRO_SEARCH_MAX_RESULTS", 5))
        with trace_tool("macro_search", {"query": query}) as span:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "search_depth": "basic",
                      "max_results": max_results, "include_answer": True},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = [{"title": r.get("title"), "url": r.get("url"),
                        "snippet": r.get("content")}
                       for r in (data.get("results") or [])[:max_results]]
            result = {"answer": data.get("answer") or "", "results": results,
                      "fetched_at": date.today().isoformat(), "_source": "tavily_api"}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("macro_search failed")
        return {"error": str(e), "_source": "tavily_api"}
```

- [ ] **Step 9: Rewrite `timing` return + add `_source`**

Replace only the final `return` dict and exception return:

```python
        with trace_tool("timing", {"ticker": ticker}) as span:
            result = {
                "_source": "intraday_technicals",
                "ticker": ticker,
                "ltp": ltp,
                "rsi14": m.get("rsi14"),
                "pct_52w_position": pct_pos,
                "near_52w_high": None if pct_pos is None else pct_pos >= 0.95,
                "near_52w_low": None if pct_pos is None else pct_pos <= 0.05,
                "breakout_20d": breakout,
                "mom_5d": pct_change_ndays(closes, 5),
                "mom_20d": pct_change_ndays(closes, 20),
                "support": support,
                "resistance": high_20d,
            }
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("timing failed")
        return {"error": str(e), "_source": "intraday_technicals"}
```

- [ ] **Step 10: Rewrite `recall` return + add `_source`**

```python
    try:
        from persistence import store

        with trace_tool("recall", {"ticker": ticker}) as span:
            ticker = ticker.upper()
            calls = store.recent_calls(ticker) or []
            trimmed = [{k: c.get(k) for k in _RECALL_FIELDS if k in c} for c in calls]
            result = {"ticker": ticker, "past_calls": trimmed, "_source": "memory_store"}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("recall failed")
        return {"error": str(e), "_source": "memory_store"}
```

- [ ] **Step 11: Run existing tests**

```bash
python -m pytest tests/test_chat_tools.py -v
```

Expected: all pass

- [ ] **Step 12: Commit**

```bash
git add agents/chat/tools.py
git commit -m "feat(tools): wrap all 9 chat tools with trace_tool + add _source provenance field"
```

---

### Task 6: Pipeline Node Spans

**Files:**
- Modify: `agents/nodes/base.py`

- [ ] **Step 1: Update `agents/nodes/base.py`**

Add import (after existing imports):

```python
from observability.chat_tracing import trace_node
```

Replace the `wrapper` function inside `agent_node`:

```python
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

            if enabled_flag is not None and not getattr(SETTINGS, enabled_flag, False):
                logger.info("[%s] disabled (%s=False) — skipping", name, enabled_flag)
                return {"audit": [audit_entry(name, state.get("status"), state.get("status"), "skipped (disabled)")]}

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
```

- [ ] **Step 2: Run existing tests**

```bash
python -m pytest tests/test_graph.py tests/test_debate.py tests/test_risk_portfolio.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add agents/nodes/base.py
git commit -m "feat(nodes): add Langfuse node spans via trace_node in agent_node decorator"
```

---

### Task 7: Batch Scoring Langfuse Trace

**Files:**
- Modify: `scoring/claude_scorer.py`

- [ ] **Step 1: Add import**

Add after existing imports in `scoring/claude_scorer.py`:

```python
from observability.chat_tracing import _get_langfuse
```

- [ ] **Step 2: Add trace at start of batch path in `score_stocks()`**

In `score_stocks()`, directly after `logger.info("Scoring %d stocks via Batch API...")`, add:

```python
    _lf = _get_langfuse()
    _lf_trace = None
    if _lf is not None:
        try:
            _lf_trace = _lf.trace(
                name="batch_scoring",
                metadata={
                    "model": SETTINGS.SCORING_MODEL,
                    "stock_count": len(stocks),
                    "batch_count": len(batches),
                    "estimated_cost_usd": round(len(stocks) * 0.00025 * 0.5, 4),
                },
            )
        except Exception:
            pass
```

After the batch loop completes (before `return all_scores`), add:

```python
    if _lf_trace is not None:
        try:
            _lf_trace.update(output={"scored": len(all_scores), "total": len(stocks)})
        except Exception:
            pass
```

- [ ] **Step 3: Run existing tests**

```bash
python -m pytest tests/test_scorer.py -v
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add scoring/claude_scorer.py
git commit -m "feat(scorer): add Langfuse trace for batch scoring path"
```

---

### Task 8: Wire `setup_logging()` into Entrypoints

**Files:**
- Modify: `run_agents.py`
- Modify: `run_intraday.py`

- [ ] **Step 1: Update `run_agents.py`**

Replace the existing `logging.basicConfig(...)` block (lines 22-26) with:

```python
from observability.logging_config import setup_logging
setup_logging()
```

- [ ] **Step 2: Update `run_intraday.py`**

Find the `logging.basicConfig()` call (or the top of the main block) and replace/add:

```python
from observability.logging_config import setup_logging
setup_logging()
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add run_agents.py run_intraday.py
git commit -m "feat: wire setup_logging() into run_agents and run_intraday entrypoints"
```

---

### Task 9: End-to-End Verification

- [ ] **Step 1: Start the stack**

```bash
docker compose -f deploy/docker-compose.obs.yml up -d
python scripts/run_chat_local.py
```

- [ ] **Step 2: Send "what is ABB last traded price" via Telegram**

Check stdout. Expect these JSON lines in order:

```json
{"level":"INFO","logger":"server.app","msg":"msg_received","chat_id":"...","text":"what is ABB last traded price","update_id":...}
{"level":"INFO","logger":"observability.tracing","msg":"intent_routed","route":"semantic","intent":"live_price","top_score":0.9}
{"level":"INFO","logger":"observability.tracing","msg":"tool_start","tool":"live_quote","args":{"symbols":["ABB"]}}
{"level":"INFO","logger":"observability.tracing","msg":"tool_call","tool":"live_quote","_source":"live_api","duration_ms":43}
{"level":"INFO","logger":"server.app","msg":"turn_complete","chat_id":"...","duration_ms":1240,"reply_length":87}
```

- [ ] **Step 3: Verify `_source` distinction**

Send "show me top IT stocks". Verify `_source` is `"snapshot_cache"` (not `"live_api"`).

- [ ] **Step 4: Check Langfuse UI**

Open `http://localhost:3000` → Traces. Expect `tool:live_quote` trace with input/output including `_source: live_api`.

- [ ] **Step 5: Verify daily pipeline**

```bash
python run_agents.py --mode research --dry-run
```

Check stdout for `node_start` / `node_done` JSON lines for each pipeline node.
