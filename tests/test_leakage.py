"""Strict anti-data-leakage tests verifying that target-game stats never leak into features."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

from nba_stats.data.schema import init_schema
from nba_stats.features.pipeline import BASE_FEATURE_COLUMNS, build_feature_dataframe


def _seed_synthetic_warehouse(con: duckdb.DuckDBPyConnection, n_games: int = 12) -> None:
    init_schema(con)
    con.execute(
        """
        INSERT INTO teams VALUES
            (1, 'DEN', 'Denver Nuggets', 'Nuggets', 'Denver', 'CO', 1976),
            (2, 'LAL', 'Los Angeles Lakers', 'Lakers', 'Los Angeles', 'CA', 1948)
        """
    )
    con.execute(
        """
        INSERT INTO players (player_id, full_name, position, pos_group, team_id, is_active) VALUES
            (101, 'Star Center', 'C', 'C', 1, TRUE),
            (102, 'Guard Sidekick', 'G', 'G', 1, TRUE),
            (201, 'Opp Guard', 'G', 'G', 2, TRUE)
        """
    )

    start = date(2025, 11, 1)
    for idx in range(n_games):
        gid = f"G{idx:04d}"
        gdate = start + timedelta(days=idx * 2)
        is_home_1 = idx % 2 == 0
        con.execute(
            """
            INSERT INTO team_game_logs
                (game_id, team_id, season, season_type, game_date, matchup, is_home, opp_team_id,
                 wl, min, pts, reb, ast, fta, off_rating, def_rating, pace)
            VALUES
                (?, 1, '2025-26', 'Regular Season', ?, 'DEN vs. LAL', ?, 2, 'W', 240, ?, 44, 28, 20, 115, 108, 99),
                (?, 2, '2025-26', 'Regular Season', ?, 'LAL @ DEN', ?, 1, 'L', 240, ?, 40, 22, 18, 108, 115, 99)
            """,
            [gid, gdate, is_home_1, 110 + idx, gid, gdate, not is_home_1, 102 + idx],
        )

        # Star Center skips game 5 and game 9 to create WOWY history
        if idx not in (5, 9):
            con.execute(
                """
                INSERT INTO player_game_logs
                    (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                     game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, fga, fta, ftm,
                     usg_pct, ts_pct, ast_pct, reb_pct)
                VALUES (?, 101, 'Star Center', 1, 'DEN', '2025-26', 'Regular Season',
                        ?, 'DEN vs. LAL', ?, 2, 'W', 35.0, 28.0, 12.0, 8.0, 18.0, 6.0, 5.0, 0.31, 0.65, 0.35, 0.20)
                """,
                [gid, gdate, is_home_1],
            )

        # Guard Sidekick plays every game, scoring more when Star Center is out
        sidekick_pts = 26.0 if idx in (5, 9) else 16.0
        sidekick_min = 36.0 if idx in (5, 9) else 30.0
        con.execute(
            """
            INSERT INTO player_game_logs
                (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                 game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, fga, fta, ftm,
                 usg_pct, ts_pct, ast_pct, reb_pct)
            VALUES (?, 102, 'Guard Sidekick', 1, 'DEN', '2025-26', 'Regular Season',
                    ?, 'DEN vs. LAL', ?, 2, 'W', ?, ?, 4.0, 6.0, 14.0, 4.0, 3.0, 0.22, 0.58, 0.24, 0.08)
            """,
            [gid, gdate, is_home_1, sidekick_min, sidekick_pts],
        )
        con.execute(
            """
            INSERT INTO player_game_logs
                (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                 game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, fga, fta, ftm,
                 usg_pct, ts_pct, ast_pct, reb_pct)
            VALUES (?, 201, 'Opp Guard', 2, 'LAL', '2025-26', 'Regular Season',
                    ?, 'LAL @ DEN', ?, 1, 'L', 32.0, 20.0, 5.0, 5.0, 15.0, 5.0, 4.0, 0.25, 0.56, 0.20, 0.10)
            """,
            [gid, gdate, not is_home_1],
        )


def test_no_data_leakage_from_target_game() -> None:
    """Mutating target-game boxscore stats must leave all features for that game 100% unchanged."""
    con1 = duckdb.connect(":memory:")
    con2 = duckdb.connect(":memory:")
    _seed_synthetic_warehouse(con1)
    _seed_synthetic_warehouse(con2)

    # Corrupt all boxscore stats on the final game G0011 in con2
    con2.execute(
        """
        UPDATE player_game_logs
        SET pts = 999.0, reb = 999.0, ast = 999.0, min = 48.0, fga = 999.0, usg_pct = 0.99
        WHERE game_id = 'G0011'
        """
    )
    con2.execute(
        """
        UPDATE team_game_logs
        SET pts = 999.0, pace = 200.0, def_rating = 200.0, off_rating = 200.0
        WHERE game_id = 'G0011'
        """
    )

    df1 = build_feature_dataframe(con1)
    df2 = build_feature_dataframe(con2)

    row1 = df1[(df1["game_id"] == "G0011") & (df1["player_id"] == 102)].iloc[0]
    row2 = df2[(df2["game_id"] == "G0011") & (df2["player_id"] == 102)].iloc[0]

    # Targets MUST differ
    assert row1["target_pts"] != row2["target_pts"]

    # Every single feature MUST be identical
    for col in BASE_FEATURE_COLUMNS:
        v1, v2 = row1[col], row2[col]
        if pd.isna(v1) and pd.isna(v2):
            continue
        assert np.isclose(float(v1), float(v2), atol=1e-9), f"Leakage detected in feature '{col}': {v1} != {v2}"


def test_wowy_captures_star_absence_and_prior_delta() -> None:
    """Verify star_teammate_out, missing_team_minutes, and causal wowy_delta_pts_p36."""
    con = duckdb.connect(":memory:")
    _seed_synthetic_warehouse(con)
    df = build_feature_dataframe(con)

    # Game 5: first time Star Center (35 min, 0.31 usg) is OUT -> no prior WOWY games yet
    g5 = df[(df["game_id"] == "G0005") & (df["player_id"] == 102)].iloc[0]
    assert g5["star_teammate_out"] == 1
    assert g5["missing_team_minutes"] == 35.0
    assert g5["wowy_sample_games"] == 0
    assert g5["wowy_delta_pts_p36"] == 0.0

    # Game 9: second time Star Center is OUT -> 1 prior game (G0005) where sidekick jumped from 16 to 26 pts
    g9 = df[(df["game_id"] == "G0009") & (df["player_id"] == 102)].iloc[0]
    assert g9["star_teammate_out"] == 1
    assert g9["wowy_sample_games"] == 1
    assert g9["wowy_delta_min"] > 0.0
    assert g9["wowy_delta_pts_p36"] > 0.0
