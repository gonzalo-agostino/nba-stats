"""LangGraph compilation and invocation helper with LangSmith tracing metadata."""

from __future__ import annotations

import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from nba_stats.agent.nodes import AgentNodes
from nba_stats.agent.state import PredictionGraphState
from nba_stats.config import Settings
from nba_stats.simulator.what_if import WhatIfSimulator


def _route_after_resolve(state: PredictionGraphState) -> str:
    return "run_ml_simulation" if state.get("resolved_ok") else "clarify_or_error"


def _route_after_verify(state: PredictionGraphState) -> str:
    if state.get("verification_passed"):
        return "format_final_output"
    if int(state.get("retry_count", 0)) < 2:
        return "explain_projection"
    return "fallback_deterministic"


def build_prediction_graph(
    simulator: WhatIfSimulator | None = None,
    parser_llm: Any | None = None,
    analyst_llm: Any | None = None,
    settings: Settings | None = None,
) -> Any:
    """Compile the NBA prediction LangGraph StateGraph."""
    cfg = settings or Settings()
    sim = simulator or WhatIfSimulator(settings=cfg)
    nodes = AgentNodes(
        simulator=sim,
        parser_llm=parser_llm,
        analyst_llm=analyst_llm,
        settings=cfg,
    )

    builder = StateGraph(PredictionGraphState)
    builder.add_node("parse_query", nodes.parse_query)
    builder.add_node("resolve_entities", nodes.resolve_entities)
    builder.add_node("clarify_or_error", nodes.clarify_or_error)
    builder.add_node("run_ml_simulation", nodes.run_ml_simulation)
    builder.add_node("explain_projection", nodes.explain_projection)
    builder.add_node("verify_output", nodes.verify_output)
    builder.add_node("format_final_output", nodes.format_final_output)
    builder.add_node("fallback_deterministic", nodes.fallback_deterministic)

    builder.add_edge(START, "parse_query")
    builder.add_edge("parse_query", "resolve_entities")
    builder.add_conditional_edges(
        "resolve_entities",
        _route_after_resolve,
        {
            "run_ml_simulation": "run_ml_simulation",
            "clarify_or_error": "clarify_or_error",
        },
    )
    builder.add_edge("clarify_or_error", END)
    builder.add_edge("run_ml_simulation", "explain_projection")
    builder.add_edge("explain_projection", "verify_output")
    builder.add_conditional_edges(
        "verify_output",
        _route_after_verify,
        {
            "format_final_output": "format_final_output",
            "explain_projection": "explain_projection",
            "fallback_deterministic": "fallback_deterministic",
        },
    )
    builder.add_edge("format_final_output", END)
    builder.add_edge("fallback_deterministic", END)

    return builder.compile()


def run_agent_query(
    query: str,
    settings: Settings | None = None,
    graph: Any | None = None,
) -> PredictionGraphState:
    """Invoke the compiled LangGraph agent with LangSmith tracing metadata."""
    cfg = settings or Settings()
    if cfg.langsmith_tracing and cfg.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", cfg.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", cfg.langsmith_project)

    compiled = graph or build_prediction_graph(settings=cfg)
    return compiled.invoke(
        {"user_query": query},
        config={
            "run_name": "nba-stats-agent",
            "tags": ["nba-stats", cfg.vertex_model_name],
            "metadata": {"model": cfg.vertex_model_name},
        },
    )
