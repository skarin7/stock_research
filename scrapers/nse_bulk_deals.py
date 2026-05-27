"""
Downloads and parses NSE bulk/block deals for a given trading date.
API: https://www.nseindia.com/api/historical/bulk-deals?from=DD-MM-YYYY&to=DD-MM-YYYY
NSE requires browser-like headers + session cookies from the homepage.
Note: NSE Cloudflare protection may block direct API access; failure is handled
gracefully in main.py.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com"
BULK_DEALS_REPORT_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
BULK_DEALS_URL = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BULK_DEALS_REPORT_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}


def _get_nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    # Seed cookies by visiting homepage then the report page
    session.get(NSE_HOME, timeout=15)
    time.sleep(1)
    session.get(BULK_DEALS_REPORT_PAGE, timeout=15)
    time.sleep(1)
    return session


def download_bulk_deals(for_date: Optional[date] = None) -> pd.DataFrame:
    """
    Download NSE bulk deals for `for_date` (defaults to today).
    Returns a DataFrame with columns:
    symbol, client_name, buy_sell, quantity, price, deal_type
    """
    target = for_date or date.today()
    # NSE API needs a range; using previous day → target captures today's deals correctly
    from_date = (target - timedelta(days=1)).strftime("%d-%m-%Y")
    to_date = target.strftime("%d-%m-%Y")

    logger.info("Downloading NSE bulk deals for %s (range: %s to %s)", to_date, from_date, to_date)
    _empty = pd.DataFrame(columns=["symbol", "client_name", "buy_sell", "quantity", "price", "deal_type"])
    try:
        session = _get_nse_session()
        resp = session.get(BULK_DEALS_URL, params={"optionType": "bulk_deals", "from": from_date, "to": to_date}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("NSE bulk deals unavailable (bot-protection or holiday): %s", e)
        return _empty

    payload = resp.json()

    # NSE returns {"data": [...]} or the list directly
    records = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not records:
        logger.info("No bulk deals found for %s", to_date)
        return _empty

    df = pd.DataFrame(records)

    # Normalise column names
    col_map = {
        "BD_SYMBOL": "symbol",
        "BD_CLIENT_NAME": "client_name",
        "BD_BUY_SELL": "buy_sell",
        "BD_QTY_TRD": "quantity",
        "BD_TP_WATP": "price",
        "BD_SCRIP_NAME": "deal_type",  # no deal_type field; use scrip name as fallback
    }
    df = df.rename(columns={c: col_map[c] for c in df.columns if c in col_map})

    for col in ["symbol", "client_name", "buy_sell"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    df["symbol"] = df["symbol"].str.upper()
    df["quantity"] = pd.to_numeric(df.get("quantity", 0), errors="coerce").fillna(0).astype(int)
    df["price"] = pd.to_numeric(df.get("price", 0), errors="coerce").fillna(0.0)

    keep = [c for c in ["symbol", "client_name", "buy_sell", "quantity", "price", "deal_type"] if c in df.columns]
    df = df[keep]

    logger.info("Bulk deals: %d records for %s", len(df), to_date)
    return df


def group_bulk_deals_by_symbol(df: pd.DataFrame) -> dict[str, list[dict]]:
    """
    Group bulk deals DataFrame into a dict keyed by symbol.
    Each value is a list of deal dicts: {client, action, qty, price}.
    """
    result: dict[str, list[dict]] = {}
    for _, row in df.iterrows():
        sym = row["symbol"]
        deal = {
            "client": row.get("client_name", ""),
            "action": row.get("buy_sell", "").upper(),
            "qty": int(row.get("quantity", 0)),
            "price": float(row.get("price", 0)),
        }
        result.setdefault(sym, []).append(deal)
    return result
