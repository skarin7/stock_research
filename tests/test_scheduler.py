"""Tests for scheduler/runner.py — cron-check logic, seeding, and due detection."""
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Minimal ScheduleRow stand-in (no DB needed)
# ---------------------------------------------------------------------------
class _Row:
    def __init__(self, name, mode, cron_expr, enabled=True):
        self.name = name
        self.mode = mode
        self.cron_expr = cron_expr
        self.enabled = enabled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(rows):
    """Return a mock session whose query().filter_by(enabled=True).all() filters rows."""
    session = MagicMock()
    q = MagicMock()
    q.count.return_value = len(rows)

    def _filter_by(**kwargs):
        filtered = MagicMock()
        enabled = kwargs.get("enabled")
        result = [r for r in rows if enabled is None or r.enabled == enabled]
        filtered.all.return_value = result
        return filtered

    q.filter_by.side_effect = _filter_by
    session.query.return_value = q
    return session


@contextmanager
def _patch_session(rows):
    """Patch persistence.db.session_scope and persistence.models.ScheduleRow."""
    session = _make_session(rows)

    fake_db = types.ModuleType("persistence.db")

    @contextmanager
    def _scope():
        yield session

    fake_db.session_scope = _scope

    fake_models = types.ModuleType("persistence.models")
    fake_models.ScheduleRow = _Row

    with patch.dict(
        sys.modules,
        {
            "persistence": types.ModuleType("persistence"),
            "persistence.db": fake_db,
            "persistence.models": fake_models,
        },
    ):
        yield session


# ---------------------------------------------------------------------------
# Import runner after patching so its top-level imports resolve
# ---------------------------------------------------------------------------

import importlib

# croniter may not be installed in the test env; provide a minimal stand-in.
try:
    from croniter import croniter as _real_croniter
    _CRONITER = _real_croniter
except ModuleNotFoundError:
    class _FakeCroniter:
        """Minimal croniter: next fire = start_time + 15s (always within 30s window)."""
        def __init__(self, expr, start_time=0):
            if not isinstance(expr, str) or len(expr.split()) not in (5, 6):
                raise ValueError(f"bad cron expr: {expr!r}")
            # "59 23 31 12 *" sentinel → simulate far-future (never due)
            self._next = start_time + (86400 * 180 if "59 23 31 12" in expr else 15)

        def get_next(self, typ=float):
            return typ(self._next)

    _CRONITER = _FakeCroniter


def _load_runner():
    # Inject croniter stub before importing runner so the module-level
    # `from croniter import croniter` resolves even without the package.
    fake_croniter_pkg = types.ModuleType("croniter")
    fake_croniter_pkg.croniter = _CRONITER
    sys.modules.setdefault("croniter", fake_croniter_pkg)

    for key in list(sys.modules):
        if key in ("scheduler.runner", "scheduler"):
            del sys.modules[key]
    import scheduler.runner as runner
    return runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDueSchedules:
    def test_cron_fires_within_poll_window(self):
        # "* * * * *" fires every minute; within a 30s window there will be a hit.
        rows = [_Row("research", "research", "* * * * *")]
        with _patch_session(rows):
            runner = _load_runner()
            now = datetime.now(timezone.utc)
            due = runner._due_schedules(now)
        assert ("research", "research") in due

    def test_cron_not_due_far_future(self):
        # Expr fires at 23:59 on Dec 31 only — will not be due right now.
        rows = [_Row("research", "research", "59 23 31 12 *")]
        with _patch_session(rows):
            runner = _load_runner()
            now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
            due = runner._due_schedules(now)
        assert due == []

    def test_bad_cron_expr_logs_warning_not_raises(self, caplog):
        rows = [_Row("broken", "research", "not-a-cron")]
        with _patch_session(rows):
            runner = _load_runner()
            import logging
            with caplog.at_level(logging.WARNING, logger="scheduler"):
                due = runner._due_schedules(datetime.now(timezone.utc))
        assert due == []
        assert any("broken" in m for m in caplog.messages)

    def test_disabled_row_skipped(self):
        rows = [_Row("research", "research", "* * * * *", enabled=False)]
        with _patch_session(rows):
            runner = _load_runner()
            due = runner._due_schedules(datetime.now(timezone.utc))
        assert due == []

    def test_multiple_rows_partial_due(self):
        rows = [
            _Row("research", "research", "* * * * *"),       # always due
            _Row("intraday", "intraday", "59 23 31 12 *"),   # never due now
        ]
        with _patch_session(rows):
            runner = _load_runner()
            now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
            due = runner._due_schedules(now)
        assert ("research", "research") in due
        assert ("intraday", "intraday") not in due

    def test_croniter_import_resolves(self):
        """Regression: croniter call must not raise NameError ('croni' split bug)."""
        rows = [_Row("watch", "watch", "*/3 * * * 1-5")]
        with _patch_session(rows):
            runner = _load_runner()
            try:
                runner._due_schedules(datetime.now(timezone.utc))
            except NameError as e:
                pytest.fail(f"NameError in _due_schedules: {e}")


class TestRunMode:
    def test_subprocess_uses_mode_flag(self):
        """Regression: run_agents.py must receive --mode <mode>, not bare positional."""
        with _patch_session([]):
            runner = _load_runner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner._run_mode("watch", "watch")
        args = mock_run.call_args[0][0]
        assert "--mode" in args
        assert "watch" in args
        assert args[args.index("--mode") + 1] == "watch"
