"""Portfolio (book) persistence for paper mode.

File-based store keyed on ``config.POSITIONS_FILE`` so paper positions persist
across runs without a database (mirrors the MemorySaver fallback used elsewhere).
DB-backed positions land with the live order lifecycle (broker iteration).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import config

from agents.contracts import PortfolioState

logger = logging.getLogger("persistence.store")


def _path() -> Path:
    return Path(getattr(config, "POSITIONS_FILE", "output/positions.json"))


def load_portfolio() -> PortfolioState:
    p = _path()
    if p.exists():
        try:
            return PortfolioState(**json.loads(p.read_text()))
        except Exception as e:  # corrupt / schema drift → start fresh
            logger.warning("could not load portfolio (%s) — starting fresh", e)
    return PortfolioState(cash=float(getattr(config, "TRADING_CAPITAL_INR", 0.0)))


def save_portfolio(book: PortfolioState) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book.model_dump(), indent=2, default=str))
    logger.info("portfolio saved → %s (%d positions)", p, len(book.positions))


def recompute(book: PortfolioState) -> PortfolioState:
    """Recompute total + per-sector exposure (value at entry) from positions."""
    total = 0.0
    sectors: dict[str, float] = {}
    for pos in book.positions:
        val = pos.qty * pos.avg_price
        total += val
        key = pos.sector or "Unknown"
        sectors[key] = sectors.get(key, 0.0) + val
    book.total_exposure = round(total, 2)
    book.sector_exposure = {k: round(v, 2) for k, v in sectors.items()}
    return book
