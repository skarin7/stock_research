"""add technicals and earnings_proximity to daily_snapshot

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29
"""
from __future__ import annotations

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.add_column("daily_snapshot", sa.Column("technicals", sa.JSON(), nullable=True))
    op.add_column("daily_snapshot", sa.Column("earnings_proximity", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_snapshot", "earnings_proximity")
    op.drop_column("daily_snapshot", "technicals")
