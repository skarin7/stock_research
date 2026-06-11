"""Daily snapshot store: row join, file round-trip, DB round-trip (sqlite). No network."""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_cfg = types.SimpleNamespace(
    ANTHROPIC_API_KEY="test",
    DATABASE_URL="",
    OUTPUT_DIR="output",
)
sys.modules["config"] = types.SimpleNamespace(SETTINGS=_cfg)

from persistence import store as store_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _bind(tmp_path):
    _cfg.OUTPUT_DIR = str(tmp_path)
    _cfg.DATABASE_URL = ""
    store_mod.SETTINGS = _cfg
    yield


_STOCKS = [
    {"symbol": "AAA", "company": "Alpha Ltd", "sector": "IT", "pe_ratio": 12.0,
     "sector_pe": 20.0, "market_cap_cr": 5000.0, "ltp": 101.5, "delivery_pct": 60.0,
     "volume_ratio": 1.8, "52w_high": 120.0, "52w_low": 80.0},
    {"symbol": "BBB", "company": "Beta Ltd", "sector": "Pharma", "pe_ratio": 30.0,
     "ltp": 250.0},
    {"company": "no symbol — dropped"},
]
_CARDS = [
    {"ticker": "AAA", "composite_score": 7.4,
     "signals": {"value": {"score": 8, "reason": "cheap"}},
     "investment_rationale": "cheap and liquid", "risk_flags": ["fii outflow"],
     "earnings_proximity": True},
]
_NEWS = {"AAA": {"headlines": ["Alpha wins big order"], "sentiment": "neutral"}}


def test_build_snapshot_rows_joins_by_symbol():
    rows = store_mod.build_snapshot_rows(_STOCKS, _CARDS, _NEWS)
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]

    aaa = rows[0]
    assert aaa["pe_ratio"] == 12.0
    assert aaa["week52_high"] == 120.0
    assert aaa["composite_score"] == 7.4
    assert aaa["news"] == ["Alpha wins big order"]
    assert aaa["rationale"] == "cheap and liquid"
    assert aaa["earnings_proximity"] is True

    bbb = rows[1]  # no scorecard / no news → empty score fields, not a crash
    assert bbb["composite_score"] is None
    assert bbb["news"] == []


def test_file_roundtrip_and_latest_selection(tmp_path):
    rows = store_mod.build_snapshot_rows(_STOCKS, _CARDS, _NEWS)
    store_mod.save_daily_snapshot("2026-06-09", [rows[1]])
    store_mod.save_daily_snapshot("2026-06-10", rows)

    run_date, loaded = store_mod.load_latest_snapshot()
    assert run_date == "2026-06-10"
    assert {r["symbol"] for r in loaded} == {"AAA", "BBB"}
    assert loaded[0]["composite_score"] == 7.4


def test_load_returns_empty_when_no_snapshot():
    assert store_mod.load_latest_snapshot() == (None, [])


def test_db_roundtrip_sqlite(tmp_path):
    _cfg.DATABASE_URL = f"sqlite:///{tmp_path}/snap.db"

    import persistence.db as db_mod
    db_mod._engine = None
    db_mod._Session = None
    db_mod.SETTINGS = _cfg
    assert db_mod.init_db()

    rows = store_mod.build_snapshot_rows(_STOCKS, _CARDS, _NEWS)
    store_mod.save_daily_snapshot("2026-06-10", rows)
    store_mod.save_daily_snapshot("2026-06-10", rows)  # upsert: no duplicate PK error

    run_date, loaded = store_mod.load_latest_snapshot()
    assert run_date == "2026-06-10"
    assert len(loaded) == 2
    aaa = next(r for r in loaded if r["symbol"] == "AAA")
    assert aaa["signals"]["value"]["score"] == 8
    assert aaa["earnings_proximity"] is True

    db_mod._engine = None
    db_mod._Session = None
