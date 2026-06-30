"""
Downloads and parses NSE EOD Bhavcopy for a given trading date.

Sources (previous trading day — bhavcopy is EOD, not available intraday):
  OHLCV + 52w: nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip
               → pd{DDMMYY}.csv inside the zip
  Delivery %:  nsearchives.nseindia.com/archives/equities/mto/MTO_{DDMMYY}.DAT
"""

import io
import logging
import zipfile
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

from scrapers.http_client import NSE_HEADERS, NSE_TIMEOUT

logger = logging.getLogger(__name__)

_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{ddmmyy}.zip"
)
_MTO_URL = (
    "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT"
)


def _prev_trading_day(ref: date) -> date:
    """Return the most recent weekday before ref (skips Sat/Sun; no holiday calendar)."""
    d = ref - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def _fetch_pr_zip(trading_day: date) -> pd.DataFrame:
    """Download PR zip, extract pd{DDMMYY}.csv, return DataFrame (EQ only)."""
    ddmmyy = trading_day.strftime("%d%m%y")
    url = _BHAVCOPY_URL.format(ddmmyy=ddmmyy)
    logger.info("Downloading NSE Bhavcopy zip: %s", url)

    resp = requests.get(url, headers=NSE_HEADERS, timeout=NSE_TIMEOUT)
    if resp.status_code == 404:
        raise FileNotFoundError(
            f"NSE Bhavcopy not found for {trading_day} — may be a holiday"
        )
    resp.raise_for_status()

    ddmmyyyy = trading_day.strftime("%d%m%Y")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        pd_name = f"pd{ddmmyyyy}.csv"
        if pd_name not in zf.namelist():
            available = zf.namelist()
            raise FileNotFoundError(
                f"{pd_name} not in zip; available: {available}"
            )
        with zf.open(pd_name) as f:
            df = pd.read_csv(f)

    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "SYMBOL": "symbol",
        "SERIES": "series",
        "OPEN_PRICE": "open",
        "HIGH_PRICE": "high",
        "LOW_PRICE": "low",
        "CLOSE_PRICE": "close",
        "NET_TRDQTY": "volume",
        "HI_52_WK": "52w_high",
        "LO_52_WK": "52w_low",
    })

    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["series"] = df["series"].str.strip()
    df = df[df["series"] == "EQ"]

    for col in ["open", "high", "low", "close", "volume", "52w_high", "52w_low"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
            )

    keep = [c for c in ["symbol", "close", "volume", "52w_high", "52w_low"] if c in df.columns]
    return df[keep].drop_duplicates(subset="symbol").set_index("symbol")


def _fetch_mto(trading_day: date) -> pd.DataFrame:
    """Download MTO DAT file, return DataFrame with deliv_qty + delivery_pct (EQ only)."""
    ddmmyyyy = trading_day.strftime("%d%m%Y")
    url = _MTO_URL.format(ddmmyyyy=ddmmyyyy)
    logger.info("Downloading NSE MTO: %s", url)

    resp = requests.get(url, headers=NSE_HEADERS, timeout=NSE_TIMEOUT)
    if resp.status_code == 404:
        logger.warning("MTO not found for %s — delivery data unavailable", trading_day)
        return pd.DataFrame()
    resp.raise_for_status()

    # MTO format: RecordType, SrNo, Symbol, Series, QtdQty, DelivQty, DelivPct
    # Skip header rows (record type != 20)
    rows = []
    for line in resp.text.splitlines():
        parts = line.split(",")
        if len(parts) >= 7 and parts[0].strip() == "20" and parts[3].strip() == "EQ":
            rows.append({
                "symbol": parts[2].strip().upper(),
                "deliv_qty": parts[5].strip(),
                "delivery_pct": parts[6].strip(),
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["deliv_qty"] = pd.to_numeric(df["deliv_qty"], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")
    return df.drop_duplicates(subset="symbol").set_index("symbol")


def download_bhavcopy(for_date: Optional[date] = None) -> pd.DataFrame:
    """
    Download and parse NSE Bhavcopy for the trading day before `for_date`.
    Returns a DataFrame indexed by symbol (EQ series only), with columns:
      close, volume, 52w_high, 52w_low, deliv_qty, delivery_pct
    """
    ref = for_date or date.today()
    trading_day = _prev_trading_day(ref)

    pr = _fetch_pr_zip(trading_day)
    mto = _fetch_mto(trading_day)

    if not mto.empty:
        df = pr.join(mto, how="left")
    else:
        df = pr

    logger.info("Bhavcopy: %d EQ symbols loaded for %s", len(df), trading_day)
    return df
