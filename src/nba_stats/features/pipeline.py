"""End-to-end feature pipeline assembling SQL, EWMA, and WOWY features without data leakage."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

from nba_stats.config import FeaturesConfig, Settings, load_features_config
from nba_stats.features.sql_features import compute_base_sql_features
from nba_stats.features.wowy import compute_wowy_features

logger = logging.getLogger(__name__)

POS_GROUP_MAP: dict[str, int] = {"G": 0, "F": 1, "C": 2, "UNK": -1}

BASE_FEATURE_COLUMNS: list[str] = [
    # Context & fatigue
    "is_home",
    "pos_group_code",
    "games_played_prior",
    "rest_days",
    "is_back_to_back",
    "player_rest_days",
    "is_returning_from_absence",
    "games_in_last_7_days",
    "games_in_last_14_days",
    "road_trip_game_num",
    "min_last_game",
    # Rolling 5
    "min_avg_5",
    "pts_avg_5",
    "reb_avg_5",
    "ast_avg_5",
    "fga_avg_5",
    "fg3a_avg_5",
    "fta_avg_5",
    "usg_pct_avg_5",
    "ts_pct_avg_5",
    # Rolling 10
    "min_avg_10",
    "pts_avg_10",
    "reb_avg_10",
    "ast_avg_10",
    "fga_avg_10",
    "fg3a_avg_10",
    "fta_avg_10",
    "ftm_avg_10",
    "oreb_avg_10",
    "dreb_avg_10",
    "stl_avg_10",
    "blk_avg_10",
    "tov_avg_10",
    "pf_avg_10",
    "plus_minus_avg_10",
    "usg_pct_avg_10",
    "ts_pct_avg_10",
    "ast_pct_avg_10",
    "reb_pct_avg_10",
    "min_std_10",
    "pts_std_10",
    "reb_std_10",
    "ast_std_10",
    "pts_per36_10",
    "reb_per36_10",
    "ast_per36_10",
    "fga_per36_10",
    "stl_per36_10",
    "blk_per36_10",
    "fta_per36_10",
    "tov_per36_10",
    # Rolling 20 & trends
    "min_avg_20",
    "pts_avg_20",
    "reb_avg_20",
    "ast_avg_20",
    "fga_avg_20",
    "usg_pct_avg_20",
    "ts_pct_avg_20",
    "min_trend_5_20",
    "pts_trend_5_20",
    "reb_trend_5_20",
    "ast_trend_5_20",
    # EWMA (strictly shifted by 1)
    "min_ewma_3",
    "min_ewma_10",
    "pts_ewma_10",
    "reb_ewma_10",
    "ast_ewma_10",
    "fga_ewma_10",
    "usg_pct_ewma_10",
    # Matchup, opponent defense, blowout proxy & DvP
    "team_pace_10",
    "team_off_rating_10",
    "team_def_rating_10",
    "team_net_rating_10",
    "opp_pace_10",
    "opp_off_rating_10",
    "opp_def_rating_10",
    "opp_net_rating_10",
    "expected_margin_proxy",
    "abs_expected_margin",
    "expected_game_pace",
    "opp_pts_allowed_10",
    "opp_reb_allowed_10",
    "opp_ast_allowed_10",
    "opp_fta_allowed_10",
    "opp_efg_allowed_10",
    "opp_fg3a_allowed_10",
    "opp_tov_forced_10",
    "opp_oreb_allowed_10",
    "opp_dvp_pts_10",
    "opp_dvp_reb_10",
    "opp_dvp_ast_10",
    # Own-team absences & WOWY
    "missing_team_minutes",
    "missing_team_usage_pct",
    "star_teammate_out",
    "num_rotation_players_out",
    "missing_same_pos_minutes",
    "missing_same_pos_usage",
    # Opponent absences (rim protection / perimeter defense / blowout)
    "opp_missing_team_minutes",
    "opp_missing_team_usage_pct",
    "opp_star_out",
    "opp_missing_center_minutes",
    "opp_missing_def_stocks",
    "opp_missing_same_pos_minutes",
    # Individual shrunken WOWY
    "wowy_sample_games",
    "wowy_delta_min",
    "wowy_delta_usg",
    "wowy_delta_pts_p36",
    "wowy_delta_reb_p36",
    "wowy_delta_ast_p36",
]


def _add_shifted_ewma_features(
    df: pd.DataFrame,
    con: duckdb.DuckDBPyConnection,
    span: int = 10,
) -> pd.DataFrame:
    """Compute strictly shifted (shift(1)) EWMA features per player."""
    raw = con.execute(
        """
        SELECT game_id, player_id, game_date, min, pts, reb, ast, fga, usg_pct
        FROM player_game_logs
        ORDER BY player_id, game_date, game_id
        """
    ).fetchdf()

    if raw.empty:
        df["min_ewma_3"] = pd.Series(dtype="float64")
        for col in ["min", "pts", "reb", "ast", "fga", "usg_pct"]:
            df[f"{col}_ewma_{span}"] = pd.Series(dtype="float64")
        return df

    ewma_cols: dict[str, pd.Series] = {}
    grouped = raw.groupby("player_id", sort=False)
    ewma_cols["min_ewma_3"] = grouped["min"].transform(
        lambda s: s.shift(1).ewm(span=3, adjust=False, min_periods=1).mean()
    )
    for col in ["min", "pts", "reb", "ast", "fga", "usg_pct"]:
        ewma_cols[f"{col}_ewma_{span}"] = grouped[col].transform(
            lambda s: s.shift(1).ewm(span=span, adjust=False, min_periods=1).mean()
        )

    ewma_df = pd.concat([raw[["game_id", "player_id"]], pd.DataFrame(ewma_cols)], axis=1)
    return df.merge(ewma_df, on=["game_id", "player_id"], how="left")


def build_feature_dataframe(
    con: duckdb.DuckDBPyConnection,
    config: FeaturesConfig | None = None,
) -> pd.DataFrame:
    """Build the complete leak-free feature matrix directly from a DuckDB connection."""
    cfg = config or FeaturesConfig()
    logger.info("Computing base SQL features (fatigue, rolling, matchup, DvP)...")
    base_df = compute_base_sql_features(con)
    if base_df.empty:
        return base_df

    logger.info("Computing shifted EWMA features (span=%d)...", cfg.ewma_span)
    df = _add_shifted_ewma_features(base_df, con, span=cfg.ewma_span)

    logger.info("Computing WOWY and team absence features...")
    wowy_df = compute_wowy_features(con, config=cfg)
    df = df.merge(wowy_df, on=["game_id", "player_id"], how="left")

    df["pos_group_code"] = df["pos_group"].map(POS_GROUP_MAP).fillna(-1).astype("int64")
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    return df


def build_and_save_features(
    settings: Settings | None = None,
    config: FeaturesConfig | None = None,
    output_path: Path | None = None,
) -> tuple[pd.DataFrame, Path]:
    """Connect to DuckDB warehouse, compute all features, and save to Parquet."""
    settings = settings or Settings()
    settings.ensure_dirs()
    cfg = config or load_features_config(settings)
    out_path = output_path or (settings.processed_dir / "features.parquet")

    with duckdb.connect(str(settings.duckdb_path), read_only=True) as con:
        df = build_feature_dataframe(con, config=cfg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    logger.info("Saved %d feature rows (%d cols) to %s", len(df), len(df.columns), out_path)
    return df, out_path
