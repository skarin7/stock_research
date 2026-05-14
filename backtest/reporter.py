"""
Generates a backtest summary dict suitable for embedding in the daily HTML report,
and computes signal-level accuracy metrics from the backtest log.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

BACKTEST_LOG = Path(config.OUTPUT_DIR) / "backtest_log.json"


def load_backtest_log() -> list[dict]:
    if not BACKTEST_LOG.exists():
        return []
    with open(BACKTEST_LOG) as f:
        return json.load(f)


def latest_backtest_summary() -> Optional[dict]:
    """
    Return a summary dict for the most recent completed backtest entry.
    Used for embedding in today's HTML report.
    """
    log = load_backtest_log()
    if not log:
        return None

    latest = log[-1]
    m = latest.get("metrics", {})
    return {
        "signal_date": latest.get("signal_date"),
        "win_rate_pct": m.get("win_rate_pct"),
        "avg_return_t5_pct": m.get("avg_return_t5_pct"),
        "nifty50_return_pct": m.get("nifty50_return_pct"),
        "alpha_pct": m.get("alpha_pct"),
        "best_pick": m.get("best_pick"),
        "worst_pick": m.get("worst_pick"),
        "total_days_tracked": len(log),
        "overall_win_rate": _overall_win_rate(log),
    }


def _overall_win_rate(log: list[dict]) -> Optional[float]:
    rates = [e["metrics"]["win_rate_pct"] for e in log if e.get("metrics", {}).get("win_rate_pct") is not None]
    if not rates:
        return None
    return round(sum(rates) / len(rates), 1)


def signal_accuracy_report() -> dict:
    """
    Analyse which signals (bulk_deals, news_sentiment, etc.) correlate most with
    positive T+5 returns across all backtest entries.
    Returns dict: {signal_name: {"avg_score_winners": x, "avg_score_losers": y, "correlation": z}}
    """
    log = load_backtest_log()
    if not log:
        return {}

    # We'd need scores.json for each date to cross-reference signals with returns
    signal_data: dict[str, dict] = {}
    signal_names = list(config.SIGNAL_WEIGHTS.keys())

    for entry in log:
        date_str = entry.get("signal_date", "")
        scores_path = Path(config.OUTPUT_DIR) / date_str / "scores.json"
        if not scores_path.exists():
            continue

        with open(scores_path) as f:
            scores = {s.get("ticker"): s for s in json.load(f)}

        for pick in entry.get("picks", []):
            sym = pick["symbol"]
            t5_ret = pick.get("t5_return_pct")
            if t5_ret is None:
                continue
            winner = t5_ret > 0
            scorecard = scores.get(sym, {})
            signals = scorecard.get("signals", {})

            for sig in signal_names:
                score = signals.get(sig, {}).get("score")
                if score is None:
                    continue
                d = signal_data.setdefault(sig, {"winner_scores": [], "loser_scores": []})
                if winner:
                    d["winner_scores"].append(score)
                else:
                    d["loser_scores"].append(score)

    result = {}
    for sig, d in signal_data.items():
        w = d["winner_scores"]
        l = d["loser_scores"]
        avg_w = round(sum(w) / len(w), 2) if w else None
        avg_l = round(sum(l) / len(l), 2) if l else None
        diff = round(avg_w - avg_l, 2) if avg_w is not None and avg_l is not None else None
        result[sig] = {
            "avg_score_winners": avg_w,
            "avg_score_losers": avg_l,
            "score_diff": diff,
            "sample_size": len(w) + len(l),
        }

    # Sort by score_diff descending (most predictive signal first)
    result = dict(sorted(result.items(), key=lambda x: x[1].get("score_diff") or 0, reverse=True))
    return result
