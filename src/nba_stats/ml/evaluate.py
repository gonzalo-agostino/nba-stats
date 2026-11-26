"""Temporal cross-validation splits and backtest evaluation metrics vs 10-game baseline."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error


def expanding_date_splits(
    dates: pd.Series,
    n_splits: int = 5,
    min_train_fraction: float = 0.45,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Create chronological expanding-window splits grouped strictly by unique game_date."""
    unique_dates = np.sort(pd.to_datetime(dates).dropna().unique())
    n_dates = len(unique_dates)
    if n_dates < n_splits + 2:
        raise ValueError(f"Not enough unique dates ({n_dates}) for {n_splits} temporal splits.")

    start_val_idx = max(1, int(n_dates * min_train_fraction))
    remaining = n_dates - start_val_idx
    fold_size = max(1, remaining // n_splits)

    date_values = pd.to_datetime(dates).to_numpy()
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        val_start = start_val_idx + k * fold_size
        val_end = n_dates if k == n_splits - 1 else min(n_dates, val_start + fold_size)
        if val_start >= n_dates or val_start >= val_end:
            break

        train_cutoff = unique_dates[val_start - 1]
        val_first = unique_dates[val_start]
        val_last = unique_dates[val_end - 1]

        train_idx = np.where(date_values <= train_cutoff)[0]
        val_idx = np.where((date_values >= val_first) & (date_values <= val_last))[0]
        if len(train_idx) > 0 and len(val_idx) > 0:
            splits.append((train_idx, val_idx))

    return splits


def _metric_block(y_true: pd.Series, y_pred: pd.Series, y_base: pd.Series) -> dict[str, float]:
    mask = y_true.notna() & y_pred.notna() & y_base.notna()
    n = int(mask.sum())
    if n == 0:
        return {
            "n": 0,
            "model_mae": float("nan"),
            "baseline_mae": float("nan"),
            "mae_improvement_pct": float("nan"),
            "model_rmse": float("nan"),
            "baseline_rmse": float("nan"),
            "rmse_improvement_pct": float("nan"),
        }

    yt = y_true[mask]
    yp = y_pred[mask]
    yb = y_base[mask]

    model_mae = float(mean_absolute_error(yt, yp))
    base_mae = float(mean_absolute_error(yt, yb))
    model_rmse = float(root_mean_squared_error(yt, yp))
    base_rmse = float(root_mean_squared_error(yt, yb))

    mae_imp = ((base_mae - model_mae) / base_mae * 100.0) if base_mae > 0 else 0.0
    rmse_imp = ((base_rmse - model_rmse) / base_rmse * 100.0) if base_rmse > 0 else 0.0

    return {
        "n": n,
        "model_mae": round(model_mae, 4),
        "baseline_mae": round(base_mae, 4),
        "mae_improvement_pct": round(mae_imp, 2),
        "model_rmse": round(model_rmse, 4),
        "baseline_rmse": round(base_rmse, 4),
        "rmse_improvement_pct": round(rmse_imp, 2),
    }


def evaluate_backtest(
    oof_df: pd.DataFrame,
    targets: list[str] | None = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Evaluate OOF predictions vs 10-game rolling average baseline across key slices."""
    eval_targets = targets or ["min", "pts", "reb", "ast", "pra"]
    slices: dict[str, pd.Series] = {
        "all": pd.Series(True, index=oof_df.index),
        "b2b": oof_df["is_back_to_back"] == 1,
        "star_out": oof_df["star_teammate_out"] == 1,
    }

    results: dict[str, dict[str, dict[str, float]]] = {}
    for slice_name, mask in slices.items():
        sub = oof_df[mask]
        results[slice_name] = {}
        for target in eval_targets:
            results[slice_name][target] = _metric_block(
                y_true=sub[f"target_{target}"],
                y_pred=sub[f"pred_{target}"],
                y_base=sub[f"{target}_avg_10"],
            )
    return results
