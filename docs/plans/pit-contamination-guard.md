# PIT contamination guard (mini-spec)

> Status: **ship now.** Carved from `docs/plans/eval-harness.md` (which stays deferred). This is the cheap, decoupled piece: the *warning label* on the non-PIT door, not the backtest time-machine itself.

## Why now (and only this)

The full leak-free harness is deferred — nothing consumes it yet, and the intraday scorer is not sizing real positions. But one piece is cheap insurance worth taking immediately: a guard that makes any **look-ahead historical re-run** self-announce, so neither the user nor a future automation ever trusts contaminated output by accident.

Non-PIT inputs (return *today's* values regardless of `ref_date`): `fundamentals.py` `tk.info`, `fetch_news`, `nse_index` membership. Therefore `main.py --date <past>` / `run_agents.py --date <past>` scores history with future knowledge → look-ahead bias.

## Scope (locked, minimal)

1. **Alarm at the shared chokepoint** — `enrichment/fundamentals.py` `enrich_fundamentals(stocks, ref_date)`. Both entrypoints route through it (`main.py` via `stages.enrich_market_and_fundamentals`, `agents/nodes/research.py:103`), so the guard lives in **one** place.
   - `pit_safe = ref_date >= date.today()`.
   - When not PIT-safe: one loud `logger.warning(...)` naming the bias and that the run is not for validation.
   - Stamp every returned stock dict with `stock["pit_safe"] = pit_safe`.

2. **Self-documenting cache** — `persistence/store.py` `build_snapshot_rows` carries `pit_safe` (default `True`) into each snapshot row. Lands in `output/<date>/snapshot.json` (the chat agent's daily cache). DB insert key list is unchanged, so no schema break — the file (the no-DB fallback) carries the flag.

3. **Test** — `tests/test_pit_guard.py`: past `ref_date` ⇒ warning fired + every stock `pit_safe=False`; `ref_date=today`/`None` ⇒ `pit_safe=True`. yfinance fully mocked.

4. **Docs** — `CLAUDE.md`: note the PIT-safe vs unsafe split and that `pit_safe=false` flags contaminated runs.

## Explicitly out of scope (defer with the harness)

- **`scores.json` tagging.** Its rows come from the closed-shape `Scorecard` contract (no `extra`); threading `pit_safe` through needs contract surgery — disproportionate for a guard. The loud log warning + the snapshot flag already cover the self-documentation need. Revisit when the full harness lands.
- The walk-forward engine, baselines, metrics, cost model — all stay in `eval-harness.md`.

## Verification

1. `python -m pytest tests/test_pit_guard.py -v` — guard fires on past date, not on today.
2. `python -m pytest tests/ -v` — stays green.
3. Manual: `python main.py --date <past>` → WARNING in logs, `pit_safe: false` in `output/<past>/snapshot.json`.
