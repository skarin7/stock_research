"""Protective auto-exit — the ONLY automated order path.

By construction this can only SELL-to-close an EXISTING long; it never opens
risk. It wraps ``broker.place_order`` with a second, stricter layer of
auto-trade guardrails on top of the broker's own default-deny gate:

    ticker in AUTO_TRADE_ALLOWLIST (non-empty)  AND  within window
    AND  broker reconciliation confirms the position  AND  daily order/notional
    caps not exceeded.

Any failed guard raises ``ExitRefused`` and places NO order — the caller treats
that as "keep the position and alert a human" (HITL fallback). The actual SDK
call still passes through ``place_order`` so the live-trading gate + kill-switch
are re-checked there too (defense in depth).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from config import SETTINGS

from agents.broker.groww_trader import place_order
from agents.contracts import TradeProposal

logger = logging.getLogger("agents.auto_exit")

_IST = ZoneInfo("Asia/Kolkata")


class ExitRefused(Exception):
    """A guardrail blocked the auto-exit; no order was placed."""


def _now_ist() -> datetime:
    return datetime.now(_IST)


def _within_window() -> bool:
    try:
        start, end = SETTINGS.AUTO_TRADE_WINDOW.split("-")
    except (ValueError, AttributeError):
        return False
    return start <= _now_ist().strftime("%H:%M") <= end


def _ledger() -> dict:
    """Load today's auto-trade ledger (resets daily)."""
    path = SETTINGS.AUTO_TRADE_LEDGER
    today = _now_ist().date().isoformat()
    try:
        with open(path) as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    if d.get("date") != today:
        d = {"date": today, "count": 0, "notional": 0.0}
    return d


def _commit(d: dict) -> None:
    path = SETTINGS.AUTO_TRADE_LEDGER
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f)


def _broker_qty(ticker: str):
    """Reconcile against broker truth. Returns held qty, or None if unverifiable.

    SEAM: confirm the exact growwapi method + response shape against the
    installed SDK before enabling live (same caveat as groww_trader.place_order).
    Monkeypatched in tests.
    """
    try:
        from enrichment.market_data.groww import default_client

        resp = default_client().get_positions()
        rows = resp.get("positions", resp) if isinstance(resp, dict) else resp
        for p in rows or []:
            if p.get("trading_symbol") == ticker:
                return int(p.get("quantity", 0))
        return 0
    except Exception as e:
        logger.warning("auto_exit reconcile %s failed: %s", ticker, e)
        return None


def auto_exit(position, price: float, reason: str) -> tuple[str, str]:
    """Submit a guarded protective SELL-to-close for ``position`` at ~``price``.

    Returns (broker_order_id, status). Raises ExitRefused if any guard fails
    (no order placed).
    """
    g = SETTINGS

    if not getattr(g, "AUTO_TRADE_ALLOWLIST", frozenset()):
        raise ExitRefused("AUTO_TRADE_ALLOWLIST empty — auto-exit disabled")
    if position.ticker not in getattr(g, "AUTO_TRADE_ALLOWLIST", frozenset()):
        raise ExitRefused(f"{position.ticker} not in AUTO_TRADE_ALLOWLIST → HITL")
    if not _within_window():
        raise ExitRefused(f"outside trade window {g.AUTO_TRADE_WINDOW}")

    held = _broker_qty(position.ticker)
    if held is None:
        raise ExitRefused("broker reconcile failed — refusing to act blind")
    if held <= 0:
        raise ExitRefused(f"broker holds {held} of {position.ticker} — position drift")
    qty = min(int(position.qty), held)  # never sell more than the broker actually holds

    led = _ledger()
    notional = qty * float(price)
    if led["count"] + 1 > g.MAX_ORDERS_PER_DAY:
        raise ExitRefused(f"daily order cap {g.MAX_ORDERS_PER_DAY} reached")
    if led["notional"] + notional > g.MAX_DAILY_NOTIONAL:
        raise ExitRefused(
            f"daily notional cap ₹{g.MAX_DAILY_NOTIONAL:.0f} reached "
            f"({led['notional']:.0f}+{notional:.0f})"
        )

    proposal = TradeProposal(
        proposal_id=f"autoexit-{position.ticker}-{led['date']}-{led['count']}",
        ticker=position.ticker,
        side="SELL",
        qty=qty,
        order_type="MARKET",
        rationale=f"auto-exit: {reason}",
    )
    order_id, status = place_order(proposal, mode="live")  # re-checks live gate + kill-switch

    led["count"] += 1
    led["notional"] = round(led["notional"] + notional, 2)
    _commit(led)
    logger.warning("AUTO-EXIT %s x%d @%.2f (%s) → %s (%s)",
                   position.ticker, qty, price, reason, order_id, status)
    return order_id, status
