"""Unit tests for Parlay Builder algorithm and DuckDB persistence/grading."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from nba_stats.api.parlay import ParlayService, build_parlay_recommendations
from nba_stats.api.schemas import ParlayBuildRequest, SaveParlayLegInput, SaveParlayRequest
from nba_stats.data.schema import init_schema


def _sample_board_items() -> list[dict]:
    return [
        {
            "id": "2026-11-28_101_pts",
            "game_id": "G1",
            "player_id": 101,
            "player_name": "Nikola Jokic",
            "team_abbreviation": "DEN",
            "opponent_abbreviation": "LAL",
            "is_home": True,
            "is_back_to_back": False,
            "injury_status": None,
            "blowout_risk": False,
            "stat": "pts",
            "bookmaker": "fanduel",
            "line": 26.5,
            "recommended_side": "OVER",
            "odds_decimal": 1.90,
            "ml_projection": 29.5,
            "ml_edge": 3.0,
            "ml_agrees": True,
            "conviction_score": 88,
            "l5": {"hits": 4, "total": 5, "pct": 80.0},
            "l10": {"hits": 8, "total": 10, "pct": 80.0},
            "l20": {"hits": 15, "total": 20, "pct": 75.0},
        },
        {
            "id": "2026-11-28_101_ast",
            "game_id": "G1",
            "player_id": 101,
            "player_name": "Nikola Jokic",
            "team_abbreviation": "DEN",
            "opponent_abbreviation": "LAL",
            "is_home": True,
            "is_back_to_back": False,
            "injury_status": None,
            "blowout_risk": False,
            "stat": "ast",
            "bookmaker": "fanduel",
            "line": 9.5,
            "recommended_side": "OVER",
            "odds_decimal": 1.85,
            "ml_projection": 11.2,
            "ml_edge": 1.7,
            "ml_agrees": True,
            "conviction_score": 84,
            "l5": {"hits": 4, "total": 5, "pct": 80.0},
            "l10": {"hits": 7, "total": 10, "pct": 70.0},
            "l20": {"hits": 14, "total": 20, "pct": 70.0},
        },
        {
            "id": "2026-11-28_202_pts",
            "game_id": "G2",
            "player_id": 202,
            "player_name": "Jalen Brunson",
            "team_abbreviation": "NYK",
            "opponent_abbreviation": "BOS",
            "is_home": False,
            "is_back_to_back": False,
            "injury_status": None,
            "blowout_risk": False,
            "stat": "pts",
            "bookmaker": "draftkings",
            "line": 24.5,
            "recommended_side": "OVER",
            "odds_decimal": 1.92,
            "ml_projection": 27.1,
            "ml_edge": 2.6,
            "ml_agrees": True,
            "conviction_score": 82,
            "l5": {"hits": 4, "total": 5, "pct": 80.0},
            "l10": {"hits": 7, "total": 10, "pct": 70.0},
            "l20": {"hits": 13, "total": 20, "pct": 65.0},
        },
        {
            "id": "2026-11-28_303_reb",
            "game_id": "G3",
            "player_id": 303,
            "player_name": "Domantas Sabonis",
            "team_abbreviation": "SAC",
            "opponent_abbreviation": "PHX",
            "is_home": True,
            "is_back_to_back": False,
            "injury_status": "DTD",
            "blowout_risk": True,
            "stat": "reb",
            "bookmaker": "fanduel",
            "line": 12.5,
            "recommended_side": "OVER",
            "odds_decimal": 1.95,
            "ml_projection": 14.0,
            "ml_edge": 1.5,
            "ml_agrees": True,
            "conviction_score": 65,
            "l5": {"hits": 3, "total": 5, "pct": 60.0},
            "l10": {"hits": 6, "total": 10, "pct": 60.0},
            "l20": {"hits": 12, "total": 20, "pct": 60.0},
        },
    ]


def test_build_parlay_allows_same_player_different_stats_and_locks_picks() -> None:
    items = _sample_board_items()
    req = ParlayBuildRequest(
        target_odds=3.5,
        locked_pick_ids=["2026-11-28_101_pts"],
        max_legs=3,
        one_per_player=False,
        exclude_blowout=True,
        exclude_dtd=True,
    )
    out = build_parlay_recommendations(items, req)
    assert len(out["parlays"]) >= 1
    top = out["parlays"][0]
    ids = [lg["pick_id"] for lg in top["legs"]]
    assert "2026-11-28_101_pts" in ids
    # Blowout + DTD player 303 should be excluded
    assert "2026-11-28_303_reb" not in ids
    # Same player (101) with different stat (ast) is allowed
    all_ids_across = {lg["pick_id"] for p in out["parlays"] for lg in p["legs"]}
    assert "2026-11-28_101_ast" in all_ids_across


def test_parlay_persistence_and_automatic_grading(tmp_path: Path) -> None:
    db_path = tmp_path / "test_parlays.duckdb"
    with duckdb.connect(str(db_path)) as con:
        init_schema(con)
        con.execute(
            """
            INSERT INTO player_game_logs (
                game_id, game_date, season, season_type, player_id, player_name,
                team_id, team_abbreviation, opp_team_id, is_home, wl,
                min, pts, reb, ast, stl, blk, tov, pf, fga, fgm, fg3a, fg3m, fta, ftm, plus_minus
            ) VALUES
                ('G1', ?, '2026-27', 'Regular Season', 101, 'Nikola Jokic', 1, 'DEN', 2, true, 'W',
                 36.0, 31.0, 12.0, 11.0, 1.0, 1.0, 2.0, 2.0, 20.0, 12.0, 4.0, 2.0, 6.0, 5.0, 10.0),
                ('G2', ?, '2026-27', 'Regular Season', 202, 'Jalen Brunson', 3, 'NYK', 4, false, 'W',
                 35.0, 28.0, 3.0, 7.0, 1.0, 0.0, 2.0, 2.0, 19.0, 10.0, 6.0, 3.0, 5.0, 5.0, 6.0)
            """,
            [date(2026, 11, 27), date(2026, 11, 27)],
        )

    svc = ParlayService(duckdb_path=db_path)
    saved = svc.save_parlay(
        SaveParlayRequest(
            game_date="2026-11-27",
            label="Más Segura (2 patas)",
            target_odds=3.5,
            combined_odds=3.65,
            estimated_prob=52.0,
            stake_units=1.0,
            legs=[
                SaveParlayLegInput(
                    pick_id="2026-11-27_101_pts",
                    player_id=101,
                    player_name="Nikola Jokic",
                    team_abbreviation="DEN",
                    opponent_abbreviation="LAL",
                    stat="pts",
                    side="OVER",
                    line=26.5,
                    odds_decimal=1.90,
                    locked=True,
                ),
                SaveParlayLegInput(
                    pick_id="2026-11-27_202_pts",
                    player_id=202,
                    player_name="Jalen Brunson",
                    team_abbreviation="NYK",
                    opponent_abbreviation="BOS",
                    stat="pts",
                    side="OVER",
                    line=24.5,
                    odds_decimal=1.92,
                    locked=False,
                ),
            ],
        )
    )
    assert saved["status"] == "PENDING"

    grade_res = svc.grade_pending_parlays()
    assert grade_res["graded"] == 1
    assert grade_res["wins"] == 1

    history = svc.get_parlay_history()
    assert history["overall"]["wins"] == 1
    assert history["overall"]["win_rate_pct"] == 100.0
    assert history["overall"]["net_units"] == 2.65
    assert history["parlays"][0]["legs_won"] == 2
