# Agent Profile Config — Design Spec

**Date:** 2026-06-27  
**Status:** Approved

## Problem

Nine `ENABLE_*` flags + `AGENT_MODE` + `ENABLE_LIVE_TRADING` + `GROWW_TRADING_ENABLED` in `.env` are confusing for operators. Wrong combinations (e.g. `ENABLE_TRADING_AGENT=true` without `ENABLE_RISK_AGENT=true`) silently produce broken runs.

## Solution

Single `AGENT_PROFILE` env var. Profile expands to all flags at load time inside `settings.py`. Agents unchanged — they still read `ENABLE_*` flags on `SETTINGS`.

## User-Facing Config (post-change)

```env
AGENT_PROFILE=research    # research | paper | live
ENABLE_CHAT_AGENT=false   # add-on service, works with any profile
```

All other `ENABLE_*` flags and `AGENT_MODE` removed from `.env.example`.

## Profile → Flag Map

| Flag | research | paper | live |
|---|---|---|---|
| AGENT_MODE | research | paper | live |
| ENABLE_RESEARCH_AGENT | ✓ | ✓ | ✓ |
| ENABLE_ANALYST_AGENT | ✓ | ✓ | ✓ |
| ENABLE_MEMORY_AGENT | ✓ | ✓ | ✓ |
| ENABLE_DEBATE_AGENT | — | ✓ | ✓ |
| ENABLE_RISK_AGENT | — | ✓ | ✓ |
| ENABLE_PORTFOLIO_AGENT | — | ✓ | ✓ |
| ENABLE_TRADING_AGENT | — | ✓ | ✓ |
| ENABLE_MONITORING_AGENT | — | ✓ | ✓ |
| ENABLE_LIVE_TRADING | — | — | ✓ |
| GROWW_TRADING_ENABLED | — | — | ✓ |
| ENABLE_AUTO_EXIT | — | — | ✓ |

## Architecture

### `settings.py` changes

1. Add `_PROFILE_FLAGS: dict[str, dict]` — profile map (source of truth).
2. Add `AGENT_PROFILE: str = "research"` field to `Settings`.
3. After `Settings` object is constructed, call `_apply_profile(settings)`:
   - Looks up profile in map.
   - Invalid profile → `ValueError` at startup (fail fast).
   - Sets all `ENABLE_*` and `AGENT_MODE` fields on the object.
4. Log: `INFO agents.settings profile=research → research+analyst+memory agents ON`.

### `.env.example` changes

- Remove: all `ENABLE_*` flags, `AGENT_MODE`
- Add: `AGENT_PROFILE=research  # research | paper | live`
- Keep: `ENABLE_CHAT_AGENT=false` (orthogonal service toggle)

### Agents (no changes)

`agent_node` decorator and broker guards continue reading `ENABLE_*` flags from `SETTINGS`. They are unaware profiles exist.

## What Does NOT Change

- `agent_node` decorator
- All node files (`research.py`, `analyst.py`, etc.)
- Broker safety gates (`groww_trader.py`, `auto_exit.py`)
- Graph routing (`graph.py`)
- Test fixtures (tests that set `ENABLE_*` directly still work)

## Error Handling

- Unknown `AGENT_PROFILE` value → `ValueError` with message listing valid profiles — crash at import time before any run starts.

## Testing

- Unit test: each profile sets the expected flag values.
- Existing tests unaffected (they set `ENABLE_*` flags directly on a mock settings object).
