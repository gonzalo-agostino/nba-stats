"""Anti-leakage WOWY (With Or Without You), own-team absences, and opponent absences in DuckDB SQL."""

from __future__ import annotations

import duckdb
import pandas as pd

from nba_stats.config import FeaturesConfig


def compute_wowy_features(
    con: duckdb.DuckDBPyConnection,
    config: FeaturesConfig | None = None,
) -> pd.DataFrame:
    """Compute own-team absences, opponent absences, and shrunken WOWY deltas strictly prior to game_date."""
    cfg = config or FeaturesConfig()
    min_rot = float(cfg.rotation.min_avg_minutes)
    star_usg = float(cfg.rotation.star_usg_threshold)
    star_min = float(cfg.rotation.star_min_threshold)
    shrinkage_k = float(cfg.wowy.shrinkage_k)

    query = f"""
    WITH player_rolling_15 AS (
        SELECT
            p.player_id,
            p.team_id,
            p.season,
            p.game_id,
            p.game_date,
            COALESCE(pl.pos_group, 'UNK') AS pos_group,
            AVG(p.min) OVER (
                PARTITION BY p.player_id
                ORDER BY p.game_date, p.game_id
                ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
            ) AS avg_min_15,
            AVG(COALESCE(p.usg_pct, 0.18)) OVER (
                PARTITION BY p.player_id
                ORDER BY p.game_date, p.game_id
                ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
            ) AS avg_usg_15,
            AVG(COALESCE(p.stl, 0.0) + COALESCE(p.blk, 0.0)) OVER (
                PARTITION BY p.player_id
                ORDER BY p.game_date, p.game_id
                ROWS BETWEEN 14 PRECEDING AND CURRENT ROW
            ) AS avg_stocks_15
        FROM player_game_logs p
        LEFT JOIN players pl ON p.player_id = pl.player_id
    ),
    team_games AS (
        SELECT DISTINCT game_id, team_id, season, game_date
        FROM team_game_logs
    ),
    prior_candidates AS (
        SELECT
            tg.game_id,
            tg.team_id,
            tg.season,
            tg.game_date,
            pr.player_id,
            pr.pos_group,
            pr.avg_min_15,
            pr.avg_usg_15,
            pr.avg_stocks_15,
            ROW_NUMBER() OVER (
                PARTITION BY tg.game_id, tg.team_id, pr.player_id
                ORDER BY pr.game_date DESC, pr.game_id DESC
            ) AS rn,
            pr.team_id AS last_team_id
        FROM team_games tg
        JOIN player_rolling_15 pr
          ON pr.season = tg.season
         AND pr.game_date < tg.game_date
    ),
    expected_rotation AS (
        SELECT
            game_id,
            team_id,
            season,
            game_date,
            player_id,
            pos_group,
            avg_min_15,
            avg_usg_15,
            avg_stocks_15
        FROM prior_candidates
        WHERE rn = 1
          AND last_team_id = team_id
          AND avg_min_15 >= {min_rot}
    ),
    played_in_game AS (
        SELECT DISTINCT game_id, team_id, player_id
        FROM player_game_logs
        WHERE COALESCE(min, 0) > 0
    ),
    missing_players AS (
        SELECT
            er.game_id,
            er.team_id,
            er.season,
            er.game_date,
            er.player_id AS missing_player_id,
            er.pos_group AS missing_pos_group,
            er.avg_min_15 AS missing_min,
            er.avg_usg_15 AS missing_usg,
            er.avg_stocks_15 AS missing_stocks,
            CASE
                WHEN er.avg_usg_15 >= {star_usg} OR er.avg_min_15 >= {star_min} THEN 1
                ELSE 0
            END AS is_star
        FROM expected_rotation er
        LEFT JOIN played_in_game pg
          ON er.game_id = pg.game_id
         AND er.team_id = pg.team_id
         AND er.player_id = pg.player_id
        WHERE pg.player_id IS NULL
    ),
    team_missing_agg AS (
        SELECT
            game_id,
            team_id,
            SUM(missing_min) AS missing_team_minutes,
            SUM(missing_usg * missing_min) / 240.0 AS missing_team_usage_pct,
            SUM(missing_stocks) AS missing_def_stocks,
            MAX(is_star) AS star_teammate_out,
            COUNT(*) AS num_rotation_players_out,
            SUM(CASE WHEN missing_pos_group = 'G' THEN missing_min ELSE 0.0 END) AS missing_g_min,
            SUM(CASE WHEN missing_pos_group = 'F' THEN missing_min ELSE 0.0 END) AS missing_f_min,
            SUM(CASE WHEN missing_pos_group = 'C' THEN missing_min ELSE 0.0 END) AS missing_c_min,
            SUM(CASE WHEN missing_pos_group = 'G' THEN missing_usg * missing_min / 240.0 ELSE 0.0 END) AS missing_g_usg,
            SUM(CASE WHEN missing_pos_group = 'F' THEN missing_usg * missing_min / 240.0 ELSE 0.0 END) AS missing_f_usg,
            SUM(CASE WHEN missing_pos_group = 'C' THEN missing_usg * missing_min / 240.0 ELSE 0.0 END) AS missing_c_usg
        FROM missing_players
        GROUP BY game_id, team_id
    ),
    primary_missing_star AS (
        SELECT
            game_id,
            team_id,
            missing_player_id AS primary_missing_star_id
        FROM (
            SELECT
                game_id,
                team_id,
                missing_player_id,
                ROW_NUMBER() OVER (
                    PARTITION BY game_id, team_id
                    ORDER BY (missing_usg * missing_min) DESC, missing_player_id ASC
                ) AS rn
            FROM missing_players
            WHERE is_star = 1
        )
        WHERE rn = 1
    ),
    target_wowy_rows AS (
        SELECT
            p.game_id,
            p.player_id,
            p.team_id,
            p.game_date,
            pms.primary_missing_star_id
        FROM player_game_logs p
        JOIN primary_missing_star pms
          ON p.game_id = pms.game_id
         AND p.team_id = pms.team_id
        WHERE p.player_id <> pms.primary_missing_star_id
    ),
    wowy_history AS (
        SELECT
            t.game_id,
            t.player_id,
            CASE WHEN star_pg.player_id IS NULL THEN 1 ELSE 0 END AS star_was_out,
            hist.min AS h_min,
            COALESCE(hist.usg_pct, 0.18) AS h_usg,
            CASE WHEN hist.min > 0 THEN 36.0 * hist.pts / hist.min ELSE NULL END AS h_pts_p36,
            CASE WHEN hist.min > 0 THEN 36.0 * hist.reb / hist.min ELSE NULL END AS h_reb_p36,
            CASE WHEN hist.min > 0 THEN 36.0 * hist.ast / hist.min ELSE NULL END AS h_ast_p36
        FROM target_wowy_rows t
        JOIN player_game_logs hist
          ON hist.player_id = t.player_id
         AND hist.team_id = t.team_id
         AND hist.game_date < t.game_date
        LEFT JOIN played_in_game star_pg
          ON star_pg.game_id = hist.game_id
         AND star_pg.team_id = hist.team_id
         AND star_pg.player_id = t.primary_missing_star_id
    ),
    wowy_agg AS (
        SELECT
            game_id,
            player_id,
            SUM(CASE WHEN star_was_out = 1 THEN 1 ELSE 0 END) AS n_out,
            SUM(CASE WHEN star_was_out = 0 THEN 1 ELSE 0 END) AS n_in,
            AVG(CASE WHEN star_was_out = 1 THEN h_min END) AS min_out,
            AVG(CASE WHEN star_was_out = 0 THEN h_min END) AS min_in,
            AVG(CASE WHEN star_was_out = 1 THEN h_usg END) AS usg_out,
            AVG(CASE WHEN star_was_out = 0 THEN h_usg END) AS usg_in,
            AVG(CASE WHEN star_was_out = 1 THEN h_pts_p36 END) AS pts_p36_out,
            AVG(CASE WHEN star_was_out = 0 THEN h_pts_p36 END) AS pts_p36_in,
            AVG(CASE WHEN star_was_out = 1 THEN h_reb_p36 END) AS reb_p36_out,
            AVG(CASE WHEN star_was_out = 0 THEN h_reb_p36 END) AS reb_p36_in,
            AVG(CASE WHEN star_was_out = 1 THEN h_ast_p36 END) AS ast_p36_out,
            AVG(CASE WHEN star_was_out = 0 THEN h_ast_p36 END) AS ast_p36_in
        FROM wowy_history
        GROUP BY game_id, player_id
    )
    SELECT
        p.game_id,
        p.player_id,
        -- Own team absences
        COALESCE(tma.missing_team_minutes, 0.0) AS missing_team_minutes,
        COALESCE(tma.missing_team_usage_pct, 0.0) AS missing_team_usage_pct,
        COALESCE(tma.star_teammate_out, 0) AS star_teammate_out,
        COALESCE(tma.num_rotation_players_out, 0) AS num_rotation_players_out,
        CASE COALESCE(pl.pos_group, 'UNK')
            WHEN 'G' THEN COALESCE(tma.missing_g_min, 0.0)
            WHEN 'F' THEN COALESCE(tma.missing_f_min, 0.0)
            WHEN 'C' THEN COALESCE(tma.missing_c_min, 0.0)
            ELSE 0.0
        END AS missing_same_pos_minutes,
        CASE COALESCE(pl.pos_group, 'UNK')
            WHEN 'G' THEN COALESCE(tma.missing_g_usg, 0.0)
            WHEN 'F' THEN COALESCE(tma.missing_f_usg, 0.0)
            WHEN 'C' THEN COALESCE(tma.missing_c_usg, 0.0)
            ELSE 0.0
        END AS missing_same_pos_usage,
        -- Opponent absences (defense weakened / blowout risk)
        COALESCE(oma.missing_team_minutes, 0.0) AS opp_missing_team_minutes,
        COALESCE(oma.missing_team_usage_pct, 0.0) AS opp_missing_team_usage_pct,
        COALESCE(oma.star_teammate_out, 0) AS opp_star_out,
        COALESCE(oma.missing_c_min, 0.0) AS opp_missing_center_minutes,
        COALESCE(oma.missing_def_stocks, 0.0) AS opp_missing_def_stocks,
        CASE COALESCE(pl.pos_group, 'UNK')
            WHEN 'G' THEN COALESCE(oma.missing_g_min, 0.0)
            WHEN 'F' THEN COALESCE(oma.missing_f_min, 0.0)
            WHEN 'C' THEN COALESCE(oma.missing_c_min, 0.0)
            ELSE 0.0
        END AS opp_missing_same_pos_minutes,
        -- Individual shrunken WOWY deltas
        COALESCE(wa.n_out, 0) AS wowy_sample_games,
        CASE
            WHEN COALESCE(wa.n_out, 0) > 0 AND COALESCE(wa.n_in, 0) > 0
            THEN (wa.n_out / (wa.n_out + {shrinkage_k})) * (wa.min_out - wa.min_in)
            ELSE 0.0
        END AS wowy_delta_min,
        CASE
            WHEN COALESCE(wa.n_out, 0) > 0 AND COALESCE(wa.n_in, 0) > 0
            THEN (wa.n_out / (wa.n_out + {shrinkage_k})) * (wa.usg_out - wa.usg_in)
            ELSE 0.0
        END AS wowy_delta_usg,
        CASE
            WHEN COALESCE(wa.n_out, 0) > 0 AND COALESCE(wa.n_in, 0) > 0
            THEN (wa.n_out / (wa.n_out + {shrinkage_k})) * (wa.pts_p36_out - wa.pts_p36_in)
            ELSE 0.0
        END AS wowy_delta_pts_p36,
        CASE
            WHEN COALESCE(wa.n_out, 0) > 0 AND COALESCE(wa.n_in, 0) > 0
            THEN (wa.n_out / (wa.n_out + {shrinkage_k})) * (wa.reb_p36_out - wa.reb_p36_in)
            ELSE 0.0
        END AS wowy_delta_reb_p36,
        CASE
            WHEN COALESCE(wa.n_out, 0) > 0 AND COALESCE(wa.n_in, 0) > 0
            THEN (wa.n_out / (wa.n_out + {shrinkage_k})) * (wa.ast_p36_out - wa.ast_p36_in)
            ELSE 0.0
        END AS wowy_delta_ast_p36
    FROM player_game_logs p
    LEFT JOIN players pl ON p.player_id = pl.player_id
    LEFT JOIN team_missing_agg tma
      ON p.game_id = tma.game_id
     AND p.team_id = tma.team_id
    LEFT JOIN team_missing_agg oma
      ON p.game_id = oma.game_id
     AND p.opp_team_id = oma.team_id
    LEFT JOIN wowy_agg wa
      ON p.game_id = wa.game_id
     AND p.player_id = wa.player_id
    """
    return con.execute(query).fetchdf()
