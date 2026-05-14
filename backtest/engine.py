"""
Backtest engine: loads previous day's scores.json, fetches actual T+1/T+3/T+5
close prices via Groww API, and computes win rate, avg return, and alpha vs Nifty 50.
Results are appended to output/backtest_log.json.
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

NIFTY50_SYMBOL = "NIFTY50"  # Groww symbol for Nifty 50 index
BACKTEST_LOG = Path(config.OUTPUT_DIR) / "backtest_log.json"

# NSE holidays — extend this list annually or load from an external source
_NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 25),  # Holi
    date(2026, 4, 2),   # Ram Navami (tentative)
    date(2026, 4, 14),  # Dr. Ambedkar Jayanti / Baisakhi
    date(2026, 5, 1),   # Maharashtra Day
    date(2026, 8, 15),  # Independence Day
    date(2026, 10, 2),  # Gandhi Jayanti
    date(2026, 11, 4),  # Diwali Laxmi Puja (tentative)
    date(2026, 12, 25), # Christmas
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _NSE_HOLIDAYS_2026


def nth_trading_day(from_date: date, n: int) -> date:
    """Return the Nth trading day after from_date."""
    current = from_date
    count = 0
    while count < n:
        current += timedelta(days=1)
        if is_trading_day(current):
            count += 1
    return current


def _load_scores(scores_path: Path) -> list[dict]:
    if not scores_path.exists():
        return []
    with open(scores_path) as f:
        return json.load(f)


def _fetch_close(symbol: str, target_date: date) -> Optional[float]:
    """Fetch closing price for a symbol on a given date via Groww API."""
    try:
        from enrichment.groww_client import get_candles
        candles = get_candles(symbol, lookback_days=10, to_date=target_date)
        # Find the candle matching target_date
        for c in reversed(candles):
            c_date = date.fromisoformat(c[0][:10])
            if c_date <= target_date:
                return float(c[4])  # close price
    except Exception as e:
        logger.warning("Could not fetch close for %s on %s: %s", symbol, target_date, e)
    return None


def run_backtest(signal_date: date) -> Optional[dict]:
    """
    Run backtest for signals generated on `signal_date`.
    Looks up output/{signal_date}/scores.json, fetches T+1/T+3/T+5 closes.
    Returns a backtest result dict, or None if no scores found.
    """
    date_str = signal_date.strftime("%Y-%m-%d")
    scores_path = Path(config.OUTPUT_DIR) / date_str / "scores.json"
    all_scores = _load_scores(scores_path)

    if not all_scores:
        logger.warning("No scores found for %s — skipping backtest", date_str)
        return None

    # Take top-10 by composite_score
    top10 = sorted(all_scores, key=lambda x: x.get("composite_score", 0), reverse=True)[:10]

    t1 = nth_trading_day(signal_date, 1)
    t3 = nth_trading_day(signal_date, 3)
    t5 = nth_trading_day(signal_date, 5)

    # Nifty 50 benchmark returns
    nifty_t0 = _fetch_close(NIFTY50_SYMBOL, signal_date)
    nifty_t5 = _fetch_close(NIFTY50_SYMBOL, t5)
    nifty_return = (
        round((nifty_t5 - nifty_t0) / nifty_t0 * 100, 2)
        if nifty_t0 and nifty_t5
        else None
    )

    picks = []
    for stock in top10:
        sym = stock.get("ticker") or stock.get("symbol", "")
        # Entry price = signal day's close (from scores.json if available, else fetch)
        entry = stock.get("ltp") or _fetch_close(sym, signal_date)
        if not entry:
            logger.warning("No entry price for %s — skipping", sym)
            continue

        c_t1 = _fetch_close(sym, t1)
        c_t3 = _fetch_close(sym, t3)
        c_t5 = _fetch_close(sym, t5)

        def pct(close):
            return round((close - entry) / entry * 100, 2) if close else None

        picks.append({
            "symbol": sym,
            "composite_score": stock.get("composite_score"),
            "entry_price": entry,
            "t1_close": c_t1, "t1_return_pct": pct(c_t1),
            "t3_close": c_t3, "t3_return_pct": pct(c_t3),
            "t5_close": c_t5, "t5_return_pct": pct(c_t5),
        })

    if not picks:
        return None

    t5_returns = [p["t5_return_pct"] for p in picks if p["t5_return_pct"] is not None]
    win_rate = round(sum(1 for r in t5_returns if r > 0) / len(t5_returns) * 100, 1) if t5_returns else None
    avg_return = round(sum(t5_returns) / len(t5_returns), 2) if t5_returns else None
    alpha = round(avg_return - nifty_return, 2) if avg_return is not None and nifty_return is not None else None

    picks_with_t5 = [p for p in picks if p["t5_return_pct"] is not None]
    best = max(picks_with_t5, key=lambda p: p["t5_return_pct"], default=None)
    worst = min(picks_with_t5, key=lambda p: p["t5_return_pct"], default=None)

    result = {
        "signal_date": date_str,
        "evaluated_on": date.today().strftime("%Y-%m-%d"),
        "t1_date": t1.strftime("%Y-%m-%d"),
        "t3_date": t3.strftime("%Y-%m-%d"),
        "t5_date": t5.strftime("%Y-%m-%d"),
        "picks": picks,
        "metrics": {
            "win_rate_pct": win_rate,
            "avg_return_t5_pct": avg_return,
            "nifty50_return_pct": nifty_return,
            "alpha_pct": alpha,
            "best_pick": {"symbol": best["symbol"], "return_pct": best["t5_return_pct"]} if best else None,
            "worst_pick": {"symbol": worst["symbol"], "return_pct": worst["t5_return_pct"]} if worst else None,
            "stocks_evaluated": len(picks),
        },
    }

    logger.info(
        "Backtest %s: win_rate=%.1f%% avg_return=%.2f%% alpha=%.2f%%",
        date_str,
        win_rate or 0,
        avg_return or 0,
        alpha or 0,
    )
    return result


def append_backtest_log(result: dict):
    """Append a backtest result to the running backtest_log.json."""
    BACKTEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if BACKTEST_LOG.exists():
        with open(BACKTEST_LOG) as f:
            log = json.load(f)

    # Replace entry for same signal_date if it exists
    log = [e for e in log if e.get("signal_date") != result["signal_date"]]
    log.append(result)
    log.sort(key=lambda x: x["signal_date"])

    with open(BACKTEST_LOG, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    logger.info("Backtest log updated → %s (%d entries)", BACKTEST_LOG, len(log))
