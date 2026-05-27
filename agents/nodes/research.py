"""Research agent — Stages 1-4 of the legacy pipeline, wrapped as one node.

Deterministic. Reuses scrapers/ + enrichment/ unchanged; heavy SDK imports are
done lazily inside the function (mirroring main.py) so the agent layer stays
importable without growwapi / google-genai present.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import config

from agents.contracts import EnrichedStock, EnrichmentResult, UniverseResult
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus

logger = logging.getLogger("agents.research")


def _load_skip_list(skip_path: Path) -> dict:
    if skip_path.exists():
        return json.loads(skip_path.read_text())
    return {}


@agent_node("research", enabled_flag="ENABLE_RESEARCH_AGENT")
def research_node(state: AgentState) -> dict:
    report_date = date.fromisoformat(state["report_date"])
    dry_run = state.get("dry_run", False)
    universe_src = config.STOCK_UNIVERSE.lower()

    skip_path = Path(config.OUTPUT_DIR) / "skip_list.json"
    skip_path.parent.mkdir(parents=True, exist_ok=True)
    skip_list = _load_skip_list(skip_path)

    # ── Stage 1: universe ─────────────────────────────────────────────────────
    if universe_src == "screener":
        from scrapers.screener_scraper import ScreenerScraper
        stocks = ScreenerScraper().fetch_screen()
        stocks = [s for s in stocks if not s["symbol"].isdigit()]   # drop BSE-only numerics
    else:
        from scrapers.nse_index import fetch_index_stocks
        stocks = fetch_index_stocks(universe_src)

    if skip_list:
        stocks = [s for s in stocks if s["symbol"] not in skip_list]

    total_screened = len(stocks)

    if len(stocks) > config.MAX_STOCKS_TO_SCORE:
        stocks = sorted(stocks, key=lambda s: float(s.get("market_cap_cr") or 0), reverse=True)
        stocks = stocks[:config.MAX_STOCKS_TO_SCORE]

    if dry_run:
        stocks = stocks[:config.DRY_RUN_STOCK_COUNT]

    if not stocks:
        logger.error("No stocks in universe — failing research")
        return {"status": RunStatus.FAILED, "total_screened": 0}

    # ── Stage 2: bhavcopy + bulk deals ────────────────────────────────────────
    from scrapers.nse_bhavcopy import download_bhavcopy
    from scrapers.nse_bulk_deals import download_bulk_deals, group_bulk_deals_by_symbol

    try:
        bhavcopy = download_bhavcopy(report_date)
    except Exception as e:
        logger.warning("Bhavcopy unavailable: %s", e)
        bhavcopy = None
    try:
        bulk_map = group_bulk_deals_by_symbol(download_bulk_deals(report_date))
    except Exception as e:
        logger.warning("Bulk deals failed: %s", e)
        bulk_map = {}

    for stock in stocks:
        sym = stock["symbol"]
        if bhavcopy is not None and sym in bhavcopy.index:
            row = bhavcopy.loc[sym]
            stock.setdefault("delivery_pct", row.get("delivery_pct"))
            stock.setdefault("52w_high", row.get("52w_high"))
            stock.setdefault("52w_low", row.get("52w_low"))
            stock["bhavcopy_volume"] = row.get("volume")
            stock["bhavcopy_close"] = row.get("close")
        stock["bulk_deals"] = bulk_map.get(sym, [])

    # ── Stage 3: Groww enrichment + fundamentals ──────────────────────────────
    from enrichment.market_data import enrich_stocks
    stocks = enrich_stocks(stocks)

    no_data = [s["symbol"] for s in stocks if s.get("no_data")]
    if no_data:
        for sym in no_data:
            skip_list[sym] = str(report_date)
        skip_path.write_text(json.dumps(skip_list, indent=2))
    stocks = [s for s in stocks if not s.get("no_data")]

    from enrichment.fundamentals import enrich_fundamentals
    stocks = enrich_fundamentals(stocks, ref_date=report_date)

    # ── Stage 4: news + macro ─────────────────────────────────────────────────
    from enrichment.news_fetcher import fetch_macro_context, fetch_news_batch
    news_map = fetch_news_batch(stocks)
    sectors = sorted({s.get("sector") for s in stocks if s.get("sector")})
    macro_context, sector_macro_map = fetch_macro_context(sectors)

    enriched = EnrichmentResult(
        stocks=[EnrichedStock.from_legacy(s) for s in stocks],
        news_map=news_map,
        macro_context=macro_context,
        sector_macro_map=sector_macro_map or {},
    )
    logger.info("Research complete: %d stocks enriched (%d screened)", len(stocks), total_screened)
    return {
        "status": RunStatus.RUNNING,
        "total_screened": total_screened,
        "universe": UniverseResult(source=universe_src, total_screened=total_screened,
                                   symbols=[s["symbol"] for s in stocks]),
        "enriched": enriched,
    }
