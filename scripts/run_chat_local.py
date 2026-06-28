"""Local development chat loop — long-polls Telegram getUpdates.

No public URL needed; useful for validating the agent before deploying the
Cloud Run webhook. Exits cleanly on Ctrl-C.

Usage:
    python scripts/run_chat_local.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv()

from config import SETTINGS  # noqa: E402

from observability.logging_config import setup_logging
setup_logging()
logger = logging.getLogger("chat_local")

_BASE = f"https://api.telegram.org/bot{SETTINGS.TELEGRAM_BOT_TOKEN}"
_ALLOWED_CHAT = str(SETTINGS.TELEGRAM_CHAT_ID)
_TIMEOUT = 30   # long-poll timeout seconds
_RETRY_DELAY = 5


def _get_updates(offset: int | None) -> list[dict]:
    params = {"timeout": _TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{_BASE}/getUpdates", params=params, timeout=_TIMEOUT + 5)
    resp.raise_for_status()
    return resp.json().get("result", [])


def _send(chat_id: str, text: str) -> None:
    from server.app import _send

    _send(text, chat_id)


def main() -> None:
    if not SETTINGS.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot poll")
        sys.exit(1)

    from agents.chat.agent import run_turn

    logger.info("Polling Telegram (chat=%s) — Ctrl-C to stop", _ALLOWED_CHAT)
    offset: int | None = None

    while True:
        try:
            updates = _get_updates(offset)
        except KeyboardInterrupt:
            logger.info("Stopped.")
            break
        except Exception as e:
            logger.warning("getUpdates failed: %s — retry in %ds", e, _RETRY_DELAY)
            time.sleep(_RETRY_DELAY)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if not chat_id or not text:
                continue
            if _ALLOWED_CHAT and chat_id != _ALLOWED_CHAT:
                continue
            logger.info("→ %s: %s", chat_id, text[:80])
            _send(chat_id, "🔎 Researching…")
            reply = run_turn(chat_id, text)
            _send(chat_id, reply)


if __name__ == "__main__":
    main()
