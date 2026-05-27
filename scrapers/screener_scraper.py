"""
Screener.in scraper: authenticates via session cookie, fetches a saved screen,
and returns a list of stock dicts with quantitative metrics.
"""

import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import SETTINGS

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.screener.in/login/"
SCREEN_URL = "https://www.screener.in/screens/{screen_id}/"

# Screener.in column name → internal key mapping (handles label variations).
# Keys are lowercased substrings matched against actual header text; first match wins.
_COLUMN_MAP = {
    "name": "company",
    "symbol": "symbol",
    "cmp": "price",              # "CMPRs." = Current Market Price
    "current price": "price",
    "p/e": "pe_ratio",
    "mar cap": "market_cap_cr",  # "Mar CapRs.Cr."
    "market cap": "market_cap_cr",
    "debt / eq": "debt_equity",  # "Debt / Eq" short form in screens
    "debt / equity": "debt_equity",
    "delivery %": "delivery_pct",
    "%deliverble": "delivery_pct",
    "52w h": "52w_high",
    "52w l": "52w_low",
    "1 year high": "52w_high",
    "1 year low": "52w_low",
}


def _parse_number(text: str) -> Optional[float]:
    """Strip commas, ₹, Cr suffixes and return float, or None if not parseable."""
    cleaned = re.sub(r"[₹,\s]", "", text.strip())
    # Handle 'Cr' suffix (crores already implied in market cap columns)
    cleaned = re.sub(r"Cr$", "", cleaned, flags=re.IGNORECASE)
    try:
        return float(cleaned)
    except ValueError:
        return None


class ScreenerScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.screener.in/",
        })
        self._authenticated = False

    def _login(self):
        """POST credentials to Screener.in and capture session cookie."""
        logger.info("Authenticating with Screener.in")
        # Fetch the login page to get the CSRF token
        resp = self.session.get(LOGIN_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if not csrf_input:
            raise RuntimeError("Could not find CSRF token on Screener.in login page")
        csrf = csrf_input["value"]

        payload = {
            "csrfmiddlewaretoken": csrf,
            "username": SETTINGS.SCREENER_EMAIL,
            "password": SETTINGS.SCREENER_PASSWORD,
        }
        resp = self.session.post(LOGIN_URL, data=payload, timeout=15)
        resp.raise_for_status()

        # Screener redirects to / on success; a failed login stays on /login/
        if "/login/" in resp.url:
            raise RuntimeError("Screener.in login failed — check SCREENER_EMAIL / SCREENER_PASSWORD")

        self._authenticated = True
        logger.info("Screener.in authentication successful")

    def _ensure_authenticated(self):
        if not self._authenticated:
            self._login()

    def list_saved_screens(self) -> list[dict]:
        """Fetch the user's saved screens list, returning id, name, and full path."""
        self._ensure_authenticated()
        resp = self.session.get("https://www.screener.in/screens/", timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        screens = []
        seen = set()
        for a in soup.find_all("a", href=re.compile(r"/screens/\d+")):
            href = a["href"].rstrip("/") + "/"
            m = re.search(r"/screens/(\d+)", href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                screens.append({
                    "id": m.group(1),
                    "name": a.get_text(strip=True),
                    "path": href,  # full path including slug, e.g. /screens/3656846/good-stocks/
                })
        return screens

    def _resolve_screen_url(self, screen_id: str) -> str:
        """
        Return the correct URL for a screen ID.
        If SCREENER_SCREEN_SLUG is set in config, use it directly.
        Otherwise try slug-less URL; on 404 look up the slug from the saved screens list.
        """
        # Fast path: slug provided explicitly in config
        if SETTINGS.SCREENER_SCREEN_SLUG:
            url = f"https://www.screener.in/screens/{screen_id}/{SETTINGS.SCREENER_SCREEN_SLUG}/"
            logger.info("Using explicit slug URL: %s", url)
            return url

        base = SCREEN_URL.format(screen_id=screen_id)
        resp = self.session.head(base, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return resp.url
        if resp.status_code in (301, 302):
            return resp.headers.get("Location", base)

        # 404 — look up slug from saved screens list
        logger.info("Slug-less URL returned %d, looking up slug for screen %s", resp.status_code, screen_id)
        saved = self.list_saved_screens()
        match = next((s for s in saved if s["id"] == str(screen_id)), None)
        if match:
            full_url = "https://www.screener.in" + match["path"]
            logger.info("Resolved screen URL: %s", full_url)
            return full_url

        if saved:
            hint = "\n  ".join(f"ID {s['id']:>10} → {s['name']}" for s in saved[:10])
            raise RuntimeError(
                f"Screen ID '{screen_id}' not found.\n"
                f"Your saved screens:\n  {hint}\n"
                f"Either update SCREENER_SCREEN_ID in .env, or add SCREENER_SCREEN_SLUG=<slug> "
                f"(the slug is the part of the URL after the ID)."
            )
        raise RuntimeError(
            f"Screen ID '{screen_id}' not found and no saved screens found.\n"
            f"Add SCREENER_SCREEN_SLUG=<slug> to .env (from your screen URL: /screens/{screen_id}/<slug>/)."
        )

    def _fetch_screen_page(self, screen_id: str, page: int = 1) -> str:
        if not hasattr(self, "_screen_url_cache"):
            self._screen_url_cache = {}
        if screen_id not in self._screen_url_cache:
            self._screen_url_cache[screen_id] = self._resolve_screen_url(screen_id)
        url = self._screen_url_cache[screen_id]

        params = {"page": page} if page > 1 else {}
        resp = self.session.get(url, params=params, timeout=20)
        if resp.status_code == 403:
            logger.warning("Screener.in returned 403, re-authenticating")
            self._authenticated = False
            self._login()
            resp = self.session.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.text

    def _parse_table(self, html: str) -> tuple[list[dict], bool]:
        """
        Parse the results table from a Screener.in screen page.
        Returns (rows, has_next_page).
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_=re.compile(r"data-table|screen-table", re.I))
        if not table:
            # Screener sometimes wraps in a div with id="screener-table"
            table = soup.find("table")
        if not table:
            logger.warning("No results table found on Screener.in screen page")
            return [], False

        # Build header → column index mapping.
        # Screener.in screens have no <thead>; headers are <th> in the first <tr>.
        all_rows = table.find_all("tr")
        if not all_rows:
            return [], False

        header_row = all_rows[0]
        headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
        logger.debug("Screener headers: %s", headers)

        col_idx = {}
        for col_label, internal_key in _COLUMN_MAP.items():
            if internal_key in col_idx:
                continue  # already mapped by a higher-priority key
            for i, h in enumerate(headers):
                if col_label in h:
                    col_idx[internal_key] = i
                    break

        data_rows = all_rows[1:]  # skip the header row

        rows = []
        for tr in data_rows:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue

            # Try to get ticker symbol from first anchor href: /company/TICKER/
            ticker = None
            first_a = tr.find("a", href=re.compile(r"/company/"))
            if first_a:
                m = re.search(r"/company/([^/]+)/", first_a["href"])
                if m:
                    ticker = m.group(1).upper()

            def _get(key: str) -> Optional[str]:
                idx = col_idx.get(key)
                return cells[idx] if idx is not None and idx < len(cells) else None

            company_name = _get("company") or (cells[0] if cells else "")
            symbol = ticker or (_get("symbol") or "").upper()

            row = {
                "symbol": symbol,
                "company": company_name,
                "pe_ratio": _parse_number(_get("pe_ratio") or ""),
                "market_cap_cr": _parse_number(_get("market_cap_cr") or ""),
                "debt_equity": _parse_number(_get("debt_equity") or ""),
                "delivery_pct": _parse_number(_get("delivery_pct") or ""),
                "price": _parse_number(_get("price") or ""),
                "52w_high": _parse_number(_get("52w_high") or ""),
                "52w_low": _parse_number(_get("52w_low") or ""),
            }
            if symbol:
                rows.append(row)

        # Check for a "next page" link (Screener uses rel="next" or text "Next")
        has_next = bool(
            soup.find("a", rel="next") or
            soup.find("a", string=re.compile(r"^\s*(next|›)\s*$", re.I))
        )

        return rows, has_next

    def fetch_screen(self, screen_id: Optional[str] = None, max_pages: int = 10) -> list[dict]:
        """
        Fetch all pages of a Screener.in saved screen.
        Returns a list of stock dicts passing the quantitative filters in SETTINGS.
        """
        self._ensure_authenticated()
        sid = screen_id or SETTINGS.SCREENER_SCREEN_ID
        all_rows: list[dict] = []

        for page in range(1, max_pages + 1):
            logger.info("Fetching Screener.in screen %s, page %d", sid, page)
            html = self._fetch_screen_page(sid, page)
            rows, has_next = self._parse_table(html)
            all_rows.extend(rows)
            logger.info("  Got %d rows (total so far: %d)", len(rows), len(all_rows))
            if not has_next:
                break
            time.sleep(1)  # polite delay between pages

        logger.info("Screener.in: %d stocks fetched before filtering", len(all_rows))
        filtered = self._apply_filters(all_rows)
        logger.info("Screener.in: %d stocks after quantitative filter", len(filtered))
        return filtered

    @staticmethod
    def _apply_filters(stocks: list[dict]) -> list[dict]:
        """Apply quantitative thresholds from SETTINGS.SCREENER_FILTERS."""
        f = SETTINGS.SCREENER_FILTERS
        result = []
        for s in stocks:
            if s["market_cap_cr"] and s["market_cap_cr"] < f["min_market_cap_cr"]:
                continue
            if s["debt_equity"] and s["debt_equity"] > f["max_debt_equity"]:
                continue
            if s["delivery_pct"] and s["delivery_pct"] < f["min_delivery_pct"]:
                continue
            result.append(s)
        return result
