"""SQLAlchemy ORM models for agent + trading state.

App tables (LangGraph owns checkpoints/store via its own savers):
  runs, trade_proposals, positions, orders, agent_audit, memory
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    report_date: Mapped[str] = mapped_column(String, index=True)
    mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TradeProposalRow(Base):
    __tablename__ = "trade_proposals"

    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    conviction: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)


class PositionRow(Base):
    __tablename__ = "positions"

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    avg_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrderRow(Base):
    __tablename__ = "orders"

    broker_order_id: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AgentAudit(Base):
    __tablename__ = "agent_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String)        # agent | human | system
    node: Mapped[str] = mapped_column(String)
    old_status: Mapped[str | None] = mapped_column(String, nullable=True)
    new_status: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailySnapshotRow(Base):
    """One enriched + scored stock from a daily run — the chat agent's screen cache."""

    __tablename__ = "daily_snapshot"

    run_date: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[str] = mapped_column(String, default="")
    sector: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_cr: Mapped[float | None] = mapped_column(Float, nullable=True)
    ltp: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    week52_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    week52_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    news: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    technicals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    earnings_proximity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GrowwTokenRow(Base):
    """Cross-process cache for the daily Groww access token.

    Scale-to-zero Cloud Run regenerates a fresh TOTP login on every cold start;
    Groww rate-limits its access-token endpoint (meant ~once/day). This single
    row lets all processes share one token until it expires at 6 AM IST, so a
    burst of cold starts triggers at most one login. ``expires_at`` is the daily
    expiry boundary — ``load_groww_token`` drops the row once it's past.
    """

    __tablename__ = "groww_token"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # "default"
    token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)      # next 6 AM IST (UTC-naive)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IntentEmbeddingRow(Base):
    """Cached embeddings of the chat intent exemplar bank (master data).

    One row per (exemplar_hash, model). ``labels`` and ``vectors`` are parallel
    JSON arrays. The master intents change rarely, so this is embedded once and
    re-embedded only when the exemplar set or model changes (→ new hash)."""

    __tablename__ = "intent_embeddings"

    exemplar_hash: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    labels: Mapped[list] = mapped_column(JSON)
    vectors: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryRow(Base):
    __tablename__ = "memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
