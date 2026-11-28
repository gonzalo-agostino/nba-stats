"""Read-only query interface over the local DuckDB NBA stats warehouse."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from nba_stats.config import Settings
from nba_stats.data.schema import init_schema


class NBARepository:
    """Query helper for inspecting and reading cached NBA data from DuckDB."""

    def __init__(self, duckdb_path: Path | None = None, settings: Settings | None = None) -> None:
        settings = settings or Settings()
        self.duckdb_path = duckdb_path or settings.duckdb_path

    def _connect(self, read_only: bool = True) -> duckdb.DuckDBPyConnection:
        if not self.duckdb_path.exists():
            self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(self.duckdb_path))
            init_schema(con)
            if read_only:
                con.close()
                return duckdb.connect(str(self.duckdb_path), read_only=True)
            return con
        return duckdb.connect(str(self.duckdb_path), read_only=read_only)

    def get_table_counts(self) -> pd.DataFrame:
        tables = [
            "teams",
            "players",
            "team_rosters",
            "games",
            "team_game_logs",
            "player_game_logs",
            "ingestion_log",
        ]
        with self._connect(read_only=True) as con:
            rows = []
            for table in tables:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchall()[0][0]
                rows.append({"table": table, "rows": int(count)})
        return pd.DataFrame(rows)

    def get_season_coverage(self) -> pd.DataFrame:
        query = """
        SELECT
            season,
            COUNT(DISTINCT game_id) AS games,
            COUNT(*) AS player_logs,
            MIN(game_date) AS min_date,
            MAX(game_date) AS max_date
        FROM player_game_logs
        GROUP BY season
        ORDER BY season
        """
        with self._connect(read_only=True) as con:
            return con.execute(query).fetchdf()

    def find_players(self, name_query: str, limit: int = 10) -> pd.DataFrame:
        query = """
        SELECT
            p.player_id,
            p.full_name,
            p.position,
            p.pos_group,
            t.abbreviation AS team,
            p.is_active
        FROM players p
        LEFT JOIN teams t ON p.team_id = t.team_id
        WHERE strip_accents(LOWER(p.full_name)) LIKE strip_accents(LOWER(?))
        ORDER BY p.is_active DESC, p.full_name ASC
        LIMIT ?
        """
        with self._connect(read_only=True) as con:
            return con.execute(query, [f"%{name_query.strip()}%", limit]).fetchdf()

    def get_player_recent_logs(
        self,
        name_query: str,
        limit: int = 10,
        season: str | None = None,
    ) -> pd.DataFrame:
        params: list[object] = [f"%{name_query.strip()}%"]
        season_clause = ""
        if season:
            season_clause = "AND season = ?"
            params.append(season)
        params.append(limit)

        query = f"""
        SELECT
            game_date,
            season,
            player_name,
            team_abbreviation AS team,
            matchup,
            wl,
            min,
            pts,
            reb,
            ast,
            (pts + reb + ast) AS pra,
            fga,
            fta,
            ftm,
            usg_pct,
            ts_pct
        FROM player_game_logs
        WHERE strip_accents(LOWER(player_name)) LIKE strip_accents(LOWER(?))
        {season_clause}
        ORDER BY game_date DESC, game_id DESC
        LIMIT ?
        """
        with self._connect(read_only=True) as con:
            return con.execute(query, params).fetchdf()
