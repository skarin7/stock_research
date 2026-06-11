"""Monitoring agent — watches open positions during market hours.

Designed to run as a SHORT, scheduled job (every few minutes during market
hours), NOT a 24/7 service — so it stays within the scale-to-zero cost model.
Each run: load the book, fetch a live price per position, evaluate stop-losses,
emit Alerts, and notify on critical ones.

Stop action:
  paper book (default): auto-exit the stopped position (credit cash, drop it,
    persist) — that's the point of a stop.
  live (ENABLE_LIVE_TRADING): alert only — real exits must go through the broker
    (deferred), so we never silently sell a real position here.

Price lookup is isolated behind ``_current_price`` so tests can monkeypatch it.
"""

from __future__ import annotations

import logging

from config import SETTINGS

from agents.contracts import Alert
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from persistence.store import load_portfolio, recompute, save_portfolio

logger = logging.getLogger("agents.monitor")


def _current_price(ticker: str):
    """Live price for a ticker (Groww quote → yfinance fallback). Monkeypatched in tests."""
    try:
        from enrichment.market_data import get_default_provider
        q = get_default_provider().get_quote(ticker)
        if q and q.get("ltp"):
            return float(q["ltp"])
    except Exception:
        pass
    try:
        import yfinance as yf
        price = yf.Ticker(f"{ticker}.NS").fast_info.last_price
        return float(price) if price else None
    except Exception:
        return None


def _notify(alerts: list[Alert]) -> None:
    if not (getattr(SETTINGS, "TELEGRAM_BOT_TOKEN", "") and getattr(SETTINGS, "TELEGRAM_CHAT_ID", "")):
        return
    from notifications.telegram_notifier import _send_text

    body = "\n".join(f"{a.severity.upper()} <b>{a.ticker}</b>: {a.message}" for a in alerts)
    _send_text(SETTINGS.TELEGRAM_CHAT_ID, f"<b>⚠️ Position alerts</b>\n{body}")


@agent_node("monitor", enabled_flag="ENABLE_MONITORING_AGENT")
def monitoring_node(state: AgentState) -> dict:
    book = state.get("book") or load_portfolio()
    if not book.positions:
        logger.info("monitor: no open positions")
        return {"status": RunStatus.COMPLETED}

    live = bool(getattr(SETTINGS, "ENABLE_LIVE_TRADING", False))
    alerts: list[Alert] = []
    remaining = []
    exited = 0

    for pos in book.positions:
        price = _current_price(pos.ticker)
        if price is None:
            alerts.append(Alert(ticker=pos.ticker, kind="anomaly", severity="warn",
                                message="no live price"))
            remaining.append(pos)
            continue

        if pos.stop_price and price <= pos.stop_price:
            alerts.append(Alert(ticker=pos.ticker, kind="stop_triggered", severity="critical",
                                message=f"price {price:.2f} ≤ stop {pos.stop_price:.2f}"))
            if not live:                       # paper: auto-exit
                book.cash = round(book.cash + pos.qty * price, 2)
                exited += 1
                continue
            remaining.append(pos)              # live: alert only, keep position
        else:
            remaining.append(pos)

    if exited:
        book.positions = remaining
        book = recompute(book)
        save_portfolio(book)
        logger.info("monitor: auto-exited %d stopped position(s); cash=%.2f", exited, book.cash)

    if any(a.severity == "critical" for a in alerts):
        _notify([a for a in alerts if a.severity == "critical"])

    logger.info("monitor: %d position(s) checked, %d alert(s)", len(book.positions) + exited, len(alerts))
    return {"status": RunStatus.COMPLETED, "alerts": alerts, "book": book}
