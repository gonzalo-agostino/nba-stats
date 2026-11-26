"""Two-stage LightGBM training (Stage 1: MIN -> Stage 2: PTS, REB, AST -> PRA)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from nba_stats.config import Settings, TrainingConfig, load_training_config
from nba_stats.features.pipeline import BASE_FEATURE_COLUMNS
from nba_stats.ml.evaluate import evaluate_backtest, expanding_date_splits

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    metrics: dict[str, dict[str, dict[str, float]]]
    oof_rows: int
    train_rows: int
    models_dir: Path
    oof_path: Path


def _params_for_target(cfg: TrainingConfig, target: str) -> dict[str, Any]:
    params = dict(cfg.lightgbm)
    if target in cfg.target_objectives:
        params["objective"] = cfg.target_objectives[target]
        params["metric"] = "mae"
    return params


def _build_lgbm(params: dict[str, Any]) -> tuple[lgb.LGBMRegressor, int]:
    clean = dict(params)
    early_stopping = int(clean.pop("early_stopping_rounds", 50))
    return lgb.LGBMRegressor(**clean), early_stopping


def _fit_lgbm(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: pd.DataFrame | None,
    y_val: pd.Series | None,
    params: dict[str, Any],
) -> lgb.LGBMRegressor:
    model, early_stopping = _build_lgbm(params)
    if X_val is not None and y_val is not None and len(X_val) > 0:
        model.fit(
            X_train,
            y_train,
            eval_X=X_val,
            eval_y=y_val,
            callbacks=[lgb.early_stopping(early_stopping, verbose=False)],
        )
    else:
        model.fit(X_train, y_train)
    return model


def _generate_causal_oof_minutes(
    df_subset: pd.DataFrame,
    feature_cols: list[str],
    params: dict[str, Any],
    n_inner_splits: int = 3,
) -> pd.Series:
    """Generate strictly causal out-of-fold pred_min within a training slice."""
    oof_min = pd.Series(np.nan, index=df_subset.index, dtype="float64")
    sub_reset = df_subset.reset_index(drop=True)
    inner_splits = expanding_date_splits(
        sub_reset["game_date"],
        n_splits=n_inner_splits,
        min_train_fraction=0.30,
    )
    for tr_idx, va_idx in inner_splits:
        model = _fit_lgbm(
            sub_reset.iloc[tr_idx][feature_cols],
            sub_reset.iloc[tr_idx]["target_min"],
            sub_reset.iloc[va_idx][feature_cols],
            sub_reset.iloc[va_idx]["target_min"],
            params,
        )
        preds = np.clip(model.predict(sub_reset.iloc[va_idx][feature_cols]), 0.0, 48.0)
        oof_min.iloc[va_idx] = preds
    return oof_min


def train_and_backtest(
    features_df: pd.DataFrame | None = None,
    settings: Settings | None = None,
    config: TrainingConfig | None = None,
) -> TrainingResult:
    """Run temporal expanding-window backtest and train production two-stage LightGBM models."""
    settings = settings or Settings()
    settings.ensure_dirs()
    cfg = config or load_training_config(settings)

    if features_df is None:
        feat_path = settings.processed_dir / "features.parquet"
        if not feat_path.exists():
            raise FileNotFoundError(
                f"Feature dataset not found at {feat_path}. Run `nba-stats build-features` first."
            )
        features_df = pd.read_parquet(feat_path)

    stage1_cols = [c for c in BASE_FEATURE_COLUMNS if c in features_df.columns]
    stage2_cols = [*stage1_cols, "pred_min"]

    # Filter out cold-start rows with fewer than min_prior_games and non-null base targets
    target_mask = features_df["games_played_prior"] >= cfg.validation.min_prior_games
    for t in [cfg.stage1_target, *cfg.stage2_targets]:
        target_mask &= features_df[f"target_{t}"].notna()

    eligible = (
        features_df[target_mask]
        .sort_values(["game_date", "game_id", "player_id"])
        .reset_index(drop=True)
    )

    splits = expanding_date_splits(
        eligible["game_date"],
        n_splits=cfg.validation.n_splits,
        min_train_fraction=cfg.validation.min_train_fraction,
    )
    logger.info("Running %d-fold temporal backtest on %d rows...", len(splits), len(eligible))

    oof_frames: list[pd.DataFrame] = []
    best_iterations: dict[str, list[int]] = {
        "min": [],
        **{t: [] for t in cfg.stage2_targets},
    }

    for fold_idx, (train_idx, val_idx) in enumerate(splits, start=1):
        train_df = eligible.iloc[train_idx].copy()
        val_df = eligible.iloc[val_idx].copy()

        # Stage 1: predict MIN on val_df
        min_model = _fit_lgbm(
            train_df[stage1_cols],
            train_df["target_min"],
            val_df[stage1_cols],
            val_df["target_min"],
            cfg.lightgbm,
        )
        best_iterations["min"].append(int(min_model.best_iteration_ or cfg.lightgbm["n_estimators"]))
        val_df["pred_min"] = np.clip(min_model.predict(val_df[stage1_cols]), 0.0, 48.0)

        # Causal OOF pred_min on train_df for Stage 2 (prevents leakage & train/serve skew)
        train_df["pred_min"] = _generate_causal_oof_minutes(
            train_df,
            feature_cols=stage1_cols,
            params=cfg.lightgbm,
            n_inner_splits=3,
        )
        stage2_train = train_df[train_df["pred_min"].notna()]

        # Stage 2: predict all base stats conditioned on pred_min
        for target in cfg.stage2_targets:
            t_params = _params_for_target(cfg, target)
            model = _fit_lgbm(
                stage2_train[stage2_cols],
                stage2_train[f"target_{target}"],
                val_df[stage2_cols],
                val_df[f"target_{target}"],
                t_params,
            )
            best_iterations[target].append(
                int(model.best_iteration_ or t_params["n_estimators"])
            )
            val_df[f"pred_{target}"] = np.clip(model.predict(val_df[stage2_cols]), 0.0, None)

        # Derived composites (PRA = PTS + REB + AST)
        for comp_name, parts in cfg.derived_targets.items():
            val_df[f"pred_{comp_name}"] = sum(val_df[f"pred_{p}"] for p in parts)

        val_df["fold"] = fold_idx
        oof_frames.append(val_df)
        logger.info("Completed fold %d/%d (val_rows=%d).", fold_idx, len(splits), len(val_df))

    oof_df = pd.concat(oof_frames, ignore_index=True)
    eval_targets = [cfg.stage1_target, *cfg.stage2_targets, *cfg.derived_targets.keys()]
    metrics = evaluate_backtest(oof_df, targets=eval_targets)

    # Save OOF predictions
    oof_path = settings.artifacts_dir / "backtest_oof.parquet"
    keep_cols = [
        "fold",
        "game_id",
        "player_id",
        "player_name",
        "team_abbreviation",
        "season",
        "game_date",
        "is_back_to_back",
        "star_teammate_out",
        *[f"target_{t}" for t in eval_targets],
        *[f"pred_{t}" for t in eval_targets],
        *[f"{t}_avg_10" for t in eval_targets],
    ]
    oof_df[keep_cols].to_parquet(oof_path, index=False)

    # Fit final production models on all eligible data
    logger.info("Fitting final production models on %d rows...", len(eligible))
    final_min_params = dict(cfg.lightgbm)
    final_min_params["n_estimators"] = max(50, int(np.median(best_iterations["min"])))
    final_min_model = _fit_lgbm(
        eligible[stage1_cols],
        eligible["target_min"],
        None,
        None,
        final_min_params,
    )
    joblib.dump(final_min_model, settings.models_dir / "min_model.joblib")

    # Causal OOF pred_min across all eligible rows for final Stage 2 models
    full_oof_min = _generate_causal_oof_minutes(
        eligible,
        feature_cols=stage1_cols,
        params=cfg.lightgbm,
        n_inner_splits=4,
    )
    eligible_s2 = eligible.copy()
    eligible_s2["pred_min"] = full_oof_min
    eligible_s2 = eligible_s2[eligible_s2["pred_min"].notna()]

    for target in cfg.stage2_targets:
        t_params = _params_for_target(cfg, target)
        t_params["n_estimators"] = max(50, int(np.median(best_iterations[target])))
        t_model = _fit_lgbm(
            eligible_s2[stage2_cols],
            eligible_s2[f"target_{target}"],
            None,
            None,
            t_params,
        )
        joblib.dump(t_model, settings.models_dir / f"{target}_model.joblib")

    metadata = {
        "trained_at_utc": datetime.now(UTC).isoformat(),
        "train_rows": len(eligible),
        "oof_rows": len(oof_df),
        "stage1_features": stage1_cols,
        "stage2_features": stage2_cols,
        "stage1_target": cfg.stage1_target,
        "stage2_targets": cfg.stage2_targets,
        "derived_targets": cfg.derived_targets,
        "metrics": metrics,
    }
    with (settings.models_dir / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    return TrainingResult(
        metrics=metrics,
        oof_rows=len(oof_df),
        train_rows=len(eligible),
        models_dir=settings.models_dir,
        oof_path=oof_path,
    )
