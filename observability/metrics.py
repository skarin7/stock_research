"""Prometheus metrics for the agent system.

All metric helpers degrade to no-ops when ``prometheus_client`` is not
installed, so importing this module is always safe.
"""

from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    _ENABLED = True
except Exception:  # prometheus_client not installed
    _ENABLED = False


if _ENABLED:
    RUN_DURATION = Histogram("agent_run_duration_seconds", "End-to-end run duration")
    NODE_LATENCY = Histogram("agent_node_latency_seconds", "Per-node latency", ["node"])
    RUN_COST_USD = Gauge("agent_run_cost_usd", "Estimated LLM cost for the latest run")
    RUN_TOKENS = Gauge("agent_run_tokens", "Total tokens for the latest run")
    PROPOSALS = Counter("agent_trade_proposals_total", "Trade proposals", ["status"])
    BUDGET_EXCEEDED = Counter("agent_budget_exceeded_total", "Runs halted on budget")
    NODE_ERRORS = Counter("agent_node_errors_total", "Node errors", ["node"])


def start_metrics_server() -> bool:
    """Expose /metrics on config.METRICS_PORT. Returns True if started."""
    if not _ENABLED:
        logger.debug("prometheus_client not installed — metrics server not started")
        return False
    try:
        start_http_server(config.METRICS_PORT)
        logger.info("Prometheus metrics on :%d", config.METRICS_PORT)
        return True
    except Exception as e:
        logger.warning("Could not start metrics server: %s", e)
        return False


def push_metrics(job: str = "stock-intelligence") -> bool:
    """Push the current metrics to a Pushgateway (for scale-to-zero batch runs).

    No-op unless prometheus_client is installed AND PROMETHEUS_PUSHGATEWAY_URL is set.
    """
    url = getattr(config, "PROMETHEUS_PUSHGATEWAY_URL", "")
    if not (_ENABLED and url):
        return False
    try:
        from prometheus_client import REGISTRY, push_to_gateway

        push_to_gateway(url, job=job, registry=REGISTRY)
        logger.info("Pushed metrics to gateway %s (job=%s)", url, job)
        return True
    except Exception as e:
        logger.warning("Metric push failed: %s", e)
        return False


def observe_node_latency(node: str, seconds: float) -> None:
    if _ENABLED:
        NODE_LATENCY.labels(node=node).observe(seconds)


def set_run_cost(usd: float, tokens: int) -> None:
    if _ENABLED:
        RUN_COST_USD.set(usd)
        RUN_TOKENS.set(tokens)


def inc_proposal(status: str) -> None:
    if _ENABLED:
        PROPOSALS.labels(status=status).inc()


def inc_budget_exceeded() -> None:
    if _ENABLED:
        BUDGET_EXCEEDED.inc()


def inc_node_error(node: str) -> None:
    if _ENABLED:
        NODE_ERRORS.labels(node=node).inc()
