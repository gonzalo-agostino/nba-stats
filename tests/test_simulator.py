"""Unit tests for the real-time What-If ML simulator."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from nba_stats.config import Settings, TrainingConfig, ValidationConfig
from nba_stats.data.schema import init_schema
from nba_stats.features.pipeline import build_feature_dataframe
from nba_stats.ml.train import train_and_backtest
from nba_stats.simulator.schemas import ScenarioRequest
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator


def _build_temp_warehouse_and_models(tmp_path: Path) -> Settings:
    settings = Settings(nba_stats_data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()

    con = duckdb.connect(str(settings.duckdb_path))
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
            (101, 'Nikola Jokić', 'C', 'C', 1, TRUE),
            (102, 'Jamal Murray', 'G', 'G', 1, TRUE),
            (201, 'LeBron James', 'F', 'F', 2, TRUE)
        """
    )

    rng = np.random.default_rng(7)
    start = date(2025, 11, 1)
    for idx in range(28):
        gid = f"G{idx:04d}"
        gdate = start + timedelta(days=idx * 2)
        is_home_1 = idx % 2 == 0
        con.execute(
            """
            INSERT INTO team_game_logs
                (game_id, team_id, season, season_type, game_date, matchup, is_home, opp_team_id,
                 wl, min, pts, reb, ast, fta, off_rating, def_rating, pace)
            VALUES
                (?, 1, '2025-26', 'Regular Season', ?, 'DEN vs. LAL', ?, 2, 'W', 240, 115, 45, 29, 20, 116, 109, 99),
                (?, 2, '2025-26', 'Regular Season', ?, 'LAL @ DEN', ?, 1, 'L', 240, 108, 41, 23, 19, 109, 116, 99)
            """,
            [gid, gdate, is_home_1, gid, gdate, not is_home_1],
        )

        star_out = idx in (6, 12, 18, 24)
        if not star_out:
            con.execute(
                """
                INSERT INTO player_game_logs
                    (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                     game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, stl, blk, tov, pf,
                     fga, fta, ftm, usg_pct, ts_pct, ast_pct, reb_pct)
                VALUES (?, 101, 'Nikola Jokić', 1, 'DEN', '2025-26', 'Regular Season',
                        ?, 'DEN vs. LAL', ?, 2, 'W', 36.0, 29.0, 13.0, 9.0, 1.5, 0.8, 3.0, 2.5,
                        19.0, 6.0, 5.0, 0.31, 0.66, 0.40, 0.21)
                """,
                [gid, gdate, is_home_1],
            )

        m_min = 37.0 if star_out else 32.0 + float(rng.normal(0, 1.0))
        m_pts = 27.0 if star_out else 20.0 + float(rng.normal(0, 1.5))
        con.execute(
            """
            INSERT INTO player_game_logs
                (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                 game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, stl, blk, tov, pf,
                 fga, fta, ftm, usg_pct, ts_pct, ast_pct, reb_pct)
            VALUES (?, 102, 'Jamal Murray', 1, 'DEN', '2025-26', 'Regular Season',
                    ?, 'DEN vs. LAL', ?, 2, 'W', ?, ?, 4.0, 6.5, 1.1, 0.4, 2.2, 2.0,
                    16.0, 4.0, 3.5, 0.25, 0.58, 0.28, 0.07)
            """,
            [gid, gdate, is_home_1, m_min, m_pts],
        )
        con.execute(
            """
            INSERT INTO player_game_logs
                (game_id, player_id, player_name, team_id, team_abbreviation, season, season_type,
                 game_date, matchup, is_home, opp_team_id, wl, min, pts, reb, ast, stl, blk, tov, pf,
                 fga, fta, ftm, usg_pct, ts_pct, ast_pct, reb_pct)
            VALUES (?, 201, 'LeBron James', 2, 'LAL', '2025-26', 'Regular Season',
                    ?, 'LAL @ DEN', ?, 1, 'L', 35.0, 25.0, 7.0, 8.0, 1.2, 0.7, 3.5, 1.8,
                    18.0, 6.0, 4.5, 0.29, 0.61, 0.34, 0.13)
            """,
            [gid, gdate, not is_home_1],
        )

    feat_df = build_feature_dataframe(con)
    con.close()

    train_cfg = TrainingConfig(
        validation=ValidationConfig(n_splits=3, min_train_fraction=0.45, min_prior_games=5),
        lightgbm={
            "objective": "regression_l1",
            "metric": "mae",
            "learning_rate": 0.1,
            "num_leaves": 15,
            "min_child_samples": 5,
            "n_estimators": 30,
            "early_stopping_rounds": 10,
            "random_state": 42,
            "verbose": -1,
        },
    )
    train_and_backtest(features_df=feat_df, settings=settings, config=train_cfg)
    return settings


def test_what_if_simulator_with_star_out_and_wowy(tmp_path: Path) -> None:
    settings = _build_temp_warehouse_and_models(tmp_path)
    sim = WhatIfSimulator(settings=settings)

    res = sim.simulate(
        ScenarioRequest(
            player_name="Jamal Murray",
            opponent_team="LAL",
            out_players=["Nikola Jokic"],  # accent-insensitive resolution!
            opponent_out_players=["LeBron James"],
            is_back_to_back=True,
            is_home=True,
        )
    )

    assert res.player_name == "Jamal Murray"
    assert res.opponent_abbreviation == "LAL"
    assert res.out_players_resolved == ["Nikola Jokić"]
    assert res.opp_out_players_resolved == ["LeBron James"]
    assert res.star_teammate_out is True
    assert res.missing_team_minutes == 36.0
    assert res.opponent_defense is not None
    assert res.opponent_defense.opp_missing_minutes == 35.0
    assert res.opponent_defense.def_rating_rank is not None
    assert res.wowy_context is not None
    assert res.wowy_context.sample_games_without == 4
    assert res.wowy_context.raw_delta_pts > 0.0
    assert np.isclose(
        res.scenario_projection.pra,
        res.scenario_projection.pts + res.scenario_projection.reb + res.scenario_projection.ast,
        atol=0.02,
    )
    assert np.isclose(
        res.scenario_projection.pr,
        res.scenario_projection.pts + res.scenario_projection.reb,
        atol=0.02,
    )
    assert np.isclose(
        res.scenario_projection.pa,
        res.scenario_projection.pts + res.scenario_projection.ast,
        atol=0.02,
    )
    assert len(res.shap_drivers["pts"]) == 5
    assert len(res.shap_drivers["blk"]) == 5


def test_what_if_simulator_unknown_player_raises(tmp_path: Path) -> None:
    settings = _build_temp_warehouse_and_models(tmp_path)
    sim = WhatIfSimulator(settings=settings)
    try:
        sim.simulate(ScenarioRequest(player_name="Nonexistent Player XYZ"))
        raise AssertionError("Expected EntityResolutionError")
    except EntityResolutionError:
        pass
