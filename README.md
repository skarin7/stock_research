# Stock Intelligence

A daily stock-scoring pipeline for Indian markets (NSE/BSE) that pulls fundamentals, enriches with live price data and news, scores each stock with Claude AI, and delivers a ranked HTML report + Telegram notification.

## How it works

Seven sequential stages run each morning:

```
1. Stock universe   → Screener.in custom screen or NSE index (Nifty 50/100/200/500)
2. NSE data         → Bhavcopy (delivery %, 52-wk range) + bulk deals
3. Groww enrichment → Live quotes (growwapi) + OHLC candles (yfinance)
4. News + macro     → RSS headlines per stock; Gemini for macro context
5. Claude scoring   → Haiku scores 8 signals (1–10); Batch API for ≥20 stocks
6. Rank + report    → Weighted composite score → scores.json + report.html (Sonnet narrative)
7. Backtest         → T+1/T+3/T+5 performance vs Nifty 50, logged to backtest_log.json
```

## Prerequisites

- Python 3.12+
- Anthropic API key (required)
- Gemini API key (optional — macro context; 1500 req/day free tier)
- Telegram bot token + chat ID (optional — notifications)
- Groww account with TOTP (optional — live quotes fallback to yfinance)
- Screener.in account (optional — only if `STOCK_UNIVERSE=screener`)

## Setup

```bash
cd stock-intelligence
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

## Configuration

Edit `.env` — all fields except `ANTHROPIC_API_KEY` are optional:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | **Required.** Get from console.anthropic.com |
| `GEMINI_API_KEY` | Macro context via Gemini. Get from aistudio.google.com |
| `TELEGRAM_BOT_TOKEN` | Notification bot. Get from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat/channel ID |
| `STOCK_UNIVERSE` | `nifty50` / `nifty100` / `nifty200` / `nifty500` / `screener` (default: `nifty200`) |
| `SCREENER_EMAIL` | Only needed when `STOCK_UNIVERSE=screener` |
| `SCREENER_PASSWORD` | Only needed when `STOCK_UNIVERSE=screener` |
| `SCREENER_SCREEN_ID` | Numeric ID from screener.in/screens/XXXXXX/ |
| `GROWW_TOTP_SECRET` | TOTP secret for Groww auth (preferred over JWT) |
| `MAX_STOCKS_TO_SCORE` | Cap on stocks sent to Claude (default: 100, sorted by market cap) |

## Running

```bash
# Full pipeline
bash run_local.sh

# Dry-run — 5 stocks, fast
bash run_local.sh --dry-run

# Skip backtest (no prior day's scores needed)
bash run_local.sh --skip-backtest

# Skip Sonnet narrative (saves cost)
bash run_local.sh --skip-narrative

# Specific date
python main.py --date 2026-05-14
```

## Output

Each run writes to `output/YYYY-MM-DD/`:

```
output/
  YYYY-MM-DD/
    scores.json     # ranked list with signal breakdown per stock
    report.html     # visual report with Sonnet narrative
  backtest_log.json # cumulative T+1/T+3/T+5 win rate vs Nifty 50
```

## Tests

```bash
python -m pytest tests/ -v
```

No `.env` needed — all 12 tests mock `config` entirely. Tests cover prompts, ranker, backtest engine, and Screener filters.

## Deployment (GCP Cloud Run)

```bash
# One-time setup
bash deploy/setup_gcp.sh

# Build and deploy
gcloud builds submit --config cloudbuild.yaml
```

Secrets are injected as environment variables — no `.env` file in the container. The image is based on `python:3.12-slim` with `gcc`, `libxml2-dev`, and `libxslt-dev` for lxml.

For scheduled daily runs, use `scheduler/cron.sh` or Cloud Scheduler pointing at the Cloud Run job.

## Project layout

```
stock-intelligence/
  main.py               # pipeline orchestrator
  config.py             # settings loaded from .env
  scrapers/             # Stages 1–2: universe + NSE bhavcopy/bulk deals
  enrichment/           # Stages 3–4: Groww + yfinance + news + Gemini
  scoring/              # Stages 5–6: Claude Haiku scoring + ranker
  backtest/             # Stage 7: T+1/T+3/T+5 backtest
  reports/              # HTML report (Jinja2) + Sonnet narrative
  notifications/        # Telegram delivery
  tests/                # pytest unit tests
  output/               # run artifacts (gitignored)
  scheduler/cron.sh     # cron wrapper
  deploy/setup_gcp.sh   # GCP setup script
```
