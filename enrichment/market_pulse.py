"""Market-pulse data fetchers for the intraday shock watcher (Part A).

Pure data access + one optional cheap LLM classifier. No graph/state logic —
``agents/nodes/pulse.py`` owns the trigger evaluation, debounce and alerting.

All fetchers are free: index/global moves via yfinance, shock headlines via the
Google-News RSS helper already used by the daily pipeline. The LLM classifier is
the only paid path and is gated/tiered by the caller.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

logger = logging.getLogger("enrichment.market_pulse")

# yfinance symbols for the watched markets.
NIFTY = "^NSEI"
INDIA_VIX = "^INDIAVIX"


def _pct_change(ticker: str) -> tuple[float | None, float | None]:
    """(% change vs prior close, last level) for a yfinance ticker. (None, None)
    on failure or insufficient data. Closed markets simply return stale/None and
    the caller treats that as 'no signal'."""
    import yfinance as yf

    end = date.today()
    start = end - timedelta(days=10)  # buffer for weekends/holidays
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if df.empty:
            return None, None
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        closes = [float(c) for c in df["Close"].tolist()]
    except Exception as e:
        logger.warning("pulse fetch failed for %s: %s", ticker, e)
        return None, None

    if len(closes) < 2 or closes[-2] == 0:
        return None, None
    pct = round((closes[-1] - closes[-2]) / closes[-2] * 100.0, 2)
    return pct, round(closes[-1], 2)


def index_levels() -> dict:
    """NIFTY + India VIX % change vs prior close and last level.

    NOTE: market **breadth**/advance-decline is intentionally omitted — there is
    no reliable free source (same call as the intraday scorer's Tier C)."""
    nifty_pct, nifty_level = _pct_change(NIFTY)
    vix_pct, vix_level = _pct_change(INDIA_VIX)
    return {
        "nifty_pct": nifty_pct, "nifty_level": nifty_level,
        "vix_pct": vix_pct, "vix_level": vix_level,
    }


def global_signals(tickers: dict[str, float]) -> dict[str, dict]:
    """Per-ticker % move + threshold breach for the global cross-asset basket.

    ``tickers`` maps a yfinance symbol → its threshold (negative = drop trigger,
    positive = rise trigger). Returns {symbol: {pct, level, threshold, breached}}.
    A closed market (None pct) contributes breached=False."""
    out: dict[str, dict] = {}
    for sym, threshold in (tickers or {}).items():
        pct, level = _pct_change(sym)
        breached = False
        if pct is not None:
            breached = pct <= threshold if threshold < 0 else pct >= threshold
        out[sym] = {"pct": pct, "level": level, "threshold": threshold, "breached": breached}
    return out


def shock_headlines(keywords: list[str], max_age_hours: int = 6, cap: int = 8) -> list[str]:
    """Recent headlines matching any shock keyword (trusted sources only)."""
    from datetime import datetime, timezone

    from enrichment.news_fetcher import _rss_items

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen: set[str] = set()
    titles: list[str] = []
    for kw in keywords or []:
        try:
            items = _rss_items(kw)
        except Exception as e:
            logger.warning("shock headline fetch failed for %r: %s", kw, e)
            continue
        for it in items:
            ts = it.get("ts")
            if ts is not None and ts < cutoff:
                continue
            title = it.get("title", "")
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
    return titles[:cap]


_CLASSIFY_PROMPT = (
    "You are a market-risk filter for an Indian equities trader. Given recent "
    "news headlines, decide if ANY indicates a SUDDEN market-moving shock "
    "(crash, war/attack, major policy/rate surprise, circuit breaker, sharp "
    "commodity/FX move). Reply ONLY with compact JSON: "
    '{"is_shock": true|false, "severity": "low|medium|high", "summary": "<one line>"}.\n\n'
    "Headlines:\n{headlines}"
)


def classify_shock(headlines: list[str]) -> dict:
    """One cheap LLM call: are these headlines a market shock? Never raises.

    Returns {is_shock, severity, summary}. Falls back to is_shock=False if no
    headlines or the LLM is unavailable."""
    if not headlines:
        return {"is_shock": False, "severity": "low", "summary": ""}
    try:
        from agents.llm import get_chat_model

        model = get_chat_model(max_tokens=200, temperature=0.0)
        prompt = _CLASSIFY_PROMPT.format(headlines="\n".join(f"- {h}" for h in headlines))
        resp = model.invoke(prompt)
        text = getattr(resp, "content", resp)
        if isinstance(text, list):  # some providers return content blocks
            text = "".join(getattr(b, "text", str(b)) for b in text)
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        return {
            "is_shock": bool(data.get("is_shock", False)),
            "severity": str(data.get("severity", "low")),
            "summary": str(data.get("summary", "")),
        }
    except Exception as e:
        logger.warning("shock classifier failed: %s", e)
        return {"is_shock": False, "severity": "low", "summary": ""}
