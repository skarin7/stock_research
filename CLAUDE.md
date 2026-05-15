# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The repo root contains one active project: `stock-intelligence/` — an NSE/BSE daily stock-scoring pipeline. All code lives there; the root also holds a planning doc (`StockIntelligenceSystem_Plan.docx`).

```
stock-intelligence/
  main.py               # pipeline orchestrator (7 stages)
  config.py             # all settings loaded from .env
  scrapers/             # Stage 1–2: stock universe + NSE bhavcopy/bulk deals
  enrichment/           # Stage 3–4: Groww API (quotes/OHLC) + news + Gemini macro
  scoring/              # Stage 5–6: Claude Haiku batch scoring + weighted ranker
  backtest/             # Stage 7: T+1/T+3/T+5 backtest vs Nifty 50
  reports/              # HTML report (Jinja2) + Claude Sonnet narrative
  notifications/        # Telegram delivery
  tests/                # pytest unit tests (no API keys needed)
  output/               # YYYY-MM-DD/scores.json + report.html, backtest_log.json
  scheduler/cron.sh     # cron wrapper for production scheduling
  deploy/setup_gcp.sh   # GCP Cloud Run setup
```

## Setup

```bash
cd stock-intelligence
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
```

Required env vars: `ANTHROPIC_API_KEY`. All others are optional with graceful fallback (see `config.py`).

## Running

```bash
# Full run
bash run_local.sh

# Dry-run (5 stocks, fast)
bash run_local.sh --dry-run

# Skip backtest (saves time if no prior day's scores exist)
bash run_local.sh --skip-backtest

# Skip Sonnet narrative (saves cost)
bash run_local.sh --skip-narrative

# Specific date
python main.py --date 2026-05-14
```

## Tests

```bash
# Run all tests from stock-intelligence/
cd stock-intelligence
python -m pytest tests/ -v

# Single test
python -m pytest tests/test_scorer.py::TestRanker::test_composite_score_weighted -v
```

Tests mock `config` entirely — no `.env` needed. 12 tests cover: prompts, ranker, backtest engine, and Screener filters.

## Pipeline architecture

`main.py` runs 7 sequential stages:

1. **Stock universe** — either Screener.in custom screen (`STOCK_UNIVERSE=screener`) or NSE index (`nifty50`/`nifty100`/`nifty200`/`nifty500`). BSE-only (numeric) symbols are dropped. A persistent `output/skip_list.json` excludes stocks with no price data.
2. **NSE Bhavcopy + Bulk Deals** — delivery %, 52-week range, institutional bulk deals from NSE CSV dumps.
3. **Groww enrichment** — live quotes via `growwapi` SDK; OHLC candles via `yfinance` (free, `.NS` suffix). TOTP auth preferred over legacy JWT.
4. **News + macro** — RSS headlines per stock; Gemini API for macro context (falls back to empty string if no key).
5. **Claude Haiku scoring** — each stock → JSON scorecard with 8 weighted signals (1–10). Uses synchronous API for <20 stocks, Batch API (50% cheaper) for ≥20.
6. **Rank + report** — `ranker.py` computes weighted composite score; `reports/daily_report.py` writes `output/YYYY-MM-DD/{scores.json, report.html}`. Claude Sonnet writes the narrative section.
7. **Backtest** — reads previous day's `scores.json`, fetches T+1/T+3/T+5 closes via yfinance, computes win rate + alpha vs Nifty 50, appends to `output/backtest_log.json`.

## Key design decisions

- **Signal weights** in `config.SIGNAL_WEIGHTS` must sum to 1.0. The ranker normalises against present signals only, so partial scorecards are handled gracefully.
- **Scorer threshold** `_SYNC_THRESHOLD = 20` in `claude_scorer.py` controls sync vs batch split.
- **Groww rate limit** is `GROWW_RATE_LIMIT_DELAY_MS` (default 200 ms); set to 0 in tests.
- **`MAX_STOCKS_TO_SCORE`** (default 100) caps the Groww/Claude expense: stocks are sorted by market cap and the tail is dropped before enrichment.
- Claude models are `claude-haiku-4-5` (scoring) and `claude-sonnet-4-6` (narrative); both are in `config.py`.

## Deployment (GCP Cloud Run)

```bash
# Build image (Cloud Build / Kaniko)
gcloud builds submit --config stock-intelligence/cloudbuild.yaml

# Run job (secrets injected as env vars — no .env in container)
```

The Dockerfile uses `python:3.12-slim` and needs `gcc`, `libxml2-dev`, `libxslt-dev` for lxml.
