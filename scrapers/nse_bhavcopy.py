"""
Downloads and parses the NSE EOD Bhavcopy CSV for a given trading date.
Contains: symbol, close price, volume, deliverable qty, delivery %.
URL pattern: https://archives.nseindia.com/products/content/sec_bhavdata_full_{DD-Mon-YYYY}.csv
"""

import io
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd
import requests

from scrapers.http_client import NSE_HEADERS, NSE_TIMEOUT

logger = logging.getLogger(__name__)

BHAVCOPY_URL = (
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{date_str}.csv"
)

# Canonical column renames (NSE headers vary slightly across dates)
_COLUMN_RENAMES = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "PREV_CLOSE": "prev_close",
    "LTP": "close",
    "CLOSE": "close",
    "VWAP": "vwap",
    "52W H": "52w_high",
    "52W L": "52w_low",
    "VOLUME": "volume",
    "VALUE": "value",
    "NO OF TRADES": "trades",
    "DELIV QTY": "deliv_qty",
    "DELIV. %": "delivery_pct",
    "%DELIVERBLE": "delivery_pct",
}


def _date_str(for_date: date) -> str:
    """Format date as DD-Mon-YYYY (e.g. 12-May-2026)."""
    return for_date.strftime("%d-%b-%Y")


def download_bhavcopy(for_date: Optional[date] = None) -> pd.DataFrame:
    """
    Download and parse NSE Bhavcopy for `for_date` (defaults to today).
    Returns a DataFrame indexed by symbol (EQ series only), with columns:
    symbol, close, volume, deliv_qty, delivery_pct, 52w_high, 52w_low
    """
    target = for_date or date.today()
    date_str = _date_str(target)
    url = BHAVCOPY_URL.format(date_str=date_str)

    logger.info("Downloading NSE Bhavcopy: %s", url)
    resp = requests.get(url, headers=NSE_HEADERS, timeout=NSE_TIMEOUT)
    if resp.status_code == 404:
        raise FileNotFoundError(
            f"NSE Bhavcopy not found for {date_str} — may be a holiday or weekend"
        )
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))

    # Strip whitespace from column names
    df.columns = df.columns.str.strip()

    # Rename known columns
    rename_map = {c: _COLUMN_RENAMES[c] for c in df.columns if c in _COLUMN_RENAMES}
    df = df.rename(columns=rename_map)

    # Keep EQ series only (avoid derivatives, ETFs, etc.)
    if "series" in df.columns:
        df = df[df["series"].str.strip() == "EQ"]

    # Normalise symbol
    df["symbol"] = df["symbol"].str.strip().str.upper()

    # Numeric coercion for key columns
    numeric_cols = ["close", "volume", "deliv_qty", "delivery_pct", "52w_high", "52w_low"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")

    keep = [c for c in ["symbol", "close", "volume", "deliv_qty", "delivery_pct", "52w_high", "52w_low"] if c in df.columns]
    df = df[keep].drop_duplicates(subset="symbol").set_index("symbol")

    logger.info("Bhavcopy: %d EQ symbols loaded for %s", len(df), date_str)
    return df
