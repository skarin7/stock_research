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
    logger.info("Incoming message from chat %s: %.80s…", chat_id, text)

    # Trade approval commands resume a suspended live run (place/reject the order)
    # instead of going to the research chat agent.
    if text.startswith("/approve") or text.startswith("/reject"):
        from agents.approval import handle_approval_command

        try:
            reply = handle_approval_command(text)
        except Exception as e:
            logger.error("approval command failed: %s", e)
            reply = f"⚠️ Could not process approval: {e}"
        if reply is not None:
            _send(reply, chat_id)
        return {"ok": True}

    # Placeholder so the user sees activity immediately.
    try:
        _send("🔎 Researching…", chat_id)
    except Exception as e:
        logger.warning("Could not send placeholder: %s", e)

    from agents.chat.agent import run_turn

    reply = run_turn(chat_id, text)
    try:
        _send(reply, chat_id)
    except Exception as e:
        logger.error("Could not send reply: %s", e)

    return {"ok": True}
