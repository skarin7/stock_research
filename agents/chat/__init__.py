"""Conversational chat agent — Telegram-facing tool-calling agent.

Wraps the existing pipeline modules as LangChain tools behind a single
LangGraph ReAct agent (one thread per chat). Research/recommendation only;
order placement stays in the gated trading chain.
"""
