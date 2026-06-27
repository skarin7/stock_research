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


_PROFILE_FLAGS: dict[str, dict] = {
    "research": {
        "AGENT_MODE": "research",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": False,
        "ENABLE_RISK_AGENT": False,
        "ENABLE_PORTFOLIO_AGENT": False,
        "ENABLE_TRADING_AGENT": False,
        "ENABLE_MONITORING_AGENT": False,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": False,
        "GROWW_TRADING_ENABLED": False,
        "ENABLE_AUTO_EXIT": False,
    },
    "paper": {
        "AGENT_MODE": "paper",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": True,
        "ENABLE_RISK_AGENT": True,
        "ENABLE_PORTFOLIO_AGENT": True,
        "ENABLE_TRADING_AGENT": True,
        "ENABLE_MONITORING_AGENT": True,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": False,
        "GROWW_TRADING_ENABLED": False,
        "ENABLE_AUTO_EXIT": False,
    },
    "live": {
        "AGENT_MODE": "live",
        "ENABLE_RESEARCH_AGENT": True,
        "ENABLE_ANALYST_AGENT": True,
        "ENABLE_DEBATE_AGENT": True,
        "ENABLE_RISK_AGENT": True,
        "ENABLE_PORTFOLIO_AGENT": True,
        "ENABLE_TRADING_AGENT": True,
        "ENABLE_MONITORING_AGENT": True,
        "ENABLE_MEMORY_AGENT": True,
        "ENABLE_LIVE_TRADING": True,
        "GROWW_TRADING_ENABLED": True,
        "ENABLE_AUTO_EXIT": True,
    },
}


def _apply_profile(settings: "Settings") -> "Settings":
    """Expand AGENT_PROFILE into individual flags. Raises ValueError for unknown profiles."""
    import logging
    profile = settings.AGENT_PROFILE.strip().lower()
    if profile not in _PROFILE_FLAGS:
        raise ValueError(
            f"Unknown AGENT_PROFILE={profile!r}. Valid values: {', '.join(_PROFILE_FLAGS)}"
        )
    flags = _PROFILE_FLAGS[profile]
    result = replace(settings, **flags)
    agents_on = [k.removeprefix("ENABLE_").lower() for k, v in flags.items() if k.startswith("ENABLE_") and v]
    logging.getLogger("agents.settings").info(
        "profile=%s → %s agents ON", profile, "+".join(agents_on) if agents_on else "none"
    )
    return result


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

    # --- Signal weights / screener filters ---
    SIGNAL_WEIGHTS: dict = field(default_factory=_default_signal_weights)
    SCREENER_FILTERS: dict = field(default_factory=_default_screener_filters)

    # --- Pipeline settings ---
    TOP_N_STOCKS: int = 15
    MAX_STOCKS_TO_SCORE: int = 100
    DRY_RUN_STOCK_COUNT: int = 5
    GROWW_RATE_LIMIT_DELAY_MS: int = 200
    SCORING_BATCH_SIZE: int = 10
    OHLC_LOOKBACK_DAYS: int = 10
    EARNINGS_PROXIMITY_DAYS: int = 5
    GROWW_BASE_URL: str = "https://api.groww.in/v1/market"

    # --- Intraday prediction system ---
    INTRADAY_SCORE_THRESHOLD: int = 5
    INTRADAY_HIGH_CONVICTION: int = 7
    INTRADAY_TOP_N: int = 10
    INTRADAY_HISTORY_DAYS: int = 400

    # --- Output directory ---
    OUTPUT_DIR: str = "output"

    # --- Multi-agent system ---
    AGENT_PROFILE: str = "research"      # research | paper | live
    AGENT_MODE: str = "research"         # set by profile — do not set directly
    ENABLE_RESEARCH_AGENT: bool = True
    ENABLE_ANALYST_AGENT: bool = True
    ENABLE_DEBATE_AGENT: bool = False
    ENABLE_RISK_AGENT: bool = False
    ENABLE_PORTFOLIO_AGENT: bool = False
    ENABLE_TRADING_AGENT: bool = False
    ENABLE_MONITORING_AGENT: bool = False
    ENABLE_MEMORY_AGENT: bool = False

    # --- Live-trading hard gate ---
    ENABLE_LIVE_TRADING: bool = False
    GROWW_TRADING_ENABLED: bool = False
    KILL_SWITCH: bool = False
    KILL_SWITCH_FILE: str = os.path.join("output", "kill_switch.flag")

    # --- Auto-execution guardrails (protective exits only; never opens risk) ---
    ENABLE_AUTO_EXIT: bool = False               # auto stop/target SELL-to-close
    AUTO_TRADE_ALLOWLIST: frozenset = field(default_factory=frozenset)  # eligible symbols; empty = none
    MAX_DAILY_NOTIONAL: float = 50000.0          # ₹ ceiling across all auto orders/day
    MAX_ORDERS_PER_DAY: int = 10
    AUTO_TRADE_WINDOW: str = "09:20-15:20"       # IST HH:MM-HH:MM; skip open/close minutes
    AUTO_TRADE_LEDGER: str = os.path.join("output", "auto_trade_ledger.json")

    # --- Human-approval gate ---
    APPROVAL_TIMEOUT_SEC: int = 900
    APPROVAL_CHANNEL: str = "telegram"

    # --- Conversational chat agent (Telegram webhook) ---
    ENABLE_CHAT_AGENT: bool = False
    CHAT_MODEL: str = ""                 # empty → falls back to REPORT_MODEL
    TELEGRAM_WEBHOOK_SECRET: str = ""
    MAX_CHAT_TOOL_CALLS: int = 8
    MAX_CHAT_TURN_COST_USD: float = 0.25
    SNAPSHOT_STALE_DAYS: int = 3
    TAVILY_API_KEY: str = ""             # macro_search web grounding (free tier)
    MACRO_SEARCH_MAX_RESULTS: int = 5

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
            INTRADAY_SCORE_THRESHOLD=int(os.environ.get("INTRADAY_SCORE_THRESHOLD", "5")),
            INTRADAY_HIGH_CONVICTION=int(os.environ.get("INTRADAY_HIGH_CONVICTION", "7")),
            INTRADAY_TOP_N=int(os.environ.get("INTRADAY_TOP_N", "10")),
            INTRADAY_HISTORY_DAYS=int(os.environ.get("INTRADAY_HISTORY_DAYS", "400")),
            OUTPUT_DIR=output_dir,
            AGENT_PROFILE=os.environ.get("AGENT_PROFILE", "research").strip().lower(),
            KILL_SWITCH=_flag("KILL_SWITCH", "false"),
            KILL_SWITCH_FILE=os.path.join(output_dir, "kill_switch.flag"),
            ENABLE_AUTO_EXIT=_flag("ENABLE_AUTO_EXIT", "false"),
            AUTO_TRADE_ALLOWLIST=_csv_set("AUTO_TRADE_ALLOWLIST", ""),
            MAX_DAILY_NOTIONAL=float(os.environ.get("MAX_DAILY_NOTIONAL", "50000")),
            MAX_ORDERS_PER_DAY=int(os.environ.get("MAX_ORDERS_PER_DAY", "10")),
            AUTO_TRADE_WINDOW=os.environ.get("AUTO_TRADE_WINDOW", "09:20-15:20"),
            AUTO_TRADE_LEDGER=os.path.join(output_dir, "auto_trade_ledger.json"),
            APPROVAL_TIMEOUT_SEC=int(os.environ.get("APPROVAL_TIMEOUT_SEC", "900")),
            APPROVAL_CHANNEL=os.environ.get("APPROVAL_CHANNEL", "telegram"),
            ENABLE_CHAT_AGENT=_flag("ENABLE_CHAT_AGENT", "false"),
            CHAT_MODEL=os.environ.get("CHAT_MODEL", ""),
            TELEGRAM_WEBHOOK_SECRET=os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
            MAX_CHAT_TOOL_CALLS=int(os.environ.get("MAX_CHAT_TOOL_CALLS", "8")),
            MAX_CHAT_TURN_COST_USD=float(os.environ.get("MAX_CHAT_TURN_COST_USD", "0.25")),
            SNAPSHOT_STALE_DAYS=int(os.environ.get("SNAPSHOT_STALE_DAYS", "3")),
            TAVILY_API_KEY=os.environ.get("TAVILY_API_KEY", ""),
            MACRO_SEARCH_MAX_RESULTS=int(os.environ.get("MACRO_SEARCH_MAX_RESULTS", "5")),
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
        return _apply_profile(base)


__all__ = ["Settings", "replace", "_PROFILE_FLAGS", "_apply_profile"]
