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
        "news": (row.get("news") or [])[:3],
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
    sort_by: str = "composite_score",
    limit: int = 10,
) -> dict:
    """Screen the latest daily scored universe (cached from the nightly run).

    Filters: pe_max/pe_min (trailing PE), sector (substring match),
    min_score (composite 1-10), has_news (only stocks with recent headlines).
    sort_by: "composite_score" (desc), "pe_ratio" (asc) or "market_cap_cr" (desc).
    Returns as_of date and stale flag — always tell the user the data date.
    """
    try:
        meta, rows = _snapshot_rows()
        if not rows:
            return {**meta, "error": "no snapshot available — the daily run has not produced data yet"}

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
        return {**meta, "total_matched": len(matched), "stocks": [_trim(r) for r in matched[:limit]]}
    except Exception as e:
        logger.exception("screen_snapshot failed")
        return {"error": str(e)}


@tool
def live_quote(symbols: list[str]) -> dict:
    """Live quote (LTP, OHLC, volume, 52w range) for up to 5 NSE symbols."""
    try:
        from enrichment.market_data import get_default_provider

        provider = get_default_provider()
        out = {}
        for sym in symbols[:_MAX_QUOTE_SYMBOLS]:
            try:
                out[sym.upper()] = provider.get_quote(sym.upper()) or {"error": "no data"}
            except Exception as e:
                out[sym.upper()] = {"error": str(e)}
        return {"quotes": out}
    except Exception as e:
        logger.exception("live_quote failed")
        return {"error": str(e)}


@tool
def fetch_news(symbols: list[str]) -> dict:
    """Fresh news headlines (Google News RSS) for up to 5 NSE symbols."""
    try:
        from enrichment.news_fetcher import fetch_news_batch

        meta, rows = _snapshot_rows(symbols)
        company = {r["symbol"]: r.get("company", "") for r in rows if r.get("symbol")}
        stocks = [{"symbol": s.upper(), "company": company.get(s.upper(), "")}
                  for s in symbols[:_MAX_NEWS_SYMBOLS]]
        news = fetch_news_batch(stocks)
        return {"news": {sym: (v or {}).get("headlines", []) for sym, v in news.items()}}
    except Exception as e:
        logger.exception("fetch_news failed")
        return {"error": str(e)}


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
            return {**meta, "error": "none of those symbols are in the snapshot"}

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

        news_map = fetch_news_batch(stocks)
        scored = score_stocks(stocks, news_map)
        ranked = rank_stocks(scored, top_n=len(scored))
        return {
            **meta,
            "scores": [
                {"ticker": c.get("ticker"), "composite_score": c.get("composite_score"),
                 "rationale": c.get("investment_rationale", "")[:300],
                 "risk_flags": (c.get("risk_flags") or [])[:3]}
                for c in ranked
            ],
        }
    except Exception as e:
        logger.exception("score_subset failed")
        return {"error": str(e)}


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
            return {"error": f"{ticker} not found in the latest snapshot"}
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
        return {
            **meta,
            "ticker": ticker,
            "direction": conv.get("direction", "neutral"),
            "conviction": conv.get("conviction", 0.0),
            "bull_case": result.get("bull_case", ""),
            "bear_case": result.get("bear_case", ""),
        }
    except Exception as e:
        logger.exception("deep_dive failed")
        return {"error": str(e)}


@tool
def get_portfolio() -> dict:
    """Current paper-trading book: cash, open positions, sector exposure."""
    try:
        from persistence import store

        book = store.recompute(store.load_portfolio())
        return json.loads(book.model_dump_json())
    except Exception as e:
        logger.exception("get_portfolio failed")
        return {"error": str(e)}


CHAT_TOOLS = [screen_snapshot, live_quote, fetch_news, score_subset, deep_dive, get_portfolio]
