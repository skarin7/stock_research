"""Process-wide configuration: the single typed Settings instance.

All settings live in settings.py as a frozen Settings dataclass. Modules read
`from config import SETTINGS` and reference `SETTINGS.X`. Tests construct their
own Settings and expose it as `config.SETTINGS`.
"""
from dotenv import load_dotenv

from settings import Settings

load_dotenv()

SETTINGS = Settings.from_env()


def trading_enabled() -> bool:
    """True when TRADING_MODE is paper or live (trading chain is active)."""
    return SETTINGS.TRADING_MODE in ("paper", "live")


def live_trading() -> bool:
    """True when TRADING_MODE is live (real broker + HITL approval required)."""
    return SETTINGS.TRADING_MODE == "live"


__all__ = ["SETTINGS", "trading_enabled", "live_trading"]
