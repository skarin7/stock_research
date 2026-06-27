"""Engine/session helpers for the app tables.

No-ops gracefully when DATABASE_URL is unset so the research-mode path runs
without Postgres. Call ``init_db()`` once (or use Alembic) to create tables.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from config import SETTINGS

logger = logging.getLogger("persistence")

_engine = None
_Session = None


def _sa_url(url: str) -> str:
    """Rewrite postgresql:// → postgresql+psycopg:// so SQLAlchemy uses psycopg3.
    The repo uses psycopg[binary] (v3); psycopg2 is not installed."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg" + url[len("postgresql"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg" + url[len("postgres"):]
    return url


def _ensure_engine():
    global _engine, _Session
    if _engine is not None:
        return _engine
    if not SETTINGS.DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    _engine = create_engine(_sa_url(SETTINGS.DATABASE_URL), pool_pre_ping=True)
    _Session = sessionmaker(bind=_engine)
    return _engine


def init_db() -> bool:
    """Create app tables. Returns False if no DB configured."""
    if not SETTINGS.DATABASE_URL:
        logger.info("DATABASE_URL unset — skipping DB init")
        return False
    from persistence.models import Base

    Base.metadata.create_all(_ensure_engine())
    logger.info("App tables ensured")
    return True


@contextmanager
def session_scope():
    _ensure_engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
