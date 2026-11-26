"""Deterministic real-time What-If ML simulator backed by DuckDB and LightGBM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from nba_stats.config import FeaturesConfig, Settings, load_features_config
from nba_stats.features.pipeline import POS_GROUP_MAP
from nba_stats.ml.explain import explain_prediction
from nba_stats.simulator.schemas import (
    ALL_STAT_KEYS,
    OpponentDefenseContext,
    ScenarioRequest,
    ShapDriver,
    SimulationResult,
    StatProjection,
    WowyContext,
)


class EntityResolutionError(ValueError):
    """Raised when a requested player or team cannot be uniquely resolved."""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass
class ResolvedPlayer:
    player_id: int
    full_name: str
    team_id: int
    team_abbreviation: str
    pos_group: str


class WhatIfSimulator:
    """Runs Base vs What-If predictions using saved two-stage LightGBM models."""

    def __init__(
        self,
        settings: Settings | None = None,
        features_config: FeaturesConfig | None = None,
        duckdb_path: Path | None = None,
        models_dir: Path | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.features_config = features_config or load_features_config(self.settings)
        self.duckdb_path = duckdb_path or self.settings.duckdb_path
        self.models_dir = models_dir or self.settings.models_dir

        meta_path = self.models_dir / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(
                f"Model metadata not found at {meta_path}. Run `nba-stats train` first."
            )
        with meta_path.open("r", encoding="utf-8") as fh:
            self.metadata: dict[str, Any] = json.load(fh)

        self.stage1_features: list[str] = list(self.metadata["stage1_features"])
        self.stage2_features: list[str] = list(self.metadata["stage2_features"])
        self.stage2_targets: list[str] = list(
            self.metadata.get("stage2_targets", ["pts", "reb", "ast"])
        )
        self.min_model: lgb.LGBMRegressor = joblib.load(self.models_dir / "min_model.joblib")
        self.stage2_models: dict[str, lgb.LGBMRegressor] = {
            t: joblib.load(self.models_dir / f"{t}_model.joblib")
            for t in self.stage2_targets
            if (self.models_dir / f"{t}_model.joblib").exists()
        }

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.duckdb_path), read_only=True)

    def resolve_player(
        self,
        con: duckdb.DuckDBPyConnection,
        name_query: str,
        team_id_hint: int | None = None,
    ) -> ResolvedPlayer:
        q = name_query.strip()
        if not q:
            raise EntityResolutionError("El nombre del jugador está vacío.")

        rows = con.execute(
            """
            WITH latest_log AS (
                SELECT
                    player_id,
                    player_name,
                    team_id,
                    team_abbreviation,
                    ROW_NUMBER() OVER (
                        PARTITION BY player_id
                        ORDER BY game_date DESC, game_id DESC
                    ) AS rn
                FROM player_game_logs
            )
            SELECT
                l.player_id,
                l.player_name,
                l.team_id,
                l.team_abbreviation,
                COALESCE(p.pos_group, 'UNK') AS pos_group
            FROM latest_log l
            LEFT JOIN players p ON l.player_id = p.player_id
            WHERE l.rn = 1
              AND strip_accents(LOWER(l.player_name)) LIKE strip_accents(LOWER(?))
            ORDER BY
                CASE WHEN strip_accents(LOWER(l.player_name)) = strip_accents(LOWER(?)) THEN 0 ELSE 1 END,
                CASE WHEN ? IS NOT NULL AND l.team_id = ? THEN 0 ELSE 1 END,
                l.player_name ASC
            LIMIT 10
            """,
            [f"%{q}%", q, team_id_hint, team_id_hint],
        ).fetchall()

        if not rows:
            raise EntityResolutionError(f"No se encontró ningún jugador que coincida con '{name_query}'.")

        exact = [r for r in rows if r[1].lower() == q.lower()]
        if len(exact) == 1:
            r = exact[0]
            return ResolvedPlayer(int(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4]))

        if team_id_hint is not None:
            same_team = [r for r in rows if int(r[2]) == team_id_hint]
            if len(same_team) == 1:
                r = same_team[0]
                return ResolvedPlayer(int(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4]))

        if len(rows) == 1:
            r = rows[0]
            return ResolvedPlayer(int(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4]))

        norm_rows = con.execute(
            """
            SELECT player_id FROM (
                SELECT DISTINCT player_id, player_name
                FROM player_game_logs
                WHERE strip_accents(LOWER(player_name)) = strip_accents(LOWER(?))
            )
            """,
            [q],
        ).fetchall()
        if len(norm_rows) == 1:
            pid = int(norm_rows[0][0])
            for r in rows:
                if int(r[0]) == pid:
                    return ResolvedPlayer(int(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4]))

        candidates = [f"{r[1]} ({r[3]})" for r in rows[:5]]
        raise EntityResolutionError(
            f"El nombre '{name_query}' es ambiguo. Candidatos: {', '.join(candidates)}",
            candidates=candidates,
        )

    def resolve_team(
        self,
        con: duckdb.DuckDBPyConnection,
        team_query: str | None,
    ) -> tuple[int | None, str]:
        if not team_query or not team_query.strip():
            return None, "AVG"

        q = team_query.strip()
        rows = con.execute(
            """
            SELECT team_id, abbreviation, full_name
            FROM teams
            WHERE LOWER(abbreviation) = LOWER(?)
               OR LOWER(full_name) LIKE LOWER(?)
               OR LOWER(nickname) LIKE LOWER(?)
               OR LOWER(city) LIKE LOWER(?)
            ORDER BY
                CASE WHEN LOWER(abbreviation) = LOWER(?) THEN 0 ELSE 1 END,
                full_name ASC
            LIMIT 5
            """,
            [q, f"%{q}%", f"%{q}%", f"%{q}%", q],
        ).fetchall()

        if not rows:
            raise EntityResolutionError(f"No se encontró el equipo rival '{team_query}'.")
        if len(rows) > 1 and rows[0][1].lower() != q.lower():
            candidates = [f"{r[2]} ({r[1]})" for r in rows]
            raise EntityResolutionError(
                f"El equipo '{team_query}' es ambiguo. Candidatos: {', '.join(candidates)}",
                candidates=candidates,
            )
        return int(rows[0][0]), str(rows[0][1])

    def _fetch_player_recent_features(
        self,
        con: duckdb.DuckDBPyConnection,
        player: ResolvedPlayer,
    ) -> dict[str, float]:
        logs = con.execute(
            """
            SELECT
                game_date, min, pts, reb, oreb, dreb, ast, stl, blk, tov, pf,
                fga, fg3a, fta, ftm, plus_minus, usg_pct, ts_pct, ast_pct, reb_pct
            FROM player_game_logs
            WHERE player_id = ?
            ORDER BY game_date DESC, game_id DESC
            LIMIT 25
            """,
            [player.player_id],
        ).fetchdf()

        if logs.empty:
            raise ValueError(f"No hay game logs históricos para {player.full_name}.")

        total_games = con.execute(
            "SELECT COUNT(*) FROM player_game_logs WHERE player_id = ?",
            [player.player_id],
        ).fetchall()[0][0]

        asc = logs.iloc[::-1].reset_index(drop=True)
        last5 = logs.head(5)
        last10 = logs.head(10)
        last20 = logs.head(20)

        min_sum_10 = float(last10["min"].sum())
        span = int(self.features_config.ewma_span)

        feats: dict[str, float] = {
            "pos_group_code": float(POS_GROUP_MAP.get(player.pos_group, -1)),
            "games_played_prior": float(total_games),
            "min_last_game": float(logs.iloc[0]["min"]),
            # Rolling 5
            "min_avg_5": float(last5["min"].mean()),
            "pts_avg_5": float(last5["pts"].mean()),
            "reb_avg_5": float(last5["reb"].mean()),
            "ast_avg_5": float(last5["ast"].mean()),
            "fga_avg_5": float(last5["fga"].mean()),
            "fg3a_avg_5": float(last5["fg3a"].mean()),
            "fta_avg_5": float(last5["fta"].mean()),
            "usg_pct_avg_5": float(last5["usg_pct"].mean()),
            "ts_pct_avg_5": float(last5["ts_pct"].mean()),
            # Rolling 10
            "min_avg_10": float(last10["min"].mean()),
            "pts_avg_10": float(last10["pts"].mean()),
            "reb_avg_10": float(last10["reb"].mean()),
            "ast_avg_10": float(last10["ast"].mean()),
            "fga_avg_10": float(last10["fga"].mean()),
            "fg3a_avg_10": float(last10["fg3a"].mean()),
            "fta_avg_10": float(last10["fta"].mean()),
            "ftm_avg_10": float(last10["ftm"].mean()),
            "oreb_avg_10": float(last10["oreb"].mean()),
            "dreb_avg_10": float(last10["dreb"].mean()),
            "stl_avg_10": float(last10["stl"].mean()),
            "blk_avg_10": float(last10["blk"].mean()),
            "tov_avg_10": float(last10["tov"].mean()),
            "pf_avg_10": float(last10["pf"].mean()),
            "plus_minus_avg_10": float(last10["plus_minus"].mean()),
            "usg_pct_avg_10": float(last10["usg_pct"].mean()),
            "ts_pct_avg_10": float(last10["ts_pct"].mean()),
            "ast_pct_avg_10": float(last10["ast_pct"].mean()),
            "reb_pct_avg_10": float(last10["reb_pct"].mean()),
            "min_std_10": float(last10["min"].std()) if len(last10) > 1 else 0.0,
            "pts_std_10": float(last10["pts"].std()) if len(last10) > 1 else 0.0,
            "reb_std_10": float(last10["reb"].std()) if len(last10) > 1 else 0.0,
            "ast_std_10": float(last10["ast"].std()) if len(last10) > 1 else 0.0,
            "pts_per36_10": 36.0 * float(last10["pts"].sum()) / min_sum_10 if min_sum_10 > 0 else 0.0,
            "reb_per36_10": 36.0 * float(last10["reb"].sum()) / min_sum_10 if min_sum_10 > 0 else 0.0,
            "ast_per36_10": 36.0 * float(last10["ast"].sum()) / min_sum_10 if min_sum_10 > 0 else 0.0,
            "fga_per36_10": 36.0 * float(last10["fga"].sum()) / min_sum_10 if min_sum_10 > 0 else 0.0,
            # Rolling 20
            "min_avg_20": float(last20["min"].mean()),
            "pts_avg_20": float(last20["pts"].mean()),
            "reb_avg_20": float(last20["reb"].mean()),
            "ast_avg_20": float(last20["ast"].mean()),
            "fga_avg_20": float(last20["fga"].mean()),
            "usg_pct_avg_20": float(last20["usg_pct"].mean()),
            "ts_pct_avg_20": float(last20["ts_pct"].mean()),
        }
        feats["min_trend_5_20"] = feats["min_avg_5"] - feats["min_avg_20"]
        feats["pts_trend_5_20"] = feats["pts_avg_5"] - feats["pts_avg_20"]
        feats["reb_trend_5_20"] = feats["reb_avg_5"] - feats["reb_avg_20"]
        feats["ast_trend_5_20"] = feats["ast_avg_5"] - feats["ast_avg_20"]

        for col in ["min", "pts", "reb", "ast", "fga", "usg_pct"]:
            feats[f"{col}_ewma_{span}"] = float(
                asc[col].ewm(span=span, adjust=False, min_periods=1).mean().iloc[-1]
            )

        return feats

    def _fetch_matchup_features(
        self,
        con: duckdb.DuckDBPyConnection,
        team_id: int,
        opp_team_id: int | None,
        opp_abbr: str,
        pos_group: str,
    ) -> tuple[dict[str, float], OpponentDefenseContext]:
        team_row = con.execute(
            """
            SELECT
                AVG(pace),
                AVG(off_rating),
                AVG(def_rating),
                AVG(COALESCE(net_rating, off_rating - def_rating))
            FROM (
                SELECT pace, off_rating, def_rating, net_rating
                FROM team_game_logs
                WHERE team_id = ?
                ORDER BY game_date DESC, game_id DESC
                LIMIT 10
            )
            """,
            [team_id],
        ).fetchall()[0]

        def_rank: int | None = None
        pace_rank: int | None = None

        if opp_team_id is not None:
            opp_row = con.execute(
                """
                SELECT
                    AVG(t.pace),
                    AVG(t.off_rating),
                    AVG(t.def_rating),
                    AVG(COALESCE(t.net_rating, t.off_rating - t.def_rating)),
                    AVG(o.pts),
                    AVG(o.reb),
                    AVG(o.ast),
                    AVG(o.fta),
                    AVG(o.efg_pct),
                    AVG(o.fg3a),
                    AVG(o.tov),
                    AVG(o.oreb)
                FROM (
                    SELECT game_id, opp_team_id, pace, off_rating, def_rating, net_rating
                    FROM team_game_logs
                    WHERE team_id = ?
                    ORDER BY game_date DESC, game_id DESC
                    LIMIT 10
                ) t
                LEFT JOIN team_game_logs o
                  ON t.game_id = o.game_id AND t.opp_team_id = o.team_id
                """,
                [opp_team_id],
            ).fetchall()[0]

            rank_rows = con.execute(
                """
                WITH recent_team AS (
                    SELECT
                        team_id,
                        def_rating,
                        pace,
                        ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY game_date DESC, game_id DESC) AS rn
                    FROM team_game_logs
                ),
                team_avgs AS (
                    SELECT
                        team_id,
                        AVG(def_rating) AS avg_def,
                        AVG(pace) AS avg_pace
                    FROM recent_team
                    WHERE rn <= 10
                    GROUP BY team_id
                ),
                ranked AS (
                    SELECT
                        team_id,
                        RANK() OVER (ORDER BY avg_def ASC) AS def_rank,
                        RANK() OVER (ORDER BY avg_pace DESC) AS pace_rank
                    FROM team_avgs
                )
                SELECT def_rank, pace_rank
                FROM ranked
                WHERE team_id = ?
                """,
                [opp_team_id],
            ).fetchall()
            if rank_rows:
                def_rank = int(rank_rows[0][0])
                pace_rank = int(rank_rows[0][1])

            dvp_row = con.execute(
                """
                SELECT AVG(pos_pts), AVG(pos_reb), AVG(pos_ast)
                FROM (
                    SELECT p.game_id, SUM(p.pts) AS pos_pts, SUM(p.reb) AS pos_reb, SUM(p.ast) AS pos_ast
                    FROM player_game_logs p
                    LEFT JOIN players pl ON p.player_id = pl.player_id
                    WHERE p.opp_team_id = ? AND COALESCE(pl.pos_group, 'UNK') = ?
                    GROUP BY p.game_id, p.game_date
                    ORDER BY p.game_date DESC, p.game_id DESC
                    LIMIT 10
                )
                """,
                [opp_team_id, pos_group],
            ).fetchall()[0]
        else:
            opp_row = con.execute(
                """
                SELECT
                    AVG(pace),
                    AVG(off_rating),
                    AVG(def_rating),
                    AVG(COALESCE(net_rating, off_rating - def_rating)),
                    AVG(pts),
                    AVG(reb),
                    AVG(ast),
                    AVG(fta),
                    AVG(efg_pct),
                    AVG(fg3a),
                    AVG(tov),
                    AVG(oreb)
                FROM team_game_logs
                """
            ).fetchall()[0]
            dvp_row = (np.nan, np.nan, np.nan)

        team_pace = float(team_row[0]) if team_row[0] is not None else 99.0
        team_off = float(team_row[1]) if team_row[1] is not None else 113.0
        team_def = float(team_row[2]) if team_row[2] is not None else 113.0
        team_net = float(team_row[3]) if team_row[3] is not None else (team_off - team_def)

        opp_pace = float(opp_row[0]) if opp_row[0] is not None else 99.0
        opp_off = float(opp_row[1]) if opp_row[1] is not None else 113.0
        opp_def = float(opp_row[2]) if opp_row[2] is not None else 113.0
        opp_net = float(opp_row[3]) if opp_row[3] is not None else (opp_off - opp_def)
        margin_proxy = team_net - opp_net

        opp_pts_allowed = float(opp_row[4]) if opp_row[4] is not None else 113.0
        opp_reb_allowed = float(opp_row[5]) if opp_row[5] is not None else 44.0
        opp_ast_allowed = float(opp_row[6]) if opp_row[6] is not None else 26.0
        opp_fta_allowed = float(opp_row[7]) if opp_row[7] is not None else 22.0
        opp_efg_allowed = float(opp_row[8]) if opp_row[8] is not None else 0.54
        opp_fg3a_allowed = float(opp_row[9]) if opp_row[9] is not None else 36.0
        opp_tov_forced = float(opp_row[10]) if opp_row[10] is not None else 14.0
        opp_oreb_allowed = float(opp_row[11]) if opp_row[11] is not None else 10.5
        dvp_pts = float(dvp_row[0]) if dvp_row[0] is not None and not pd.isna(dvp_row[0]) else None

        feats = {
            "team_pace_10": team_pace,
            "team_off_rating_10": team_off,
            "team_def_rating_10": team_def,
            "team_net_rating_10": team_net,
            "opp_pace_10": opp_pace,
            "opp_off_rating_10": opp_off,
            "opp_def_rating_10": opp_def,
            "opp_net_rating_10": opp_net,
            "expected_margin_proxy": margin_proxy,
            "abs_expected_margin": abs(margin_proxy),
            "expected_game_pace": (team_pace + opp_pace) / 2.0,
            "opp_pts_allowed_10": opp_pts_allowed,
            "opp_reb_allowed_10": opp_reb_allowed,
            "opp_ast_allowed_10": opp_ast_allowed,
            "opp_fta_allowed_10": opp_fta_allowed,
            "opp_efg_allowed_10": opp_efg_allowed,
            "opp_fg3a_allowed_10": opp_fg3a_allowed,
            "opp_tov_forced_10": opp_tov_forced,
            "opp_oreb_allowed_10": opp_oreb_allowed,
            "opp_dvp_pts_10": dvp_pts if dvp_pts is not None else np.nan,
            "opp_dvp_reb_10": float(dvp_row[1]) if dvp_row[1] is not None else np.nan,
            "opp_dvp_ast_10": float(dvp_row[2]) if dvp_row[2] is not None else np.nan,
        }

        opp_ctx = OpponentDefenseContext(
            team_abbreviation=opp_abbr,
            def_rating_10=round(opp_def, 1),
            def_rating_rank=def_rank,
            pace_10=round(opp_pace, 1),
            pace_rank=pace_rank,
            pts_allowed_10=round(opp_pts_allowed, 1),
            reb_allowed_10=round(opp_reb_allowed, 1),
            ast_allowed_10=round(opp_ast_allowed, 1),
            efg_allowed_10=round(opp_efg_allowed, 3),
            fg3a_allowed_10=round(opp_fg3a_allowed, 1),
            tov_forced_10=round(opp_tov_forced, 1),
            dvp_pts_10=round(dvp_pts, 1) if dvp_pts is not None else None,
            net_rating_diff=round(margin_proxy, 1),
        )
        return feats, opp_ctx

    def _compute_wowy_override(
        self,
        con: duckdb.DuckDBPyConnection,
        player: ResolvedPlayer,
        out_player_names: list[str],
    ) -> tuple[dict[str, float], list[str], list[str], WowyContext | None]:
        overrides: dict[str, float] = {
            "missing_team_minutes": 0.0,
            "missing_team_usage_pct": 0.0,
            "star_teammate_out": 0.0,
            "num_rotation_players_out": 0.0,
            "missing_same_pos_minutes": 0.0,
            "missing_same_pos_usage": 0.0,
            "wowy_sample_games": 0.0,
            "wowy_delta_min": 0.0,
            "wowy_delta_usg": 0.0,
            "wowy_delta_pts_p36": 0.0,
            "wowy_delta_reb_p36": 0.0,
            "wowy_delta_ast_p36": 0.0,
        }
        resolved_out: list[ResolvedPlayer] = []
        unresolved: list[str] = []

        for raw_name in out_player_names:
            try:
                rp = self.resolve_player(con, raw_name, team_id_hint=player.team_id)
                if rp.player_id != player.player_id:
                    resolved_out.append(rp)
            except EntityResolutionError:
                unresolved.append(raw_name)

        if not resolved_out:
            return overrides, [], unresolved, None

        star_usg = float(self.features_config.rotation.star_usg_threshold)
        star_min = float(self.features_config.rotation.star_min_threshold)
        shrinkage_k = float(self.features_config.wowy.shrinkage_k)

        out_profiles: list[tuple[ResolvedPlayer, float, float]] = []
        for rp in resolved_out:
            row = con.execute(
                """
                SELECT AVG(min), AVG(COALESCE(usg_pct, 0.18))
                FROM (
                    SELECT min, usg_pct
                    FROM player_game_logs
                    WHERE player_id = ?
                    ORDER BY game_date DESC, game_id DESC
                    LIMIT 15
                )
                """,
                [rp.player_id],
            ).fetchall()[0]
            avg_min = float(row[0]) if row[0] is not None else 0.0
            avg_usg = float(row[1]) if row[1] is not None else 0.18
            out_profiles.append((rp, avg_min, avg_usg))

            overrides["missing_team_minutes"] += avg_min
            overrides["missing_team_usage_pct"] += (avg_usg * avg_min) / 240.0
            overrides["num_rotation_players_out"] += 1.0
            if avg_usg >= star_usg or avg_min >= star_min:
                overrides["star_teammate_out"] = 1.0
            if rp.pos_group == player.pos_group and player.pos_group != "UNK":
                overrides["missing_same_pos_minutes"] += avg_min
                overrides["missing_same_pos_usage"] += (avg_usg * avg_min) / 240.0

        primary_rp, _, _ = max(out_profiles, key=lambda x: x[1] * x[2])

        wowy_row = con.execute(
            """
            WITH played_star AS (
                SELECT DISTINCT game_id
                FROM player_game_logs
                WHERE player_id = ? AND team_id = ? AND COALESCE(min, 0) > 0
            ),
            hist AS (
                SELECT
                    CASE WHEN ps.game_id IS NULL THEN 1 ELSE 0 END AS star_out,
                    p.min,
                    p.pts,
                    p.reb,
                    p.ast,
                    COALESCE(p.usg_pct, 0.18) AS usg_pct,
                    CASE WHEN p.min > 0 THEN 36.0 * p.pts / p.min END AS pts_p36,
                    CASE WHEN p.min > 0 THEN 36.0 * p.reb / p.min END AS reb_p36,
                    CASE WHEN p.min > 0 THEN 36.0 * p.ast / p.min END AS ast_p36
                FROM player_game_logs p
                LEFT JOIN played_star ps ON p.game_id = ps.game_id
                WHERE p.player_id = ? AND p.team_id = ?
            )
            SELECT
                SUM(CASE WHEN star_out = 1 THEN 1 ELSE 0 END) AS n_out,
                SUM(CASE WHEN star_out = 0 THEN 1 ELSE 0 END) AS n_in,
                AVG(CASE WHEN star_out = 1 THEN min END) AS min_out,
                AVG(CASE WHEN star_out = 0 THEN min END) AS min_in,
                AVG(CASE WHEN star_out = 1 THEN pts END) AS pts_out,
                AVG(CASE WHEN star_out = 0 THEN pts END) AS pts_in,
                AVG(CASE WHEN star_out = 1 THEN reb END) AS reb_out,
                AVG(CASE WHEN star_out = 0 THEN reb END) AS reb_in,
                AVG(CASE WHEN star_out = 1 THEN ast END) AS ast_out,
                AVG(CASE WHEN star_out = 0 THEN ast END) AS ast_in,
                AVG(CASE WHEN star_out = 1 THEN usg_pct END) AS usg_out,
                AVG(CASE WHEN star_out = 0 THEN usg_pct END) AS usg_in,
                AVG(CASE WHEN star_out = 1 THEN pts_p36 END) AS pts_p36_out,
                AVG(CASE WHEN star_out = 0 THEN pts_p36 END) AS pts_p36_in,
                AVG(CASE WHEN star_out = 1 THEN reb_p36 END) AS reb_p36_out,
                AVG(CASE WHEN star_out = 0 THEN reb_p36 END) AS reb_p36_in,
                AVG(CASE WHEN star_out = 1 THEN ast_p36 END) AS ast_p36_out,
                AVG(CASE WHEN star_out = 0 THEN ast_p36 END) AS ast_p36_in
            FROM hist
            """,
            [primary_rp.player_id, player.team_id, player.player_id, player.team_id],
        ).fetchall()[0]

        n_out = int(wowy_row[0] or 0)
        n_in = int(wowy_row[1] or 0)
        wowy_ctx: WowyContext | None = None
        if n_out > 0 and n_in > 0:
            shrink = n_out / (n_out + shrinkage_k)
            raw_d_min = float(wowy_row[2] - wowy_row[3])
            raw_d_pts = float(wowy_row[4] - wowy_row[5])
            raw_d_reb = float(wowy_row[6] - wowy_row[7])
            raw_d_ast = float(wowy_row[8] - wowy_row[9])
            d_usg = float(wowy_row[10] - wowy_row[11])
            d_pts_p36 = float(wowy_row[12] - wowy_row[13])
            d_reb_p36 = float(wowy_row[14] - wowy_row[15])
            d_ast_p36 = float(wowy_row[16] - wowy_row[17])

            overrides["wowy_sample_games"] = float(n_out)
            overrides["wowy_delta_min"] = shrink * raw_d_min
            overrides["wowy_delta_usg"] = shrink * d_usg
            overrides["wowy_delta_pts_p36"] = shrink * d_pts_p36
            overrides["wowy_delta_reb_p36"] = shrink * d_reb_p36
            overrides["wowy_delta_ast_p36"] = shrink * d_ast_p36

            wowy_ctx = WowyContext(
                primary_out_player=primary_rp.full_name,
                sample_games_without=n_out,
                sample_games_with=n_in,
                raw_delta_min=round(raw_d_min, 2),
                raw_delta_pts=round(raw_d_pts, 2),
                raw_delta_reb=round(raw_d_reb, 2),
                raw_delta_ast=round(raw_d_ast, 2),
                shrunk_delta_min=round(overrides["wowy_delta_min"], 2),
                shrunk_delta_pts_p36=round(overrides["wowy_delta_pts_p36"], 2),
            )

        return overrides, [rp.full_name for rp in resolved_out], unresolved, wowy_ctx

    def _compute_opponent_out_override(
        self,
        con: duckdb.DuckDBPyConnection,
        player: ResolvedPlayer,
        opp_team_id: int | None,
        opp_out_names: list[str],
    ) -> tuple[dict[str, float], list[str], list[str]]:
        overrides: dict[str, float] = {
            "opp_missing_team_minutes": 0.0,
            "opp_missing_team_usage_pct": 0.0,
            "opp_star_out": 0.0,
            "opp_missing_center_minutes": 0.0,
            "opp_missing_def_stocks": 0.0,
            "opp_missing_same_pos_minutes": 0.0,
        }
        if not opp_out_names:
            return overrides, [], []

        star_usg = float(self.features_config.rotation.star_usg_threshold)
        star_min = float(self.features_config.rotation.star_min_threshold)

        resolved: list[str] = []
        unresolved: list[str] = []
        for raw_name in opp_out_names:
            try:
                rp = self.resolve_player(con, raw_name, team_id_hint=opp_team_id)
            except EntityResolutionError:
                unresolved.append(raw_name)
                continue

            row = con.execute(
                """
                SELECT AVG(min), AVG(COALESCE(usg_pct, 0.18)), AVG(COALESCE(stl, 0.0) + COALESCE(blk, 0.0))
                FROM (
                    SELECT min, usg_pct, stl, blk
                    FROM player_game_logs
                    WHERE player_id = ?
                    ORDER BY game_date DESC, game_id DESC
                    LIMIT 15
                )
                """,
                [rp.player_id],
            ).fetchall()[0]
            avg_min = float(row[0]) if row[0] is not None else 0.0
            avg_usg = float(row[1]) if row[1] is not None else 0.18
            avg_stocks = float(row[2]) if row[2] is not None else 0.0

            resolved.append(rp.full_name)
            overrides["opp_missing_team_minutes"] += avg_min
            overrides["opp_missing_team_usage_pct"] += (avg_usg * avg_min) / 240.0
            overrides["opp_missing_def_stocks"] += avg_stocks
            if avg_usg >= star_usg or avg_min >= star_min:
                overrides["opp_star_out"] = 1.0
            if rp.pos_group == "C":
                overrides["opp_missing_center_minutes"] += avg_min
            if rp.pos_group == player.pos_group and player.pos_group != "UNK":
                overrides["opp_missing_same_pos_minutes"] += avg_min

        return overrides, resolved, unresolved

    def _predict_row(
        self,
        row_dict: dict[str, float],
        with_shap: bool = False,
    ) -> tuple[StatProjection, dict[str, list[ShapDriver]]]:
        x1 = pd.DataFrame([{c: row_dict.get(c, np.nan) for c in self.stage1_features}])
        pred_min = float(np.clip(self.min_model.predict(x1)[0], 0.0, 48.0))

        x2 = x1.copy()
        x2["pred_min"] = pred_min
        x2 = x2[self.stage2_features]

        preds: dict[str, float] = {"min": round(pred_min, 2)}
        for target in ["pts", "reb", "ast", "stl", "blk", "tov", "pf", "fga", "ftm"]:
            model = self.stage2_models.get(target)
            if model is not None:
                val = float(np.clip(model.predict(x2)[0], 0.0, None))
                preds[target] = round(val, 2)
            else:
                preds[target] = 0.0

        preds["pra"] = round(preds["pts"] + preds["reb"] + preds["ast"], 2)
        preds["pr"] = round(preds["pts"] + preds["reb"], 2)
        preds["pa"] = round(preds["pts"] + preds["ast"], 2)

        drivers: dict[str, list[ShapDriver]] = {}
        if with_shap:
            drivers["min"] = [
                ShapDriver(**d) for d in explain_prediction(self.min_model, x1, top_k=5)
            ]
            for target, model in self.stage2_models.items():
                drivers[target] = [
                    ShapDriver(**d) for d in explain_prediction(model, x2, top_k=5)
                ]

        return StatProjection(**preds), drivers

    def simulate(self, request: ScenarioRequest) -> SimulationResult:
        """Run Base (rested, healthy teams) vs What-If (B2B / team OUT / opponent OUT) simulation."""
        with self._connect() as con:
            player = self.resolve_player(con, request.player_name)
            opp_team_id, opp_abbr = self.resolve_team(con, request.opponent_team)

            player_feats = self._fetch_player_recent_features(con, player)
            matchup_feats, opp_def_ctx = self._fetch_matchup_features(
                con,
                team_id=player.team_id,
                opp_team_id=opp_team_id,
                opp_abbr=opp_abbr,
                pos_group=player.pos_group,
            )
            wowy_overrides, resolved_out, unresolved_out, wowy_ctx = self._compute_wowy_override(
                con,
                player=player,
                out_player_names=request.out_players,
            )
            opp_overrides, opp_resolved_out, opp_unresolved_out = self._compute_opponent_out_override(
                con,
                player=player,
                opp_team_id=opp_team_id,
                opp_out_names=request.opponent_out_players,
            )

        base_row: dict[str, float] = {
            **player_feats,
            **matchup_feats,
            "is_home": 1.0 if request.is_home else 0.0,
            "rest_days": 1.0,
            "is_back_to_back": 0.0,
            "player_rest_days": 1.0,
            "games_in_last_7_days": 3.0,
            "games_in_last_14_days": 6.0,
            "road_trip_game_num": 0.0 if request.is_home else 1.0,
            "missing_team_minutes": 0.0,
            "missing_team_usage_pct": 0.0,
            "star_teammate_out": 0.0,
            "num_rotation_players_out": 0.0,
            "missing_same_pos_minutes": 0.0,
            "missing_same_pos_usage": 0.0,
            "opp_missing_team_minutes": 0.0,
            "opp_missing_team_usage_pct": 0.0,
            "opp_star_out": 0.0,
            "opp_missing_center_minutes": 0.0,
            "opp_missing_def_stocks": 0.0,
            "opp_missing_same_pos_minutes": 0.0,
            "wowy_sample_games": 0.0,
            "wowy_delta_min": 0.0,
            "wowy_delta_usg": 0.0,
            "wowy_delta_pts_p36": 0.0,
            "wowy_delta_reb_p36": 0.0,
            "wowy_delta_ast_p36": 0.0,
        }

        effective_rest = 0 if request.is_back_to_back else (
            request.rest_days if request.rest_days is not None else 1
        )
        scenario_row = dict(base_row)
        scenario_row.update(wowy_overrides)
        scenario_row.update(opp_overrides)
        scenario_row["is_back_to_back"] = 1.0 if request.is_back_to_back else 0.0
        scenario_row["rest_days"] = float(min(3, effective_rest))
        scenario_row["player_rest_days"] = float(effective_rest)
        if request.is_back_to_back:
            scenario_row["games_in_last_7_days"] = 4.0

        base_proj, _ = self._predict_row(base_row, with_shap=False)
        scen_proj, shap_drivers = self._predict_row(scenario_row, with_shap=True)

        deltas = StatProjection(
            **{
                k: round(getattr(scen_proj, k) - getattr(base_proj, k), 2)
                for k in ALL_STAT_KEYS
            }
        )

        opp_def_ctx.opp_missing_minutes = round(opp_overrides["opp_missing_team_minutes"], 1)
        opp_def_ctx.opp_missing_def_stocks = round(opp_overrides["opp_missing_def_stocks"], 2)
        opp_def_ctx.opp_star_out = bool(opp_overrides["opp_star_out"] >= 1.0)

        return SimulationResult(
            player_id=player.player_id,
            player_name=player.full_name,
            team_id=player.team_id,
            team_abbreviation=player.team_abbreviation,
            opponent_abbreviation=opp_abbr,
            is_home=request.is_home,
            is_back_to_back=request.is_back_to_back,
            rest_days=effective_rest,
            out_players_resolved=resolved_out,
            out_players_unresolved=unresolved_out,
            opp_out_players_resolved=opp_resolved_out,
            opp_out_players_unresolved=opp_unresolved_out,
            missing_team_minutes=round(wowy_overrides["missing_team_minutes"], 1),
            missing_team_usage_pct=round(wowy_overrides["missing_team_usage_pct"], 4),
            star_teammate_out=bool(wowy_overrides["star_teammate_out"] >= 1.0),
            base_projection=base_proj,
            scenario_projection=scen_proj,
            deltas=deltas,
            shap_drivers=shap_drivers,
            wowy_context=wowy_ctx,
            opponent_defense=opp_def_ctx,
        )

    def _fast_top_drivers(
        self,
        model: lgb.LGBMRegressor,
        x_row: pd.DataFrame,
        top_k: int = 3,
    ) -> list[ShapDriver]:
        """Exact LightGBM native TreeSHAP via pred_contrib=True (~1ms per row)."""
        contrib = np.asarray(model.predict(x_row, pred_contrib=True)).reshape(-1)[:-1]
        feature_names = list(x_row.columns)
        values = x_row.iloc[0].to_dict()
        order = np.argsort(np.abs(contrib))[::-1][:top_k]
        drivers: list[ShapDriver] = []
        for idx in order:
            fname = feature_names[int(idx)]
            sv = float(contrib[int(idx)])
            raw_val = values[fname]
            val = None if pd.isna(raw_val) else round(float(raw_val), 4)
            drivers.append(
                ShapDriver(
                    feature=fname,
                    value=val,
                    shap_value=round(sv, 4),
                    direction="+" if sv >= 0 else "-",
                )
            )
        return drivers

    def simulate_team(
        self,
        team_id: int,
        opponent_team_id: int | None = None,
        out_player_ids: list[int] | None = None,
        is_back_to_back: bool = False,
        is_home: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Fast batch boxscore simulation for a team's top rotation players (cost $0 LLM)."""
        out_id_set = set(out_player_ids or [])

        with self._connect() as con:
            team_rows = con.execute(
                "SELECT abbreviation FROM teams WHERE team_id = ?",
                [team_id],
            ).fetchall()
            if not team_rows:
                raise EntityResolutionError(f"No se encontró el equipo con team_id={team_id}.")
            team_abbr = str(team_rows[0][0])

            opp_abbr = "AVG"
            if opponent_team_id is not None:
                opp_rows = con.execute(
                    "SELECT abbreviation FROM teams WHERE team_id = ?",
                    [opponent_team_id],
                ).fetchall()
                if not opp_rows:
                    raise EntityResolutionError(
                        f"No se encontró el equipo rival con team_id={opponent_team_id}."
                    )
                opp_abbr = str(opp_rows[0][0])

            roster_rows = con.execute(
                """
                WITH latest_player_team AS (
                    SELECT
                        player_id,
                        player_name,
                        team_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY player_id
                            ORDER BY game_date DESC, game_id DESC
                        ) AS rn
                    FROM player_game_logs
                ),
                active_on_team AS (
                    SELECT player_id, player_name
                    FROM latest_player_team
                    WHERE rn = 1 AND team_id = ?
                ),
                recent_logs AS (
                    SELECT
                        p.player_id,
                        a.player_name,
                        p.min,
                        ROW_NUMBER() OVER (
                            PARTITION BY p.player_id
                            ORDER BY p.game_date DESC, p.game_id DESC
                        ) AS g_rn
                    FROM player_game_logs p
                    JOIN active_on_team a
                      ON p.player_id = a.player_id
                     AND p.team_id = ?
                ),
                weighted AS (
                    SELECT
                        player_id,
                        player_name,
                        SUM(min * POWER(0.85, g_rn - 1)) / NULLIF(SUM(POWER(0.85, g_rn - 1)), 0) AS ewma_min
                    FROM recent_logs
                    WHERE g_rn <= 15
                    GROUP BY player_id, player_name
                )
                SELECT
                    w.player_id,
                    w.player_name,
                    COALESCE(pl.pos_group, 'UNK') AS pos_group,
                    COALESCE(w.ewma_min, 0.0) AS ewma_min
                FROM weighted w
                LEFT JOIN players pl ON w.player_id = pl.player_id
                ORDER BY w.ewma_min DESC
                LIMIT ?
                """,
                [team_id, team_id, limit],
            ).fetchall()

            # Map out_player_ids to their names
            out_names: list[str] = [
                str(r[1]) for r in roster_rows if int(r[0]) in out_id_set
            ]

            # Cache matchup features per pos_group across the rotation (max 4 SQL lookups)
            matchup_cache: dict[str, tuple[dict[str, float], OpponentDefenseContext]] = {}
            team_opp_defense: OpponentDefenseContext | None = None
            result_rows: list[dict[str, Any]] = []

            for r in roster_rows:
                pid = int(r[0])
                pname = str(r[1])
                pgroup = str(r[2])
                ewma_min = round(float(r[3]), 1)

                if pid in out_id_set:
                    result_rows.append(
                        {
                            "player_id": pid,
                            "player_name": pname,
                            "pos_group": pgroup,
                            "ewma_min": ewma_min,
                            "is_out": True,
                            "base": None,
                            "scenario": None,
                            "deltas": None,
                            "top_drivers": [],
                            "wowy": None,
                        }
                    )
                    continue

                rp = ResolvedPlayer(
                    player_id=pid,
                    full_name=pname,
                    team_id=team_id,
                    team_abbreviation=team_abbr,
                    pos_group=pgroup,
                )
                if pgroup not in matchup_cache:
                    matchup_cache[pgroup] = self._fetch_matchup_features(
                        con,
                        team_id=team_id,
                        opp_team_id=opponent_team_id,
                        opp_abbr=opp_abbr,
                        pos_group=pgroup,
                    )
                matchup_feats, opp_def_ctx = matchup_cache[pgroup]
                if team_opp_defense is None:
                    team_opp_defense = opp_def_ctx

                player_feats = self._fetch_player_recent_features(con, rp)
                wowy_overrides, _, _, wowy_ctx = self._compute_wowy_override(
                    con,
                    player=rp,
                    out_player_names=out_names,
                )

                base_row: dict[str, float] = {
                    **player_feats,
                    **matchup_feats,
                    "is_home": 1.0 if is_home else 0.0,
                    "rest_days": 1.0,
                    "is_back_to_back": 0.0,
                    "player_rest_days": 1.0,
                    "games_in_last_7_days": 3.0,
                    "games_in_last_14_days": 6.0,
                    "road_trip_game_num": 0.0 if is_home else 1.0,
                    "missing_team_minutes": 0.0,
                    "missing_team_usage_pct": 0.0,
                    "star_teammate_out": 0.0,
                    "num_rotation_players_out": 0.0,
                    "missing_same_pos_minutes": 0.0,
                    "missing_same_pos_usage": 0.0,
                    "opp_missing_team_minutes": 0.0,
                    "opp_missing_team_usage_pct": 0.0,
                    "opp_star_out": 0.0,
                    "opp_missing_center_minutes": 0.0,
                    "opp_missing_def_stocks": 0.0,
                    "opp_missing_same_pos_minutes": 0.0,
                    "wowy_sample_games": 0.0,
                    "wowy_delta_min": 0.0,
                    "wowy_delta_usg": 0.0,
                    "wowy_delta_pts_p36": 0.0,
                    "wowy_delta_reb_p36": 0.0,
                    "wowy_delta_ast_p36": 0.0,
                }

                scenario_row = dict(base_row)
                scenario_row.update(wowy_overrides)
                scenario_row["is_back_to_back"] = 1.0 if is_back_to_back else 0.0
                scenario_row["rest_days"] = 0.0 if is_back_to_back else 1.0
                scenario_row["player_rest_days"] = 0.0 if is_back_to_back else 1.0
                if is_back_to_back:
                    scenario_row["games_in_last_7_days"] = 4.0

                base_proj, _ = self._predict_row(base_row, with_shap=False)
                scen_proj, _ = self._predict_row(scenario_row, with_shap=False)

                # Fast native TreeSHAP top-3 drivers on PTS model
                x1 = pd.DataFrame([{c: scenario_row.get(c, np.nan) for c in self.stage1_features}])
                x2 = x1.copy()
                x2["pred_min"] = scen_proj.min
                x2 = x2[self.stage2_features]
                pts_model = self.stage2_models.get("pts", self.min_model)
                top_drivers = self._fast_top_drivers(pts_model, x2, top_k=3)

                deltas = StatProjection(
                    **{
                        k: round(getattr(scen_proj, k) - getattr(base_proj, k), 2)
                        for k in ALL_STAT_KEYS
                    }
                )
                result_rows.append(
                    {
                        "player_id": pid,
                        "player_name": pname,
                        "pos_group": pgroup,
                        "ewma_min": ewma_min,
                        "is_out": False,
                        "base": base_proj,
                        "scenario": scen_proj,
                        "deltas": deltas,
                        "top_drivers": top_drivers,
                        "wowy": wowy_ctx,
                    }
                )

        return {
            "team_id": team_id,
            "team_abbreviation": team_abbr,
            "opponent_team_id": opponent_team_id,
            "opponent_abbreviation": opp_abbr,
            "is_home": is_home,
            "is_back_to_back": is_back_to_back,
            "opponent_defense": team_opp_defense,
            "rows": result_rows,
        }
