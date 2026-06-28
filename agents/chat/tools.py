"""Tools exposed to the chat agent — thin wrappers over the existing modules.

Every tool returns a JSON-serializable dict and converts any exception into
``{"error": ...}`` so a dead data source degrades to an explanation in the
reply instead of crashing the agent loop. Symbol counts are capped per call to
bound latency and API spend.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from langchain_core.tools import tool

from config import SETTINGS

logger = logging.getLogger("agents.chat.tools")

from observability.chat_tracing import trace_tool

_MAX_QUOTE_SYMBOLS = 5
_MAX_NEWS_SYMBOLS = 5
_MAX_SCORE_SYMBOLS = 10
_MAX_DEEP_DIVES_PER_TURN = 1

# Reset by run_turn() at the start of every chat turn.
_turn_state = {"deep_dives": 0}


def reset_turn_state() -> None:
    _turn_state["deep_dives"] = 0


def _snapshot_staleness(run_date: str | None) -> dict:
    if not run_date:
        return {"as_of": None, "stale": True}
    try:
        age = (date.today() - date.fromisoformat(run_date)).days
    except ValueError:
        return {"as_of": run_date, "stale": True}
    return {"as_of": run_date, "stale": age > int(getattr(SETTINGS, "SNAPSHOT_STALE_DAYS", 3))}


def _trim(row: dict) -> dict:
    """Compact snapshot row for the LLM: drop per-signal reasons, cap headlines."""
    return {
        "symbol": row.get("symbol"),
        "company": row.get("company"),
        "sector": row.get("sector"),
        "pe_ratio": row.get("pe_ratio"),
        "sector_pe": row.get("sector_pe"),
        "market_cap_cr": row.get("market_cap_cr"),
        "ltp": row.get("ltp"),
        "composite_score": row.get("composite_score"),
        "news": (row.get("headline_items") or row.get("news") or [])[:3],
        "rationale": (row.get("rationale") or "")[:300],
        "risk_flags": (row.get("risk_flags") or [])[:3],
        "earnings_proximity": row.get("earnings_proximity"),
    }


def _snapshot_rows(symbols: list[str] | None = None) -> tuple[dict, list[dict]]:
    from persistence import store

    run_date, rows = store.load_latest_snapshot()
    meta = _snapshot_staleness(run_date)
    if symbols:
        wanted = {s.upper() for s in symbols}
        rows = [r for r in rows if (r.get("symbol") or "").upper() in wanted]
    return meta, rows


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
    name (company name substring match — use to find a ticker by company name,
    e.g. name="ola electric" returns the NSE symbol and score),
    as_of (ISO date YYYY-MM-DD — loads the snapshot for that specific date;
    omit for the latest run).
    sort_by: "composite_score" (desc), "pe_ratio" (asc) or "market_cap_cr" (desc).
    Returns as_of date and stale flag — always tell the user the data date.
    """
    try:
        if as_of:
            from persistence import store as _store
            run_date, rows = _store.load_snapshot_for_date(as_of)
            meta = {"as_of": run_date or as_of, "stale": run_date is None}
        else:
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
            if name and name.lower() not in (r.get("company") or r.get("symbol") or "").lower():
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
        with trace_tool("screen_snapshot", {"sector": sector, "min_score": min_score, "limit": limit, "as_of": as_of}) as span:
            result = {**meta, "_source": _source,
                      "total_matched": len(matched), "stocks": [_trim(r) for r in matched[:limit]]}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("screen_snapshot failed")
        return {"error": str(e), "_source": "snapshot_cache"}


@tool
def live_quote(symbols: list[str]) -> dict:
    """Live quote (LTP, OHLC, volume, 52w range) for up to 5 NSE symbols."""
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


@tool
def fetch_news(symbols: list[str]) -> dict:
    """Fresh news headlines (Google News RSS) for up to 5 NSE symbols."""
    try:
        from enrichment.news_fetcher import fetch_news_batch

        meta, rows = _snapshot_rows(symbols)
        company = {r["symbol"]: r.get("company", "") for r in rows if r.get("symbol")}
        stocks = [{"symbol": s.upper(), "company": company.get(s.upper(), "")}
                  for s in symbols[:_MAX_NEWS_SYMBOLS]]
        with trace_tool("fetch_news", {"symbols": [s["symbol"] for s in stocks]}) as span:
            news = fetch_news_batch(stocks)
            result = {
                "news": {
                    sym: (v or {}).get("headline_items") or
                         [{"text": h} for h in (v or {}).get("headlines", [])]
                    for sym, v in news.items()
                },
                "_source": "google_news_rss",
            }
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("fetch_news failed")
        return {"error": str(e), "_source": "google_news_rss"}


@tool
def score_subset(symbols: list[str]) -> dict:
    """Re-score up to 10 symbols with fresh news + live prices (LLM scorecards).

    Use after screening, on the shortlist only — slower and costs LLM tokens.
    Returns composite scores (1-10) with per-signal reasoning.
    """
    try:
        from enrichment.news_fetcher import fetch_news_batch
        from scoring.claude_scorer import score_stocks
        from scoring.ranker import rank_stocks

        meta, rows = _snapshot_rows(symbols[:_MAX_SCORE_SYMBOLS])
        if not rows:
            return {**meta, "error": "none of those symbols are in the snapshot", "_source": "claude_haiku_sync"}

        # Snapshot rows already carry the daily enrichment; top up price + news live.
        stocks = []
        for r in rows:
            s = {k: r.get(k) for k in ("symbol", "company", "sector", "pe_ratio",
                                       "sector_pe", "market_cap_cr", "ltp",
                                       "delivery_pct", "volume_ratio")}
            s["52w_high"], s["52w_low"] = r.get("week52_high"), r.get("week52_low")
            stocks.append(s)
        try:
            from enrichment.market_data import get_default_provider
            provider = get_default_provider()
            for s in stocks:
                q = provider.get_quote(s["symbol"]) or {}
                s["ltp"] = q.get("ltp") or s.get("ltp")
        except Exception as e:
            logger.warning("live price top-up failed: %s", e)

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


@tool
def deep_dive(ticker: str) -> dict:
    """Bull-vs-bear debate on one stock → direction + conviction (0-1).

    Expensive (multiple LLM rounds): max one per question, only when the user
    wants a deep view on a specific stock.
    """
    if _turn_state["deep_dives"] >= _MAX_DEEP_DIVES_PER_TURN:
        return {"error": "deep_dive already used this turn — answer with the data you have"}
    _turn_state["deep_dives"] += 1
    try:
        from agents.contracts import EnrichedStock, Scorecard
        from agents.nodes.debate import _build_context, build_debate_subgraph

        ticker = ticker.upper()
        meta, rows = _snapshot_rows([ticker])
        if not rows:
            return {"error": f"{ticker} not found in the latest snapshot", "_source": "debate_subgraph"}
        row = rows[0]

        scorecard = Scorecard.from_legacy({
            "ticker": ticker,
            "composite_score": row.get("composite_score") or 0.0,
            "signals": row.get("signals") or {},
            "investment_rationale": row.get("rationale", ""),
            "risk_flags": row.get("risk_flags") or [],
        })
        stock = EnrichedStock(
            symbol=ticker,
            sector=row.get("sector"),
            pe_ratio=row.get("pe_ratio"),
            sector_pe=row.get("sector_pe"),
            ltp=row.get("ltp"),
            week52_high=row.get("week52_high"),
            week52_low=row.get("week52_low"),
            delivery_pct=row.get("delivery_pct"),
            volume_ratio=row.get("volume_ratio"),
        )
        context = _build_context(scorecard, stock, "")
        max_rounds = max(1, int(getattr(SETTINGS, "MAX_DEBATE_ROUNDS", 3)))
        result = build_debate_subgraph().invoke(
            {"ticker": ticker, "context": context, "rounds": 0,
             "max_rounds": max_rounds, "tokens": 0, "transcript": []},
            {"recursion_limit": max_rounds * 3 + 5},
        )
        conv = result.get("conviction") or {}
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


@tool
def get_portfolio() -> dict:
    """Current paper-trading book: cash, open positions, sector exposure."""
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


@tool
def macro_search(query: str) -> dict:
    """Web search for current macro / geopolitical events (e.g. "Iran war impact
    on Indian markets crude oil"). Use this to ground event-driven questions, then
    map the event to sectors and call screen_snapshot to name the affected stocks.

    Returns a one-line answer plus source results (title, url, snippet) and the
    fetch date. Always cite the source URLs and the date; it is news-derived
    analysis, not a forecast.
    """
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


@tool
def timing(ticker: str, lookback_days: Optional[int] = None) -> dict:
    """Technical entry/exit read for one NSE stock (deterministic, no LLM).

    Returns RSI(14), position within the 52-week range, 20-day breakout flag,
    5/20-day momentum, and nearest support/resistance. Use lookback_days to
    override the default 400-day window (e.g. lookback_days=30 for last month).
    Compose the buy-zone / stop / target verdict yourself from these numbers —
    and always state the risks.
    """
    try:
        from enrichment.market_data import get_default_provider
        from intraday.technicals import compute_metrics, pct_change_ndays

        ticker = ticker.upper()
        provider = get_default_provider()
        effective_days = max(lookback_days, 260) if lookback_days else 400
        candles = provider.get_ohlcv(ticker, days=effective_days) or []
        m = compute_metrics(candles)

        closes = [float(c[4]) for c in candles]
        lows = [float(c[3]) for c in candles]
        ltp = (provider.get_quote(ticker) or {}).get("ltp") or m.get("close")

        high_52w = m.get("high_52w")
        low_52w = min(lows) if lows else None
        high_20d = m.get("high_20d")
        support = min(lows[-20:]) if lows else None

        pct_pos = None
        if (ltp is not None and high_52w is not None and low_52w is not None
                and high_52w > low_52w):
            pct_pos = round((ltp - low_52w) / (high_52w - low_52w), 3)

        breakout = None
        if ltp is not None and high_20d is not None:
            breakout = ltp > high_20d

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


_RECALL_FIELDS = ("ticker", "score", "conviction", "rationale", "regime", "outcome", "date")


@tool
def recall(ticker: str) -> dict:
    """Past calls this agent recorded on a stock (score, conviction, rationale,
    regime, outcome). Use when the user asks what you thought of a stock before.
    Returns an empty list when there is no prior record.
    """
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

        if dt_from > dt_to:
            return {"error": f"from_date {from_date} is after to_date {to_date}", "_source": "ohlcv_candles"}

        today = _date.today()
        days_needed = (today - dt_from).days + 10  # fetch from today back to from_date

        with trace_tool("historical_performance", {"symbols": syms, "from_date": from_date, "to_date": to_date}) as span:
            results = {}
            for sym in syms:
                try:
                    candles = provider.get_ohlcv(sym, days=days_needed) or []
                    # candles: list of [date_str, open, high, low, close, volume]
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

            result = {"results": results, "_source": "ohlcv_candles",
                      "from_date": from_date, "to_date": to_date}
            span.set_output(result)
        return result
    except Exception as e:
        logger.exception("historical_performance failed")
        return {"error": str(e), "_source": "ohlcv_candles"}


CHAT_TOOLS = [screen_snapshot, live_quote, fetch_news, score_subset, deep_dive,
              get_portfolio, macro_search, timing, recall, historical_performance]
