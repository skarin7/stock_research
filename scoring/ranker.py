"""
Weighted composite scorer and top-N ranker.
Applies signal weights from SETTINGS.SIGNAL_WEIGHTS to each scored stock.
"""

import logging

from config import SETTINGS

logger = logging.getLogger(__name__)


def compute_composite(scorecard: dict) -> float:
    """
    Compute weighted composite score from a Claude scorecard.
    Individual signal scores are 1–10; composite is also 1–10.
    """
    signals = scorecard.get("signals", {})
    weights = SETTINGS.SIGNAL_WEIGHTS
    total_weight = 0.0
    weighted_sum = 0.0
    for signal_name, weight in weights.items():
        sig = signals.get(signal_name, {})
        score = sig.get("score")
        if score is not None:
            weighted_sum += float(score) * weight
            total_weight += weight

    if total_weight == 0:
        return 0.0
    # Normalise in case some signals were missing
    return round(weighted_sum / total_weight, 2)


def rank_stocks(scorecards: list[dict], top_n: int | None = None) -> list[dict]:
    """
    Attach composite scores and return top-N stocks sorted descending.
    Earnings-proximity stocks are flagged but not excluded.
    """
    top_n = SETTINGS.TOP_N_STOCKS if top_n is None else top_n
    ranked = []
    for card in scorecards:
        card = dict(card)
        card["composite_score"] = compute_composite(card)
        ranked.append(card)

    ranked.sort(key=lambda x: x["composite_score"], reverse=True)
    top = ranked[:top_n]

    logger.info(
        "Ranked %d stocks → top %d | scores: %.1f – %.1f",
        len(ranked), len(top),
        top[0]["composite_score"] if top else 0,
        top[-1]["composite_score"] if top else 0,
    )
    return top
