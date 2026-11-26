"""Shared LangGraph state definition for NBA prediction workflows."""

from __future__ import annotations

from typing import TypedDict

from nba_stats.simulator.schemas import ScenarioRequest, SimulationResult


class PredictionGraphState(TypedDict, total=False):
    user_query: str
    parsed_scenario: ScenarioRequest | None
    resolved_ok: bool
    clarification_message: str | None
    ml_result: SimulationResult | None
    draft_analysis: str | None
    verification_passed: bool
    verification_errors: list[str]
    retry_count: int
    final_response: str
