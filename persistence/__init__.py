"""Postgres persistence for agent + trading state (runs, proposals, positions,
orders, audit). LangGraph manages its own checkpoint/store tables separately.

Only imported when DATABASE_URL is configured; requires SQLAlchemy.
"""
