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

# --- LLM provider ---
# "anthropic" (default) | "openrouter". OpenRouter is OpenAI-compatible and hosts
# cheap reasoning models (DeepSeek/Qwen/Kimi) ~10-50x cheaper than Claude. The
# Anthropic Batch API (50% off) only applies to the anthropic provider; OpenRouter
# uses concurrent sync calls. See llm_router.py.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- Models (per provider; Claude models are the default) ---
SCORING_MODEL = "claude-haiku-4-5"
REPORT_MODEL = "claude-sonnet-4-6"
OPENROUTER_SCORING_MODEL = os.environ.get("OPENROUTER_SCORING_MODEL", "deepseek/deepseek-chat")
OPENROUTER_REPORT_MODEL = os.environ.get("OPENROUTER_REPORT_MODEL", "deepseek/deepseek-chat")

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


# ──────────────────────────────────────────────────────────────────────────────
# Multi-agent system (LangGraph) — all trading defaults OFF
# ──────────────────────────────────────────────────────────────────────────────
def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# Run mode: "research" (report only) | "paper" (simulated fills) | "live" (real orders)
AGENT_MODE = os.environ.get("AGENT_MODE", "research").strip().lower()

# Per-agent feature flags (research + analyst on; everything else off until built out)
ENABLE_RESEARCH_AGENT = _flag("ENABLE_RESEARCH_AGENT", "true")
ENABLE_ANALYST_AGENT = _flag("ENABLE_ANALYST_AGENT", "true")
ENABLE_DEBATE_AGENT = _flag("ENABLE_DEBATE_AGENT", "false")
ENABLE_RISK_AGENT = _flag("ENABLE_RISK_AGENT", "false")
ENABLE_PORTFOLIO_AGENT = _flag("ENABLE_PORTFOLIO_AGENT", "false")
ENABLE_TRADING_AGENT = _flag("ENABLE_TRADING_AGENT", "false")
ENABLE_MONITORING_AGENT = _flag("ENABLE_MONITORING_AGENT", "false")
ENABLE_MEMORY_AGENT = _flag("ENABLE_MEMORY_AGENT", "false")

# Live-trading hard gate (must be true AND AGENT_MODE == "live" AND no kill-switch)
ENABLE_LIVE_TRADING = _flag("ENABLE_LIVE_TRADING", "false")
GROWW_TRADING_ENABLED = _flag("GROWW_TRADING_ENABLED", "false")
KILL_SWITCH = _flag("KILL_SWITCH", "false")
KILL_SWITCH_FILE = os.path.join(OUTPUT_DIR, "kill_switch.flag")

# Human-approval gate
APPROVAL_TIMEOUT_SEC = int(os.environ.get("APPROVAL_TIMEOUT_SEC", "900"))   # 15 min
APPROVAL_CHANNEL = os.environ.get("APPROVAL_CHANNEL", "telegram")           # telegram | cli

# Cost / iteration guardrails (prevent runaway loops + bound spend)
MAX_DEBATE_ROUNDS = int(os.environ.get("MAX_DEBATE_ROUNDS", "3"))
DEBATE_TOP_N = int(os.environ.get("DEBATE_TOP_N", "5"))   # debate only the top-N ranked (cost lever)
MAX_GRAPH_STEPS = int(os.environ.get("MAX_GRAPH_STEPS", "50"))              # LangGraph recursion_limit
MAX_NODE_RETRIES = int(os.environ.get("MAX_NODE_RETRIES", "2"))
MAX_RUN_COST_USD = float(os.environ.get("MAX_RUN_COST_USD", "5.0"))         # halt run if exceeded
MAX_RUN_TOKENS = int(os.environ.get("MAX_RUN_TOKENS", "5000000"))

# Risk limits
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))
MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "0.10"))        # of capital per name
MAX_SECTOR_PCT = float(os.environ.get("MAX_SECTOR_PCT", "0.30"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "0.05"))
BLOCK_NEAR_EARNINGS = _flag("BLOCK_NEAR_EARNINGS", "true")
TRADING_CAPITAL_INR = float(os.environ.get("TRADING_CAPITAL_INR", "100000"))
MIN_CONVICTION_TO_TRADE = float(os.environ.get("MIN_CONVICTION_TO_TRADE", "0.6"))

# Persistence (Postgres for agent + trading state; research output stays as files)
DATABASE_URL = os.environ.get("DATABASE_URL", "")                          # empty → MemorySaver fallback
POSITIONS_FILE = os.path.join(OUTPUT_DIR, "positions.json")
PROPOSALS_FILE = os.path.join(OUTPUT_DIR, "proposals.json")   # awaiting-approval proposals (approver visibility)
MEMORY_FILE = os.path.join(OUTPUT_DIR, "memory.jsonl")

# Observability (self-hosted: Langfuse traces + Prometheus/Grafana metrics)
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))
# Optional: push metrics to a Prometheus Pushgateway at end of run (batch jobs
# scale to zero, so the pull /metrics endpoint is never scraped). For Grafana
# Cloud, point this at a Pushgateway or use Grafana Alloy (remote_write). Empty → no push.
PROMETHEUS_PUSHGATEWAY_URL = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "")
