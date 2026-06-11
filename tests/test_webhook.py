"""Webhook server: auth, allowlist, dedup, happy path. No network/keys."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    TELEGRAM_BOT_TOKEN="token",
    TELEGRAM_CHAT_ID="111",
    TELEGRAM_WEBHOOK_SECRET="s3cr3t",
    ANTHROPIC_API_KEY="test",
    LLM_PROVIDER="anthropic",
    REPORT_MODEL="claude-sonnet-4-6",
    CHAT_MODEL="",
    DATABASE_URL="",
    OUTPUT_DIR="output",
    SNAPSHOT_STALE_DAYS=3,
    MAX_CHAT_TOOL_CALLS=8,
    KILL_SWITCH=False,
    KILL_SWITCH_FILE="/tmp/__webhook_kill__.flag",
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import server.app as app_mod
    monkeypatch.setattr(app_mod, "_settings", lambda: _cfg)

    # Patch run_turn and _send so no real I/O happens
    monkeypatch.setattr(
        "agents.chat.agent.run_turn",
        lambda chat_id, text: f"answer for {chat_id}: {text}",
        raising=False,
    )
    import agents.chat.agent as agent_mod
    monkeypatch.setattr(agent_mod, "run_turn",
                        lambda chat_id, text: f"answer for {chat_id}")

    sent = []
    monkeypatch.setattr(app_mod, "_send", lambda text, chat_id: sent.append((chat_id, text)))

    app_mod._SEEN_UPDATES.clear()
    return TestClient(app_mod.app), sent


def _body(chat_id="111", text="hello", update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": int(chat_id)},
            "text": text,
        },
    }


def _headers(secret="s3cr3t"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


# ── auth ──────────────────────────────────────────────────────────────────────

def test_bad_secret_returns_403(client):
    tc, _ = client
    r = tc.post("/telegram/webhook", json=_body(), headers=_headers("wrong"))
    assert r.status_code == 403


def test_missing_secret_returns_403_when_configured(client):
    tc, _ = client
    r = tc.post("/telegram/webhook", json=_body(), headers={})
    assert r.status_code == 403


def test_no_secret_configured_accepts_all(client, monkeypatch):
    tc, sent = client
    import server.app as app_mod
    cfg2 = types.SimpleNamespace(**{**vars(_cfg), "TELEGRAM_WEBHOOK_SECRET": ""})
    monkeypatch.setattr(app_mod, "_settings", lambda: cfg2)
    r = tc.post("/telegram/webhook", json=_body(), headers={})
    assert r.status_code == 200
    assert any("answer" in m for _, m in sent)


# ── allowlist ─────────────────────────────────────────────────────────────────

def test_foreign_chat_id_is_dropped_silently(client):
    tc, sent = client
    r = tc.post("/telegram/webhook", json=_body(chat_id="999"), headers=_headers())
    assert r.status_code == 200
    assert not any("answer" in m for _, m in sent)


# ── dedup ─────────────────────────────────────────────────────────────────────

def test_duplicate_update_id_runs_turn_once(client):
    tc, sent = client
    tc.post("/telegram/webhook", json=_body(update_id=42), headers=_headers())
    tc.post("/telegram/webhook", json=_body(update_id=42), headers=_headers())
    answers = [m for _, m in sent if "answer" in m]
    assert len(answers) == 1


# ── happy path ────────────────────────────────────────────────────────────────

def test_happy_path_sends_placeholder_then_answer(client):
    tc, sent = client
    r = tc.post("/telegram/webhook", json=_body(), headers=_headers())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    messages = [m for _, m in sent]
    assert any("Researching" in m for m in messages)
    assert any("answer" in m for m in messages)


def test_healthz(client):
    tc, _ = client
    assert tc.get("/healthz").status_code == 200


# ── edge cases ────────────────────────────────────────────────────────────────

def test_non_text_message_dropped(client):
    tc, sent = client
    body = {"update_id": 5, "message": {"chat": {"id": 111}, "photo": []}}
    r = tc.post("/telegram/webhook", json=body, headers=_headers())
    assert r.status_code == 200
    assert not sent


def test_malformed_body_returns_200(client):
    tc, _ = client
    r = tc.post("/telegram/webhook", data=b"not json",
                headers={**_headers(), "content-type": "application/json"})
    assert r.status_code == 200
