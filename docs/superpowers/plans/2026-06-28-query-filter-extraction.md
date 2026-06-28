# Query Filter Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract structured filters (date ranges, lookback periods) from natural language queries and pass them to tools so "what are stocks with growth in the past month" or "performance on Jun 20th" routes directly to the correct API/DB query instead of the agent guessing.

**Architecture:** New `agents/chat/query_filters.py` extracts a `QueryFilters` dataclass from text using pure regex (zero cost, zero latency). The result is appended to the agent's hint in `run_turn()` so the ReAct agent sees explicit structured context. Three tool changes: `screen_snapshot` gains `as_of` for historical snapshots; `timing` gains `lookback_days` override; a new `historical_performance` tool handles growth-over-period queries directly. `persistence/store.py` gains `load_snapshot_for_date()`.

**Tech Stack:** `re`, `datetime` from stdlib. No new dependencies.

---

## File Map

| File | Change |
|------|--------|
| `agents/chat/query_filters.py` | New module: `QueryFilters` dataclass + `extract_filters(text)` |
| `agents/chat/agent.py` | Wire `extract_filters()` into `run_turn()`, append structured hint |
| `agents/chat/tools.py` | Add `as_of` to `screen_snapshot`; add `lookback_days` to `timing`; add `historical_performance` tool |
| `persistence/store.py` | Add `load_snapshot_for_date(date_str)` |
| `tests/test_query_filters.py` | New test file |

---

## Task 1 — `query_filters.py`: Extract Structured Filters from Text

**Files:**
- Create: `agents/chat/query_filters.py`
- Test: `tests/test_query_filters.py` (create)

### Step 1: Write failing tests

- [ ] Create `tests/test_query_filters.py`:

```python
"""Tests for natural-language query filter extraction — zero dependencies."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.chat.query_filters import QueryFilters, extract_filters


class TestRelativeDateExtraction:
    """Relative expressions must resolve to correct lookback_days."""

    def test_last_7_days(self):
        f = extract_filters("show me stock performance last 7 days")
        assert f.lookback_days == 7
        assert f.period_label != ""

    def test_past_week(self):
        f = extract_filters("top stocks past week")
        assert f.lookback_days == 7

    def test_last_week(self):
        f = extract_filters("which stocks gained last week")
        assert f.lookback_days == 7

    def test_last_month(self):
        f = extract_filters("growth in the last month")
        assert f.lookback_days == 30

    def test_past_month(self):
        f = extract_filters("performance past month")
        assert f.lookback_days == 30

    def test_last_quarter(self):
        f = extract_filters("returns last quarter")
        assert f.lookback_days == 90

    def test_last_year(self):
        f = extract_filters("annual returns last year")
        assert f.lookback_days == 365

    def test_yesterday(self):
        f = extract_filters("what happened in markets yesterday")
        today = date.today()
        assert f.date_from == today - timedelta(days=1)
        assert f.date_to == today - timedelta(days=1)

    def test_last_n_days_numeric(self):
        f = extract_filters("show me the last 14 days performance")
        assert f.lookback_days == 14

    def test_last_n_weeks_numeric(self):
        f = extract_filters("performance over the last 3 weeks")
        assert f.lookback_days == 21

    def test_last_n_months_numeric(self):
        f = extract_filters("growth over the last 2 months")
        assert f.lookback_days == 60


class TestAbsoluteDateExtraction:
    """Specific dates must parse to the correct date object."""

    def test_iso_date(self):
        f = extract_filters("what was the market on 2026-06-20")
        assert f.date_from == date(2026, 6, 20)
        assert f.date_to == date(2026, 6, 20)

    def test_dd_mm_yyyy(self):
        f = extract_filters("performance on 20/06/2026")
        assert f.date_from == date(2026, 6, 20)

    def test_month_name_day(self):
        f = extract_filters("prices on Jun 20th")
        today = date.today()
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20

    def test_month_name_day_no_suffix(self):
        f = extract_filters("snapshot for June 20")
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20

    def test_day_month_name(self):
        f = extract_filters("data on 20 June")
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20


class TestNoFilters:
    """Queries without time references must return empty QueryFilters."""

    def test_generic_query(self):
        f = extract_filters("show me IT stocks with PE under 30")
        assert f.lookback_days is None
        assert f.date_from is None
        assert f.date_to is None
        assert f.period_label == ""

    def test_empty_string(self):
        f = extract_filters("")
        assert f.lookback_days is None

    def test_stock_name_only(self):
        f = extract_filters("What is PE ratio of TCS?")
        assert f.lookback_days is None
```

- [ ] Run to confirm failure:
```
python -m pytest tests/test_query_filters.py -v
```
Expected: FAIL — `agents.chat.query_filters` not found.

### Step 2: Create `agents/chat/query_filters.py`

- [ ] Create the file:

```python
"""Structural extraction of time/date filters from natural language queries.

Pure regex — zero LLM calls, zero latency. Handles common Indian equity
research time expressions. Returns a QueryFilters dataclass; missing fields
are None (caller treats them as "no filter applied").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30,
              "quarter": 90, "quarters": 90, "year": 365, "years": 365}

# last/past N <unit>  — e.g. "last 7 days", "past 3 weeks"
_RE_LAST_N = re.compile(
    r'\b(?:last|past|over\s+the\s+last|in\s+the\s+(?:last|past))\s+(\d+)\s+(days?|weeks?|months?|quarters?|years?)\b',
    re.IGNORECASE,
)

# last/past <word>  — e.g. "last week", "past month"
_RE_LAST_WORD = re.compile(
    r'\b(?:last|past)\s+(week|month|quarter|year)\b',
    re.IGNORECASE,
)

# yesterday
_RE_YESTERDAY = re.compile(r'\byesterday\b', re.IGNORECASE)

# ISO date: 2026-06-20
_RE_ISO = re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b')

# DD/MM/YYYY or DD-MM-YYYY
_RE_DMY = re.compile(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b')

# Jun 20th / June 20 / 20 Jun / 20 June  (with optional year)
_RE_MDY = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b',
    re.IGNORECASE,
)
_RE_DM = re.compile(
    r'\b(\d{1,2})(?:st|nd|rd|th)?\s+'
    r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'(?:\s+(\d{4}))?\b',
    re.IGNORECASE,
)


@dataclass
class QueryFilters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    lookback_days: Optional[int] = None
    period_label: str = ""

    def has_filter(self) -> bool:
        return any([self.date_from, self.date_to, self.lookback_days])

    def as_hint(self) -> str:
        """Format as a one-line context hint for the agent."""
        if not self.has_filter():
            return ""
        parts = []
        if self.period_label:
            parts.append(f"period={self.period_label!r}")
        if self.lookback_days:
            parts.append(f"lookback_days={self.lookback_days}")
        if self.date_from:
            parts.append(f"date_from={self.date_from.isoformat()}")
        if self.date_to:
            parts.append(f"date_to={self.date_to.isoformat()}")
        return "(time_context: " + ", ".join(parts) + ")"


def _resolve_year(year_str: str | None, month: int, day: int) -> int:
    """If year is missing, use current year; roll back one year if the date is in the future."""
    if year_str:
        return int(year_str)
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate > today:
        return today.year - 1
    return today.year


def extract_filters(text: str) -> QueryFilters:
    """Extract time/date filters from free-form query text.

    Returns empty QueryFilters when no time expression is found.
    Never raises — any parse error returns empty filters.
    """
    if not text:
        return QueryFilters()

    try:
        today = date.today()

        # last/past N <unit>
        m = _RE_LAST_N.search(text)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            days = n * _UNIT_DAYS.get(unit.rstrip("s") + ("s" if not unit.endswith("s") else ""),
                                      _UNIT_DAYS.get(unit, 1))
            return QueryFilters(
                lookback_days=days,
                date_from=today - timedelta(days=days),
                date_to=today,
                period_label=m.group(0),
            )

        # last/past <word>
        m = _RE_LAST_WORD.search(text)
        if m:
            unit = m.group(1).lower()
            days = _UNIT_DAYS.get(unit, 7)
            return QueryFilters(
                lookback_days=days,
                date_from=today - timedelta(days=days),
                date_to=today,
                period_label=m.group(0),
            )

        # yesterday
        if _RE_YESTERDAY.search(text):
            d = today - timedelta(days=1)
            return QueryFilters(date_from=d, date_to=d, period_label="yesterday")

        # ISO date
        m = _RE_ISO.search(text)
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # DD/MM/YYYY
        m = _RE_DMY.search(text)
        if m:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # Month Day  (e.g. "Jun 20th")
        m = _RE_MDY.search(text)
        if m:
            month = _MONTH_NAMES[m.group(1).lower()[:3]]
            day = int(m.group(2))
            year = _resolve_year(m.group(3), month, day)
            d = date(year, month, day)
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # Day Month  (e.g. "20 June")
        m = _RE_DM.search(text)
        if m:
            day = int(m.group(1))
            month = _MONTH_NAMES[m.group(2).lower()[:3]]
            year = _resolve_year(m.group(3), month, day)
            d = date(year, month, day)
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

    except Exception:
        pass  # any date parse error → no filter

    return QueryFilters()
```

### Step 3: Run tests — expect pass

- [ ] `python -m pytest tests/test_query_filters.py -v`
Expected: all pass.

### Step 4: Commit

```bash
git add agents/chat/query_filters.py tests/test_query_filters.py
git commit -m "feat(chat): add query filter extraction for time/date expressions"
```

---

## Task 2 — Wire Filters into `run_turn()` + Tool Updates

**Files:**
- Modify: `agents/chat/agent.py`
- Modify: `agents/chat/tools.py`
- Modify: `persistence/store.py`
- Test: `tests/test_query_filters.py` (extend)

### Step 1: Wire `extract_filters()` into `run_turn()` in `agent.py`

- [ ] In `run_turn()`, after the `_sanitize_input` block and before the intent routing section, add:

```python
    # Extract time/date filters from query and append to agent hint
    from agents.chat.query_filters import extract_filters as _extract_filters
    _qfilters = _extract_filters(text)
    _filter_hint = _qfilters.as_hint()  # "" when no filter found
```

- [ ] Then, in the intent routing block where `hint` is set, change:

```python
            if is_research_intent(routed_intent):
                hint = f"(intent: {routed_intent})\n"
```

to:

```python
            if is_research_intent(routed_intent):
                hint = f"(intent: {routed_intent})\n"
                if _filter_hint:
                    hint += f"{_filter_hint}\n"
```

### Step 2: Add `load_snapshot_for_date()` to `persistence/store.py`

- [ ] Append to `persistence/store.py` (after `load_latest_snapshot`):

```python
def load_snapshot_for_date(date_str: str) -> tuple[str | None, list[dict]]:
    """Load snapshot for a specific date. DB first, then file fallback.

    Returns (run_date, rows) — same shape as load_latest_snapshot().
    Returns (None, []) if no data exists for that date.
    """
    if getattr(SETTINGS, "DATABASE_URL", ""):
        try:
            from persistence.db import session_scope
            from persistence.models import DailySnapshotRow

            with session_scope() as s:
                rows = (
                    s.query(DailySnapshotRow)
                    .filter(DailySnapshotRow.run_date == date_str)
                    .all()
                )
                if rows:
                    keep = (*_SNAPSHOT_FIELDS, "composite_score", "signals", "news",
                            "rationale", "risk_flags", "technicals")
                    return date_str, [
                        {**{k: getattr(r, k) for k in keep},
                         "earnings_proximity": bool(r.earnings_proximity)}
                        for r in rows
                    ]
        except Exception as e:
            logger.warning("DB snapshot load for %s failed: %s — trying file", date_str, e)

    p = _snapshot_file(date_str)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data.get("run_date", date_str), data.get("stocks", [])
        except Exception as e:
            logger.warning("file snapshot load for %s failed: %s", date_str, e)

    return None, []
```

### Step 3: Add `as_of` parameter to `screen_snapshot` tool in `tools.py`

- [ ] In `tools.py`, update the `screen_snapshot` tool signature and docstring:

```python
@tool
def screen_snapshot(
    pe_max: Optional[float] = None,
    pe_min: Optional[float] = None,
    sector: Optional[str] = None,
    min_score: Optional[float] = None,
    has_news: bool = False,
    name: Optional[str] = None,
    as_of: Optional[str] = None,
    sort_by: str = "composite_score",
    limit: int = 10,
) -> dict:
    """Screen the latest daily scored universe (cached from the nightly run).

    Filters: pe_max/pe_min (trailing PE), sector (substring match),
    min_score (composite 1-10), has_news (only stocks with recent headlines),
    name (company name substring match — use to find a ticker by company name),
    as_of (ISO date YYYY-MM-DD — loads the snapshot for that specific date;
    omit for the latest run).
    sort_by: "composite_score" (desc), "pe_ratio" (asc) or "market_cap_cr" (desc).
    Returns as_of date and stale flag — always tell the user the data date.
    """
```

- [ ] In the body of `screen_snapshot`, replace:

```python
        meta, rows = _snapshot_rows()
        _source = "snapshot_cache" if rows else "no_snapshot"
```

with:

```python
        if as_of:
            from persistence import store as _store
            run_date, rows = _store.load_snapshot_for_date(as_of)
            meta = {"as_of": run_date or as_of, "stale": run_date is None}
        else:
            meta, rows = _snapshot_rows()
        _source = "snapshot_cache" if rows else "no_snapshot"
```

### Step 4: Add `lookback_days` parameter to `timing` tool in `tools.py`

- [ ] Update the `timing` tool signature:

```python
@tool
def timing(ticker: str, lookback_days: Optional[int] = None) -> dict:
    """Technical entry/exit read for one NSE stock (deterministic, no LLM).

    Returns RSI(14), position within the 52-week range, 20-day breakout flag,
    5/20-day momentum, and nearest support/resistance. Use lookback_days to
    override the default 400-day window (e.g. lookback_days=30 for last month).
    Compose the buy-zone / stop / target verdict yourself from these numbers.
    """
```

- [ ] In the body of `timing`, change:

```python
        candles = provider.get_ohlcv(ticker, days=400) or []
```

to:

```python
        candles = provider.get_ohlcv(ticker, days=lookback_days or 400) or []
```

### Step 5: Add `historical_performance` tool to `tools.py`

- [ ] After the `timing` tool, add:

```python
@tool
def historical_performance(symbols: list[str], from_date: str, to_date: str) -> dict:
    """Price % change for up to 5 NSE symbols over a date range.

    from_date / to_date: ISO format YYYY-MM-DD.
    Uses OHLCV candles — returns open price on from_date and close on to_date.
    Use for questions like 'which stocks grew most last month' or 'Reliance
    return since Jun 20'.
    """
    try:
        from enrichment.market_data import get_default_provider
        from datetime import date as _date

        provider = get_default_provider()
        syms = [s.upper() for s in symbols[:5]]

        dt_from = _date.fromisoformat(from_date)
        dt_to = _date.fromisoformat(to_date)
        days_needed = (dt_to - dt_from).days + 30  # buffer for trading-day gaps

        results = {}
        for sym in syms:
            try:
                candles = provider.get_ohlcv(sym, days=days_needed) or []
                # candles: list of [date_str, open, high, low, close, volume]
                # Filter to range
                in_range = [c for c in candles
                            if from_date <= str(c[0])[:10] <= to_date]
                if len(in_range) < 2:
                    results[sym] = {"error": "insufficient candles in range"}
                    continue
                open_price = float(in_range[0][1])
                close_price = float(in_range[-1][4])
                pct_change = round((close_price - open_price) / open_price * 100, 2) if open_price else None
                results[sym] = {
                    "from_date": str(in_range[0][0])[:10],
                    "to_date": str(in_range[-1][0])[:10],
                    "open": open_price,
                    "close": close_price,
                    "pct_change": pct_change,
                }
            except Exception as e:
                results[sym] = {"error": str(e)}

        with trace_tool("historical_performance", {"symbols": syms, "from_date": from_date, "to_date": to_date}) as span:
            result = {"results": results, "_source": "ohlcv_candles",
                      "from_date": from_date, "to_date": to_date}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("historical_performance failed")
        return {"error": str(e), "_source": "ohlcv_candles"}
```

- [ ] Add `historical_performance` to `CHAT_TOOLS`:

```python
CHAT_TOOLS = [screen_snapshot, live_quote, fetch_news, score_subset, deep_dive,
              get_portfolio, macro_search, timing, recall, historical_performance]
```

### Step 6: Update system prompt in `agent.py` to mention new tools

- [ ] In `_SYSTEM_PROMPT`, after the existing "Data discipline" bullet about `timing`, add:

```python
- For growth/performance questions over a period ("which stocks grew last month", \
"Reliance return since Jun 20"), check the time_context hint in the message — \
if lookback_days or date_from/date_to are given, call \
historical_performance(symbols, from_date, to_date) with those dates. \
For a specific past date's snapshot, call screen_snapshot(as_of="YYYY-MM-DD").
```

### Step 7: Write integration tests

- [ ] Append to `tests/test_query_filters.py`:

```python
class TestRunTurnFilterHint:
    """run_turn must append time_context hint when filters are found."""

    def test_filter_hint_appended_to_message(self, monkeypatch):
        import agents.chat.agent as agent_mod
        from unittest.mock import MagicMock

        captured = {}

        def fake_invoke(payload, cfg):
            captured["msg"] = payload["messages"][0][1]
            fake_msg = MagicMock()
            fake_msg.type = "ai"
            fake_msg.content = "Reliance grew 5% last month."
            return {"messages": [fake_msg]}

        monkeypatch.setattr("agents.supervisor.kill_switch_active", lambda: False)
        monkeypatch.setattr("agents.chat.intent.route_intent",
                            lambda t: {"intent": "research", "confidence": 0.9, "route": "regex"})
        monkeypatch.setattr("agents.chat.tools.reset_turn_state", lambda: None)
        monkeypatch.setattr("agents.chat.cache.check", lambda **kw: None)
        monkeypatch.setattr("agents.chat.cache.put", lambda **kw: None)

        fake_agent = MagicMock()
        fake_agent.invoke = fake_invoke
        monkeypatch.setattr(agent_mod, "_get_agent", lambda: fake_agent)

        agent_mod.run_turn("123", "How did Reliance do last month?")
        assert "time_context" in captured["msg"]
        assert "lookback_days=30" in captured["msg"]


class TestLoadSnapshotForDate:
    """store.load_snapshot_for_date must return empty list for missing dates."""

    def test_missing_date_returns_empty(self, monkeypatch, tmp_path):
        import persistence.store as st_mod

        monkeypatch.setattr("persistence.store.SETTINGS",
                            type("S", (), {"DATABASE_URL": "", "OUTPUT_DIR": str(tmp_path)})())
        run_date, rows = st_mod.load_snapshot_for_date("2020-01-01")
        assert rows == []
        assert run_date is None
```

- [ ] Run tests:
```
python -m pytest tests/test_query_filters.py -v
```
Expected: all pass.

- [ ] Run full suite:
```
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all pass.

### Step 8: Commit

```bash
git add agents/chat/agent.py agents/chat/tools.py persistence/store.py tests/test_query_filters.py
git commit -m "feat(chat): wire query filters into run_turn + add as_of/lookback_days to tools + historical_performance"
```

---

## Self-Review

**Spec coverage:**
- [x] Extract date filters from "past month", "Jun 20th" → `extract_filters()` regex
- [x] Append structured hint to agent → `as_hint()` in `run_turn()`
- [x] Route to Groww API with correct date range → `timing(lookback_days=N)` → `provider.get_ohlcv(ticker, days=N)`
- [x] Route to stock universe DB with date filter → `screen_snapshot(as_of="YYYY-MM-DD")` → `load_snapshot_for_date()`
- [x] Growth over a period → `historical_performance(symbols, from_date, to_date)` new tool
- [x] Graceful degradation — all paths return `QueryFilters()` with no fields on failure

**What this does NOT do:**
- Complex expressions like "since IPO", "before the RBI meeting" — these need LLM date resolution. Add as a fallback later if needed.
- Intraday time references ("last 2 hours") — out of scope, daily granularity only.
- "Q1 2026" / "FY24" → quarter/fiscal year expansion. Low frequency; add to `_RE_LAST_WORD` patterns later.

**Placeholder scan:** No TBDs. All code complete.

**Type consistency:**
- `extract_filters(text: str) -> QueryFilters` — used identically in `run_turn()`, tests, and `as_hint()`
- `load_snapshot_for_date(date_str: str) -> tuple[str | None, list[dict]]` — same shape as `load_latest_snapshot()`
- `historical_performance(symbols: list[str], from_date: str, to_date: str) -> dict` — matches tool decorator and `CHAT_TOOLS` list
