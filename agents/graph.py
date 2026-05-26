"""Build the LangGraph StateGraph that orchestrates the agents.

    research → analyst → [debate → risk → portfolio → trading] → finalize → END

Any terminal status short-circuits to ``finalize`` (which still emits whatever
artifacts exist). The trading chain is gated OFF by default, so the research
path is research → analyst → finalize.
"""

from __future__ import annotations

import logging

import config

from agents.nodes.analyst import analyst_node
from agents.nodes.finalize import finalize_node
from agents.nodes.research import research_node
from agents.nodes.stubs import debate_node, portfolio_node, risk_node, trading_node
from agents.state import AgentState
from agents.supervisor import next_or_finalize, route_after_analyst, route_after_research

logger = logging.getLogger("agents.graph")


def get_checkpointer():
    """Postgres checkpointer when DATABASE_URL is set; else in-memory (dev/tests)."""
    if config.DATABASE_URL:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            saver = PostgresSaver.from_conn_string(config.DATABASE_URL)
            saver.setup()
            logger.info("Using PostgresSaver checkpointer")
            return saver
        except Exception as e:
            logger.warning("PostgresSaver unavailable (%s) — falling back to MemorySaver", e)
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


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
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or get_checkpointer())
