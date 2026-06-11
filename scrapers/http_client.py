"""Shared HTTP utilities for NSE scrapers."""
from __future__ import annotations

import requests

NSE_TIMEOUT = 30          # seconds
NSE_SESSION_DELAY = 1.0   # seconds between cookie-priming requests

NSE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def nse_session(extra_headers: dict[str, str] | None = None) -> requests.Session:
    """requests.Session pre-loaded with NSE browser headers."""
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    if extra_headers:
        s.headers.update(extra_headers)
    return s
