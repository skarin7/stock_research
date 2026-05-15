import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # optional; falls back to RSS
# TOTP auth (preferred — no daily expiry). Generate from groww.in/trade-api/api-keys
GROWW_TOTP_TOKEN = os.environ.get("GROWW_TOTP_TOKEN", "")
GROWW_TOTP_SECRET = os.environ.get("GROWW_TOTP_SECRET", "")
# Legacy JWT (fallback if TOTP not configured)
GROWW_API_KEY = os.environ.get("GROWW_API_KEY", "")
GROWW_API_SECRET = os.environ.get("GROWW_API_SECRET", "")
# Telegram — optional; get token from @BotFather, chat_id from getUpdates
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Stock universe source ---
# "nifty50" | "nifty100" | "nifty200" | "nifty500" | "screener"
STOCK_UNIVERSE = os.environ.get("STOCK_UNIVERSE", "nifty200")

SCREENER_EMAIL = os.environ.get("SCREENER_EMAIL", "")
SCREENER_PASSWORD = os.environ.get("SCREENER_PASSWORD", "")
SCREENER_SCREEN_ID = os.environ.get("SCREENER_SCREEN_ID", "")
SCREENER_SCREEN_SLUG = os.environ.get("SCREENER_SCREEN_SLUG", "")  # optional slug from URL

# --- Models ---
SCORING_MODEL = "claude-haiku-4-5"
REPORT_MODEL = "claude-sonnet-4-6"

# --- Signal weights (must sum to 1.0) ---
SIGNAL_WEIGHTS = {
    "news_sentiment":      0.20,
    "bulk_deals":          0.20,
    "momentum":            0.15,
    "value":               0.20,
    "delivery_pct":        0.10,
    "52w_position":        0.05,
    "institutional_trend": 0.05,
    "sector_rotation":     0.05,
}

# --- Screener.in quantitative filter thresholds ---
SCREENER_FILTERS = {
    "max_pe_ratio_vs_sector": 1.0,   # PE must be <= sector median
    "min_delivery_pct": 50.0,
    "min_volume_ratio": 1.5,         # vs 20-day avg
    "max_debt_equity": 1.5,
    "min_market_cap_cr": 500,
}

# --- Pipeline settings ---
TOP_N_STOCKS = 15
MAX_STOCKS_TO_SCORE = 100           # cap after Screener fetch, before Groww/Claude
DRY_RUN_STOCK_COUNT = 5
GROWW_RATE_LIMIT_DELAY_MS = 200     # milliseconds between Groww API calls
SCORING_BATCH_SIZE = 10             # stocks per Claude Batch API call
OHLC_LOOKBACK_DAYS = 10
EARNINGS_PROXIMITY_DAYS = 5         # flag if within N trading days of results

# --- Groww API base URL ---
GROWW_BASE_URL = "https://api.groww.in/v1/market"

# --- Output directory ---
OUTPUT_DIR = "output"
