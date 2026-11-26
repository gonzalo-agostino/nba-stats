"""Fast exact TreeSHAP attribution utilities using LightGBM native pred_contrib."""

from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd


def explain_prediction(
    model: lgb.LGBMRegressor,
    x_row: pd.DataFrame,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Compute exact TreeSHAP values for a single-row DataFrame and return top-K drivers.

    Uses LightGBM's native C++ ``pred_contrib=True`` (~1ms per row vs ~250ms creating a
    fresh ``shap.TreeExplainer``), producing mathematically identical TreeSHAP values.
    """
    if len(x_row) != 1:
        raise ValueError(f"explain_prediction expects a 1-row DataFrame, got {len(x_row)} rows.")

    raw_contrib = np.asarray(model.predict(x_row, pred_contrib=True)).reshape(-1)
    # Last element is the expected base value (bias)
    contrib = raw_contrib[:-1]
    feature_names = list(x_row.columns)
    values = x_row.iloc[0].to_dict()

    order = np.argsort(np.abs(contrib))[::-1][:top_k]
    drivers: list[dict[str, Any]] = []
    for idx in order:
        fname = feature_names[int(idx)]
        sv = float(contrib[int(idx)])
        raw_val = values[fname]
        val = None if pd.isna(raw_val) else float(raw_val)
        drivers.append(
            {
                "feature": fname,
                "value": round(val, 4) if val is not None else None,
                "shap_value": round(sv, 4),
                "direction": "+" if sv >= 0 else "-",
            }
        )
    return drivers
