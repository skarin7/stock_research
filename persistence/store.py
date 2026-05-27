"""Portfolio (book) persistence for paper mode.

File-based store keyed on ``SETTINGS.POSITIONS_FILE`` so paper positions persist
across runs without a database (mirrors the MemorySaver fallback used elsewhere).
DB-backed positions land with the live order lifecycle (broker iteration).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import SETTINGS

from agents.contracts import PortfolioState

logger = logging.getLogger("persistence.store")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    return Path(getattr(SETTINGS, "POSITIONS_FILE", "output/positions.json"))


def load_portfolio() -> PortfolioState:
    p = _path()
    if p.exists():
        try:
            return PortfolioState(**json.loads(p.read_text()))
        except Exception as e:  # corrupt / schema drift → start fresh
            logger.warning("could not load portfolio (%s) — starting fresh", e)
    return PortfolioState(cash=float(getattr(SETTINGS, "TRADING_CAPITAL_INR", 0.0)))


def save_portfolio(book: PortfolioState) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book.model_dump(), indent=2, default=str))
    logger.info("portfolio saved → %s (%d positions)", p, len(book.positions))


def save_proposals(proposals) -> None:
    """Persist proposals (keyed by id) so an out-of-process approver can see them."""
    p = Path(getattr(SETTINGS, "PROPOSALS_FILE", "output/proposals.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_proposals()
    existing.update({pr.proposal_id: pr.model_dump() for pr in proposals})
    p.write_text(json.dumps(existing, indent=2, default=str))


def load_proposals() -> dict:
    p = Path(getattr(SETTINGS, "PROPOSALS_FILE", "output/proposals.json"))
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


# ── long-term memory (append-only jsonl; query by namespace) ───────────────────

def record_memory(namespace: str, key: str, value: dict) -> None:
    """Append a compact memory entry (summaries, not raw payloads)."""
    p = Path(getattr(SETTINGS, "MEMORY_FILE", "output/memory.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now_iso(), "namespace": namespace, "key": key, "value": value}
    with p.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def query_memory(namespace: str | None = None, limit: int | None = None) -> list[dict]:
    p = Path(getattr(SETTINGS, "MEMORY_FILE", "output/memory.jsonl"))
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if namespace and e.get("namespace") != namespace:
            continue
        rows.append(e)
    return rows[-limit:] if limit else rows


def recent_calls(ticker: str, limit: int = 5) -> list[dict]:
    """Past calls (score/conviction/rationale/regime/outcome) for a ticker — for agents to query."""
    matches = [e["value"] for e in query_memory("calls") if e.get("value", {}).get("ticker") == ticker]
    return matches[-limit:]


def latest_signal_perf() -> dict | None:
    rows = query_memory("signal_perf")
    return rows[-1]["value"] if rows else None


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
