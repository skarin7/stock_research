from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Repo root on sys.path so persistence.* imports work.
sys.path.insert(0, str(Path(__file__).parent.parent))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Wire our SQLAlchemy Base so autogenerate sees all models.
from persistence.models import Base  # noqa: E402
target_metadata = Base.metadata


def _db_url() -> str:
    """Prefer DATABASE_URL env var; fall back to alembic.ini sqlalchemy.url."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        url = config.get_main_option("sqlalchemy.url") or ""
    # psycopg3 dialect
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg" + url[len("postgresql"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg" + url[len("postgres"):]
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
