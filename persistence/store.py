"""Portfolio (book) persistence for paper mode.

File-based store keyed on ``SETTINGS.POSITIONS_FILE`` so paper positions persist
across runs without a database (mirrors the MemorySaver fallback used elsewhere).
DB-backed positions land with the live order lifecycle (broker iteration).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import SETTINGS

from agents.contracts import PortfolioState

logger = logging.getLogger("persistence.store")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    return Path(getattr(SETTINGS, "POSITIONS_FILE", "output/positions.json"))


def load_portfolio() -> PortfolioState:
    p = _path()
    if p.exists():
        try:
            return PortfolioState(**json.loads(p.read_text()))
        except Exception as e:  # corrupt / schema drift → start fresh
            logger.warning("could not load portfolio (%s) — starting fresh", e)
    return PortfolioState(cash=float(getattr(SETTINGS, "TRADING_CAPITAL_INR", 0.0)))


def save_portfolio(book: PortfolioState) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(book.model_dump(), indent=2, default=str))
    logger.info("portfolio saved → %s (%d positions)", p, len(book.positions))


def save_proposals(proposals) -> None:
    """Persist proposals (keyed by id) so an out-of-process approver can see them."""
    p = Path(getattr(SETTINGS, "PROPOSALS_FILE", "output/proposals.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_proposals()
    existing.update({pr.proposal_id: pr.model_dump() for pr in proposals})
    p.write_text(json.dumps(existing, indent=2, default=str))


def load_proposals() -> dict:
    p = Path(getattr(SETTINGS, "PROPOSALS_FILE", "output/proposals.json"))
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


# ── daily snapshot (chat agent's screen cache) ─────────────────────────────────
#
# The daily run writes one row per enriched + scored stock. The chat agent's
# screen_snapshot tool filters the latest snapshot instead of re-running the
# pipeline. DB-backed when DATABASE_URL is set; output/<date>/snapshot.json is
# always written and doubles as the no-DB fallback.

_SNAPSHOT_FIELDS = (
    "symbol", "company", "sector", "pe_ratio", "sector_pe", "market_cap_cr",
    "ltp", "delivery_pct", "volume_ratio", "week52_high", "week52_low",
)


def build_snapshot_rows(stocks: list[dict], scorecards: list[dict], news_map: dict) -> list[dict]:
    """Join enriched stock dicts with their scorecards + headlines by symbol."""
    cards = {c.get("ticker"): c for c in scorecards}
    rows = []
    for s in stocks:
        sym = s.get("symbol")
        if not sym:
            continue
        card = cards.get(sym, {})
        row = {k: s.get(k) for k in _SNAPSHOT_FIELDS}
        row["week52_high"] = s.get("52w_high", row.get("week52_high"))
        row["week52_low"] = s.get("52w_low", row.get("week52_low"))
        row.update(
            composite_score=card.get("composite_score"),
            signals=card.get("signals"),
            news=(news_map.get(sym) or {}).get("headlines", []),
            rationale=card.get("investment_rationale", ""),
            risk_flags=card.get("risk_flags", []),
            earnings_proximity=bool(card.get("earnings_proximity", False)),
            technicals=s.get("technicals") or {},
            pit_safe=bool(s.get("pit_safe", True)),  # False on look-ahead historical re-runs
        )
        rows.append(row)
    return rows


def _snapshot_file(run_date: str) -> Path:
    return Path(getattr(SETTINGS, "OUTPUT_DIR", "output")) / run_date / "snapshot.json"


def save_daily_snapshot(run_date: str, rows: list[dict]) -> None:
    """Persist the day's snapshot: always to file, plus DB upsert when configured."""
    p = _snapshot_file(run_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"run_date": run_date, "stocks": rows}, indent=2, default=str))
    logger.info("snapshot saved → %s (%d stocks)", p, len(rows))

    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    from persistence.db import session_scope
    from persistence.models import DailySnapshotRow

    with session_scope() as s:
        s.query(DailySnapshotRow).filter(DailySnapshotRow.run_date == run_date).delete()
        for row in rows:
            s.add(DailySnapshotRow(
                run_date=run_date,
                earnings_proximity=int(bool(row.get("earnings_proximity"))),
                **{k: row.get(k) for k in (*_SNAPSHOT_FIELDS, "composite_score",
                                           "signals", "news", "rationale", "risk_flags",
                                           "technicals")},
            ))
    logger.info("snapshot upserted to DB for %s", run_date)


def load_latest_snapshot() -> tuple[str | None, list[dict]]:
    """Latest snapshot as (run_date, rows). DB first, file fallback, ([], None) if none."""
    if getattr(SETTINGS, "DATABASE_URL", ""):
        try:
            from sqlalchemy import func

            from persistence.db import session_scope
            from persistence.models import DailySnapshotRow

            with session_scope() as s:
                latest = s.query(func.max(DailySnapshotRow.run_date)).scalar()
                if latest:
                    rows = s.query(DailySnapshotRow).filter(DailySnapshotRow.run_date == latest).all()
                    keep = (*_SNAPSHOT_FIELDS, "composite_score", "signals", "news",
                            "rationale", "risk_flags", "technicals")
                    return latest, [
                        {**{k: getattr(r, k) for k in keep},
                         "earnings_proximity": bool(r.earnings_proximity)}
                        for r in rows
                    ]
        except Exception as e:
            logger.warning("DB snapshot load failed (%s) — trying files", e)

    out = Path(getattr(SETTINGS, "OUTPUT_DIR", "output"))
    candidates = sorted(out.glob("*/snapshot.json"), key=lambda p: p.parent.name)
    for p in reversed(candidates):
        try:
            data = json.loads(p.read_text())
            return data.get("run_date", p.parent.name), data.get("stocks", [])
        except Exception as e:
            logger.warning("could not read %s: %s", p, e)
    return None, []


def load_snapshot_for_date(date_str: str) -> tuple[str | None, list[dict]]:
    """Load snapshot for a specific date. DB first, then file fallback.

    Returns (run_date, rows) — same shape as load_latest_snapshot().
    Returns (None, []) if no data exists for that date.
    """
    if getattr(SETTINGS, "DATABASE_URL", ""):
        try:
            from persistence.db import session_scope
            from persistence.models import DailySnapshotRow

            with session_scope() as s:
                rows = (
                    s.query(DailySnapshotRow)
                    .filter(DailySnapshotRow.run_date == date_str)
                    .all()
                )
                if rows:
                    keep = (*_SNAPSHOT_FIELDS, "composite_score", "signals", "news",
                            "rationale", "risk_flags", "technicals")
                    return date_str, [
                        {**{k: getattr(r, k) for k in keep},
                         "earnings_proximity": bool(r.earnings_proximity)}
                        for r in rows
                    ]
        except Exception as e:
            logger.warning("DB snapshot load for %s failed: %s — trying file", date_str, e)

    p = _snapshot_file(date_str)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return data.get("run_date", date_str), data.get("stocks", [])
        except Exception as e:
            logger.warning("file snapshot load for %s failed: %s", date_str, e)

    return None, []


# ── market-pulse state (debounce / baselines for the shock watcher) ────────────

def _pulse_state_path() -> Path:
    return Path(getattr(SETTINGS, "PULSE_STATE_FILE", "output/pulse_state.json"))


def _db_save_pulse_state(state: dict) -> None:
    """Upsert pulse state into Postgres pulse_state table (single-row pattern, id=1)."""
    from datetime import datetime

    from persistence.db import session_scope
    from persistence.models import PulseStateRow

    state_json = json.dumps(state, default=str)
    with session_scope() as s:
        row = s.get(PulseStateRow, 1)
        if row is None:
            s.add(PulseStateRow(id=1, state_json=state_json, updated_at=datetime.utcnow()))
        else:
            row.state_json = state_json
            row.updated_at = datetime.utcnow()


def _db_load_pulse_state() -> dict:
    """Load pulse state from Postgres pulse_state table."""
    from persistence.db import session_scope
    from persistence.models import PulseStateRow

    with session_scope() as s:
        row = s.get(PulseStateRow, 1)
        if row is None:
            return {}
        return json.loads(row.state_json)


def load_pulse_state() -> dict:
    """Per-trigger armed flags + last-alert timestamps + last news-check time.

    DB-backed when DATABASE_URL is set (survives Cloud Run cold starts);
    falls back to JSON file on DB error or when DATABASE_URL is unset.
    Returns an empty dict on first run / corrupt file.
    """
    if getattr(SETTINGS, "DATABASE_URL", ""):
        try:
            return _db_load_pulse_state()
        except Exception as e:
            logger.warning("pulse_state DB load failed (%s) — falling back to JSON", e)
    p = _pulse_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        logger.warning("could not load pulse state (%s) — starting fresh", e)
        return {}


def save_pulse_state(state: dict) -> None:
    """Persist pulse state.

    DB-backed when DATABASE_URL is set; falls back to JSON file on DB error
    or when DATABASE_URL is unset.
    """
    if getattr(SETTINGS, "DATABASE_URL", ""):
        try:
            _db_save_pulse_state(state)
            return
        except Exception as e:
            logger.warning("pulse_state DB save failed (%s) — falling back to JSON", e)
    p = _pulse_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, default=str))


# ── Groww access-token cache (cross-process; survives Cloud Run cold starts) ───
#
# Scale-to-zero regenerates a fresh TOTP login per cold start and Groww
# rate-limits its access-token endpoint. One shared DB row lets every process
# reuse the same token until it expires at 6 AM IST (daily). No-op without a DB
# (local dev relies on the in-process client cache instead).

_GROWW_TOKEN_KEY = "default"
_ENC_PREFIX = "enc:v1:"  # marks a Fernet-encrypted payload; absent ⇒ legacy plaintext


def _derive_fernet_key(passphrase: str, salt: bytes):
    """Derive a urlsafe-base64 Fernet key from passphrase + salt via PBKDF2-HMAC-SHA256."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def _encrypt_token(token: str) -> str:
    """Encrypt the token with a per-write random salt. Returns plaintext unchanged
    when GROWW_TOKEN_ENC_KEY is unset or cryptography is unavailable."""
    passphrase = getattr(SETTINGS, "GROWW_TOKEN_ENC_KEY", "")
    if not passphrase:
        return token
    try:
        import base64
        import os

        from cryptography.fernet import Fernet

        salt = os.urandom(16)
        blob = salt + Fernet(_derive_fernet_key(passphrase, salt)).encrypt(token.encode())
        return _ENC_PREFIX + base64.urlsafe_b64encode(blob).decode()
    except Exception as e:
        logger.warning("Groww token encryption failed (%s) — storing plaintext", e)
        return token


def _decrypt_token(stored: str) -> str | None:
    """Inverse of _encrypt_token. Plaintext (no prefix) passes through. Returns
    None when an encrypted blob can't be decrypted (missing key / wrong key)."""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext row
    passphrase = getattr(SETTINGS, "GROWW_TOKEN_ENC_KEY", "")
    if not passphrase:
        logger.warning("Groww token is encrypted but GROWW_TOKEN_ENC_KEY is unset — cannot decrypt")
        return None
    try:
        import base64

        from cryptography.fernet import Fernet

        blob = base64.urlsafe_b64decode(stored[len(_ENC_PREFIX):].encode())
        salt, ct = blob[:16], blob[16:]
        return Fernet(_derive_fernet_key(passphrase, salt)).decrypt(ct).decode()
    except Exception as e:
        logger.warning("Groww token decryption failed (%s) — forcing re-login", e)
        return None


def save_groww_token(token: str, expires_at: datetime) -> None:
    """Upsert the shared Groww access token (encrypted at rest when a key is set).
    expires_at is stored UTC-naive."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    from persistence.db import session_scope
    from persistence.models import GrowwTokenRow

    exp = expires_at.astimezone(timezone.utc).replace(tzinfo=None) if expires_at.tzinfo else expires_at
    stored = _encrypt_token(token)
    try:
        with session_scope() as s:
            row = s.get(GrowwTokenRow, _GROWW_TOKEN_KEY)
            if row is None:
                s.add(GrowwTokenRow(key=_GROWW_TOKEN_KEY, token=stored, expires_at=exp,
                                    created_at=datetime.utcnow()))
            else:
                row.token = stored
                row.expires_at = exp
                row.created_at = datetime.utcnow()
        logger.info("Groww token cached to DB (encrypted=%s, expires %s UTC)",
                    stored.startswith(_ENC_PREFIX), exp)
    except Exception as e:
        logger.warning("Groww token cache write failed (%s)", e)


def load_groww_token() -> str | None:
    """Return the cached Groww token if present, unexpired, and decryptable; else None.

    Past expiry (or on a decrypt failure) the row is deleted (daily expiry),
    forcing a one-time re-login.
    """
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    from persistence.db import session_scope
    from persistence.models import GrowwTokenRow

    try:
        with session_scope() as s:
            row = s.get(GrowwTokenRow, _GROWW_TOKEN_KEY)
            if row is None:
                return None
            if datetime.utcnow() >= row.expires_at:
                s.delete(row)  # daily expiry — drop stale row, caller re-logins
                return None
            token = _decrypt_token(row.token)
            if token is None:
                s.delete(row)  # undecryptable — drop so a fresh login replaces it
            return token
    except Exception as e:
        logger.warning("Groww token cache read failed (%s)", e)
        return None


# ── chat intent exemplar bank (embed-once cache; Postgres) ─────────────────────

def load_intent_bank(exemplar_hash: str, model: str) -> tuple[list[str], list[list[float]]] | None:
    """Return (labels, vectors) for the cached exemplar bank, or None if absent
    / no DB / read error (caller then embeds + saves)."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    try:
        from persistence.db import session_scope
        from persistence.models import IntentEmbeddingRow

        with session_scope() as s:
            row = s.get(IntentEmbeddingRow, {"exemplar_hash": exemplar_hash, "model": model})
            if row is not None:
                return list(row.labels), list(row.vectors)
    except Exception as e:
        logger.warning("intent bank load failed (%s) — will re-embed", e)
    return None


def save_intent_bank(exemplar_hash: str, model: str, labels: list[str],
                     vectors: list[list[float]]) -> None:
    """Persist the embedded bank. No-op without DATABASE_URL; never raises."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    try:
        from persistence.db import session_scope
        from persistence.models import IntentEmbeddingRow

        with session_scope() as s:
            s.merge(IntentEmbeddingRow(
                exemplar_hash=exemplar_hash, model=model, labels=labels, vectors=vectors,
            ))
        logger.info("intent bank cached to PG (hash=%s, model=%s)", exemplar_hash, model)
    except Exception as e:
        logger.warning("intent bank save failed: %s", e)


# ── long-term memory (append-only jsonl; query by namespace) ───────────────────

def record_memory(namespace: str, key: str, value: dict) -> None:
    """Append a compact memory entry (summaries, not raw payloads)."""
    p = Path(getattr(SETTINGS, "MEMORY_FILE", "output/memory.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now_iso(), "namespace": namespace, "key": key, "value": value}
    with p.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def query_memory(namespace: str | None = None, limit: int | None = None) -> list[dict]:
    p = Path(getattr(SETTINGS, "MEMORY_FILE", "output/memory.jsonl"))
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if namespace and e.get("namespace") != namespace:
            continue
        rows.append(e)
    return rows[-limit:] if limit else rows


def recent_calls(ticker: str, limit: int = 5) -> list[dict]:
    """Past calls (score/conviction/rationale/regime/outcome) for a ticker — for agents to query."""
    matches = [e["value"] for e in query_memory("calls") if e.get("value", {}).get("ticker") == ticker]
    return matches[-limit:]


def latest_signal_perf() -> dict | None:
    rows = query_memory("signal_perf")
    return rows[-1]["value"] if rows else None


def recompute(book: PortfolioState) -> PortfolioState:
    """Recompute total + per-sector exposure (value at entry) from positions."""
    total = 0.0
    sectors: dict[str, float] = {}
    for pos in book.positions:
        val = pos.qty * pos.avg_price
        total += val
        key = pos.sector or "Unknown"
        sectors[key] = sectors.get(key, 0.0) + val
    book.total_exposure = round(total, 2)
    book.sector_exposure = {k: round(v, 2) for k, v in sectors.items()}
    return book


# ── chat response cache (prompt cache; exact + semantic lookup) ─────────────────

def lookup_chat_cache_exact(query_hash: str) -> str | None:
    """Return cached response for exact hash match, or None. No-op without DB."""
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    try:
        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        with session_scope() as s:
            row = (
                s.query(ChatResponseCache)
                .filter(
                    ChatResponseCache.query_hash == query_hash,
                    ChatResponseCache.expires_at > datetime.utcnow(),
                )
                .order_by(ChatResponseCache.created_at.desc())
                .first()
            )
            return row.response if row else None
    except Exception as e:
        logger.warning("cache exact lookup failed: %s", e)
        return None


def lookup_chat_cache_semantic(
    embedding: list[float], threshold: float = 0.95, limit: int = 200
) -> str | None:
    """Return cached response for semantically similar query, or None.

    Loads up to ``limit`` recent non-expired rows, computes cosine similarity
    in Python (embeddings are L2-normalised, so dot product = cosine), returns
    the best match above ``threshold``. No-op without DB or numpy.
    """
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return None
    try:
        import numpy as np

        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        with session_scope() as s:
            rows = (
                s.query(ChatResponseCache)
                .filter(ChatResponseCache.expires_at > datetime.utcnow())
                .order_by(ChatResponseCache.created_at.desc())
                .limit(limit)
                .all()
            )
        if not rows:
            return None

        q = np.array(embedding, dtype=np.float32)
        best_sim, best_resp = 0.0, None
        for row in rows:
            if not row.query_embedding:
                continue
            v = np.array(row.query_embedding, dtype=np.float32)
            sim = float(np.dot(q, v))
            if sim > best_sim:
                best_sim, best_resp = sim, row.response

        return best_resp if best_sim >= threshold else None
    except Exception as e:
        logger.warning("cache semantic lookup failed: %s", e)
        return None


def store_chat_cache(
    query_hash: str,
    query_text: str,
    query_embedding: list[float],
    response: str,
    intent: str,
    ttl_seconds: int,
) -> None:
    """Persist a query-response pair. No-op without DB.

    Append-only: duplicate hashes accumulate; lookup uses ``order_by(created_at.desc()).first()``
    so the latest entry always wins. Expired rows are pruned by the ``expires_at`` filter.
    """
    if not getattr(SETTINGS, "DATABASE_URL", ""):
        return
    try:
        from persistence.db import session_scope
        from persistence.models import ChatResponseCache

        expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        with session_scope() as s:
            s.add(ChatResponseCache(
                query_hash=query_hash,
                query_text=query_text,
                query_embedding=query_embedding,
                response=response,
                intent=intent,
                expires_at=expires_at,
            ))
    except Exception as e:
        logger.warning("cache store failed: %s", e)
