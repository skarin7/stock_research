"""Langfuse callback factory.

Returns a LangChain callback handler when Langfuse is installed and credentials
are configured; otherwise returns an empty list so LLM calls run untraced
(no-op). Never raises on missing deps/keys.
"""

from __future__ import annotations

import logging

from config import SETTINGS

logger = logging.getLogger(__name__)

_handler = None
_resolved = False


def get_callbacks() -> list:
    global _handler, _resolved
    if not _resolved:
        _resolved = True
        if SETTINGS.LANGFUSE_PUBLIC_KEY and SETTINGS.LANGFUSE_SECRET_KEY:
            try:
                from langfuse.langchain import CallbackHandler

                _handler = CallbackHandler()
                logger.info("Langfuse tracing enabled (host=%s)", SETTINGS.LANGFUSE_HOST)
            except Exception as e:  # missing dep or bad config — degrade silently
                logger.info("Langfuse unavailable (%s) — tracing disabled", e)
                _handler = None
        else:
            logger.debug("Langfuse keys not set — tracing disabled")
    return [_handler] if _handler else []
