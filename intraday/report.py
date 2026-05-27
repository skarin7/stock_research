"""Render the intraday watchlist (spec section 9 format) and persist it.

Writes ``output/YYYY-MM-DD/intraday_watchlist.{json,txt}`` and builds the
Telegram message body.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from config import SETTINGS

logger = logging.getLogger(__name__)

_DISCLAIMER = "⚠️ Not financial advice. Use strict stop-losses."


def _band_header(band: str) -> str:
    return {
        "HIGH": "🔥 HIGH CONVICTION (Score 7+)",
        "MODERATE": "👀 WATCH LIST (Score 5–6) — Confirm at open",
    }.get(band, band)


def build_alert(
    watchlist: list[dict],
    report_date: date,
    nifty_change_pct: Optional[float] = None,
) -> str:
    """Build the watchlist alert text (HTML, Telegram-ready)."""
    lines = [f"<b>📈 INTRADAY WATCHLIST — {report_date.isoformat()}</b>"]
    if nifty_change_pct is not None:
        lines.append(f"Nifty close: {nifty_change_pct:+.2f}%")
    lines.append("")

    if not watchlist:
        lines.append(f"No stocks scored ≥ {SETTINGS.INTRADAY_SCORE_THRESHOLD} today. No edge — sit out.")
        lines.append("")
        lines.append(f"<i>{_DISCLAIMER}</i>")
        return "\n".join(lines)

    high = [r for r in watchlist if r["conviction"] == "HIGH"]
    moderate = [r for r in watchlist if r["conviction"] == "MODERATE"]

    for band, rows in (("HIGH", high), ("MODERATE", moderate)):
        if not rows:
            continue
        lines.append(f"<b>{_band_header(band)}</b>")
        for i, r in enumerate(rows, 1):
            sym = r["symbol"]
            score = r["score"]
            lines.append(f"{i}. <b>{sym}</b> — Score: {score}")
            for reason in r.get("reasons", []):
                lines.append(f"   ✔ {reason}")
            close = r.get("close")
            if close:
                lines.append(f"   Entry: above ₹{close:,.1f} on volume confirm")
        lines.append("")

    lines.append(f"<i>{_DISCLAIMER}</i>")
    return "\n".join(lines)


def write_watchlist(watchlist: list[dict], report_date: date) -> Path:
    """Persist watchlist as JSON + text under output/YYYY-MM-DD/. Returns json path."""
    out_dir = Path(SETTINGS.OUTPUT_DIR) / report_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "intraday_watchlist.json"
    json_path.write_text(json.dumps(
        {"date": report_date.isoformat(), "watchlist": watchlist},
        indent=2, ensure_ascii=False,
    ))

    txt_path = out_dir / "intraday_watchlist.txt"
    # Strip HTML tags for the plain-text artifact.
    import re
    plain = re.sub(r"<[^>]+>", "", build_alert(watchlist, report_date))
    txt_path.write_text(plain)

    logger.info("Watchlist written → %s", json_path)
    return json_path
