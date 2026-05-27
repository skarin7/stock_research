"""Groww broker — the ONLY place that can submit a real order.

Default-deny by construction: every order path re-checks, at call time, that
ALL of these hold before touching the SDK (defense in depth — the trading node
checks too):

    AGENT_MODE == "live"  AND  ENABLE_LIVE_TRADING  AND  GROWW_TRADING_ENABLED
    AND no kill-switch.

In paper mode this module is never reached (the trading node simulates fills).
Auth reuses the TOTP client from enrichment.market_data.groww.

NOTE: the exact growwapi place_order parameter names/constants should be
verified against the installed SDK version before enabling live trading — this
is the single integration seam and is gated off by default.
"""

from __future__ import annotations

import logging

from config import SETTINGS

from agents.supervisor import kill_switch_active

logger = logging.getLogger("agents.broker")


class BrokerRefused(Exception):
    """Raised when a live order is requested but the gates are not all satisfied."""


def _gate_check(mode: str) -> None:
    if kill_switch_active():
        raise BrokerRefused("kill-switch active")
    if mode != "live":
        raise BrokerRefused(f"mode={mode!r} is not 'live'")
    if not getattr(SETTINGS, "ENABLE_LIVE_TRADING", False):
        raise BrokerRefused("ENABLE_LIVE_TRADING is false")
    if not getattr(SETTINGS, "GROWW_TRADING_ENABLED", False):
        raise BrokerRefused("GROWW_TRADING_ENABLED is false")


def _trading_client():
    # Reuse the authenticated (TOTP) client; lazy so growwapi isn't needed elsewhere.
    from enrichment.market_data.groww import default_client
    return default_client()


def place_order(proposal, mode: str = "live") -> tuple[str, str]:
    """Submit a single order. Returns (broker_order_id, status). Idempotent on broker_order_id.

    Raises BrokerRefused if the gates are not all satisfied (no SDK call is made).
    """
    if proposal.broker_order_id:
        logger.warning("place_order: %s already has order %s — not resubmitting",
                       proposal.ticker, proposal.broker_order_id)
        return proposal.broker_order_id, "already_placed"

    _gate_check(mode)

    from growwapi import GrowwAPI
    client = _trading_client()

    txn = GrowwAPI.TRANSACTION_TYPE_BUY if proposal.side == "BUY" else GrowwAPI.TRANSACTION_TYPE_SELL
    is_limit = proposal.order_type == "LIMIT" and proposal.limit_price
    resp = client.place_order(
        trading_symbol=proposal.ticker,
        quantity=int(proposal.qty),
        transaction_type=txn,
        order_type=GrowwAPI.ORDER_TYPE_LIMIT if is_limit else GrowwAPI.ORDER_TYPE_MARKET,
        price=float(proposal.limit_price) if is_limit else 0,
        product=GrowwAPI.PRODUCT_CNC,
        exchange=GrowwAPI.EXCHANGE_NSE,
        segment=GrowwAPI.SEGMENT_CASH,
        validity=GrowwAPI.VALIDITY_DAY,
    )
    order_id = resp.get("groww_order_id") or resp.get("order_id") or ""
    status = resp.get("order_status") or "placed"
    logger.info("Order placed: %s %s x%d → %s (%s)", proposal.side, proposal.ticker,
                proposal.qty, order_id, status)
    return order_id, status


def get_order_status(order_id: str) -> str:
    """Best-effort status lookup for a placed order (verify SDK method when enabling live)."""
    try:
        client = _trading_client()
        resp = client.get_order_status(groww_order_id=order_id)
        return resp.get("order_status", "unknown")
    except Exception as e:
        logger.warning("get_order_status(%s) failed: %s", order_id, e)
        return "unknown"
