"""Golden evaluation dataset and LangSmith evaluators for the NBA LangGraph agent."""

from __future__ import annotations

from typing import Any

from nba_stats.agent.verifier import verify_analysis_numbers
from nba_stats.simulator.schemas import ScenarioRequest, SimulationResult

GOLDEN_EVAL_CASES: list[dict[str, Any]] = [
    {
        "query": "Cómo proyecta Jamal Murray de local contra LAL si falta Nikola Jokic?",
        "expected": {
            "player_name": "Jamal Murray",
            "opponent_team": "LAL",
            "out_players": ["Nikola Jokic"],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Proyección de Austin Reaves visitante en back-to-back contra BOS sin LeBron James ni Anthony Davis",
        "expected": {
            "player_name": "Austin Reaves",
            "opponent_team": "BOS",
            "out_players": ["LeBron James", "Anthony Davis"],
            "is_back_to_back": True,
            "is_home": False,
        },
    },
    {
        "query": "Jaylen Brown vs NYK on a B2B with Jayson Tatum suspended",
        "expected": {
            "player_name": "Jaylen Brown",
            "opponent_team": "NYK",
            "out_players": ["Jayson Tatum"],
            "is_back_to_back": True,
            "is_home": True,
        },
    },
]


def score_parse_accuracy(parsed: ScenarioRequest | None, expected: dict[str, Any]) -> float:
    """Compute exact-match fraction across key parsed ScenarioRequest fields."""
    if parsed is None:
        return 0.0
    checks = [
        expected["player_name"].lower() in parsed.player_name.lower(),
        (parsed.opponent_team or "").upper() == (expected["opponent_team"] or "").upper(),
        parsed.is_back_to_back == expected["is_back_to_back"],
        parsed.is_home == expected["is_home"],
        len(parsed.out_players) == len(expected["out_players"]),
    ]
    return sum(1.0 for c in checks if c) / len(checks)


def score_numerical_faithfulness(draft_analysis: str, ml_result: SimulationResult | None) -> float:
    """Return 1.0 if zero numerical contradictions are detected, else 0.0."""
    if ml_result is None:
        return 0.0
    passed, _ = verify_analysis_numbers(draft_analysis, ml_result)
    return 1.0 if passed else 0.0
