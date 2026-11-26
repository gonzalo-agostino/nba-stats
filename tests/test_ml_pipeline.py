"""End-to-end smoke test for two-stage LightGBM training, PRA consistency, and SHAP."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from nba_stats.config import Settings, TrainingConfig, ValidationConfig
from nba_stats.features.pipeline import BASE_FEATURE_COLUMNS
from nba_stats.ml.explain import explain_prediction
from nba_stats.ml.train import train_and_backtest


def _synthetic_features_df(n_dates: int = 30, players_per_date: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rows = []
    start = date(2025, 11, 1)
    for d in range(n_dates):
        gdate = start + timedelta(days=d)
        for p in range(players_per_date):
            base_min = 24.0 + (p % 4) * 4.0
            is_b2b = int(d % 5 == 0)
            star_out = int(d % 7 == 0)
            t_min = float(np.clip(base_min - 1.5 * is_b2b + 3.0 * star_out + rng.normal(0, 2.0), 5, 44))
            t_pts = float(np.clip(0.6 * t_min + rng.normal(0, 2.5), 0, 60))
            t_reb = float(np.clip(0.22 * t_min + rng.normal(0, 1.2), 0, 25))
            t_ast = float(np.clip(0.18 * t_min + rng.normal(0, 1.0), 0, 20))
            t_stl = float(np.clip(rng.poisson(1.1), 0, 6))
            t_blk = float(np.clip(rng.poisson(0.7), 0, 6))
            t_tov = float(np.clip(rng.poisson(2.0), 0, 8))
            t_pf = float(np.clip(rng.poisson(2.2), 0, 6))
            t_fga = float(np.clip(0.45 * t_min + rng.normal(0, 2.0), 0, 35))
            t_ftm = float(np.clip(rng.poisson(3.0), 0, 18))
            row = {
                "game_id": f"G{d:04d}",
                "player_id": 1000 + p,
                "player_name": f"Player {p}",
                "team_abbreviation": "DEN",
                "season": "2025-26",
                "game_date": pd.Timestamp(gdate),
                "target_min": t_min,
                "target_pts": t_pts,
                "target_reb": t_reb,
                "target_ast": t_ast,
                "target_stl": t_stl,
                "target_blk": t_blk,
                "target_tov": t_tov,
                "target_pf": t_pf,
                "target_fga": t_fga,
                "target_ftm": t_ftm,
                "target_pra": t_pts + t_reb + t_ast,
                "target_pr": t_pts + t_reb,
                "target_pa": t_pts + t_ast,
                "pra_avg_10": base_min * 1.0,
                "pr_avg_10": base_min * 0.82,
                "pa_avg_10": base_min * 0.78,
            }
            for col in BASE_FEATURE_COLUMNS:
                row[col] = 0.0
            row["games_played_prior"] = 10 + d
            row["is_back_to_back"] = is_b2b
            row["star_teammate_out"] = star_out
            row["min_avg_10"] = base_min
            row["pts_avg_10"] = 0.6 * base_min
            row["reb_avg_10"] = 0.22 * base_min
            row["ast_avg_10"] = 0.18 * base_min
            row["stl_avg_10"] = 1.1
            row["blk_avg_10"] = 0.7
            row["tov_avg_10"] = 2.0
            row["pf_avg_10"] = 2.2
            row["fga_avg_10"] = 0.45 * base_min
            row["ftm_avg_10"] = 3.0
            rows.append(row)
    return pd.DataFrame(rows)


def test_two_stage_training_and_shap(tmp_path: Path) -> None:
    settings = Settings(nba_stats_data_dir=tmp_path / "data", project_root=tmp_path)
    cfg = TrainingConfig(
        validation=ValidationConfig(n_splits=3, min_train_fraction=0.5, min_prior_games=5),
        lightgbm={
            "objective": "regression_l1",
            "metric": "mae",
            "learning_rate": 0.1,
            "num_leaves": 15,
            "min_child_samples": 10,
            "n_estimators": 40,
            "early_stopping_rounds": 10,
            "random_state": 42,
            "verbose": -1,
        },
    )
    df = _synthetic_features_df()
    result = train_and_backtest(features_df=df, settings=settings, config=cfg)

    assert result.oof_rows > 0
    assert (settings.models_dir / "min_model.joblib").exists()
    assert (settings.models_dir / "pts_model.joblib").exists()
    assert (settings.models_dir / "reb_model.joblib").exists()
    assert (settings.models_dir / "ast_model.joblib").exists()
    assert (settings.models_dir / "metadata.json").exists()

    # Verify exact PRA consistency on saved OOF predictions
    oof = pd.read_parquet(result.oof_path)
    assert np.allclose(
        oof["pred_pra"],
        oof["pred_pts"] + oof["pred_reb"] + oof["pred_ast"],
        atol=1e-6,
    )

    # Verify SHAP attribution works on saved Stage 1 model
    min_model = joblib.load(settings.models_dir / "min_model.joblib")
    drivers = explain_prediction(min_model, df.iloc[[0]][BASE_FEATURE_COLUMNS], top_k=3)
    assert len(drivers) == 3
    assert all("feature" in d and "shap_value" in d for d in drivers)
