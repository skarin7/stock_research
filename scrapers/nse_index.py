"""
NSE index constituent fetcher.
Downloads the official NSE index CSV and returns stocks in the same
dict format as screener_scraper so the rest of the pipeline is unchanged.

Supported indices (set STOCK_UNIVERSE in config):
  nifty50, nifty100, nifty200, nifty500
"""

import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_INDEX_URLS = {
    "nifty50":  "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "nifty500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_index_stocks(index: str = "nifty200") -> list[dict]:
    """
    Download NSE index CSV and return a list of stock dicts compatible
    with the screener_scraper output format.

    Returned keys per stock:
      symbol, company, pe_ratio, market_cap_cr, debt_equity,
      delivery_pct, price, 52w_high, 52w_low
    Fundamental fields are None — filled in later by Groww enrichment.
    """
    index = index.lower()
    url = _INDEX_URLS.get(index)
    if not url:
        raise ValueError(f"Unknown index '{index}'. Choose from: {list(_INDEX_URLS)}")

    logger.info("Downloading %s constituents from NSE", index.upper())
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    from io import StringIO
    df = pd.read_csv(StringIO(resp.text))

    # NSE CSV columns: "Company Name", "Industry", "Symbol", "Series", "ISIN Code"
    df.columns = [c.strip() for c in df.columns]

    stocks = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip().upper()
        company = str(row.get("Company Name", "")).strip()
        series = str(row.get("Series", "")).strip().upper()

        # Skip non-equity rows (index metadata, ETFs, etc.) — valid stocks have Series=EQ
        if not symbol or series not in ("EQ", ""):
            continue
        # Skip rows that look like index names rather than stock symbols
        if any(idx in symbol for idx in ("NIFTY", "SENSEX", "INDEX")):
            continue
        stocks.append({
            "symbol":        symbol,
            "company":       company,
            "pe_ratio":      None,
            "market_cap_cr": None,
            "debt_equity":   None,
            "delivery_pct":  None,
            "price":         None,
            "52w_high":      None,
            "52w_low":       None,
        })

    logger.info("NSE %s: %d constituents loaded", index.upper(), len(stocks))
    return stocks
