"""Intraday prediction system — day-before next-day watchlist engine.

A standalone evening pipeline (parallel to main.py / run_agents.py) that scores
the NSE universe on a fixed signal framework (S1–S10 positive, N1–N7 negative),
filters to score ≥ INTRADAY_SCORE_THRESHOLD, and pushes a ranked watchlist to
Telegram. Entry point: run_intraday.py.
"""
