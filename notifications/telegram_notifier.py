"""
Sends the daily stock report to a Telegram chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Start a chat with the bot, then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates  → copy chat.id
"""

import logging
from pathlib import Path

import requests

from config import SETTINGS

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_MSG = 4000  # Telegram limit is 4096; stay under for safety

BULLISH_SCORE_THRESHOLD = 7   # signal score ≥ this → green dot
BEARISH_SCORE_THRESHOLD = 4   # signal score ≤ this → red dot
TOP_STOCKS_DISPLAY = 10       # cap stock cards in the report
MEDAL_COUNT = 3               # 🥇🥈🥉 for the top 3, numbered after


def _post(method: str, **kwargs) -> bool:
    url = _API.format(token=SETTINGS.TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, timeout=30, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram %s failed: %s", method, data.get("description"))
            return False
        return True
    except Exception as e:
        logger.error("Telegram %s error: %s", method, e)
        return False


def send_text(chat_id: str, text: str) -> bool:
    return _post("sendMessage", data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })


# Keep private alias for internal callers that haven't been migrated yet.
_send_text = send_text


def send_buttons(chat_id: str, text: str, keyboard: list[list[dict]]) -> bool:
    """Send a message with an inline keyboard. ``keyboard`` is a list of button
    rows, each button {"text": str, "callback_data": str}."""
    import json

    return _post("sendMessage", data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps({"inline_keyboard": keyboard}),
    })


def answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a button tap (clears the client-side spinner)."""
    return _post("answerCallbackQuery", data={"callback_query_id": callback_query_id, "text": text})


def _sentiment_emoji(signals: dict, key: str) -> str:
    score = signals.get(key, {}).get("score", 5)
    if score >= BULLISH_SCORE_THRESHOLD:
        return "🟢"
    if score <= BEARISH_SCORE_THRESHOLD:
        return "🔴"
    return "🟡"


def _build_macro_message(report_date: str, macro_context: str) -> str:
    lines = [
        f"<b>📊 Stock Intelligence — {report_date}</b>",
        "",
    ]
    if macro_context:
        lines.append("<b>🌍 Market Macro Today</b>")
        for line in macro_context.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    return "\n".join(lines)


def _build_stock_messages(top_stocks: list[dict]) -> list[str]:
    """
    Build one or more Telegram messages for the stock picks.
    Each stock gets a clear card. Messages are split if they exceed the limit.
    """
    MEDALS = ["🥇", "🥈", "🥉"]
    messages = []
    current = ["<b>🏆 Top Picks</b>", ""]

    for i, s in enumerate(top_stocks[:TOP_STOCKS_DISPLAY]):
        ticker    = s.get("ticker", "")
        score     = s.get("composite_score", 0)
        signals   = s.get("signals", {})
        rationale = s.get("investment_rationale", "")
        flags     = s.get("risk_flags", [])
        ltp       = s.get("ltp")
        high52    = s.get("52w_high")
        low52     = s.get("52w_low")

        medal = MEDALS[i] if i < MEDAL_COUNT else f"{i + 1}."

        # Header: medal + ticker + score (no block-char bar)
        card = [f"{medal} <b>{ticker}</b>  <b>{score:.1f}/10</b>"]

        # Price line
        if ltp:
            price_line = f"💰 ₹{ltp:,.1f}"
            if high52 and low52:
                pct = int(ltp / high52 * 100)
                price_line += f"  ·  52W ₹{low52:,.0f}–{high52:,.0f} ({pct}%)"
            card.append(price_line)

        # Signal line — emoji BEFORE label so wraps stay clean
        def e(k: str) -> str:
            return _sentiment_emoji(signals, k)
        card.append(
            f"{e('news_sentiment')}News · {e('momentum')}Mom · "
            f"{e('value')}Val · {e('bulk_deals')}Bulk"
        )

        # Technicals line — MACD cross / 20d breakout / RSI (only when present)
        tech = s.get("technicals") or {}
        tech_parts = []
        if tech.get("macd_cross") == "bullish":
            tech_parts.append("📈 MACD↑")
        elif tech.get("macd_cross") == "bearish":
            tech_parts.append("📉 MACD↓")
        if tech.get("breakout_20d"):
            tech_parts.append("🚀 20d breakout")
        if tech.get("rsi14") is not None:
            tech_parts.append(f"RSI {tech['rsi14']:.0f}")
        if tech_parts:
            card.append(" · ".join(tech_parts))

        # Rationale
        if rationale:
            card.append(f"📝 {rationale[:160]}{'…' if len(rationale) > 160 else ''}")

        # Risk flags
        if flags:
            card.append(f"⚠️ {' · '.join(flags[:2])}")

        card.append("")  # spacer

        block = "\n".join(card)

        # Split into a new message if this card would overflow
        if len("\n".join(current) + block) > _MAX_MSG and len(current) > 2:
            messages.append("\n".join(current))
            current = [block]
        else:
            current.extend(card)

    if current:
        messages.append("\n".join(current))

    return messages


_LEGEND = """\
<b>📖 How to read this report</b>

<b>Score (X/10)</b>
⭐ 8–10 · Strong buy signal
👍 6–7  · Moderate signal
😐 4–5  · Neutral / watch
👎 1–3  · Weak / avoid

<b>Signal dots</b>
🟢 Bullish  🟡 Neutral  🔴 Bearish

<b>News</b> — Recent headlines sentiment
<b>Mom</b> — Price momentum (last 5 days)
<b>Val</b> — PE vs sector (undervalued?)
<b>Bulk</b> — Institutional bulk deal activity

<b>52W %</b> — Where LTP sits vs 52-week high
  90–100% · Near breakout / all-time high
  60–89%  · Mid-range
  &lt;60%    · Deep in range, possible turnaround

<b>⚠️ flags</b> — Earnings proximity or known risks

<i>Scores are AI-generated (Claude Haiku). Not financial advice.</i>"""


def send_pulse_alert(text: str) -> bool:
    """Send a market-pulse shock alert. No-op (returns False) if Telegram is
    unconfigured. Already-HTML text; chunked to stay under the Telegram limit."""
    if not SETTINGS.TELEGRAM_BOT_TOKEN or not SETTINGS.TELEGRAM_CHAT_ID:
        logger.info("Telegram not configured — skipping pulse alert")
        return False
    ok = True
    for i in range(0, len(text), _MAX_MSG):
        ok = _send_text(SETTINGS.TELEGRAM_CHAT_ID, text[i:i + _MAX_MSG]) and ok
    return ok


def send_intraday_watchlist(alert_text: str) -> bool:
    """Send the intraday next-day watchlist alert (already HTML-formatted by
    intraday/report.py). Returns True on success, False if Telegram is
    unconfigured or the send fails."""
    if not SETTINGS.TELEGRAM_BOT_TOKEN or not SETTINGS.TELEGRAM_CHAT_ID:
        logger.info("Telegram not configured — skipping intraday watchlist")
        return False

    ok = True
    # Split on blank lines if the alert exceeds Telegram's limit.
    if len(alert_text) <= _MAX_MSG:
        chunks = [alert_text]
    else:
        chunks, cur = [], ""
        for block in alert_text.split("\n\n"):
            if len(cur) + len(block) + 2 > _MAX_MSG and cur:
                chunks.append(cur)
                cur = block
            else:
                cur = f"{cur}\n\n{block}" if cur else block
        if cur:
            chunks.append(cur)

    for chunk in chunks:
        ok = _send_text(SETTINGS.TELEGRAM_CHAT_ID, chunk) and ok
    if ok:
        logger.info("Intraday watchlist sent to Telegram")
    return ok


def send_report(
    top_stocks: list[dict],
    report_path: Path,
    report_date: str,
    macro_context: str = "",
) -> bool:
    """
    Send mobile-friendly Telegram report:
      Message 1: Date + macro context
      Message 2+: Stock cards (split if > 4000 chars)
    Returns True if the macro message sent successfully.
    """
    if not SETTINGS.TELEGRAM_BOT_TOKEN or not SETTINGS.TELEGRAM_CHAT_ID:
        logger.info("Telegram not configured — skipping notification")
        return False

    chat_id = SETTINGS.TELEGRAM_CHAT_ID

    # Message 1: macro context
    macro_msg = _build_macro_message(report_date, macro_context)
    ok = _send_text(chat_id, macro_msg)

    # Message 2+: stock picks
    for msg in _build_stock_messages(top_stocks):
        _send_text(chat_id, msg)

    # Final message: legend
    _send_text(chat_id, _LEGEND)

    if ok:
        logger.info("Telegram notification sent successfully")
    return ok
