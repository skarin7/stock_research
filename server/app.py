"""Telegram webhook server — receives updates and calls the chat agent.

Cloud Run service entrypoint:
    uvicorn server.app:app --host 0.0.0.0 --port 8080

Security:
- X-Telegram-Bot-Api-Secret-Token header must match TELEGRAM_WEBHOOK_SECRET.
- Chat-ID allowlist: only TELEGRAM_CHAT_ID is served; others are silently dropped.
- update_id deduplication: Telegram retries on non-200; we always return 200 and
  track the last seen update_id to ignore retried duplicates.

On every accepted message the handler:
1. Sends a "🔎 researching…" placeholder immediately.
2. Runs the agent turn (may take up to ~30s with tool calls).
3. Sends the answer (chunked if > 4000 chars to stay inside Telegram's limit).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger("server.app")

app = FastAPI(title="stock-intelligence chat webhook")

# In-memory dedup ring: last 100 update_ids (Telegram guarantees monotone IDs
# per bot so this is sufficient without a DB when the service has 1 replica).
_SEEN_UPDATES: deque[int] = deque(maxlen=100)


@app.on_event("startup")
async def _startup():
    from observability.logging_config import setup_logging
    setup_logging()
    from persistence.db import init_db
    init_db()
    try:
        from enrichment.market_data.groww import default_client
        import asyncio
        await asyncio.get_event_loop().run_in_executor(None, default_client)
        import logging
        logging.getLogger(__name__).info("Groww token pre-warmed at server startup")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Groww pre-warm skipped at startup: %s", e)


def _settings():
    from config import SETTINGS
    return SETTINGS


def _send(text: str, chat_id: str) -> None:
    """Send text to Telegram, chunking if needed."""
    from notifications.telegram_notifier import _MAX_MSG, _send_text

    if len(text) <= _MAX_MSG:
        _send_text(chat_id, text)
        return
    # Split on blank lines first; fall back to hard-split at _MAX_MSG.
    parts = text.split("\n\n")
    chunk = ""
    for part in parts:
        candidate = f"{chunk}\n\n{part}".strip() if chunk else part
        if len(candidate) > _MAX_MSG:
            if chunk:
                _send_text(chat_id, chunk)
            chunk = part[:_MAX_MSG]
        else:
            chunk = candidate
    if chunk:
        _send_text(chat_id, chunk)


def _handle_callback(cb: dict, s) -> dict:
    """Process an inline approve/reject button tap (deterministic, no LLM/ReAct)."""
    cb_id = cb.get("id", "")
    data = cb.get("data", "") or ""
    chat_id_raw = ((cb.get("message") or {}).get("chat") or {}).get("id")

    allowed = str(getattr(s, "TELEGRAM_CHAT_ID", ""))
    if allowed and str(chat_id_raw) != allowed:
        logger.debug("Dropping callback from chat %s (not in allowlist)", chat_id_raw)
        return {"ok": True}

    from agents.approval import handle_callback
    from notifications.telegram_notifier import answer_callback

    try:
        reply = handle_callback(data)
    except Exception as e:
        logger.error("callback handling failed: %s", e)
        reply = f"⚠️ Could not process that: {e}"

    try:
        answer_callback(cb_id, "Working…")   # clears the client spinner
    except Exception as e:
        logger.warning("answerCallbackQuery failed: %s", e)

    if reply and chat_id_raw:
        _send(reply, str(chat_id_raw))
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def webhook(request: Request):
    s = _settings()

    # Auth: reject unless the secret header matches.
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = getattr(s, "TELEGRAM_WEBHOOK_SECRET", "")
    if expected and secret != expected:
        raise HTTPException(status_code=403, detail="bad secret")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return {"ok": True}  # malformed body — drop

    # Inline-button taps arrive as callback_query (no message.text).
    if body.get("callback_query"):
        return _handle_callback(body["callback_query"], s)

    update_id: int | None = body.get("update_id")
    message: dict = body.get("message") or {}
    chat_id_raw = (message.get("chat") or {}).get("id")
    text: str = (message.get("text") or "").strip()

    if not chat_id_raw or not text:
        return {"ok": True}  # not a text message

    # Allowlist check.
    allowed = str(getattr(s, "TELEGRAM_CHAT_ID", ""))
    if allowed and str(chat_id_raw) != allowed:
        logger.debug("Dropping message from chat %s (not in allowlist)", chat_id_raw)
        return {"ok": True}

    # Dedup.
    if update_id is not None:
        if update_id in _SEEN_UPDATES:
            logger.debug("Duplicate update_id %s — ignoring", update_id)
            return {"ok": True}
        _SEEN_UPDATES.append(update_id)

    chat_id = str(chat_id_raw)
    logger.info("msg_received", extra={"chat_id": chat_id, "text": text, "update_id": update_id})

    # Tier 1 — HITL approval (deterministic, NO LLM / NO ReAct). Catches the
    # explicit /approve|/reject command AND a bare "approve"/"reject" reply when
    # an approval is pending for this chat. Returns None (→ chat agent) only when
    # there's nothing pending or the message isn't a decision.
    from agents.approval import route_approval

    try:
        reply = route_approval(text)
    except Exception as e:
        logger.error("approval_routing_failed", extra={"error": str(e), "chat_id": chat_id})
        reply = f"⚠️ Could not process approval: {e}"
    if reply is not None:
        logger.info("approval_routed", extra={"chat_id": chat_id, "reply_len": len(reply)})
        _send(reply, chat_id)
        return {"ok": True}

    try:
        _send("🔎 Researching…", chat_id)
    except Exception as e:
        logger.warning("placeholder_send_failed", extra={"error": str(e)})

    import time as _time
    from agents.chat.agent import run_turn

    t0 = _time.monotonic()
    reply = run_turn(chat_id, text)
    duration_ms = round((_time.monotonic() - t0) * 1000)
    logger.info("turn_complete", extra={
        "chat_id": chat_id,
        "duration_ms": duration_ms,
        "reply_length": len(reply),
    })
    try:
        _send(reply, chat_id)
    except Exception as e:
        logger.error("reply_send_failed", extra={"error": str(e), "chat_id": chat_id})

    return {"ok": True}
