"""Structured JSON logging — call setup_logging() once at process start."""
from __future__ import annotations

import json
import logging
from typing import Any

# Standard LogRecord attributes — exclude from the JSON extra fields
_RECORD_ATTRS = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs", "msg",
    "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName", "taskName",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        out: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in _RECORD_ATTRS and not k.startswith("_"):
                out[k] = v
        return json.dumps(out, default=str)


_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON output. Idempotent — subsequent calls are no-ops."""
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
