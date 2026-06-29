"""Tests for pulse_state load/save with Postgres + JSON fallback."""
import json
import pathlib
import pytest
from unittest.mock import patch


def _make_settings(tmp_path, db_url="", pulse_file=None):
    """Return a Settings instance with the given DATABASE_URL and PULSE_STATE_FILE."""
    from settings import Settings
    state_file = str(pulse_file or (tmp_path / "pulse.json"))
    return Settings(DATABASE_URL=db_url, PULSE_STATE_FILE=state_file)


def test_save_load_roundtrip_json_fallback(monkeypatch, tmp_path):
    """Without DATABASE_URL, uses JSON file."""
    import config
    import persistence.store as store

    s = _make_settings(tmp_path)
    monkeypatch.setattr(config, "SETTINGS", s)
    monkeypatch.setattr(store, "SETTINGS", s)

    fake_state = {"last_nifty_alert": "2026-06-29T10:00:00", "armed": True}

    store.save_pulse_state(fake_state)
    result = store.load_pulse_state()
    assert result == fake_state


def test_load_returns_empty_when_no_file(monkeypatch, tmp_path):
    """JSON fallback returns {} when file doesn't exist."""
    import config
    import persistence.store as store

    state_file = str(tmp_path / "nonexistent.json")
    s = _make_settings(tmp_path, pulse_file=state_file)
    monkeypatch.setattr(config, "SETTINGS", s)
    monkeypatch.setattr(store, "SETTINGS", s)

    result = store.load_pulse_state()
    assert result == {}


def test_save_uses_db_when_database_url_set(monkeypatch, tmp_path):
    """When DATABASE_URL is set, save_pulse_state calls DB save, not file."""
    import config
    import persistence.store as store

    state_file = tmp_path / "pulse.json"
    s = _make_settings(tmp_path, db_url="postgresql://mock/db", pulse_file=str(state_file))
    monkeypatch.setattr(config, "SETTINGS", s)
    monkeypatch.setattr(store, "SETTINGS", s)

    fake_state = {"last_vix": "2026-06-29T11:00:00"}

    with patch.object(store, "_db_save_pulse_state") as mock_db_save:
        store.save_pulse_state(fake_state)
        mock_db_save.assert_called_once_with(fake_state)

    # JSON file should NOT be written
    assert not state_file.exists()


def test_load_uses_db_when_database_url_set(monkeypatch, tmp_path):
    """When DATABASE_URL is set, load_pulse_state calls DB load."""
    import config
    import persistence.store as store

    s = _make_settings(tmp_path, db_url="postgresql://mock/db")
    monkeypatch.setattr(config, "SETTINGS", s)
    monkeypatch.setattr(store, "SETTINGS", s)

    expected = {"global_armed": False}
    with patch.object(store, "_db_load_pulse_state", return_value=expected) as mock_db_load:
        result = store.load_pulse_state()
        mock_db_load.assert_called_once()
    assert result == expected


def test_db_error_falls_back_to_json(monkeypatch, tmp_path):
    """DB failure falls back to JSON file (load) or JSON file write (save)."""
    import config
    import persistence.store as store

    state_file = tmp_path / "pulse.json"
    # Pre-write a JSON file for the fallback to find
    state_file.write_text(json.dumps({"fallback": True}))

    s = _make_settings(tmp_path, db_url="postgresql://mock/db", pulse_file=str(state_file))
    monkeypatch.setattr(config, "SETTINGS", s)
    monkeypatch.setattr(store, "SETTINGS", s)

    with patch.object(store, "_db_load_pulse_state", side_effect=Exception("DB down")):
        result = store.load_pulse_state()
    assert result == {"fallback": True}
