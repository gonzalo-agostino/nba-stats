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
            "opponent_out_players": [],
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
            "opponent_out_players": [],
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
            "opponent_out_players": [],
            "is_back_to_back": True,
            "is_home": True,
        },
    },
    {
        "query": "Giannis Antetokounmpo contra PHI sin Joel Embiid en el rival",
        "expected": {
            "player_name": "Giannis Antetokounmpo",
            "opponent_team": "PHI",
            "out_players": [],
            "opponent_out_players": ["Joel Embiid"],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Luka Doncic @ SAS sin Victor Wembanyama en el rival",
        "expected": {
            "player_name": "Luka Doncic",
            "opponent_team": "SAS",
            "out_players": [],
            "opponent_out_players": ["Victor Wembanyama"],
            "is_back_to_back": False,
            "is_home": False,
        },
    },
    {
        "query": "Nikola Jokić de local contra MIN con Rudy Gobert fuera en Minnesota y sin Aaron Gordon",
        "expected": {
            "player_name": "Nikola Jokić",
            "opponent_team": "MIN",
            "out_players": ["Aaron Gordon"],
            "opponent_out_players": ["Rudy Gobert"],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Jalen Brunson en back-to-back de visitante frente a MIA",
        "expected": {
            "player_name": "Jalen Brunson",
            "opponent_team": "MIA",
            "out_players": [],
            "opponent_out_players": [],
            "is_back_to_back": True,
            "is_home": False,
        },
    },
    {
        "query": "Cómo proyecta Stephen Curry hoy?",
        "expected": {
            "player_name": "Stephen Curry",
            "opponent_team": None,
            "out_players": [],
            "opponent_out_players": [],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Tyrese Maxey vs MIL sin Joel Embiid ni Paul George",
        "expected": {
            "player_name": "Tyrese Maxey",
            "opponent_team": "MIL",
            "out_players": ["Joel Embiid", "Paul George"],
            "opponent_out_players": [],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Shai Gilgeous-Alexander away at DEN on a back-to-back without Chet Holmgren",
        "expected": {
            "player_name": "Shai Gilgeous-Alexander",
            "opponent_team": "DEN",
            "out_players": ["Chet Holmgren"],
            "opponent_out_players": [],
            "is_back_to_back": True,
            "is_home": False,
        },
    },
    {
        "query": "Alperen Sengun contra OKC con Chet Holmgren e Isaiah Hartenstein fuera en OKC",
        "expected": {
            "player_name": "Alperen Sengun",
            "opponent_team": "OKC",
            "out_players": [],
            "opponent_out_players": ["Chet Holmgren", "Isaiah Hartenstein"],
            "is_back_to_back": False,
            "is_home": True,
        },
    },
    {
        "query": "Anthony Edwards de local vs GSW en B2B sin Draymond Green en el rival",
        "expected": {
            "player_name": "Anthony Edwards",
            "opponent_team": "GSW",
            "out_players": [],
            "opponent_out_players": ["Draymond Green"],
            "is_back_to_back": True,
            "is_home": True,
        },
    },
]


def score_parse_accuracy(parsed: ScenarioRequest | None, expected: dict[str, Any]) -> float:
    """Compute exact-match fraction across key parsed ScenarioRequest fields."""
    if parsed is None:
        return 0.0
    expected_opp = expected.get("opponent_team")
    expected_opp_outs = expected.get("opponent_out_players", [])
    checks = [
        expected["player_name"].lower() in parsed.player_name.lower(),
        (parsed.opponent_team or "").upper() == (expected_opp or "").upper(),
        parsed.is_back_to_back == expected["is_back_to_back"],
        parsed.is_home == expected["is_home"],
        len(parsed.out_players) == len(expected["out_players"]),
        len(parsed.opponent_out_players) == len(expected_opp_outs),
    ]
    return sum(1.0 for c in checks if c) / len(checks)


def score_numerical_faithfulness(draft_analysis: str, ml_result: SimulationResult | None) -> float:
    """Return 1.0 if zero numerical contradictions are detected, else 0.0."""
    if ml_result is None:
        return 0.0
    passed, _ = verify_analysis_numbers(draft_analysis, ml_result)
    return 1.0 if passed else 0.0
