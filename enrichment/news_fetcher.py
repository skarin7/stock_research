"""
Fetches recent news for NSE stocks.

Per-stock:  Google News RSS filtered to trusted financial sources (free).
Per-run:    One Gemini 2.5 Flash search for India macro context (geopolitical,
            crude oil, FII flows, RBI) — requires GEMINI_API_KEY.

Trusted sources: Moneycontrol, Economic Times, Business Standard, Mint,
                 Livemint, Yahoo Finance, NDTV Profit, Financial Express.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_EMPTY = {"headlines": [], "sentiment": "neutral"}
_DELAY = 0.5  # seconds between per-stock RSS requests

_TRUSTED_SOURCES = {
    "moneycontrol", "the economic times", "economic times",
    "business standard", "mint", "livemint",
    "yahoo finance", "ndtv profit", "financial express",
    "hindu businessline", "businessline",
}

_RSS_URL = "https://news.google.com/rss/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── Per-stock RSS ─────────────────────────────────────────────────────────────

def _fetch_via_rss(symbol: str, company: str) -> dict:
    query = f"{company} {symbol} NSE stock" if company else f"{symbol} NSE stock"
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}

    resp = requests.get(_RSS_URL, params=params, headers=_HEADERS, timeout=10)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    headlines = []
    for item in root.findall(".//item"):
        # Filter to trusted financial sources only
        source_el = item.find("source")
        source_name = (source_el.text or "").strip().lower() if source_el is not None else ""
        if source_name and source_name not in _TRUSTED_SOURCES:
            continue
        title = item.findtext("title", "").strip()
        title = title.rsplit(" - ", 1)[0].strip()
        if title:
            headlines.append(title)
        if len(headlines) == 5:
            break

    return {"headlines": headlines, "sentiment": "neutral"}


def fetch_news(symbol: str, company: str = "") -> dict:
    """Fetch recent headlines for a stock via Google News RSS (trusted sources only)."""
    time.sleep(_DELAY)
    try:
        return _fetch_via_rss(symbol, company)
    except Exception as e:
        logger.error("RSS news failed for %s: %s", symbol, e)
        return _EMPTY


def fetch_news_batch(stocks: list[dict]) -> dict[str, dict]:
    """Fetch RSS headlines for all stocks; returns dict keyed by symbol."""
    logger.info("News source: Google News RSS (trusted sources)")
    results = {}
    for i, stock in enumerate(stocks):
        sym = stock["symbol"]
        logger.info("Fetching news %d/%d: %s", i + 1, len(stocks), sym)
        results[sym] = fetch_news(sym, stock.get("company", ""))
    return results


# ── One-per-run macro context via Gemini ──────────────────────────────────────

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


_MACRO_QUERY = (
    "India stock market today: FII/DII flows, crude oil price, rupee, "
    "RBI policy stance, global cues, any geopolitical risks affecting Indian markets. "
    "Summarise in 4-6 bullet points covering the most important macro factors "
    "that would influence NSE stock performance today."
)


def fetch_macro_context() -> str:
    """
    Run one Gemini search per pipeline run to get India macro context.
    Returns a plain-text summary string, or empty string if unavailable.
    """
    if not config.GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY not set — skipping macro context")
        return ""

    try:
        from google.genai import types
        client = _get_gemini_client()
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=_MACRO_QUERY,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0,
            ),
        )
        summary = resp.text.strip()
        # Strip markdown fences if present
        summary = re.sub(r"^```[a-z]*\s*|\s*```$", "", summary, flags=re.MULTILINE).strip()
        logger.info("Macro context fetched (%d chars)", len(summary))
        return summary
    except Exception as e:
        logger.warning("Macro context fetch failed: %s — scoring without it", e)
        return ""
