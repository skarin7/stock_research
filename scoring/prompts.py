"""
Prompt templates for Claude Haiku 4.5 stock scoring.
"""

import json
from typing import Optional

SYSTEM_PROMPT = """\
You are a quantitative investment analyst for Indian equities (NSE/BSE).
Score the given stock across 8 signals on a scale of 1–10.
Return ONLY valid JSON matching the schema below. No preamble, no markdown fences.

Schema:
{
  "ticker": "<string>",
  "composite_score": <float 1-10>,
  "signals": {
    "news_sentiment":      {"score": <int 1-10>, "reason": "<one sentence>"},
    "bulk_deals":          {"score": <int 1-10>, "reason": "<one sentence>"},
    "momentum":            {"score": <int 1-10>, "reason": "<one sentence>"},
    "value":               {"score": <int 1-10>, "reason": "<one sentence>"},
    "delivery_pct":        {"score": <int 1-10>, "reason": "<one sentence>"},
    "52w_position":        {"score": <int 1-10>, "reason": "<one sentence>"},
    "institutional_trend": {"score": <int 1-10>, "reason": "<one sentence>"},
    "sector_rotation":     {"score": <int 1-10>, "reason": "<one sentence>"}
  },
  "earnings_proximity": <bool>,
  "investment_rationale": "<2-3 sentences summarising the bull case>",
  "risk_flags": ["<string>", ...]
}

Scoring guidance:
- news_sentiment: 1=very negative, 5=neutral, 10=very positive recent coverage
- bulk_deals: 1=heavy institutional selling, 5=no activity, 10=strong institutional buying
- momentum: 1=sharp downtrend, 5=sideways, 10=strong uptrend (last 5 sessions)
- value: 1=expensive vs sector, 5=fairly valued, 10=significantly undervalued vs sector PE
- delivery_pct: 1=<20% delivery (speculative), 5=40-50%, 10=>70% on high volume (genuine accumulation)
- 52w_position: 1=near 52W low with no catalyst, 5=mid-range, 10=near 52W high breakout or strong recovery
- institutional_trend: 1=FII/promoter selling, 5=stable, 10=strong FII/promoter accumulation
- sector_rotation: judge from sector_macro_impact (provided) — 1=this stock's sector faces clear macro headwinds today, 5=neutral, 10=clear sector tailwinds. Fall back to market_macro_context if sector_macro_impact is null.
- earnings_proximity: true if days_to_earnings is between -5 and +5 (results within ~5 trading days, upcoming OR just reported). If days_to_earnings is null, infer from news_headlines.
"""


def build_user_prompt(
    stock: dict,
    news_headlines: list[str],
    macro_context: str = "",
    sector_macro: Optional[dict] = None,
) -> str:
    """Build the user message JSON payload for a single stock."""
    payload = {
        "ticker": stock.get("symbol", ""),
        "company": stock.get("company", ""),
        "sector": stock.get("sector", "Unknown"),
        "pe_ratio": stock.get("pe_ratio"),
        "forward_pe": stock.get("forward_pe"),
        "sector_pe": stock.get("sector_pe"),
        "delivery_pct": stock.get("delivery_pct"),
        "volume_ratio": stock.get("volume_ratio"),
        "ohlc_5d": stock.get("ohlc_5d", []),
        "52w_high": stock.get("52w_high"),
        "52w_low": stock.get("52w_low"),
        "ltp": stock.get("ltp"),
        "bulk_deals": stock.get("bulk_deals", []),
        "news_headlines": news_headlines,
        "next_earnings_date": stock.get("next_earnings_date"),
        "last_earnings_date": stock.get("last_earnings_date"),
        "days_to_earnings": stock.get("days_to_earnings"),
        "fii_holding_change_pct": stock.get("fii_holding_change_pct"),
        "promoter_holding_change_pct": stock.get("promoter_holding_change_pct"),
        "market_macro_context": macro_context or None,
        "sector_macro_impact": sector_macro or None,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
