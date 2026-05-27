"""Intraday signal scoring — a faithful port of the spec's S1–S10 / N1–N7 rules.

``score_stock`` is pure: it takes a context dict of pre-computed inputs and
returns ``{symbol, score, reasons}``. Every rule is guarded so a missing input
(None / absent key) contributes 0 points rather than crashing — that is how an
unavailable data source (dead NSE endpoint, non-F&O stock with no PCR, a Tier-C
signal that isn't wired yet) degrades gracefully.

The per-signal thresholds below are spec constants, not config knobs.
"""

from __future__ import annotations

# ── Spec thresholds (do not move to config — they define the strategy) ────────
VOLUME_SPIKE_MULT = 1.5        # S2: volume > 1.5x 20-day avg
RSI_IDEAL_LOW, RSI_IDEAL_HIGH = 55, 68   # S5: ideal momentum zone
NEAR_52W_HIGH_PCT = 0.95       # S6: within 5% of 52-week high
PCR_BULLISH = 1.2              # S8: put-call ratio > 1.2
GENTLE_3D_LOW, GENTLE_3D_HIGH = 2, 4     # S10: 2–4% over 3 sessions
CHASE_TODAY_PCT = 8            # N1: already up > 8% today
RSI_OVERBOUGHT = 75            # N2: RSI > 75
NIFTY_WEAK_PCT = -0.5          # N3: Nifty down > 0.5%
LOW_VOLUME_MULT = 0.7          # N5: volume < 0.7x 20-day avg


def score_stock(ctx: dict) -> dict:
    """Apply S1–S10 and N1–N7 to a single stock context.

    Expected context keys (all optional — None means "data unavailable"):
      has_board_meeting_tomorrow, volume_today, avg_volume_20d, close, high_20d,
      sector_peer_strong_result, rsi14, high_52w, fii_net_buyer_sector, pcr,
      unusual_call_oi, change_3d_pct, today_change_pct, in_asm_gsm,
      nifty_change_pct, fii_net_seller_sector, has_legal_issue.
    """
    score = 0
    reasons: list[str] = []

    def add(points: int, label: str):
        nonlocal score
        score += points
        sign = f"+{points}" if points >= 0 else str(points)
        reasons.append(f"[{sign}] {label}")

    g = ctx.get

    # ── Positive signals ──────────────────────────────────────────────────
    if g("has_board_meeting_tomorrow"):
        add(3, "Board meeting tomorrow (results/dividend)")

    vol, avg_vol = g("volume_today"), g("avg_volume_20d")
    if vol is not None and avg_vol:
        if vol > VOLUME_SPIKE_MULT * avg_vol:
            add(2, f"Volume {vol / avg_vol:.1f}x 20-day avg")

    close, high_20d = g("close"), g("high_20d")
    if close is not None and high_20d is not None and close > high_20d:
        add(2, "20-day breakout")

    if g("sector_peer_strong_result"):
        add(2, "Sector peer reported strong result today")

    rsi = g("rsi14")
    if rsi is not None and RSI_IDEAL_LOW <= rsi <= RSI_IDEAL_HIGH:
        add(1, f"RSI {rsi:.0f} in ideal zone (55–68)")

    high_52w = g("high_52w")
    if close is not None and high_52w:
        if close >= NEAR_52W_HIGH_PCT * high_52w:
            add(1, "Within 5% of 52-week high")

    if g("fii_net_buyer_sector"):
        add(1, "FII net buyers in sector")

    pcr = g("pcr")
    if pcr is not None and pcr > PCR_BULLISH:
        add(1, f"PCR {pcr:.2f} (bullish)")

    if g("unusual_call_oi"):
        add(1, "Unusual Call OI buildup")

    chg3 = g("change_3d_pct")
    if chg3 is not None and GENTLE_3D_LOW <= chg3 <= GENTLE_3D_HIGH:
        add(1, "Gentle 3-day momentum (2–4%)")

    # ── Negative signals ──────────────────────────────────────────────────
    chg_today = g("today_change_pct")
    if chg_today is not None and chg_today > CHASE_TODAY_PCT:
        add(-2, "Already up >8% today (chase risk)")

    if rsi is not None and rsi > RSI_OVERBOUGHT:
        add(-2, "RSI overbought >75")

    nifty = g("nifty_change_pct")
    if nifty is not None and nifty < NIFTY_WEAK_PCT:
        add(-2, "Nifty down >0.5% (weak market)")

    if g("in_asm_gsm"):
        add(-1, "In ASM/GSM surveillance")

    if vol is not None and avg_vol:
        if vol < LOW_VOLUME_MULT * avg_vol:
            add(-1, "Low volume day")

    if g("fii_net_seller_sector"):
        add(-1, "FII net sellers in sector")

    if g("has_legal_issue"):
        add(-1, "Pending regulatory/legal issue")

    return {"symbol": ctx.get("symbol", ""), "score": score, "reasons": reasons}


def conviction(score: int, high_threshold: int = 7) -> str:
    """Map a score to the spec's conviction band."""
    if score >= high_threshold:
        return "HIGH"
    if score >= 5:
        return "MODERATE"
    if score >= 3:
        return "LOW"
    return "IGNORE"
