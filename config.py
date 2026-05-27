"""Process-wide configuration: the single typed Settings instance.

All settings live in settings.py as a frozen Settings dataclass. Modules read
`from config import SETTINGS` and reference `SETTINGS.X`. Tests construct their
own Settings and expose it as `config.SETTINGS`.
"""
from dotenv import load_dotenv

from settings import Settings

load_dotenv()

SETTINGS = Settings.from_env()

__all__ = ["SETTINGS"]
