"""API endpoint tests using FastAPI TestClient over a temporary DuckDB + trained models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nba_stats.agent.graph import build_prediction_graph
from nba_stats.agent.nodes import AgentNodes
from nba_stats.api.app import create_app
from nba_stats.api.injuries import InjuryService
from nba_stats.api.slate import SlateService
from nba_stats.simulator.schemas import ScenarioRequest
from nba_stats.simulator.what_if import WhatIfSimulator
from tests.test_simulator import _build_temp_warehouse_and_models


@dataclass
class _Msg:
    content: str


class _FakeParser:
    def with_structured_output(self, schema: Any) -> Any:
        class _Runner:
            def invoke(self, messages: Any) -> ScenarioRequest:
                return ScenarioRequest(
                    player_name="Jamal Murray",
                    opponent_team="LAL",
                    out_players=["Nikola Jokic"],
                    is_back_to_back=True,
                    is_home=True,
                )

        return _Runner()


class _FakeAnalyst:
    def invoke(self, messages: Any) -> _Msg:
        return _Msg(content="- Proyección ajustada por la ausencia de Nikola Jokić y B2B.")


class _FakeSlateService(SlateService):
    def _fetch_live_scoreboard_dict(self) -> dict[str, Any]:
        from datetime import date

        return {
            "scoreboard": {
                "gameDate": date.today().isoformat(),
                "games": [
                    {
                        "gameId": "0022500999",
                        "gameStatusText": "7:30 pm ET",
                        "awayTeam": {"teamId": 1, "teamTricode": "DEN"},
                        "homeTeam": {"teamId": 2, "teamTricode": "LAL"},
                    }
                ],
            }
        }

    def _fetch_scoreboard_v2_games(self, target_date: Any) -> list[dict[str, Any]]:
        return []


class _FakeInjuryService(InjuryService):
    def _fetch_raw_json(self) -> dict[str, Any]:
        return {
            "injuries": [
                {
                    "team": {"abbreviation": "DEN"},
                    "injuries": [
                        {
                            "athlete": {"displayName": "Nikola Jokic"},
                            "status": "Out",
                            "shortComment": "Ankle",
                        }
                    ],
                }
            ]
        }


def test_api_endpoints_end_to_end(tmp_path: Path) -> None:
    settings = _build_temp_warehouse_and_models(tmp_path)
    sim = WhatIfSimulator(settings=settings)
    graph = build_prediction_graph(
        simulator=sim,
        parser_llm=_FakeParser(),
        analyst_llm=_FakeAnalyst(),
        settings=settings,
    )
    app = create_app(
        settings=settings,
        simulator=sim,
        agent_graph=graph,
        agent_nodes=AgentNodes(simulator=sim, analyst_llm=_FakeAnalyst(), settings=settings),
        slate_service=_FakeSlateService(duckdb_path=settings.duckdb_path),
        injury_service=_FakeInjuryService(),
    )
    client = TestClient(app)

    # Health & Today's Slate
    health = client.get("/api/v1/health").json()
    assert health["status"] == "ok"
    assert health["latest_game_date"] is not None

    # ESPN name normalization handles suffixes & aliases
    from nba_stats.api.injuries import normalize_name

    assert normalize_name("Jaren Jackson Jr.") == "jaren jackson"
    assert normalize_name("Trey Murphy III") == "trey murphy"
    assert normalize_name("Nic Claxton") == "nicolas claxton"
    assert normalize_name("Nikola Jokić") == "nikola jokic"
    slate = client.get("/api/v1/slate/today").json()
    assert slate["fallback"] is False
    assert len(slate["games"]) == 1
    assert slate["games"][0]["away_abbr"] == "DEN"
    assert slate["games"][0]["home_abbr"] == "LAL"
    # Second call uses 15m cache
    assert client.get("/api/v1/slate/today").json()["cached"] is True

    # Teams
    teams = client.get("/api/v1/teams").json()
    assert len(teams) == 2
    assert {t["abbreviation"] for t in teams} == {"DEN", "LAL"}

    # Players search (accent-insensitive)
    players = client.get("/api/v1/players", params={"q": "Jokic"}).json()
    assert len(players) == 1
    assert players[0]["full_name"] == "Nikola Jokić"

    # Team roster with EWMA min & usg
    roster = client.get("/api/v1/teams/1/roster").json()
    assert len(roster) == 2
    assert roster[0]["ewma_min"] > 0
    assert roster[0]["ewma_usg"] > 0

    # Simulate What-If
    sim_resp = client.post(
        "/api/v1/simulate",
        json={
            "player_name": "Jamal Murray",
            "opponent_team": "LAL",
            "out_players": ["Nikola Jokic"],
            "is_back_to_back": True,
            "is_home": True,
        },
    )
    assert sim_resp.status_code == 200
    data = sim_resp.json()
    assert data["player_name"] == "Jamal Murray"
    assert data["star_teammate_out"] is True
    assert data["out_players_resolved"] == ["Nikola Jokić"]

    # Simulate Team Boxscore (marking Jokic OUT)
    team_sim = client.post(
        "/api/v1/simulate-team",
        json={
            "team_id": 1,
            "opponent_team_id": 2,
            "out_player_ids": [101],
            "is_back_to_back": False,
            "is_home": True,
        },
    )
    assert team_sim.status_code == 200
    ts_data = team_sim.json()
    assert ts_data["team_abbreviation"] == "DEN"
    assert ts_data["opponent_abbreviation"] == "LAL"
    assert len(ts_data["rows"]) == 2
    by_id = {r["player_id"]: r for r in ts_data["rows"]}
    assert by_id[101]["is_out"] is True
    assert by_id[102]["is_out"] is False
    assert by_id[102]["scenario"]["pra"] > 0
    assert len(by_id[102]["top_drivers"]) == 3

    # Full Game Simulation with automatic injury pre-loading (Jokic auto-marked OUT!)
    game_sim = client.post(
        "/api/v1/simulate-game",
        json={
            "away_team_id": 1,
            "home_team_id": 2,
            "auto_apply_injuries": True,
            "away_b2b": False,
            "home_b2b": False,
        },
    )
    assert game_sim.status_code == 200
    gs = game_sim.json()
    assert gs["away_out_player_ids"] == [101]
    assert gs["pace_summary"]["expected_pace"] > 0
    away_by_id = {r["player_id"]: r for r in gs["away"]["rows"]}
    assert away_by_id[101]["is_out"] is True
    assert away_by_id[101]["injury_status"] == "OUT"

    # Player profile (splits + recent game logs)
    prof_resp = client.get("/api/v1/players/102/profile")
    assert prof_resp.status_code == 200
    prof = prof_resp.json()
    assert prof["full_name"] == "Jamal Murray"
    assert prof["total_games"] > 0
    assert len(prof["recent_games"]) > 0
    assert len(prof["splits"]) > 0

    # Fast direct AI explain endpoint (no parse_query LLM call)
    exp_resp = client.post(
        "/api/v1/explain",
        json={
            "player_name": "Jamal Murray",
            "opponent_team": "LAL",
            "out_players": ["Nikola Jokic"],
            "is_back_to_back": True,
            "is_home": True,
        },
    )
    assert exp_resp.status_code == 200
    exp_data = exp_resp.json()
    assert exp_data["verification_passed"] is True
    assert "Nikola Jokić" in exp_data["analysis"]

    # Ask LangGraph agent
    ask_resp = client.post(
        "/api/v1/ask",
        json={"query": "Cómo proyecta Jamal Murray contra LAL en B2B sin Jokic?"},
    )
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["ml_result"]["player_name"] == "Jamal Murray"
    assert ask_data["verification_passed"] is True
