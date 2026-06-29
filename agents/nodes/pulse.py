"""Market-pulse agent — proactive intraday shock watcher (Part A).

A short, scheduled job (every 1–2 min) that ALERTS (no broker action) on four
trigger families:

  1. Index/price move  (market hours only)
  2. India VIX spike    (market hours only)
  3. News/geopolitical  (always — a shock predicts the gap)
  4. Global cross-asset (always — the pre-open early read)

Session-only metrics (NIFTY/VIX/holdings) are gated by ``_market_open()`` so we
never poll a closed exchange. Global + news run pre-open too. Debounce is
**once-per-episode**: a trigger fires when it first crosses its threshold, then
disarms until the metric normalises (with ``PULSE_ALERT_COOLDOWN_MIN`` as a
secondary floor). State persists in ``persistence.store`` pulse-state.

Clock + data fetchers are isolated at module scope so tests monkeypatch them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config import SETTINGS

from agents.contracts import Alert
from agents.nodes.base import agent_node
from agents.state import AgentState, RunStatus
from enrichment.market_pulse import (  # noqa: F401  (patched in tests)
    classify_shock,
    global_signals,
    index_levels,
    shock_headlines,
)
from enrichment.market_pulse import _pct_change as _ticker_pct
from persistence.store import load_portfolio, load_pulse_state, save_pulse_state

logger = logging.getLogger("agents.pulse")

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


def _now_ist() -> datetime:
    """Current time in IST. Monkeypatched in tests."""
    return datetime.now(IST)


def _market_open(now: datetime | None = None) -> bool:
    now = now or _now_ist()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    o = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    c = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return o <= now <= c


def _episode_fire(pstate: dict, key: str, breached: bool, now: datetime, cooldown_min: int) -> bool:
    """Once-per-episode debounce. Fires once on threshold cross, re-arms only
    after the metric normalises. ``PULSE_ALERT_COOLDOWN_MIN`` is a secondary floor."""
    t = pstate.setdefault(key, {"armed": True, "last_alert_ts": None})
    if not breached:
        t["armed"] = True          # episode over → re-arm
        return False
    if not t.get("armed", True):
        return False               # already alerted this episode
    last = t.get("last_alert_ts")
    if last and cooldown_min:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < cooldown_min * 60:
                return False
        except Exception:
            pass
    t["armed"] = False
    t["last_alert_ts"] = now.isoformat()
    return True


def _global_event_keys(breaches: dict) -> set[str]:
    """Map breached global tickers → sector-map event keys."""
    keys: set[str] = set()
    for sym, info in breaches.items():
        if not info.get("breached"):
            continue
        pct = info.get("pct") or 0.0
        if sym == "BZ=F" and pct > 0:
            keys.add("crude_up")
        elif sym == "INR=X" and pct > 0:
            keys.add("inr_weak")
        elif sym in ("ES=F", "NQ=F"):
            keys.add("us_tech_selloff")
        elif sym in ("^KS11", "^N225", "^HSI"):
            keys.add("asia_riskoff")
    return keys


@agent_node("pulse")
def pulse_node(state: AgentState) -> dict:
    now = _now_ist()
    open_now = _market_open(now)
    pstate = load_pulse_state()
    book = load_portfolio()
    positions = book.positions or []
    cooldown = int(getattr(SETTINGS, "PULSE_ALERT_COOLDOWN_MIN", 20))

    alerts: list[Alert] = []
    idx = {"nifty_pct": None, "vix_pct": None}

    # ── Triggers 1 & 2: index + VIX (market hours only) ───────────────────────
    if open_now:
        idx = index_levels()
        nifty_pct = idx.get("nifty_pct")
        if nifty_pct is not None and nifty_pct <= -float(SETTINGS.PULSE_INDEX_DROP_PCT):
            if _episode_fire(pstate, "index", True, now, cooldown):
                exposed = ", ".join(p.ticker for p in positions) or "no open positions"
                alerts.append(Alert(ticker="NIFTY", kind="anomaly", severity="critical",
                                    message=f"NIFTY {nifty_pct:+.2f}% ({idx.get('nifty_level')}). Exposed: {exposed}"))
        else:
            _episode_fire(pstate, "index", False, now, cooldown)

        vix_pct = idx.get("vix_pct")
        if vix_pct is not None and vix_pct >= float(SETTINGS.PULSE_VIX_SPIKE_PCT):
            if _episode_fire(pstate, "vix", True, now, cooldown):
                alerts.append(Alert(ticker="INDIAVIX", kind="anomaly", severity="warn",
                                    message=f"India VIX {vix_pct:+.2f}% spike ({idx.get('vix_level')}) — risk-off"))
        else:
            _episode_fire(pstate, "vix", False, now, cooldown)

        # Per-holding intraday drop (vs prior close, via yfinance).
        for p in positions:
            pct, _ = _ticker_pct(f"{p.ticker}.NS")
            key = f"holding:{p.ticker}"
            breached = pct is not None and pct <= -float(SETTINGS.PULSE_HOLDING_DROP_PCT)
            if breached and _episode_fire(pstate, key, True, now, cooldown):
                alerts.append(Alert(ticker=p.ticker, kind="anomaly", severity="warn",
                                    message=f"{p.ticker} {pct:+.2f}% intraday"))
            elif not breached:
                _episode_fire(pstate, key, False, now, cooldown)

    # ── Trigger 4: global cross-asset (always) ────────────────────────────────
    if getattr(SETTINGS, "PULSE_GLOBAL_ENABLED", True):
        tickers = dict(getattr(SETTINGS, "PULSE_GLOBAL_TICKERS", {}) or {})
        # Crude threshold is treated as absolute (sharp move either way).
        breaches = global_signals(tickers)
        if "BZ=F" in breaches and breaches["BZ=F"].get("pct") is not None:
            thr = abs(tickers.get("BZ=F", 4.0))
            breaches["BZ=F"]["breached"] = abs(breaches["BZ=F"]["pct"]) >= thr
        any_breach = any(v.get("breached") for v in breaches.values())
        if any_breach:
            if _episode_fire(pstate, "global", True, now, cooldown):
                moved = ", ".join(
                    f"{s} {v['pct']:+.2f}%" for s, v in breaches.items()
                    if v.get("breached") and v.get("pct") is not None
                )
                event_keys = _global_event_keys(breaches)
                sect_map = getattr(SETTINGS, "PULSE_GLOBAL_SECTOR_MAP", {})
                headwind = {sec for k in event_keys for sec in sect_map.get(k, {}).get("headwind", [])}
                exposed = [p.ticker for p in positions if (p.sector or "") in headwind] or \
                          [p.ticker for p in positions]
                exposed_str = ", ".join(exposed) if exposed else "no open positions"
                sec_str = ", ".join(sorted(headwind)) or "broad market"
                alerts.append(Alert(ticker="GLOBAL", kind="anomaly", severity="critical",
                                    message=f"Global shock: {moved}. Likely Indian headwind: {sec_str}. "
                                            f"Exposed: {exposed_str}"))
        else:
            _episode_fire(pstate, "global", False, now, cooldown)

    # ── Trigger 3: news/geopolitical (always, tiered to control LLM cost) ──────
    if getattr(SETTINGS, "PULSE_NEWS_ENABLED", True):
        elevated = bool(alerts) or (
            idx.get("nifty_pct") is not None
            and abs(idx["nifty_pct"]) >= 0.7 * float(SETTINGS.PULSE_INDEX_DROP_PCT)
        )
        gap_min = int(getattr(SETTINGS, "PULSE_NEWS_MIN_GAP_MIN", 5))
        last_check = pstate.get("last_news_check")
        gap_ok = True
        if last_check:
            try:
                gap_ok = (now - datetime.fromisoformat(last_check)).total_seconds() >= gap_min * 60
            except Exception:
                gap_ok = True
        if elevated or gap_ok:
            pstate["last_news_check"] = now.isoformat()
            headlines = shock_headlines(list(getattr(SETTINGS, "PULSE_SHOCK_KEYWORDS", [])))
            verdict = classify_shock(headlines)
            is_shock = bool(verdict.get("is_shock"))
            if is_shock and _episode_fire(pstate, "news", True, now, cooldown):
                alerts.append(Alert(ticker="NEWS", kind="anomaly",
                                    severity="critical" if verdict.get("severity") == "high" else "warn",
                                    message=f"Shock news ({verdict.get('severity')}): {verdict.get('summary')}"))
            elif not is_shock:
                _episode_fire(pstate, "news", False, now, cooldown)

    save_pulse_state(pstate)

    if alerts:
        _notify(alerts)
    logger.info("pulse: market_open=%s, %d alert(s)", open_now, len(alerts))
    return {"status": RunStatus.COMPLETED, "alerts": alerts}


def _notify(alerts: list[Alert]) -> None:
    if not (getattr(SETTINGS, "TELEGRAM_BOT_TOKEN", "") and getattr(SETTINGS, "TELEGRAM_CHAT_ID", "")):
        return
    from notifications.telegram_notifier import send_pulse_alert

    body = "\n".join(f"{a.severity.upper()} <b>{a.ticker}</b>: {a.message}" for a in alerts)
    send_pulse_alert(f"<b>⚡ Market Pulse</b>\n{body}")
