"""Register the Telegram webhook URL with the bot (run once after deploy).

Usage:
    python scripts/set_webhook.py <WEBHOOK_URL> [--cert /path/to/cert.crt]

Examples:
    # Domain with valid TLS (no cert needed)
    python scripts/set_webhook.py https://yourdomain.com/telegram/webhook

    # VM IP with self-signed cert (cert must be passed so Telegram trusts it)
    python scripts/set_webhook.py https://<vm-ip>/telegram/webhook --cert /tmp/telegram-webhook.crt

The script uses TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from .env.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from config import SETTINGS  # noqa: E402

import requests  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/set_webhook.py <WEBHOOK_URL> [--cert /path/to/cert.crt]")
        sys.exit(1)

    url = sys.argv[1]
    cert_path: str | None = None
    args = sys.argv[2:]
    if "--cert" in args:
        idx = args.index("--cert")
        cert_path = args[idx + 1]

    if not SETTINGS.TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    base = f"https://api.telegram.org/bot{SETTINGS.TELEGRAM_BOT_TOKEN}"
    params: dict = {"url": url, "allowed_updates": ["message"]}
    if SETTINGS.TELEGRAM_WEBHOOK_SECRET:
        params["secret_token"] = SETTINGS.TELEGRAM_WEBHOOK_SECRET

    if cert_path:
        # Telegram requires multipart/form-data when passing a self-signed cert.
        with open(cert_path, "rb") as f:
            resp = requests.post(f"{base}/setWebhook", data=params,
                                 files={"certificate": f}, timeout=10)
    else:
        resp = requests.post(f"{base}/setWebhook", json=params, timeout=10)

    resp.raise_for_status()
    data = resp.json()
    if data.get("ok"):
        print(f"✅ Webhook set → {url}")
    else:
        print(f"❌ Failed: {data}")
        sys.exit(1)

    info = requests.get(f"{base}/getWebhookInfo", timeout=10).json()
    print(f"   Pending updates: {info.get('result', {}).get('pending_update_count', '?')}")
    print(f"   URL: {info.get('result', {}).get('url', '?')}")


if __name__ == "__main__":
    main()
