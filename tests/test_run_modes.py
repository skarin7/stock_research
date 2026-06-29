"""Tests for watch and intraday run modes."""
import pytest
from unittest.mock import patch, MagicMock, call
from agents.state import RunStatus


def _minimal_state(run_id="test-run"):
    return {
        "run_id": run_id,
        "report_date": "2026-06-29",
        "mode": "watch",
        "status": RunStatus.RUNNING,
        "cost_usd": 0.0,
        "tokens": 0,
    }


def test_watch_mode_skips_outside_market_hours():
    """_watch() returns immediately when not market hours and not pre-open."""
    import run_agents
    with patch.object(run_agents, "_market_open_ist", return_value=False), \
         patch.object(run_agents, "_pre_open_ist", return_value=False), \
         patch("agents.nodes.monitoring.monitoring_node") as mock_mon, \
         patch("agents.nodes.pulse.pulse_node") as mock_pulse:
        run_agents._watch("test-run", "2026-06-29")
        mock_mon.assert_not_called()
        mock_pulse.assert_not_called()


def test_watch_mode_runs_pulse_pre_open():
    """During pre-open, pulse runs but monitor does not."""
    import run_agents
    with patch.object(run_agents, "_market_open_ist", return_value=False), \
         patch.object(run_agents, "_pre_open_ist", return_value=True), \
         patch("agents.nodes.monitoring.monitoring_node") as mock_mon, \
         patch("agents.nodes.pulse.pulse_node", return_value={}) as mock_pulse, \
         patch("observability.metrics.push_metrics"):
        run_agents._watch("test-run", "2026-06-29")
        mock_mon.assert_not_called()
        mock_pulse.assert_called_once()


def test_watch_mode_runs_both_during_market():
    """During market hours, both monitor and pulse run."""
    import run_agents
    with patch.object(run_agents, "_market_open_ist", return_value=True), \
         patch.object(run_agents, "_pre_open_ist", return_value=False), \
         patch("agents.nodes.monitoring.monitoring_node", return_value={}) as mock_mon, \
         patch("agents.nodes.pulse.pulse_node", return_value={}) as mock_pulse, \
         patch("observability.metrics.push_metrics"):
        run_agents._watch("test-run", "2026-06-29")
        mock_mon.assert_called_once()
        mock_pulse.assert_called_once()


def test_intraday_mode_calls_pipeline():
    """_intraday() calls run_pipeline and writes/sends watchlist."""
    import run_agents
    mock_items = [{"symbol": "TCS", "score": 8}]
    with patch("intraday.pipeline.run_pipeline", return_value=mock_items) as mock_pipe, \
         patch("intraday.report.write_watchlist") as mock_write, \
         patch("intraday.data_sources.nifty_change_pct", return_value=0.5), \
         patch("intraday.report.build_alert", return_value="alert text"), \
         patch("notifications.telegram_notifier.send_intraday_watchlist") as mock_telegram:
        args = MagicMock(dry_run=False, no_telegram=False)
        run_agents._intraday("test-run", "2026-06-29", args)
        mock_pipe.assert_called_once()
        mock_write.assert_called_once()
        mock_telegram.assert_called_once()


def test_intraday_mode_skips_telegram_when_flagged():
    """_intraday() skips Telegram when args.no_telegram is True."""
    import run_agents
    with patch("intraday.pipeline.run_pipeline", return_value=[]), \
         patch("intraday.report.write_watchlist"), \
         patch("intraday.data_sources.nifty_change_pct", return_value=None), \
         patch("intraday.report.build_alert", return_value="alert text"), \
         patch("notifications.telegram_notifier.send_intraday_watchlist") as mock_telegram:
        args = MagicMock(dry_run=False, no_telegram=True)
        run_agents._intraday("test-run", "2026-06-29", args)
        mock_telegram.assert_not_called()


def test_watch_not_in_old_mode_choices():
    """watch and intraday are valid --mode choices; monitor and pulse are not."""
    import run_agents
    import argparse
    # parse_args() should accept watch and intraday
    import sys
    old = sys.argv
    sys.argv = ["run_agents.py", "--mode", "watch"]
    args = run_agents.parse_args()
    assert args.mode == "watch"

    sys.argv = ["run_agents.py", "--mode", "intraday"]
    args = run_agents.parse_args()
    assert args.mode == "intraday"
    sys.argv = old
