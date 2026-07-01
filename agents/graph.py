"""Build the LangGraph StateGraph that orchestrates the agents.

    research → analyst → [debate → risk → portfolio → trading] → finalize → END

Any terminal status short-circuits to ``finalize`` (which still emits whatever
artifacts exist). The trading chain is gated OFF by default, so the research
path is research → analyst → finalize.
"""

from __future__ import annotations

import logging

from config import SETTINGS

from agents.nodes.analyst import analyst_node
from agents.nodes.debate import debate_node
from agents.nodes.finalize import finalize_node
from agents.nodes.memory import memory_node
from agents.nodes.portfolio import portfolio_node
from agents.nodes.research import research_node
from agents.nodes.risk import risk_node
from agents.nodes.trading import trading_node
from agents.state import AgentState
from agents.supervisor import next_or_finalize, route_after_analyst, route_after_research

logger = logging.getLogger("agents.graph")


def _contract_serde():
    """Serializer that explicitly allows our Pydantic contracts through msgpack.

    AgentState carries Pydantic contract objects (EnrichmentResult, Scorecard,
    TradeProposal, …) and the RunStatus enum. LangGraph's msgpack serde only
    reconstructs types on the deserialize side if their (module, name) is
    whitelisted; unlisted types are (currently) warned-and-allowed but will be
    blocked — silently degraded to plain dicts, which breaks the ``.stocks`` /
    ``.ticker`` attribute access downstream. Whitelisting every BaseModel in
    ``agents.contracts`` (built dynamically so new contracts are covered) plus
    ``RunStatus`` kills that whole class of checkpoint-serialization bug in one
    place, independent of how many contract fields the state grows.
    """
    from pydantic import BaseModel
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from agents import contracts as _contracts
    from agents.state import RunStatus

    allowed = [
        (obj.__module__, name)
        for name, obj in vars(_contracts).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == "agents.contracts"
    ]
    allowed.append((RunStatus.__module__, RunStatus.__name__))
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


def get_checkpointer():
    """Postgres checkpointer when DATABASE_URL is set; else in-memory (dev/tests)."""
    serde = _contract_serde()
    if SETTINGS.DATABASE_URL:
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver

            conn = psycopg.connect(SETTINGS.DATABASE_URL, autocommit=True)
            saver = PostgresSaver(conn, serde=serde)
            saver.setup()
            logger.info("Using PostgresSaver checkpointer")
            return saver
        except Exception as e:
            logger.warning("PostgresSaver unavailable (%s) — falling back to MemorySaver", e)
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver(serde=serde)


def build_graph(checkpointer=None, nodes=None):
    """Compile the orchestration graph.

    ``nodes`` optionally overrides node implementations by name (used in tests
    to inject lightweight fakes for research/analyst/finalize).
    """
    from langgraph.graph import END, START, StateGraph

    impl = {
        "research": research_node,
        "analyst": analyst_node,
        "debate": debate_node,
        "risk": risk_node,
        "portfolio": portfolio_node,
        "trading": trading_node,
        "finalize": finalize_node,
        "memory": memory_node,
    }
    if nodes:
        impl.update(nodes)

    g = StateGraph(AgentState)
    for name, fn in impl.items():
        g.add_node(name, fn)

    g.add_edge(START, "research")
    g.add_conditional_edges("research", route_after_research,
                            {"analyst": "analyst", "finalize": "finalize"})
    g.add_conditional_edges("analyst", route_after_analyst,
                            {"debate": "debate", "finalize": "finalize"})
    g.add_conditional_edges("debate", next_or_finalize("risk"),
                            {"risk": "risk", "finalize": "finalize"})
    g.add_conditional_edges("risk", next_or_finalize("portfolio"),
                            {"portfolio": "portfolio", "finalize": "finalize"})
    g.add_conditional_edges("portfolio", next_or_finalize("trading"),
                            {"trading": "trading", "finalize": "finalize"})
    g.add_edge("trading", "finalize")
    g.add_edge("finalize", "memory")
    g.add_edge("memory", END)

    return g.compile(checkpointer=checkpointer or get_checkpointer())


def build_monitor_graph(checkpointer=None, nodes=None):
    """Compile the standalone monitor graph: START → monitor → END.

    Run on a short market-hours schedule (separate from the daily research run).
    """
    from langgraph.graph import END, START, StateGraph

    from agents.nodes.monitoring import monitoring_node

    node = (nodes or {}).get("monitor", monitoring_node)
    g = StateGraph(AgentState)
    g.add_node("monitor", node)
    g.add_edge(START, "monitor")
    g.add_edge("monitor", END)
    return g.compile(checkpointer=checkpointer or get_checkpointer())


def build_pulse_graph(checkpointer=None, nodes=None):
    """Compile the standalone market-pulse graph: START → pulse → END.

    Run on a tight 1–2 min schedule (incl. pre-open) to alert on market shocks.
    """
    from langgraph.graph import END, START, StateGraph

    from agents.nodes.pulse import pulse_node

    node = (nodes or {}).get("pulse", pulse_node)
    g = StateGraph(AgentState)
    g.add_node("pulse", node)
    g.add_edge(START, "pulse")
    g.add_edge("pulse", END)
    return g.compile(checkpointer=checkpointer or get_checkpointer())
