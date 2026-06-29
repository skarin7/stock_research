"""initial schema — all app tables as of first deploy

Revision ID: 0001
Revises:
Create Date: 2026-06-29
"""
from __future__ import annotations

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("report_date", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_runs_report_date", "runs", ["report_date"])

    op.create_table(
        "trade_proposals",
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("conviction", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("broker_order_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("proposal_id"),
    )
    op.create_index("ix_trade_proposals_run_id", "trade_proposals", ["run_id"])
    op.create_index("ix_trade_proposals_status", "trade_proposals", ["status"])
    op.create_index("ix_trade_proposals_ticker", "trade_proposals", ["ticker"])

    op.create_table(
        "positions",
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("sector", sa.String(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("ticker"),
    )

    op.create_table(
        "orders",
        sa.Column("broker_order_id", sa.String(), nullable=False),
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("filled_qty", sa.Integer(), nullable=False),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("broker_order_id"),
    )
    op.create_index("ix_orders_proposal_id", "orders", ["proposal_id"])

    op.create_table(
        "agent_audit",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("node", sa.String(), nullable=False),
        sa.Column("old_status", sa.String(), nullable=True),
        sa.Column("new_status", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_audit_run_id", "agent_audit", ["run_id"])

    op.create_table(
        "memory",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_namespace", "memory", ["namespace"])
    op.create_index("ix_memory_key", "memory", ["key"])

    op.create_table(
        "daily_snapshot",
        sa.Column("run_date", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("sector", sa.String(), nullable=True),
        sa.Column("pe_ratio", sa.Float(), nullable=True),
        sa.Column("sector_pe", sa.Float(), nullable=True),
        sa.Column("market_cap_cr", sa.Float(), nullable=True),
        sa.Column("ltp", sa.Float(), nullable=True),
        sa.Column("delivery_pct", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column("week52_high", sa.Float(), nullable=True),
        sa.Column("week52_low", sa.Float(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=True),
        sa.Column("signals", sa.JSON(), nullable=True),
        sa.Column("news", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("run_date", "symbol"),
    )
    op.create_index("ix_daily_snapshot_sector", "daily_snapshot", ["sector"])

    op.create_table(
        "groww_token",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("cron_expr", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "pulse_state",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "intent_embeddings",
        sa.Column("exemplar_hash", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("vectors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("exemplar_hash", "model"),
    )

    op.create_table(
        "chat_response_cache",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_embedding", sa.JSON(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_response_cache_query_hash", "chat_response_cache", ["query_hash"])
    op.create_index("ix_chat_response_cache_expires_at", "chat_response_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_table("chat_response_cache")
    op.drop_table("intent_embeddings")
    op.drop_table("pulse_state")
    op.drop_table("schedules")
    op.drop_table("groww_token")
    op.drop_table("daily_snapshot")
    op.drop_table("memory")
    op.drop_table("agent_audit")
    op.drop_table("orders")
    op.drop_table("positions")
    op.drop_table("trade_proposals")
    op.drop_table("runs")
