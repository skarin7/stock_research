#!/usr/bin/env python3
"""DB-driven cron scheduler. Reads schedules table; runs run_agents.py <mode>."""
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from croniter import croniter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

POLL_INTERVAL = 30  # seconds

_DEFAULT_SCHEDULES = [
    {"name": "research", "mode": "research", "cron_expr": "30 6 * * 1-5"},
    {"name": "intraday", "mode": "intraday", "cron_expr": "30 18 * * 1-5"},
    {"name": "watch",    "mode": "watch",    "cron_expr": "*/3 * * * 1-5"},
]

_running = True


def _stop(sig, frame):
    global _running
    log.info("SIGTERM received — shutting down")
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def _seed_defaults(session):
    from persistence.models import ScheduleRow
    if session.query(ScheduleRow).count() == 0:
        for row in _DEFAULT_SCHEDULES:
            session.add(ScheduleRow(**row))
        session.commit()
        log.info("Seeded %d default schedules", len(_DEFAULT_SCHEDULES))


def _due_schedules(now: datetime):
    """Return list of (name, mode) whose cron fired in the last POLL_INTERVAL seconds."""
    from persistence.db import session_scope
    from persistence.models import ScheduleRow

    due = []
    with session_scope() as session:
        _seed_defaults(session)
        rows = session.query(ScheduleRow).filter_by(enabled=True).all()
        for row in rows:
            try:
                it = croniter(row.cron_expr, start_time=now.timestamp() - POLL_INTERVAL)
                next_ts = it.get_next(float)
                if next_ts <= now.timestamp():
                    due.append((row.name, row.mode))
            except Exception as e:
                log.warning("Bad cron_expr for %s: %s", row.name, e)
    return due


def _run_mode(name: str, mode: str):
    log.info("Running schedule '%s' → run_agents.py %s", name, mode)
    try:
        result = subprocess.run(
            [sys.executable, "run_agents.py", "--mode", mode],
            capture_output=False,
        )
        if result.returncode != 0:
            log.warning("Schedule '%s' exited %d", name, result.returncode)
    except Exception as e:
        log.error("Schedule '%s' failed: %s", name, e)


def _prewarm_groww():
    """Pre-warm Groww token into DB cache at scheduler startup.

    All child processes (research, watch, intraday) load from DB instead of
    racing for TOTP auth when they spawn concurrently.
    """
    try:
        from enrichment.market_data.groww import default_client
        default_client()
        log.info("Groww token pre-warmed into DB cache")
    except Exception as e:
        log.warning("Groww pre-warm failed — subprocesses will auth independently (token table stays empty): %s", e)


def main():
    log.info("Stock scheduler started (poll=%ds)", POLL_INTERVAL)
    _prewarm_groww()
    while _running:
        now = datetime.now(timezone.utc)
        try:
            for name, mode in _due_schedules(now):
                _run_mode(name, mode)
        except Exception as e:
            log.error("Scheduler loop error: %s", e)
        time.sleep(POLL_INTERVAL)
    log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
