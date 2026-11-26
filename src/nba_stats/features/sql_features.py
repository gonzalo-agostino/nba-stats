"""Anti-leakage DuckDB SQL queries for fatigue, rolling form, matchup, opponent defense, and DvP."""

from __future__ import annotations

import duckdb
import pandas as pd

BASE_SQL_FEATURES_QUERY = """
WITH team_schedule AS (
    SELECT
        t.game_id,
        t.team_id,
        t.season,
        t.game_date,
        t.is_home,
        t.opp_team_id,
        t.pts,
        t.reb,
        t.ast,
        t.fta,
        t.pace,
        t.off_rating,
        t.def_rating,
        t.net_rating,
        LAG(t.game_date, 1) OVER (
            PARTITION BY t.team_id, t.season
            ORDER BY t.game_date, t.game_id
        ) AS prev_team_game_date,
        COUNT(*) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date
            RANGE BETWEEN INTERVAL 7 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING
        ) AS games_in_last_7_days,
        COUNT(*) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date
            RANGE BETWEEN INTERVAL 14 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING
        ) AS games_in_last_14_days,
        AVG(t.pace) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS team_pace_10,
        AVG(t.off_rating) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS team_off_rating_10,
        AVG(t.def_rating) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS team_def_rating_10,
        AVG(COALESCE(t.net_rating, t.off_rating - t.def_rating)) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS team_net_rating_10
    FROM team_game_logs t
),
team_allowed AS (
    SELECT
        t.game_id,
        t.team_id,
        t.game_date,
        AVG(opp.pts) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_pts_allowed_10,
        AVG(opp.reb) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_reb_allowed_10,
        AVG(opp.ast) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_ast_allowed_10,
        AVG(opp.fta) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_fta_allowed_10,
        AVG(opp.efg_pct) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_efg_allowed_10,
        AVG(opp.fg3a) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_fg3a_allowed_10,
        AVG(opp.tov) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_tov_forced_10,
        AVG(opp.oreb) OVER (
            PARTITION BY t.team_id
            ORDER BY t.game_date, t.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_oreb_allowed_10
    FROM team_game_logs t
    JOIN team_game_logs opp
      ON t.game_id = opp.game_id
     AND t.opp_team_id = opp.team_id
),
team_context AS (
    SELECT
        ts.game_id,
        ts.team_id,
        ts.season,
        ts.game_date,
        ts.is_home,
        CASE
            WHEN ts.prev_team_game_date IS NULL THEN 3
            ELSE LEAST(3, GREATEST(0, DATEDIFF('day', ts.prev_team_game_date, ts.game_date) - 1))
        END AS rest_days,
        CASE
            WHEN ts.prev_team_game_date IS NOT NULL
             AND DATEDIFF('day', ts.prev_team_game_date, ts.game_date) = 1 THEN 1
            ELSE 0
        END AS is_back_to_back,
        ts.games_in_last_7_days,
        ts.games_in_last_14_days,
        ts.team_pace_10,
        ts.team_off_rating_10,
        ts.team_def_rating_10,
        ts.team_net_rating_10,
        ta.opp_pts_allowed_10,
        ta.opp_reb_allowed_10,
        ta.opp_ast_allowed_10,
        ta.opp_fta_allowed_10,
        ta.opp_efg_allowed_10,
        ta.opp_fg3a_allowed_10,
        ta.opp_tov_forced_10,
        ta.opp_oreb_allowed_10
    FROM team_schedule ts
    LEFT JOIN team_allowed ta
      ON ts.game_id = ta.game_id
     AND ts.team_id = ta.team_id
),
road_streaks AS (
    SELECT
        game_id,
        team_id,
        is_home,
        SUM(CASE WHEN is_home THEN 1 ELSE 0 END) OVER (
            PARTITION BY team_id, season
            ORDER BY game_date, game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS home_grp,
        game_date
    FROM team_schedule
),
road_trip_num AS (
    SELECT
        game_id,
        team_id,
        CASE
            WHEN is_home THEN 0
            ELSE ROW_NUMBER() OVER (
                PARTITION BY team_id, home_grp
                ORDER BY game_date, game_id
            ) - CASE WHEN home_grp > 0 THEN 1 ELSE 0 END
        END AS road_trip_game_num
    FROM road_streaks
),
dvp_game AS (
    SELECT
        p.game_id,
        p.opp_team_id,
        p.game_date,
        COALESCE(pl.pos_group, 'UNK') AS pos_group,
        SUM(p.pts) AS pos_pts_allowed,
        SUM(p.reb) AS pos_reb_allowed,
        SUM(p.ast) AS pos_ast_allowed
    FROM player_game_logs p
    LEFT JOIN players pl ON p.player_id = pl.player_id
    WHERE p.opp_team_id IS NOT NULL
    GROUP BY p.game_id, p.opp_team_id, p.game_date, COALESCE(pl.pos_group, 'UNK')
),
dvp_rolling AS (
    SELECT
        game_id,
        opp_team_id,
        pos_group,
        AVG(pos_pts_allowed) OVER (
            PARTITION BY opp_team_id, pos_group
            ORDER BY game_date, game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_dvp_pts_10,
        AVG(pos_reb_allowed) OVER (
            PARTITION BY opp_team_id, pos_group
            ORDER BY game_date, game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_dvp_reb_10,
        AVG(pos_ast_allowed) OVER (
            PARTITION BY opp_team_id, pos_group
            ORDER BY game_date, game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS opp_dvp_ast_10
    FROM dvp_game
),
player_base AS (
    SELECT
        p.game_id,
        p.player_id,
        p.player_name,
        p.team_id,
        p.team_abbreviation,
        p.opp_team_id,
        p.season,
        p.game_date,
        CAST(p.is_home AS INTEGER) AS is_home,
        COALESCE(pl.pos_group, 'UNK') AS pos_group,
        -- All 13 targets (10 base + 3 derived)
        p.min AS target_min,
        p.pts AS target_pts,
        p.reb AS target_reb,
        p.ast AS target_ast,
        p.stl AS target_stl,
        p.blk AS target_blk,
        p.tov AS target_tov,
        p.pf AS target_pf,
        p.fga AS target_fga,
        p.ftm AS target_ftm,
        (p.pts + p.reb + p.ast) AS target_pra,
        (p.pts + p.reb) AS target_pr,
        (p.pts + p.ast) AS target_pa,
        -- Prior games count & player rest
        COUNT(*) OVER (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS games_played_prior,
        LAG(p.game_date, 1) OVER (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
        ) AS prev_player_game_date,
        LAG(p.min, 1) OVER (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
        ) AS min_last_game,
        -- Rolling 5
        AVG(p.min) OVER w5 AS min_avg_5,
        AVG(p.pts) OVER w5 AS pts_avg_5,
        AVG(p.reb) OVER w5 AS reb_avg_5,
        AVG(p.ast) OVER w5 AS ast_avg_5,
        AVG(p.fga) OVER w5 AS fga_avg_5,
        AVG(p.fg3a) OVER w5 AS fg3a_avg_5,
        AVG(p.fta) OVER w5 AS fta_avg_5,
        AVG(p.usg_pct) OVER w5 AS usg_pct_avg_5,
        AVG(p.ts_pct) OVER w5 AS ts_pct_avg_5,
        -- Rolling 10 (also serves as 10-game baseline for all 13 stats)
        AVG(p.min) OVER w10 AS min_avg_10,
        AVG(p.pts) OVER w10 AS pts_avg_10,
        AVG(p.reb) OVER w10 AS reb_avg_10,
        AVG(p.ast) OVER w10 AS ast_avg_10,
        AVG(p.pts + p.reb + p.ast) OVER w10 AS pra_avg_10,
        AVG(p.pts + p.reb) OVER w10 AS pr_avg_10,
        AVG(p.pts + p.ast) OVER w10 AS pa_avg_10,
        AVG(p.fga) OVER w10 AS fga_avg_10,
        AVG(p.fg3a) OVER w10 AS fg3a_avg_10,
        AVG(p.fta) OVER w10 AS fta_avg_10,
        AVG(p.ftm) OVER w10 AS ftm_avg_10,
        AVG(p.oreb) OVER w10 AS oreb_avg_10,
        AVG(p.dreb) OVER w10 AS dreb_avg_10,
        AVG(p.stl) OVER w10 AS stl_avg_10,
        AVG(p.blk) OVER w10 AS blk_avg_10,
        AVG(p.tov) OVER w10 AS tov_avg_10,
        AVG(p.pf) OVER w10 AS pf_avg_10,
        AVG(p.plus_minus) OVER w10 AS plus_minus_avg_10,
        AVG(p.usg_pct) OVER w10 AS usg_pct_avg_10,
        AVG(p.ts_pct) OVER w10 AS ts_pct_avg_10,
        AVG(p.ast_pct) OVER w10 AS ast_pct_avg_10,
        AVG(p.reb_pct) OVER w10 AS reb_pct_avg_10,
        STDDEV_SAMP(p.min) OVER w10 AS min_std_10,
        STDDEV_SAMP(p.pts) OVER w10 AS pts_std_10,
        STDDEV_SAMP(p.reb) OVER w10 AS reb_std_10,
        STDDEV_SAMP(p.ast) OVER w10 AS ast_std_10,
        -- Per-36 rates over last 10 (including discrete defensive/volume stats)
        36.0 * SUM(p.pts) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS pts_per36_10,
        36.0 * SUM(p.reb) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS reb_per36_10,
        36.0 * SUM(p.ast) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS ast_per36_10,
        36.0 * SUM(p.fga) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS fga_per36_10,
        36.0 * SUM(p.stl) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS stl_per36_10,
        36.0 * SUM(p.blk) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS blk_per36_10,
        36.0 * SUM(p.fta) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS fta_per36_10,
        36.0 * SUM(p.tov) OVER w10 / NULLIF(SUM(p.min) OVER w10, 0) AS tov_per36_10,
        -- Rolling 20
        AVG(p.min) OVER w20 AS min_avg_20,
        AVG(p.pts) OVER w20 AS pts_avg_20,
        AVG(p.reb) OVER w20 AS reb_avg_20,
        AVG(p.ast) OVER w20 AS ast_avg_20,
        AVG(p.fga) OVER w20 AS fga_avg_20,
        AVG(p.usg_pct) OVER w20 AS usg_pct_avg_20,
        AVG(p.ts_pct) OVER w20 AS ts_pct_avg_20
    FROM player_game_logs p
    LEFT JOIN players pl ON p.player_id = pl.player_id
    WINDOW
        w5 AS (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
            ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
        ),
        w10 AS (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ),
        w20 AS (
            PARTITION BY p.player_id
            ORDER BY p.game_date, p.game_id
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        )
)
SELECT
    pb.game_id,
    pb.player_id,
    pb.player_name,
    pb.team_id,
    pb.team_abbreviation,
    pb.opp_team_id,
    pb.season,
    pb.game_date,
    pb.is_home,
    pb.pos_group,
    pb.target_min,
    pb.target_pts,
    pb.target_reb,
    pb.target_ast,
    pb.target_stl,
    pb.target_blk,
    pb.target_tov,
    pb.target_pf,
    pb.target_fga,
    pb.target_ftm,
    pb.target_pra,
    pb.target_pr,
    pb.target_pa,
    pb.games_played_prior,
    -- Fatigue & schedule
    COALESCE(tc.rest_days, 3) AS rest_days,
    COALESCE(tc.is_back_to_back, 0) AS is_back_to_back,
    CASE
        WHEN pb.prev_player_game_date IS NULL THEN 5
        ELSE LEAST(14, GREATEST(0, DATEDIFF('day', pb.prev_player_game_date, pb.game_date) - 1))
    END AS player_rest_days,
    CASE
        WHEN pb.prev_player_game_date IS NOT NULL
         AND DATEDIFF('day', pb.prev_player_game_date, pb.game_date) - 1 >= 7 THEN 1
        ELSE 0
    END AS is_returning_from_absence,
    COALESCE(tc.games_in_last_7_days, 0) AS games_in_last_7_days,
    COALESCE(tc.games_in_last_14_days, 0) AS games_in_last_14_days,
    COALESCE(rt.road_trip_game_num, 0) AS road_trip_game_num,
    pb.min_last_game,
    -- Rolling player form
    pb.min_avg_5,
    pb.pts_avg_5,
    pb.reb_avg_5,
    pb.ast_avg_5,
    pb.fga_avg_5,
    pb.fg3a_avg_5,
    pb.fta_avg_5,
    pb.usg_pct_avg_5,
    pb.ts_pct_avg_5,
    pb.min_avg_10,
    pb.pts_avg_10,
    pb.reb_avg_10,
    pb.ast_avg_10,
    pb.pra_avg_10,
    pb.pr_avg_10,
    pb.pa_avg_10,
    pb.fga_avg_10,
    pb.fg3a_avg_10,
    pb.fta_avg_10,
    pb.ftm_avg_10,
    pb.oreb_avg_10,
    pb.dreb_avg_10,
    pb.stl_avg_10,
    pb.blk_avg_10,
    pb.tov_avg_10,
    pb.pf_avg_10,
    pb.plus_minus_avg_10,
    pb.usg_pct_avg_10,
    pb.ts_pct_avg_10,
    pb.ast_pct_avg_10,
    pb.reb_pct_avg_10,
    pb.min_std_10,
    pb.pts_std_10,
    pb.reb_std_10,
    pb.ast_std_10,
    pb.pts_per36_10,
    pb.reb_per36_10,
    pb.ast_per36_10,
    pb.fga_per36_10,
    pb.stl_per36_10,
    pb.blk_per36_10,
    pb.fta_per36_10,
    pb.tov_per36_10,
    pb.min_avg_20,
    pb.pts_avg_20,
    pb.reb_avg_20,
    pb.ast_avg_20,
    pb.fga_avg_20,
    pb.usg_pct_avg_20,
    pb.ts_pct_avg_20,
    -- Trends (5 vs 20)
    (pb.min_avg_5 - pb.min_avg_20) AS min_trend_5_20,
    (pb.pts_avg_5 - pb.pts_avg_20) AS pts_trend_5_20,
    (pb.reb_avg_5 - pb.reb_avg_20) AS reb_trend_5_20,
    (pb.ast_avg_5 - pb.ast_avg_20) AS ast_trend_5_20,
    -- Team & opponent matchup + blowout proxy
    tc.team_pace_10,
    tc.team_off_rating_10,
    tc.team_def_rating_10,
    tc.team_net_rating_10,
    opp_tc.team_pace_10 AS opp_pace_10,
    opp_tc.team_off_rating_10 AS opp_off_rating_10,
    opp_tc.team_def_rating_10 AS opp_def_rating_10,
    opp_tc.team_net_rating_10 AS opp_net_rating_10,
    (tc.team_net_rating_10 - opp_tc.team_net_rating_10) AS expected_margin_proxy,
    ABS(tc.team_net_rating_10 - opp_tc.team_net_rating_10) AS abs_expected_margin,
    (tc.team_pace_10 + opp_tc.team_pace_10) / 2.0 AS expected_game_pace,
    opp_tc.opp_pts_allowed_10,
    opp_tc.opp_reb_allowed_10,
    opp_tc.opp_ast_allowed_10,
    opp_tc.opp_fta_allowed_10,
    opp_tc.opp_efg_allowed_10,
    opp_tc.opp_fg3a_allowed_10,
    opp_tc.opp_tov_forced_10,
    opp_tc.opp_oreb_allowed_10,
    -- Defense vs Position
    dvp.opp_dvp_pts_10,
    dvp.opp_dvp_reb_10,
    dvp.opp_dvp_ast_10
FROM player_base pb
LEFT JOIN team_context tc
  ON pb.game_id = tc.game_id
 AND pb.team_id = tc.team_id
LEFT JOIN team_context opp_tc
  ON pb.game_id = opp_tc.game_id
 AND pb.opp_team_id = opp_tc.team_id
LEFT JOIN road_trip_num rt
  ON pb.game_id = rt.game_id
 AND pb.team_id = rt.team_id
LEFT JOIN dvp_rolling dvp
  ON pb.game_id = dvp.game_id
 AND pb.opp_team_id = dvp.opp_team_id
 AND pb.pos_group = dvp.pos_group
ORDER BY pb.game_date, pb.game_id, pb.player_id
"""


def compute_base_sql_features(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Execute the anti-leakage SQL feature query against DuckDB."""
    return con.execute(BASE_SQL_FEATURES_QUERY).fetchdf()
