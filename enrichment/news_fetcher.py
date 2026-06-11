"""
Fetches recent news for NSE stocks.

Per-stock:  Google News RSS filtered to trusted financial sources (free).
Per-run:    One Gemini 2.5 Flash search for India macro context (geopolitical,
            crude oil, FII flows, RBI) — requires GEMINI_API_KEY.

Trusted sources: Moneycontrol, Economic Times, Business Standard, Mint,
                 Livemint, Yahoo Finance, NDTV Profit, Financial Express.
"""

import email.utils
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config import SETTINGS

logger = logging.getLogger(__name__)

_EMPTY = {"headlines": [], "sentiment": "neutral"}
_DELAY = 0.5  # seconds between per-stock RSS requests
_MAX_HEADLINES = 5      # kept at 5 so Claude input tokens (cost) stay flat
_MAX_AGE_DAYS = 30      # drop stale headlines
_RESULTS_TERMS = '(results OR earnings OR profit OR quarterly OR "Q1" OR "Q2" OR "Q3" OR "Q4")'

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

def _parse_pubdate(s: str) -> Optional[datetime]:
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _rss_items(query: str) -> list[dict]:
    """Fetch RSS items for a query, filtered to trusted sources. Each item is
    {"title": str, "ts": datetime|None}."""
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    resp = requests.get(_RSS_URL, params=params, headers=_HEADERS, timeout=10)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        source_el = item.find("source")
        source_name = (source_el.text or "").strip().lower() if source_el is not None else ""
        if source_name and source_name not in _TRUSTED_SOURCES:
            continue
        title = item.findtext("title", "").strip()
        title = title.rsplit(" - ", 1)[0].strip()
        if not title:
            continue
        items.append({"title": title, "ts": _parse_pubdate(item.findtext("pubDate", ""))})
    return items


def _fetch_via_rss(symbol: str, company: str) -> dict:
    name = company or symbol
    general_q = f"{company} {symbol} NSE stock" if company else f"{symbol} NSE stock"
    results_q = f'"{name}" {_RESULTS_TERMS} NSE'

    results_items = _rss_items(results_q)
    general_items = _rss_items(general_q)

    cutoff = datetime.now(timezone.utc) - timedelta(days=_MAX_AGE_DAYS)
    _floor = datetime.min.replace(tzinfo=timezone.utc)

    def prep(items):
        fresh = [it for it in items if it["ts"] is None or it["ts"] >= cutoff]
        fresh.sort(key=lambda it: it["ts"] or _floor, reverse=True)  # newest first
        return fresh

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
        _gemini_client = genai.Client(api_key=SETTINGS.GEMINI_API_KEY)
    return _gemini_client


_MACRO_QUERY = (
    "India stock market today: FII/DII flows, crude oil price, rupee, "
    "RBI policy stance, global cues, any geopolitical risks affecting Indian markets. "
    "Summarise in 4-6 bullet points covering the most important macro factors "
    "that would influence NSE stock performance today."
)


def _split_macro(text: str, sectors: list[str]) -> tuple[str, dict]:
    """Separate the human-readable summary from the trailing per-sector JSON map."""
    sector_map: dict = {}
    json_str, summary = None, text

    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        json_str = m.group(1)
        summary = text[:m.start()].strip()
    elif sectors:
        idx = text.rfind("{")
        if idx != -1:
            json_str = text[idx:]
            summary = text[:idx].strip()

    if json_str:
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                sector_map = parsed
        except Exception:
            pass

    summary = re.sub(r"^```[a-z]*\s*|\s*```$", "", summary, flags=re.MULTILINE).strip()
    return summary, sector_map


def fetch_macro_context(sectors: Optional[list[str]] = None) -> tuple[str, dict]:
    """
    Run one Gemini search per pipeline run for India macro context.
    Returns (summary_text, sector_map) where sector_map keys are the provided
    sector labels → {"impact": ..., "driver": ...}. Falls back to ("", {}) or
    (summary, {}) when unavailable or unparseable. Single LLM call regardless of
    sector count, so cost is unchanged.
    """
    if not SETTINGS.GEMINI_API_KEY:
        logger.info("GEMINI_API_KEY not set — skipping macro context")
        return "", {}

    sectors = sorted({s for s in (sectors or []) if s})
    query = _MACRO_QUERY
    if sectors:
        query += (
            "\n\nThen, on new lines after the summary, output a fenced ```json block "
            "mapping EACH of these exact sector labels to its near-term impact. "
            f"Keys (use verbatim): {sectors}. "
            'Format: {"<sector>": {"impact": "positive|negative|neutral", '
            '"driver": "<short reason>"}}. Include every listed sector.'
        )

    try:
        from google.genai import types
        client = _get_gemini_client()
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0,
            ),
        )
        summary, sector_map = _split_macro(resp.text.strip(), sectors)
        logger.info("Macro context fetched (%d chars, %d sectors mapped)", len(summary), len(sector_map))
        return summary, sector_map
    except Exception as e:
        logger.warning("Macro context fetch failed: %s — scoring without it", e)
        return "", {}
