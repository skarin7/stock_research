"""Tests for natural-language query filter extraction — zero dependencies."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.chat.query_filters import QueryFilters, extract_filters


class TestRelativeDateExtraction:
    """Relative expressions must resolve to correct lookback_days."""

    def test_last_7_days(self):
        f = extract_filters("show me stock performance last 7 days")
        assert f.lookback_days == 7
        assert f.period_label != ""

    def test_past_week(self):
        f = extract_filters("top stocks past week")
        assert f.lookback_days == 7

    def test_last_week(self):
        f = extract_filters("which stocks gained last week")
        assert f.lookback_days == 7

    def test_last_month(self):
        f = extract_filters("growth in the last month")
        assert f.lookback_days == 30

    def test_past_month(self):
        f = extract_filters("performance past month")
        assert f.lookback_days == 30

    def test_last_quarter(self):
        f = extract_filters("returns last quarter")
        assert f.lookback_days == 90

    def test_last_year(self):
        f = extract_filters("annual returns last year")
        assert f.lookback_days == 365

    def test_yesterday(self):
        f = extract_filters("what happened in markets yesterday")
        today = date.today()
        assert f.date_from == today - timedelta(days=1)
        assert f.date_to == today - timedelta(days=1)

    def test_last_n_days_numeric(self):
        f = extract_filters("show me the last 14 days performance")
        assert f.lookback_days == 14
        today = date.today()
        assert f.date_from == today - timedelta(days=14)
        assert f.date_to == today

    def test_last_n_weeks_numeric(self):
        f = extract_filters("performance over the last 3 weeks")
        assert f.lookback_days == 21
        today = date.today()
        assert f.date_from == today - timedelta(days=21)
        assert f.date_to == today

    def test_last_n_months_numeric(self):
        f = extract_filters("growth over the last 2 months")
        assert f.lookback_days == 60


class TestAbsoluteDateExtraction:
    """Specific dates must parse to the correct date object."""

    def test_iso_date(self):
        f = extract_filters("what was the market on 2026-06-20")
        assert f.date_from == date(2026, 6, 20)
        assert f.date_to == date(2026, 6, 20)

    def test_dd_mm_yyyy(self):
        f = extract_filters("performance on 20/06/2026")
        assert f.date_from == date(2026, 6, 20)

    def test_month_name_day(self):
        f = extract_filters("prices on Jun 20th")
        today = date.today()
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20

    def test_month_name_day_no_suffix(self):
        f = extract_filters("snapshot for June 20")
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20

    def test_day_month_name(self):
        f = extract_filters("data on 20 June")
        assert f.date_from is not None
        assert f.date_from.month == 6
        assert f.date_from.day == 20


class TestNoFilters:
    """Queries without time references must return empty QueryFilters."""

    def test_generic_query(self):
        f = extract_filters("show me IT stocks with PE under 30")
        assert f.lookback_days is None
        assert f.date_from is None
        assert f.date_to is None
        assert f.period_label == ""

    def test_empty_string(self):
        f = extract_filters("")
        assert f.lookback_days is None

    def test_stock_name_only(self):
        f = extract_filters("What is PE ratio of TCS?")
        assert f.lookback_days is None


class TestRunTurnFilterHint:
    """run_turn must append time_context hint when filters are found."""

    def test_filter_hint_appended_to_message(self, monkeypatch):
        import agents.chat.agent as agent_mod
        from unittest.mock import MagicMock

        captured = {}

        def fake_invoke(payload, cfg):
            captured["msg"] = payload["messages"][0][1]
            fake_msg = MagicMock()
            fake_msg.type = "ai"
            fake_msg.content = "Reliance grew 5% last month."
            return {"messages": [fake_msg]}

        monkeypatch.setattr("agents.supervisor.kill_switch_active", lambda: False)
        monkeypatch.setattr(
            "agents.chat.intent.route_intent",
            lambda t: {"intent": "research", "confidence": 0.9, "route": "regex"},
        )
        monkeypatch.setattr("agents.chat.tools.reset_turn_state", lambda: None)
        monkeypatch.setattr(
            "observability.chat_tracing.trace_chat_turn",
            lambda *a, **kw: None,
        )
        # No-op for cache: check returns None (miss), put does nothing
        monkeypatch.setattr(
            "agents.chat.cache.check",
            lambda **kw: None,
        )
        monkeypatch.setattr(
            "agents.chat.cache.put",
            lambda **kw: None,
        )
        # No-op for embedding (skip semantic cache tier)
        try:
            import agents.chat.embedder as _emb_mod
            monkeypatch.setattr(_emb_mod, "available", lambda: False)
        except Exception:
            pass

        fake_agent = MagicMock()
        fake_agent.invoke = fake_invoke
        monkeypatch.setattr(agent_mod, "_get_agent", lambda: fake_agent)

        agent_mod.run_turn("123", "How did Reliance do last month?")
        assert "time_context" in captured["msg"]
        assert "lookback_days=30" in captured["msg"]


class TestLoadSnapshotForDate:
    """store.load_snapshot_for_date must return empty list for missing dates."""

    def test_missing_date_returns_empty(self, monkeypatch, tmp_path):
        import persistence.store as st_mod

        monkeypatch.setattr(
            "persistence.store.SETTINGS",
            type("S", (), {"DATABASE_URL": "", "OUTPUT_DIR": str(tmp_path)})(),
        )
        run_date, rows = st_mod.load_snapshot_for_date("2020-01-01")
        assert rows == []
        assert run_date is None
