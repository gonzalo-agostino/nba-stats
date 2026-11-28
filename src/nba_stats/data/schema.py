"""DuckDB DDL schema definitions for the local NBA stats warehouse."""

from __future__ import annotations

import duckdb

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS teams (
        team_id BIGINT PRIMARY KEY,
        abbreviation VARCHAR NOT NULL,
        full_name VARCHAR NOT NULL,
        nickname VARCHAR,
        city VARCHAR,
        state VARCHAR,
        year_founded INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        player_id BIGINT PRIMARY KEY,
        full_name VARCHAR NOT NULL,
        first_name VARCHAR,
        last_name VARCHAR,
        position VARCHAR,
        pos_group VARCHAR,
        team_id BIGINT,
        is_active BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
        game_id VARCHAR PRIMARY KEY,
        season VARCHAR NOT NULL,
        season_type VARCHAR NOT NULL,
        game_date DATE NOT NULL,
        home_team_id BIGINT NOT NULL,
        away_team_id BIGINT NOT NULL,
        home_pts DOUBLE,
        away_pts DOUBLE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_game_logs (
        game_id VARCHAR NOT NULL,
        team_id BIGINT NOT NULL,
        season VARCHAR NOT NULL,
        season_type VARCHAR NOT NULL,
        game_date DATE NOT NULL,
        matchup VARCHAR,
        is_home BOOLEAN NOT NULL,
        opp_team_id BIGINT,
        wl VARCHAR,
        min DOUBLE,
        pts DOUBLE,
        fgm DOUBLE,
        fga DOUBLE,
        fg3m DOUBLE,
        fg3a DOUBLE,
        ftm DOUBLE,
        fta DOUBLE,
        oreb DOUBLE,
        dreb DOUBLE,
        reb DOUBLE,
        ast DOUBLE,
        stl DOUBLE,
        blk DOUBLE,
        tov DOUBLE,
        pf DOUBLE,
        plus_minus DOUBLE,
        off_rating DOUBLE,
        def_rating DOUBLE,
        net_rating DOUBLE,
        pace DOUBLE,
        possessions DOUBLE,
        ts_pct DOUBLE,
        efg_pct DOUBLE,
        PRIMARY KEY (game_id, team_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_game_logs (
        game_id VARCHAR NOT NULL,
        player_id BIGINT NOT NULL,
        player_name VARCHAR NOT NULL,
        team_id BIGINT NOT NULL,
        team_abbreviation VARCHAR,
        season VARCHAR NOT NULL,
        season_type VARCHAR NOT NULL,
        game_date DATE NOT NULL,
        matchup VARCHAR,
        is_home BOOLEAN NOT NULL,
        opp_team_id BIGINT,
        wl VARCHAR,
        min DOUBLE,
        pts DOUBLE,
        reb DOUBLE,
        oreb DOUBLE,
        dreb DOUBLE,
        ast DOUBLE,
        stl DOUBLE,
        blk DOUBLE,
        tov DOUBLE,
        pf DOUBLE,
        fgm DOUBLE,
        fga DOUBLE,
        fg3m DOUBLE,
        fg3a DOUBLE,
        ftm DOUBLE,
        fta DOUBLE,
        plus_minus DOUBLE,
        usg_pct DOUBLE,
        ts_pct DOUBLE,
        efg_pct DOUBLE,
        ast_pct DOUBLE,
        reb_pct DOUBLE,
        off_rating DOUBLE,
        def_rating DOUBLE,
        pace DOUBLE,
        possessions DOUBLE,
        PRIMARY KEY (game_id, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_rosters (
        season VARCHAR NOT NULL,
        team_id BIGINT NOT NULL,
        player_id BIGINT NOT NULL,
        player_name VARCHAR NOT NULL,
        position VARCHAR,
        pos_group VARCHAR,
        jersey_number VARCHAR,
        is_active BOOLEAN DEFAULT TRUE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (season, team_id, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_log (
        endpoint VARCHAR NOT NULL,
        season VARCHAR NOT NULL,
        chunk_key VARCHAR NOT NULL,
        fetched_at TIMESTAMP NOT NULL,
        row_count BIGINT NOT NULL,
        max_game_date DATE,
        status VARCHAR NOT NULL,
        PRIMARY KEY (endpoint, season, chunk_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS prop_lines (
        game_date DATE NOT NULL,
        event_id VARCHAR NOT NULL,
        home_team VARCHAR NOT NULL,
        away_team VARCHAR NOT NULL,
        bookmaker VARCHAR NOT NULL,
        player_name VARCHAR NOT NULL,
        player_name_norm VARCHAR NOT NULL,
        stat VARCHAR NOT NULL,
        line DOUBLE NOT NULL,
        over_odds DOUBLE,
        under_odds DOUBLE,
        fetched_at TIMESTAMP NOT NULL,
        PRIMARY KEY (game_date, event_id, bookmaker, player_name_norm, stat)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tracked_picks (
        pick_id VARCHAR PRIMARY KEY,
        game_date DATE NOT NULL,
        game_id VARCHAR,
        player_id BIGINT NOT NULL,
        player_name VARCHAR NOT NULL,
        team_abbreviation VARCHAR NOT NULL,
        opponent_abbreviation VARCHAR NOT NULL,
        stat VARCHAR NOT NULL,
        bookmaker VARCHAR NOT NULL,
        line DOUBLE NOT NULL,
        side VARCHAR NOT NULL,
        odds_decimal DOUBLE NOT NULL,
        projected_value DOUBLE NOT NULL,
        edge DOUBLE NOT NULL,
        status VARCHAR NOT NULL DEFAULT 'PENDING',
        actual_value DOUBLE,
        profit_units DOUBLE,
        created_at TIMESTAMP NOT NULL,
        graded_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS game_odds (
        game_date DATE NOT NULL,
        event_id VARCHAR NOT NULL,
        home_team VARCHAR NOT NULL,
        away_team VARCHAR NOT NULL,
        bookmaker VARCHAR NOT NULL,
        spread_home DOUBLE,
        spread_away DOUBLE,
        total_points DOUBLE,
        fetched_at TIMESTAMP NOT NULL,
        PRIMARY KEY (game_date, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tracked_parlays (
        parlay_id VARCHAR PRIMARY KEY,
        game_date DATE NOT NULL,
        label VARCHAR NOT NULL,
        target_odds DOUBLE,
        combined_odds DOUBLE NOT NULL,
        estimated_prob DOUBLE,
        stake_units DOUBLE NOT NULL DEFAULT 1.0,
        legs_json VARCHAR NOT NULL,
        status VARCHAR NOT NULL DEFAULT 'PENDING',
        legs_won INTEGER NOT NULL DEFAULT 0,
        legs_lost INTEGER NOT NULL DEFAULT 0,
        legs_void INTEGER NOT NULL DEFAULT 0,
        legs_total INTEGER NOT NULL DEFAULT 0,
        effective_odds DOUBLE,
        profit_units DOUBLE,
        created_at TIMESTAMP NOT NULL,
        graded_at TIMESTAMP
    )
    """,
)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all warehouse tables if they do not exist."""
    for ddl in DDL_STATEMENTS:
        con.execute(ddl)
