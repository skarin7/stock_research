"""Typed, frozen application settings — the single source of truth for SETTINGS.

`Settings.from_env()` parses environment variables once; `SETTINGS.py` builds the
process-wide `SETTINGS` instance. Every field has a default so tests can
construct partial instances directly instead of mocking the config module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _csv_set(name: str, default: str = "") -> frozenset:
    """Parse a comma-separated env var into an upper-cased frozenset (order-free)."""
    raw = os.environ.get(name, default)
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip())



def _default_signal_weights() -> dict:
    return {
        "news_sentiment": 0.20,
        "bulk_deals": 0.20,
        "momentum": 0.15,
        "value": 0.20,
        "delivery_pct": 0.10,
        "52w_position": 0.05,
        "institutional_trend": 0.05,
        "sector_rotation": 0.05,
    }


def _default_pulse_shock_keywords() -> list:
    return [
        "nifty crash", "sensex fall", "stock market crash india",
        "war", "oil price spike", "circuit breaker nse",
    ]


def _default_pulse_global_tickers() -> dict:
    # symbol → threshold %. Negative = drop trigger, positive = rise trigger.
    return {
        "^KS11": -2.0,    # Kospi (Korea)
        "^N225": -2.0,    # Nikkei (Japan)
        "^HSI": -2.0,     # Hang Seng (Hong Kong)
        "BZ=F": 4.0,      # Brent crude (sharp move either way → use abs in node)
        "INR=X": 0.8,     # USD/INR up = rupee weakness
        "ES=F": -1.5,     # S&P 500 futures
        "NQ=F": -1.5,     # Nasdaq 100 futures
    }


def _default_pulse_global_sector_map() -> dict:
    # event key → {"headwind": [...], "tailwind": [...]} Indian sectors.
    return {
        "crude_up": {"headwind": ["Paints", "Aviation", "Tyres", "Oil Marketing"],
                     "tailwind": ["Oil Exploration"]},
        "inr_weak": {"headwind": ["Importers", "Oil Marketing"],
                     "tailwind": ["IT", "Pharma"]},
        "us_tech_selloff": {"headwind": ["IT"], "tailwind": []},
        "asia_riskoff": {"headwind": ["broad"], "tailwind": []},
    }


def _default_screener_filters() -> dict:
    return {
        "max_pe_ratio_vs_sector": 1.0,
        "min_delivery_pct": 50.0,
        "min_volume_ratio": 1.5,
        "max_debt_equity": 1.5,
        "min_market_cap_cr": 500,
    }


@dataclass(frozen=True)
class Settings:
    # --- API keys ---
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROWW_ACCESS_TOKEN: str = ""  # pre-baked daily token from Groww portal (valid until 6 AM IST)
    GROWW_TOTP_TOKEN: str = ""
    GROWW_TOTP_SECRET: str = ""
    GROWW_API_KEY: str = ""
    GROWW_API_SECRET: str = ""
    GROWW_TOKEN_ENC_KEY: str = ""  # passphrase to encrypt the cached access token at rest (Fernet+PBKDF2). Empty = plaintext.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- Stock universe source ---
    STOCK_UNIVERSE: str = "nifty200"
    SCREENER_EMAIL: str = ""
    SCREENER_PASSWORD: str = ""
    SCREENER_SCREEN_ID: str = ""
    SCREENER_SCREEN_SLUG: str = ""

    # --- LLM provider ---
    LLM_PROVIDER: str = "anthropic"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # --- Models ---
    SCORING_MODEL: str = "deepseek/deepseek-chat"
    REPORT_MODEL: str = "deepseek/deepseek-chat"
    OPENROUTER_SCORING_MODEL: str = "deepseek/deepseek-chat"
    OPENROUTER_REPORT_MODEL: str = "deepseek/deepseek-chat"
    OPENROUTER_CHAT_MODEL: str = "deepseek/deepseek-chat"

    # --- Signal weights / screener filters ---
    SIGNAL_WEIGHTS: dict = field(default_factory=_default_signal_weights)
    SCREENER_FILTERS: dict = field(default_factory=_default_screener_filters)

    # --- Pipeline settings ---
    TOP_N_STOCKS: int = 15
    MAX_STOCKS_TO_SCORE: int = 100
    DRY_RUN_STOCK_COUNT: int = 5
    GROWW_RATE_LIMIT_DELAY_MS: int = 200
    SCORING_BATCH_SIZE: int = 10
    OHLC_LOOKBACK_DAYS: int = 60  # ≥35 for MACD(12,26,9) warmup + 20d breakout
    EARNINGS_PROXIMITY_DAYS: int = 5
    GROWW_BASE_URL: str = "https://api.groww.in/v1/market"

    # --- Intraday prediction system ---
    INTRADAY_SCORE_THRESHOLD: int = 5
    INTRADAY_HIGH_CONVICTION: int = 7
    INTRADAY_TOP_N: int = 10
    INTRADAY_HISTORY_DAYS: int = 400

    # --- Output directory ---
    OUTPUT_DIR: str = "output"

    # --- Trading mode (replaces all ENABLE_* flags) ---
    TRADING_MODE: str = "off"       # off | paper | live

    # --- Live-trading hard gate ---
    KILL_SWITCH: bool = False
    KILL_SWITCH_FILE: str = os.path.join("output", "kill_switch.flag")

    # --- Auto-execution guardrails (protective exits only; never opens risk) ---
    AUTO_TRADE_ALLOWLIST: frozenset = field(default_factory=frozenset)  # eligible symbols; empty = none
    MAX_DAILY_NOTIONAL: float = 50000.0          # ₹ ceiling across all auto orders/day
    MAX_ORDERS_PER_DAY: int = 10
    AUTO_TRADE_WINDOW: str = "09:20-15:20"       # IST HH:MM-HH:MM; skip open/close minutes
    AUTO_TRADE_LEDGER: str = os.path.join("output", "auto_trade_ledger.json")

    # --- Human-approval gate ---
    APPROVAL_TIMEOUT_SEC: int = 900
    APPROVAL_CHANNEL: str = "telegram"

    # --- Conversational chat agent (Telegram webhook) ---
    CHAT_MODEL: str = ""                 # empty → falls back to REPORT_MODEL
    TELEGRAM_WEBHOOK_SECRET: str = ""
    MAX_CHAT_TOOL_CALLS: int = 8
    MAX_CHAT_TURN_COST_USD: float = 0.25
    SNAPSHOT_STALE_DAYS: int = 3
    TAVILY_API_KEY: str = ""             # macro_search web grounding (free tier)
    MACRO_SEARCH_MAX_RESULTS: int = 5

    # --- Chat intent router (semantic cosine → LLM fallback; core, self-gating) ---
    CHAT_SEMANTIC_THRESHOLD: float = 0.55     # cosine ≥ this → route by exemplar, no LLM
    CHAT_EMBED_MODEL: str = "openai/text-embedding-3-small"  # OpenRouter (OpenAI-compatible) embeddings
    CHAT_INTENT_MODEL: str = ""               # empty → SCORING_MODEL (cheap fallback classifier)
    CHAT_INTENT_MIN_CONFIDENCE: float = 0.6   # LLM confidence below this → ambiguous

    # --- Prompt response cache ---
    CHAT_CACHE_ENABLED: bool = True
    CHAT_CACHE_TTL_SECONDS: int = 1800           # 30 min — market data queries
    CHAT_CACHE_STABLE_TTL_SECONDS: int = 86400   # 24 h — NSE codes, sector info
    CHAT_CACHE_SEMANTIC_THRESHOLD: float = 0.95  # very high — only near-identical queries

    # --- Market-pulse shock watcher (Part A) ---
    PULSE_INDEX_DROP_PCT: float = 1.5            # NIFTY intraday drop % → alert
    PULSE_VIX_SPIKE_PCT: float = 15.0            # India VIX % spike → alert
    PULSE_HOLDING_DROP_PCT: float = 4.0          # per-holding intraday drop % → alert
    PULSE_ALERT_COOLDOWN_MIN: int = 20           # secondary safety floor on re-alerts
    PULSE_NEWS_ENABLED: bool = True
    PULSE_NEWS_MIN_GAP_MIN: int = 5              # min minutes between LLM news classifications
    PULSE_STATE_FILE: str = os.path.join("output", "pulse_state.json")
    PULSE_SHOCK_KEYWORDS: list = field(default_factory=_default_pulse_shock_keywords)
    PULSE_GLOBAL_ENABLED: bool = True
    PULSE_GLOBAL_TICKERS: dict = field(default_factory=_default_pulse_global_tickers)
    PULSE_GLOBAL_SECTOR_MAP: dict = field(default_factory=_default_pulse_global_sector_map)

    # --- Cost / iteration guardrails ---
    MAX_DEBATE_ROUNDS: int = 3
    DEBATE_TOP_N: int = 5
    MAX_GRAPH_STEPS: int = 50
    MAX_NODE_RETRIES: int = 2
    MAX_RUN_COST_USD: float = 5.0
    MAX_RUN_TOKENS: int = 5_000_000

    # --- Risk limits ---
    MAX_OPEN_POSITIONS: int = 5
    MAX_POSITION_PCT: float = 0.10
    MAX_SECTOR_PCT: float = 0.30
    STOP_LOSS_PCT: float = 0.05
    BLOCK_NEAR_EARNINGS: bool = True
    TRADING_CAPITAL_INR: float = 100000.0
    MIN_CONVICTION_TO_TRADE: float = 0.6

    # --- Persistence ---
    DATABASE_URL: str = ""
    POSITIONS_FILE: str = os.path.join("output", "positions.json")
    PROPOSALS_FILE: str = os.path.join("output", "proposals.json")
    MEMORY_FILE: str = os.path.join("output", "memory.jsonl")

    # --- Observability ---
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3000"
    METRICS_PORT: int = 9100
    PROMETHEUS_PUSHGATEWAY_URL: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables (with the same defaults
        and parsing the old SETTINGS.py module used)."""
        output_dir = os.environ.get("OUTPUT_DIR", "output")
        base = cls(
            ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY", ""),
            GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", ""),
            GROWW_ACCESS_TOKEN=os.environ.get("GROWW_ACCESS_TOKEN", ""),
            GROWW_TOTP_TOKEN=os.environ.get("GROWW_TOTP_TOKEN", ""),
            GROWW_TOTP_SECRET=os.environ.get("GROWW_TOTP_SECRET", ""),
            GROWW_API_KEY=os.environ.get("GROWW_API_KEY", ""),
            GROWW_API_SECRET=os.environ.get("GROWW_API_SECRET", ""),
            GROWW_TOKEN_ENC_KEY=os.environ.get("GROWW_TOKEN_ENC_KEY", ""),
            TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            TELEGRAM_CHAT_ID=os.environ.get("TELEGRAM_CHAT_ID", ""),
            STOCK_UNIVERSE=os.environ.get("STOCK_UNIVERSE", "nifty200"),
            SCREENER_EMAIL=os.environ.get("SCREENER_EMAIL", ""),
            SCREENER_PASSWORD=os.environ.get("SCREENER_PASSWORD", ""),
            SCREENER_SCREEN_ID=os.environ.get("SCREENER_SCREEN_ID", ""),
            SCREENER_SCREEN_SLUG=os.environ.get("SCREENER_SCREEN_SLUG", ""),
            LLM_PROVIDER=os.environ.get("LLM_PROVIDER", "anthropic").strip().lower(),
            OPENROUTER_API_KEY=os.environ.get("OPENROUTER_API_KEY", ""),
            OPENROUTER_BASE_URL=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            OPENROUTER_SCORING_MODEL=os.environ.get("OPENROUTER_SCORING_MODEL", "deepseek/deepseek-chat"),
            OPENROUTER_REPORT_MODEL=os.environ.get("OPENROUTER_REPORT_MODEL", "deepseek/deepseek-chat"),
            OPENROUTER_CHAT_MODEL=os.environ.get("OPENROUTER_CHAT_MODEL", "deepseek/deepseek-chat"),
            INTRADAY_SCORE_THRESHOLD=int(os.environ.get("INTRADAY_SCORE_THRESHOLD", "5")),
            INTRADAY_HIGH_CONVICTION=int(os.environ.get("INTRADAY_HIGH_CONVICTION", "7")),
            INTRADAY_TOP_N=int(os.environ.get("INTRADAY_TOP_N", "10")),
            INTRADAY_HISTORY_DAYS=int(os.environ.get("INTRADAY_HISTORY_DAYS", "400")),
            OUTPUT_DIR=output_dir,
            TRADING_MODE=os.environ.get("TRADING_MODE", "off").strip().lower(),
            KILL_SWITCH=_flag("KILL_SWITCH", "false"),
            KILL_SWITCH_FILE=os.path.join(output_dir, "kill_switch.flag"),
            AUTO_TRADE_ALLOWLIST=_csv_set("AUTO_TRADE_ALLOWLIST", ""),
            MAX_DAILY_NOTIONAL=float(os.environ.get("MAX_DAILY_NOTIONAL", "50000")),
            MAX_ORDERS_PER_DAY=int(os.environ.get("MAX_ORDERS_PER_DAY", "10")),
            AUTO_TRADE_WINDOW=os.environ.get("AUTO_TRADE_WINDOW", "09:20-15:20"),
            AUTO_TRADE_LEDGER=os.path.join(output_dir, "auto_trade_ledger.json"),
            APPROVAL_TIMEOUT_SEC=int(os.environ.get("APPROVAL_TIMEOUT_SEC", "900")),
            APPROVAL_CHANNEL=os.environ.get("APPROVAL_CHANNEL", "telegram"),
            CHAT_MODEL=os.environ.get("CHAT_MODEL", ""),
            TELEGRAM_WEBHOOK_SECRET=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
            MAX_CHAT_TOOL_CALLS=int(os.environ.get("MAX_CHAT_TOOL_CALLS", "8")),
            MAX_CHAT_TURN_COST_USD=float(os.environ.get("MAX_CHAT_TURN_COST_USD", "0.25")),
            SNAPSHOT_STALE_DAYS=int(os.environ.get("SNAPSHOT_STALE_DAYS", "3")),
            TAVILY_API_KEY=os.environ.get("TAVILY_API_KEY", ""),
            MACRO_SEARCH_MAX_RESULTS=int(os.environ.get("MACRO_SEARCH_MAX_RESULTS", "5")),
            CHAT_SEMANTIC_THRESHOLD=float(os.environ.get("CHAT_SEMANTIC_THRESHOLD", "0.55")),
            CHAT_EMBED_MODEL=os.environ.get("CHAT_EMBED_MODEL", "openai/text-embedding-3-small"),
            CHAT_INTENT_MODEL=os.environ.get("CHAT_INTENT_MODEL", ""),
            CHAT_INTENT_MIN_CONFIDENCE=float(os.environ.get("CHAT_INTENT_MIN_CONFIDENCE", "0.6")),
            CHAT_CACHE_ENABLED=os.environ.get("CHAT_CACHE_ENABLED", "true").lower() not in ("false", "0", "no"),
            CHAT_CACHE_TTL_SECONDS=int(os.environ.get("CHAT_CACHE_TTL_SECONDS", "1800")),
            CHAT_CACHE_STABLE_TTL_SECONDS=int(os.environ.get("CHAT_CACHE_STABLE_TTL_SECONDS", "86400")),
            CHAT_CACHE_SEMANTIC_THRESHOLD=float(os.environ.get("CHAT_CACHE_SEMANTIC_THRESHOLD", "0.95")),
            PULSE_INDEX_DROP_PCT=float(os.environ.get("PULSE_INDEX_DROP_PCT", "1.5")),
            PULSE_VIX_SPIKE_PCT=float(os.environ.get("PULSE_VIX_SPIKE_PCT", "15.0")),
            PULSE_HOLDING_DROP_PCT=float(os.environ.get("PULSE_HOLDING_DROP_PCT", "4.0")),
            PULSE_ALERT_COOLDOWN_MIN=int(os.environ.get("PULSE_ALERT_COOLDOWN_MIN", "20")),
            PULSE_NEWS_ENABLED=_flag("PULSE_NEWS_ENABLED", "true"),
            PULSE_NEWS_MIN_GAP_MIN=int(os.environ.get("PULSE_NEWS_MIN_GAP_MIN", "5")),
            PULSE_STATE_FILE=os.path.join(output_dir, "pulse_state.json"),
            PULSE_GLOBAL_ENABLED=_flag("PULSE_GLOBAL_ENABLED", "true"),
            MAX_DEBATE_ROUNDS=int(os.environ.get("MAX_DEBATE_ROUNDS", "3")),
            DEBATE_TOP_N=int(os.environ.get("DEBATE_TOP_N", "5")),
            MAX_GRAPH_STEPS=int(os.environ.get("MAX_GRAPH_STEPS", "50")),
            MAX_NODE_RETRIES=int(os.environ.get("MAX_NODE_RETRIES", "2")),
            MAX_RUN_COST_USD=float(os.environ.get("MAX_RUN_COST_USD", "5.0")),
            MAX_RUN_TOKENS=int(os.environ.get("MAX_RUN_TOKENS", "5000000")),
            MAX_OPEN_POSITIONS=int(os.environ.get("MAX_OPEN_POSITIONS", "5")),
            MAX_POSITION_PCT=float(os.environ.get("MAX_POSITION_PCT", "0.10")),
            MAX_SECTOR_PCT=float(os.environ.get("MAX_SECTOR_PCT", "0.30")),
            STOP_LOSS_PCT=float(os.environ.get("STOP_LOSS_PCT", "0.05")),
            BLOCK_NEAR_EARNINGS=_flag("BLOCK_NEAR_EARNINGS", "true"),
            TRADING_CAPITAL_INR=float(os.environ.get("TRADING_CAPITAL_INR", "100000")),
            MIN_CONVICTION_TO_TRADE=float(os.environ.get("MIN_CONVICTION_TO_TRADE", "0.6")),
            DATABASE_URL=os.environ.get("DATABASE_URL", ""),
            POSITIONS_FILE=os.path.join(output_dir, "positions.json"),
            PROPOSALS_FILE=os.path.join(output_dir, "proposals.json"),
            MEMORY_FILE=os.path.join(output_dir, "memory.jsonl"),
            LANGFUSE_PUBLIC_KEY=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            LANGFUSE_SECRET_KEY=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            LANGFUSE_HOST=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
            METRICS_PORT=int(os.environ.get("METRICS_PORT", "9100")),
            PROMETHEUS_PUSHGATEWAY_URL=os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", ""),
        )
        valid_modes = ("off", "paper", "live")
        if base.TRADING_MODE not in valid_modes:
            raise ValueError(
                f"TRADING_MODE={base.TRADING_MODE!r} invalid. Valid values: {', '.join(valid_modes)}"
            )
        return base


__all__ = ["Settings", "replace"]
