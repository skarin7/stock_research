# Chat intent layer — semantic router → LLM fallback (design spec)

> Status: **active** — being implemented.

## Context

**Problem.** The chat agent (`agents/chat/agent.py`) has **no intent classification**. Except for the `/approve`/`/reject` prefix rule in `server/app.py`, every message — "hi", out-of-scope, "buy 10 TCS" — spins the full Sonnet ReAct loop. Intent is implicit (LLM picks tools from prose heuristics). Consequences: cost/latency on trivial input, no disambiguation (it guesses), no intent analytics, prose-only routing that's untestable.

**Intended outcome.** A **tiered router** in front of `run_turn`:
1. **Tier 1 — HITL approval (deterministic, no LLM/ReAct).** `agents/approval.route_approval` handles the explicit `/approve|/reject <id>` command AND a **bare `approve`/`reject` reply when an approval is pending** for the chat (single pending → resolved directly; multiple → asks which id). When a ReAct/trading flow has suspended at a HITL `interrupt()`, the user's "approve" never touches the intent router or the LLM. Returns `None` (→ chat agent) only when nothing is pending or the message isn't a decision.
2. **Semantic router (primary, no LLM)** — a curated FAQ/exemplar bank (master data) is embedded **once via OpenRouter** (`openai/text-embedding-3-small`) and cached **in Postgres**; each incoming message costs **one query embedding** then **cosine similarity on CPU** against the PG-loaded bank → intent = nearest exemplar's label when top similarity ≥ threshold. The common case resolves here with **zero LLM (chat) calls**.
3. **LLM classifier (fallback only)** — when semantic similarity is below threshold (genuinely novel/ambiguous phrasing), one cheap Haiku call decides; still low confidence → `ambiguous`.
4. **Action** — canned intents (`greeting`/`out_of_scope`/`trade_intent`) → instant canned reply (skip ReAct); `ambiguous` → clarifying question; research-class → existing ReAct agent **unchanged** (with an `(intent: x)` hint). Every turn logs its intent + the route that decided it. **Fail-open**: embedder/classifier error → straight to the ReAct agent.

## Files

- **Create** `agents/chat/intent_exemplars.py` — the **master data**: `EXEMPLARS: dict[intent, list[str]]` of representative phrasings per intent (`greeting | research | entry_exit | macro | recall | portfolio | trade_intent | out_of_scope`), e.g. `entry_exit: ["when should I buy TCS", "good entry for INFY", "is it time to sell"]`. Curated, version-controlled, easy to extend.
- **Create** `agents/chat/embedder.py` — `embed(texts)->np.ndarray` via the **OpenRouter** OpenAI-compatible embeddings endpoint (`OPENROUTER_*` config + `openai` client, model `CHAT_EMBED_MODEL`). `bank_vectors()` returns the exemplar bank, **cached in Postgres** (keyed by an exemplar-set hash + model) so it embeds **once** and re-embeds only when the set/model changes; in-process memo on top. No DB → embed once per process. Embed backend error → raise, router falls through to the LLM classifier.
- **Create** `agents/chat/intent.py` —
  - `route_intent(text) -> {intent, confidence, route}`: tier 2 `embed(text)` → cosine vs bank → `(best_intent, sim)`; if `sim >= CHAT_SEMANTIC_THRESHOLD` return it (`route="semantic"`); else tier 3 `classify_intent_llm(text)` (`route="llm"`); on any embedder error skip to tier 3.
  - `classify_intent_llm(text)` — one cheap Haiku call (`get_chat_model(model=CHAT_INTENT_MODEL, max_tokens=120)`) → JSON `{intent, confidence}`; `< CHAT_INTENT_MIN_CONFIDENCE` → `ambiguous`.
  - `CANNED` replies + `is_research_intent()` helper.
- **Modify** `agents/chat/agent.py` `run_turn` — after the kill-switch check (router is core, always on): `route_intent(text)` → branch (canned / clarifying / fall-through-with-hint), `store.record_memory("chat_intent", chat_id, {intent, route, sim})`. Any exception in routing → log + proceed to the agent (fail-open).
- **Add** `persistence/models.py` `IntentEmbeddingRow` (PK `exemplar_hash`+`model`, JSON `labels`/`vectors`) + `persistence/store.py` `load_intent_bank`/`save_intent_bank` (no-op without `DATABASE_URL`).
- **Modify** `settings.py` — `CHAT_SEMANTIC_THRESHOLD` (0.55), `CHAT_EMBED_MODEL` (`openai/text-embedding-3-small`), `CHAT_INTENT_MODEL` (`SCORING_MODEL`), `CHAT_INTENT_MIN_CONFIDENCE` (0.6). The router is **core (no enable flag)** — `embedder.available()` self-gates the semantic tier on `OPENROUTER_API_KEY`.
- **Modify** `requirements.txt` — pin `numpy` (cosine math; embeddings reuse the existing `openai` client → no new heavy dep).
- **Create** `tests/test_chat_intent.py` — fully mocked embedder (deterministic vectors) + mocked LLM; assert: a phrase near an exemplar routes by **cosine with NO LLM call**; a far phrase falls to the **LLM fallback**; greeting/out_of_scope/trade_intent short-circuit (ReAct **not** invoked); ambiguous → clarifying question; embedder raising → LLM fallback; both unavailable → ReAct fall-through (fail-open); intent record written.
- **Modify** `CLAUDE.md` — document the tiered router under the chat-agent section.

## Design notes

- **Cost:** common case = **1 small embedding API call (~ms, ~\$0.00001), zero chat-LLM**. The chat LLM classifier fires only on novel phrasings below threshold. Bank embedded once into PG.
- **Reuse** `get_chat_model` (`agents/llm.py`) for the fallback, the `openai` client + `OPENROUTER_*` for embeddings, and `store.record_memory` for analytics.
- ReAct agent + 9 tools **unchanged** — this is a front door, core (no enable flag). Self-gates: no `OPENROUTER_API_KEY` → semantic tier skipped → LLM classifier.
- Cold-start: the bank is read from Postgres (one query); embedding only happens on first-ever build or after the exemplar set changes (one-off). No DB → embed once per process.
- **Threshold tuning:** `CHAT_SEMANTIC_THRESHOLD` trades router coverage vs LLM-fallback rate; start 0.55, tune from the logged `sim` distribution.

## Out of scope
- Replacing the ReAct agent or its tools.
- A trained/fine-tuned classifier — exemplar cosine + LLM fallback is sufficient at this volume.

## Verification
1. **Unit:** `python -m pytest tests/test_chat_intent.py -v` (assertions above) — the **no-LLM-on-semantic-hit** assertion is the key one.
2. **Regression:** `python -m pytest tests/ -v` green; with no `OPENROUTER_API_KEY` the router self-gates to the LLM classifier (no per-message embed error).
3. **Local smoke:** `python scripts/run_chat_local.py` — "hi" (canned, no embed-miss/LLM in logs), "when's a good time to buy INFY?" (semantic→entry_exit, timing runs), "buy 10 TCS" (canned), a nonsense one-word message (LLM fallback → clarifying question). Check logs show `route=semantic` for the common cases.
