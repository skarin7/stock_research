"""Instrumentation helpers: trace_tool(), trace_node(), trace_intent().

All helpers are fail-safe — any internal error is swallowed so instrumentation
never breaks the agent flow. Langfuse spans are no-ops when keys are unset.
"""
from __future__ import annotations

import contextlib
import logging
import time

logger = logging.getLogger("observability.tracing")

_lf_client = None
_lf_resolved = False


def _get_langfuse():
    """Singleton Langfuse client, or None when not configured."""
    global _lf_client, _lf_resolved
    if _lf_resolved:
        return _lf_client
    _lf_resolved = True
    try:
        from config import SETTINGS
        if SETTINGS.LANGFUSE_PUBLIC_KEY and SETTINGS.LANGFUSE_SECRET_KEY:
            from langfuse import Langfuse
            _lf_client = Langfuse(
                public_key=SETTINGS.LANGFUSE_PUBLIC_KEY,
                secret_key=SETTINGS.LANGFUSE_SECRET_KEY,
                host=SETTINGS.LANGFUSE_HOST,
            )
    except Exception:
        pass
    return _lf_client


def _summarize(result: dict) -> str:
    try:
        if not isinstance(result, dict):
            return "ok"
        if "error" in result:
            return f"error: {result['error']}"
        if "stocks" in result:
            return f"{len(result['stocks'])} stocks"
        if "quotes" in result:
            return f"{len(result['quotes'])} quotes"
        if "news" in result:
            return f"{len(result['news'])} symbols"
        if "scores" in result:
            return f"{len(result['scores'])} scores"
        if "past_calls" in result:
            return f"{len(result['past_calls'])} past calls"
        return "ok"
    except Exception:
        return "ok"


class _Span:
    def __init__(self, real=None):
        self._real = real
        self.output: dict = {}

    def set_output(self, data: dict) -> None:
        self.output = data or {}
        if self._real is not None:
            try:
                self._real.update(output=data)
            except Exception:
                pass

    def _end(self, error: str | None = None) -> None:
        if self._real is not None:
            try:
                if error:
                    self._real.update(level="ERROR", status_message=error)
                self._real.end()
            except Exception:
                pass


@contextlib.contextmanager
def trace_tool(name: str, args: dict):
    """Context manager — wraps a single tool call with JSON log + Langfuse span.

    Usage::

        with trace_tool("live_quote", {"symbols": ["ABB"]}) as span:
            result = _fetch(symbols)
            span.set_output(result)
        return result
    """
    t0 = time.monotonic()
    real_span = None
    lf = _get_langfuse()
    if lf is not None:
        try:
            trace = lf.trace(name=f"tool:{name}")
            real_span = trace.span(name=name, input=args)
        except Exception:
            pass
    span = _Span(real_span)

    logger.info("tool_start", extra={"tool": name, "args": args})
    try:
        yield span
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.error(
            "tool_error",
            extra={"tool": name, "args": args, "error": str(exc), "duration_ms": duration_ms},
        )
        span._end(error=str(exc))
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "tool_call",
            extra={
                "tool": name,
                "args": args,
                "result_summary": _summarize(span.output),
                "_source": span.output.get("_source", "unknown"),
                "duration_ms": duration_ms,
            },
        )
        span._end()


@contextlib.contextmanager
def trace_node(name: str, run_id: str, input_summary: dict):
    """Context manager — wraps a pipeline node execution."""
    t0 = time.monotonic()
    real_span = None
    lf = _get_langfuse()
    if lf is not None:
        try:
            trace = lf.trace(name=f"run:{run_id}", id=run_id)
            real_span = trace.span(name=f"node:{name}", input=input_summary)
        except Exception:
            pass
    span = _Span(real_span)

    logger.info("node_start", extra={"node": name, "run_id": run_id, **input_summary})
    try:
        yield span
    except Exception as exc:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.error(
            "node_error",
            extra={"node": name, "run_id": run_id, "error": str(exc), "duration_ms": duration_ms},
        )
        span._end(error=str(exc))
        raise
    else:
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "node_done",
            extra={"node": name, "run_id": run_id, "duration_ms": duration_ms, **span.output},
        )
        span._end()


def trace_intent(route: str, intent: str, score: float, confidence: float | None = None) -> None:
    """Log an intent routing decision. Never raises."""
    try:
        extra: dict = {"route": route, "intent": intent, "top_score": round(score, 3)}
        if confidence is not None:
            extra["confidence"] = round(confidence, 3)
        logger.info("intent_routed", extra=extra)
    except Exception:
        pass
