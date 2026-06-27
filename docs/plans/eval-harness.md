# Leak-free evaluation harness (design spec)

> Status: **deferred** — reference spec. Implement as a separate effort after the chat intent router.
>
> Note: the **contamination guard** piece has been carved out and shipped — see `docs/plans/pit-contamination-guard.md`. The walk-forward engine/baselines/metrics below remain deferred.

## Context

**Problem.** The current backtest (`backtest/engine.py`) only measures forward prices of *already-saved* `scores.json`, which is fine **only when scores were generated live**. There is no way to validate the strategy by replaying history, because two inputs are **not point-in-time (PIT)**:
- `enrichment/fundamentals.py` `tk.info` → today's PE/forwardPE/marketCap/sector regardless of `ref_date`.
- News (`fetch_news`) → today's headlines.
- `scrapers/nse_index.py` → only the *current* index constituents → **survivorship/membership bias**.

So a historical re-run (`main.py --date <past>`) scores the past with future knowledge → **look-ahead bias**, inflated results. The system therefore has **no proven edge** (only a forward track record would prove it, and there's barely any yet).

**What IS PIT-safe (datable):** `scrapers/nse_bhavcopy.py` `download_bhavcopy(for_date)` (dated NSE archive — price/delivery/volume, and it lists names later delisted), `get_default_provider().get_ohlcv(symbol, to_date=)` (historical candles), and the **pure deterministic scorer** `intraday/signals.py` `score_stock` + `intraday/technicals.py` `compute_metrics`.

**Intended outcome.** A walk-forward backtest that honestly validates the **deterministic intraday scorer** over history using only PIT-safe inputs, compared against naive baselines after costs — plus a **contamination guard** that flags any historical LLM/fundamental re-run as "not for validation." (The LLM daily pipeline is explicitly out of scope here — it can only be validated forward as live history accrues.)

**Scope decision (locked):** deterministic harness + guard. No forward LLM validator yet.

## Files

- **Create** `backtest/harness.py` — the walk-forward engine (CLI: `python -m backtest.harness --from YYYY-MM-DD --to YYYY-MM-DD [--top-n 10] [--no-costs]`). Pure orchestration over reused helpers; writes `output/backtest/harness_<from>_<to>.json` + a printed summary.
- **Create** `backtest/baselines.py` — `buy_nifty`, `equal_weight`, `momentum_12_1` return series over the same dates/universe (for alpha comparison).
- **Create** `backtest/metrics.py` — `summarize(returns, equity_curve)` → hit-rate, mean/median, annualized Sharpe, max drawdown, alpha vs each baseline, sample size + a `trustworthy` flag (false when n is too small / Sharpe not significant). Optional deflated-Sharpe note.
- **Modify** `enrichment/fundamentals.py` + `agents/nodes/research.py` (or `main.py` scoring path) — **contamination guard**: when `ref_date < today`, log a loud WARNING and tag the run/`scores.json`/snapshot with `pit_safe: false`. (Add `pit_safe` to the snapshot rows / scores output.)
- **Modify** `settings.py` — cost knobs: `BACKTEST_BROKERAGE_BPS` (e.g. 3), `BACKTEST_SLIPPAGE_BPS` (e.g. 10), `BACKTEST_STT_BPS` (e.g. 10), `BACKTEST_MIN_SAMPLE` (e.g. 30).
- **Create** `tests/test_harness.py` — fully mocked provider; assert leak-freeness + metrics + cost + delisting + baselines.
- **Modify** `CLAUDE.md` — document the PIT-safe vs unsafe split and the harness.

## Design

**Walk-forward loop (`harness.py`)** — for each trading day `d` in `[from, to]` (reuse `engine.py` `is_trading_day`/`nth_trading_day`):
1. **Universe = PIT, survivorship-safe.** Take EQ symbols from `download_bhavcopy(d)` (not `nse_index` — bhavcopy lists exactly what traded on `d`, including later-delisted names). Optional liquidity filter from bhavcopy turnover/volume.
2. **Score with PIT inputs only.** Per symbol, `candles = get_ohlcv(sym, to_date=d)` → `technicals.compute_metrics(candles)` → build the `score_stock` ctx, mapping `compute_metrics` keys (`close, volume_today, avg_volume_20d, high_20d, rsi14, high_52w, today_change_pct, change_3d_pct`) and `nifty_change_pct` from `^NSEI` OHLC `to_date=d`. The **non-PIT/unavailable signals degrade to 0 by design** (board-meeting S1, ASM/GSM N4, option-chain PCR/OI S8/S9 → ctx values `None`; the guarded scorer contributes 0). Document this reduced-signal subset.
3. **Pick** top-`N` by score (reuse `conviction` from `intraday/signals.py`).
4. **Forward returns** T+1/3/5 via `get_ohlcv(sym, to_date=nth_trading_day(d,k))` (PIT). **Delisting:** if no forward price, record the position as a loss (last traded price → −100% floor or last available), never silently drop — this preserves survivorship-safety on the *outcome* side too.

**Leak-free invariant (the whole point):** scoring for day `d` calls the provider only with `to_date <= d`; return measurement only with `to_date > d`. Tests assert this.

**Baselines (`baselines.py`)** over identical dates/universe: buy-and-hold `^NSEI`, equal-weight basket, and a 12-1 momentum top-N. Strategy alpha = strategy mean return − baseline return, per horizon.

**Costs (`metrics.py`)** — subtract `(BROKERAGE + STT + SLIPPAGE) bps` round-trip from each trade's return before aggregating. `--no-costs` shows gross for comparison.

**Honest metrics** — hit-rate, mean/median return, annualized Sharpe, max drawdown of the equity curve, alpha vs each baseline, and `n` (trades + periods). If `n < BACKTEST_MIN_SAMPLE` or Sharpe t-stat is weak → `trustworthy=false` with a printed caveat. **No green light from a tiny sample.**

**Contamination guard** — in the fundamentals/research path, `ref_date < date.today()` ⇒ `logger.warning("Historical re-run: fundamentals/news are NOT point-in-time — scores are look-ahead-biased, do not use for validation")` and set `pit_safe=False` on the output so it's self-documenting.

## Reused helpers (do not reimplement)
- `intraday/technicals.py` `compute_metrics`; `intraday/signals.py` `score_stock`, `conviction`.
- `enrichment/market_data.get_default_provider().get_ohlcv(..., to_date=)`.
- `scrapers/nse_bhavcopy.download_bhavcopy(for_date)`.
- `backtest/engine.py` `is_trading_day`, `nth_trading_day`, `_fetch_close`.

## Out of scope
- Forward LLM-pipeline validator (separate; needs accrued live history).
- Closing the scoring feedback loop (signal re-weighting) — depends on this harness existing first.
- Point-in-time *fundamentals* store — not feasible free; the harness simply excludes fundamentals from historical scoring.

## Verification
1. **Unit:** `python -m pytest tests/test_harness.py -v` — a mocked provider returns synthetic dated candles; assert (a) **no scoring call uses `to_date > d`** (leak-free invariant), (b) a delisted symbol (no forward candle) is booked as a loss not dropped, (c) costs reduce net return, (d) baselines + alpha + Sharpe/maxDD math on a known fixture, (e) `trustworthy=false` when `n < BACKTEST_MIN_SAMPLE`.
2. **Regression:** `python -m pytest tests/ -v` stays green.
3. **Guard:** run the research path with a past `--date` and confirm the WARNING fires and `pit_safe=false` lands in `scores.json`/snapshot.
4. **Smoke (needs network):** `python -m backtest.harness --from 2024-01-01 --to 2024-03-31 --top-n 10` → produces a summary with alpha vs buy-Nifty and a trust verdict; sanity-check that gross > net (costs applied) and the equity curve/maxDD look plausible.
