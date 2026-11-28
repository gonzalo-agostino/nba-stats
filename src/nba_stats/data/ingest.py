"""Incremental ingestion pipeline from nba_api into Parquet cache and DuckDB warehouse."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from nba_stats.config import IngestionConfig, Settings, load_ingestion_config
from nba_stats.data.client import NBAClient
from nba_stats.data.schema import init_schema

logger = logging.getLogger(__name__)


@dataclass
class SeasonIngestionSummary:
    season: str
    skipped: bool = False
    players_rows: int = 0
    team_logs_rows: int = 0
    player_logs_rows: int = 0
    games_rows: int = 0
    date_from: date | None = None


@dataclass
class IngestionReport:
    teams_rows: int = 0
    api_requests: int = 0
    seasons: list[SeasonIngestionSummary] = field(default_factory=list)


def normalize_pos_group(position: Any) -> str | None:
    """Collapse NBA position strings (e.g. 'G-F', 'F-C') into primary group G / F / C."""
    if position is None or (isinstance(position, float) and pd.isna(position)):
        return None
    text = str(position).strip().upper()
    if not text:
        return None
    primary = text.split("-")[0].strip()
    if primary.startswith("G"):
        return "G"
    if primary.startswith("F"):
        return "F"
    if primary.startswith("C"):
        return "C"
    return None


def parse_matchup(matchup: Any, team_abbr_to_id: dict[str, int]) -> tuple[bool, int | None]:
    """Parse NBA matchup string ('DEN vs. LAL' or 'DEN @ LAL') into (is_home, opp_team_id)."""
    if not isinstance(matchup, str) or not matchup.strip():
        return False, None
    cleaned = matchup.strip()
    if " vs. " in cleaned:
        _, opp_abbr = cleaned.split(" vs. ", maxsplit=1)
        return True, team_abbr_to_id.get(opp_abbr.strip().upper())
    if " @ " in cleaned:
        _, opp_abbr = cleaned.split(" @ ", maxsplit=1)
        return False, team_abbr_to_id.get(opp_abbr.strip().upper())
    return False, None


def _safe_series(df: pd.DataFrame, col: str, default: Any = None) -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _to_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _to_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def _merge_and_write_parquet(
    df: pd.DataFrame,
    path: Path,
    key_cols: list[str],
) -> None:
    """Persist DataFrame to Parquet, merging with existing snapshot on key_cols if present."""
    if df.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
    else:
        combined = df.drop_duplicates(subset=key_cols, keep="last")
    combined.to_parquet(path, index=False)


def _upsert_dataframe(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    df: pd.DataFrame,
) -> int:
    if df.empty:
        return 0
    view_name = f"_tmp_{table_name}"
    cols = ", ".join(df.columns)
    con.register(view_name, df)
    try:
        con.execute(f"INSERT OR REPLACE INTO {table_name} ({cols}) SELECT {cols} FROM {view_name}")
    finally:
        con.unregister(view_name)
    return len(df)


def _record_ingestion_log(
    con: duckdb.DuckDBPyConnection,
    endpoint: str,
    season: str,
    chunk_key: str,
    row_count: int,
    max_game_date: date | None,
    status: str = "success",
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO ingestion_log
            (endpoint, season, chunk_key, fetched_at, row_count, max_game_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            endpoint,
            season,
            chunk_key,
            datetime.now(UTC).replace(tzinfo=None),
            int(row_count),
            max_game_date,
            status,
        ],
    )


def _get_ingestion_state(
    con: duckdb.DuckDBPyConnection,
    endpoint: str,
    season: str,
    chunk_key: str = "full",
) -> tuple[str | None, date | None]:
    rows = con.execute(
        """
        SELECT status, max_game_date
        FROM ingestion_log
        WHERE endpoint = ? AND season = ? AND chunk_key = ?
        """,
        [endpoint, season, chunk_key],
    ).fetchall()
    if not rows:
        return None, None
    return rows[0][0], rows[0][1]


def _transform_teams(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "team_id": pd.to_numeric(raw["id"], errors="coerce").astype("int64"),
            "abbreviation": raw["abbreviation"].astype(str).str.upper(),
            "full_name": raw["full_name"].astype(str),
            "nickname": _safe_series(raw, "nickname"),
            "city": _safe_series(raw, "city"),
            "state": _safe_series(raw, "state"),
            "year_founded": pd.to_numeric(_safe_series(raw, "year_founded"), errors="coerce"),
        }
    )


def _transform_players_and_rosters(
    raw: pd.DataFrame,
    season: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    now = datetime.now(UTC).replace(tzinfo=None)
    player_id = pd.to_numeric(raw["PERSON_ID"], errors="coerce").astype("int64")
    first_name = _safe_series(raw, "PLAYER_FIRST_NAME", "").fillna("").astype(str)
    last_name = _safe_series(raw, "PLAYER_LAST_NAME", "").fillna("").astype(str)
    full_name = (first_name + " " + last_name).str.strip()
    position = _safe_series(raw, "POSITION")
    pos_group = position.map(normalize_pos_group)
    team_id = pd.to_numeric(_safe_series(raw, "TEAM_ID"), errors="coerce")
    roster_status = pd.to_numeric(_safe_series(raw, "ROSTER_STATUS"), errors="coerce").fillna(0)
    is_active = roster_status > 0
    jersey = _safe_series(raw, "JERSEY_NUMBER").astype("string")

    players_df = pd.DataFrame(
        {
            "player_id": player_id,
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "position": position,
            "pos_group": pos_group,
            "team_id": team_id,
            "is_active": is_active,
            "updated_at": now,
        }
    ).drop_duplicates(subset=["player_id"], keep="last")

    has_team = team_id.notna() & (team_id > 0)
    rosters_df = pd.DataFrame(
        {
            "season": season,
            "team_id": team_id[has_team].astype("int64"),
            "player_id": player_id[has_team],
            "player_name": full_name[has_team],
            "position": position[has_team],
            "pos_group": pos_group[has_team],
            "jersey_number": jersey[has_team],
            "is_active": is_active[has_team],
            "updated_at": now,
        }
    ).drop_duplicates(subset=["season", "team_id", "player_id"], keep="last")

    return players_df, rosters_df


def _transform_team_game_logs(
    base_df: pd.DataFrame,
    adv_df: pd.DataFrame | None,
    season: str,
    season_type: str,
    team_abbr_to_id: dict[str, int],
) -> pd.DataFrame:
    if base_df.empty:
        return pd.DataFrame()

    df = base_df.copy()
    df["GAME_ID"] = df["GAME_ID"].astype(str)
    df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce").astype("int64")

    if adv_df is not None and not adv_df.empty:
        adv = adv_df.copy()
        adv["GAME_ID"] = adv["GAME_ID"].astype(str)
        adv["TEAM_ID"] = pd.to_numeric(adv["TEAM_ID"], errors="coerce").astype("int64")
        adv_cols = [
            c
            for c in [
                "GAME_ID",
                "TEAM_ID",
                "OFF_RATING",
                "DEF_RATING",
                "NET_RATING",
                "PACE",
                "POSS",
                "TS_PCT",
                "EFG_PCT",
            ]
            if c in adv.columns
        ]
        adv = adv[adv_cols].drop_duplicates(subset=["GAME_ID", "TEAM_ID"], keep="last")
        df = df.merge(adv, on=["GAME_ID", "TEAM_ID"], how="left", suffixes=("", "_adv"))

    parsed = [parse_matchup(m, team_abbr_to_id) for m in _safe_series(df, "MATCHUP")]
    is_home = [p[0] for p in parsed]
    opp_team_id = [p[1] for p in parsed]

    out = pd.DataFrame(
        {
            "game_id": df["GAME_ID"],
            "team_id": df["TEAM_ID"],
            "season": season,
            "season_type": season_type,
            "game_date": _to_date(_safe_series(df, "GAME_DATE")),
            "matchup": _safe_series(df, "MATCHUP"),
            "is_home": is_home,
            "opp_team_id": pd.Series(opp_team_id, dtype="Int64"),
            "wl": _safe_series(df, "WL"),
            "min": _to_float(_safe_series(df, "MIN")),
            "pts": _to_float(_safe_series(df, "PTS")),
            "fgm": _to_float(_safe_series(df, "FGM")),
            "fga": _to_float(_safe_series(df, "FGA")),
            "fg3m": _to_float(_safe_series(df, "FG3M")),
            "fg3a": _to_float(_safe_series(df, "FG3A")),
            "ftm": _to_float(_safe_series(df, "FTM")),
            "fta": _to_float(_safe_series(df, "FTA")),
            "oreb": _to_float(_safe_series(df, "OREB")),
            "dreb": _to_float(_safe_series(df, "DREB")),
            "reb": _to_float(_safe_series(df, "REB")),
            "ast": _to_float(_safe_series(df, "AST")),
            "stl": _to_float(_safe_series(df, "STL")),
            "blk": _to_float(_safe_series(df, "BLK")),
            "tov": _to_float(_safe_series(df, "TOV")),
            "pf": _to_float(_safe_series(df, "PF")),
            "plus_minus": _to_float(_safe_series(df, "PLUS_MINUS")),
            "off_rating": _to_float(_safe_series(df, "OFF_RATING")),
            "def_rating": _to_float(_safe_series(df, "DEF_RATING")),
            "net_rating": _to_float(_safe_series(df, "NET_RATING")),
            "pace": _to_float(_safe_series(df, "PACE")),
            "possessions": _to_float(_safe_series(df, "POSS")),
            "ts_pct": _to_float(_safe_series(df, "TS_PCT")),
            "efg_pct": _to_float(_safe_series(df, "EFG_PCT")),
        }
    )
    return out.drop_duplicates(subset=["game_id", "team_id"], keep="last")


def _transform_player_game_logs(
    base_df: pd.DataFrame,
    adv_df: pd.DataFrame | None,
    season: str,
    season_type: str,
    team_abbr_to_id: dict[str, int],
) -> pd.DataFrame:
    if base_df.empty:
        return pd.DataFrame()

    df = base_df.copy()
    df["GAME_ID"] = df["GAME_ID"].astype(str)
    df["PLAYER_ID"] = pd.to_numeric(df["PLAYER_ID"], errors="coerce").astype("int64")
    df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce").astype("int64")

    if adv_df is not None and not adv_df.empty:
        adv = adv_df.copy()
        adv["GAME_ID"] = adv["GAME_ID"].astype(str)
        adv["PLAYER_ID"] = pd.to_numeric(adv["PLAYER_ID"], errors="coerce").astype("int64")
        adv_cols = [
            c
            for c in [
                "GAME_ID",
                "PLAYER_ID",
                "USG_PCT",
                "TS_PCT",
                "EFG_PCT",
                "AST_PCT",
                "REB_PCT",
                "OFF_RATING",
                "DEF_RATING",
                "PACE",
                "POSS",
            ]
            if c in adv.columns
        ]
        adv = adv[adv_cols].drop_duplicates(subset=["GAME_ID", "PLAYER_ID"], keep="last")
        df = df.merge(adv, on=["GAME_ID", "PLAYER_ID"], how="left", suffixes=("", "_adv"))

    parsed = [parse_matchup(m, team_abbr_to_id) for m in _safe_series(df, "MATCHUP")]
    is_home = [p[0] for p in parsed]
    opp_team_id = [p[1] for p in parsed]

    out = pd.DataFrame(
        {
            "game_id": df["GAME_ID"],
            "player_id": df["PLAYER_ID"],
            "player_name": _safe_series(df, "PLAYER_NAME", "").astype(str),
            "team_id": df["TEAM_ID"],
            "team_abbreviation": _safe_series(df, "TEAM_ABBREVIATION"),
            "season": season,
            "season_type": season_type,
            "game_date": _to_date(_safe_series(df, "GAME_DATE")),
            "matchup": _safe_series(df, "MATCHUP"),
            "is_home": is_home,
            "opp_team_id": pd.Series(opp_team_id, dtype="Int64"),
            "wl": _safe_series(df, "WL"),
            "min": _to_float(_safe_series(df, "MIN")),
            "pts": _to_float(_safe_series(df, "PTS")),
            "reb": _to_float(_safe_series(df, "REB")),
            "oreb": _to_float(_safe_series(df, "OREB")),
            "dreb": _to_float(_safe_series(df, "DREB")),
            "ast": _to_float(_safe_series(df, "AST")),
            "stl": _to_float(_safe_series(df, "STL")),
            "blk": _to_float(_safe_series(df, "BLK")),
            "tov": _to_float(_safe_series(df, "TOV")),
            "pf": _to_float(_safe_series(df, "PF")),
            "fgm": _to_float(_safe_series(df, "FGM")),
            "fga": _to_float(_safe_series(df, "FGA")),
            "fg3m": _to_float(_safe_series(df, "FG3M")),
            "fg3a": _to_float(_safe_series(df, "FG3A")),
            "ftm": _to_float(_safe_series(df, "FTM")),
            "fta": _to_float(_safe_series(df, "FTA")),
            "plus_minus": _to_float(_safe_series(df, "PLUS_MINUS")),
            "usg_pct": _to_float(_safe_series(df, "USG_PCT")),
            "ts_pct": _to_float(_safe_series(df, "TS_PCT")),
            "efg_pct": _to_float(_safe_series(df, "EFG_PCT")),
            "ast_pct": _to_float(_safe_series(df, "AST_PCT")),
            "reb_pct": _to_float(_safe_series(df, "REB_PCT")),
            "off_rating": _to_float(_safe_series(df, "OFF_RATING")),
            "def_rating": _to_float(_safe_series(df, "DEF_RATING")),
            "pace": _to_float(_safe_series(df, "PACE")),
            "possessions": _to_float(_safe_series(df, "POSS")),
        }
    )
    return out.drop_duplicates(subset=["game_id", "player_id"], keep="last")


def _build_games_from_team_logs(team_logs_df: pd.DataFrame) -> pd.DataFrame:
    if team_logs_df.empty:
        return pd.DataFrame()
    home = team_logs_df[team_logs_df["is_home"]][
        ["game_id", "season", "season_type", "game_date", "team_id", "pts"]
    ].rename(columns={"team_id": "home_team_id", "pts": "home_pts"})
    away = team_logs_df[~team_logs_df["is_home"]][
        ["game_id", "team_id", "pts"]
    ].rename(columns={"team_id": "away_team_id", "pts": "away_pts"})
    games = home.merge(away, on="game_id", how="inner")
    return games[
        [
            "game_id",
            "season",
            "season_type",
            "game_date",
            "home_team_id",
            "away_team_id",
            "home_pts",
            "away_pts",
        ]
    ].drop_duplicates(subset=["game_id"], keep="last")


def run_ingestion(
    seasons: list[str] | None = None,
    force: bool = False,
    include_advanced: bool = True,
    settings: Settings | None = None,
    config: IngestionConfig | None = None,
    client: NBAClient | None = None,
) -> IngestionReport:
    """Run incremental ingestion for the requested NBA seasons into Parquet + DuckDB."""
    settings = settings or Settings()
    settings.ensure_dirs()
    config = config or load_ingestion_config(settings)
    client = client or NBAClient(rate_limit=config.rate_limit)
    target_seasons = seasons or config.default_seasons

    report = IngestionReport()
    con = duckdb.connect(str(settings.duckdb_path))
    try:
        init_schema(con)

        # 1. Static teams (0 network calls)
        teams_df = _transform_teams(client.get_static_teams())
        _merge_and_write_parquet(
            teams_df,
            settings.raw_dir / "teams.parquet",
            key_cols=["team_id"],
        )
        report.teams_rows = _upsert_dataframe(con, "teams", teams_df)
        team_abbr_to_id = dict(zip(teams_df["abbreviation"], teams_df["team_id"], strict=False))

        # 2. Season-by-season ingestion
        for season in target_seasons:
            is_active = season == config.active_season
            summary = SeasonIngestionSummary(season=season)

            pl_status, pl_max_date = _get_ingestion_state(con, "player_game_logs", season)
            tl_status, tl_max_date = _get_ingestion_state(con, "team_game_logs", season)

            if not force and not is_active and pl_status == "success" and tl_status == "success":
                logger.info("Skipping completed season %s (already ingested).", season)
                summary.skipped = True
                report.seasons.append(summary)
                continue

            date_from: date | None = None
            if not force and is_active and pl_max_date is not None and tl_max_date is not None:
                date_from = min(pl_max_date, tl_max_date)
                summary.date_from = date_from
                logger.info("Incremental refresh for active season %s from %s.", season, date_from)

            # 2a. Player index / rosters
            pi_status, _ = _get_ingestion_state(con, "player_index", season)
            if force or is_active or pi_status != "success":
                raw_players = client.fetch_player_index(season=season)
                players_df, rosters_df = _transform_players_and_rosters(raw_players, season=season)
                _merge_and_write_parquet(
                    players_df,
                    settings.raw_dir / "players" / f"season={season}.parquet",
                    key_cols=["player_id"],
                )
                _merge_and_write_parquet(
                    rosters_df,
                    settings.raw_dir / "team_rosters" / f"season={season}.parquet",
                    key_cols=["season", "team_id", "player_id"],
                )
                summary.players_rows = _upsert_dataframe(con, "players", players_df)
                _upsert_dataframe(con, "team_rosters", rosters_df)
                _record_ingestion_log(con, "player_index", season, "full", len(players_df), None)

            # 2b. Team game logs (Base + Advanced)
            raw_team_base = client.fetch_team_game_logs(
                season=season,
                season_type=config.season_type,
                measure_type="Base",
                date_from=date_from,
            )
            raw_team_adv = (
                client.fetch_team_game_logs(
                    season=season,
                    season_type=config.season_type,
                    measure_type="Advanced",
                    date_from=date_from,
                )
                if include_advanced
                else None
            )
            team_logs_df = _transform_team_game_logs(
                raw_team_base,
                raw_team_adv,
                season=season,
                season_type=config.season_type,
                team_abbr_to_id=team_abbr_to_id,
            )
            _merge_and_write_parquet(
                team_logs_df,
                settings.raw_dir / "team_game_logs" / f"season={season}.parquet",
                key_cols=["game_id", "team_id"],
            )
            summary.team_logs_rows = _upsert_dataframe(con, "team_game_logs", team_logs_df)

            games_df = _build_games_from_team_logs(team_logs_df)
            summary.games_rows = _upsert_dataframe(con, "games", games_df)

            team_max_date = (
                team_logs_df["game_date"].dropna().max() if not team_logs_df.empty else tl_max_date
            )
            _record_ingestion_log(
                con,
                "team_game_logs",
                season,
                "full",
                len(team_logs_df),
                team_max_date,
            )

            # 2c. Player game logs (Base + Advanced)
            raw_player_base = client.fetch_player_game_logs(
                season=season,
                season_type=config.season_type,
                measure_type="Base",
                date_from=date_from,
            )
            raw_player_adv = (
                client.fetch_player_game_logs(
                    season=season,
                    season_type=config.season_type,
                    measure_type="Advanced",
                    date_from=date_from,
                )
                if include_advanced
                else None
            )
            player_logs_df = _transform_player_game_logs(
                raw_player_base,
                raw_player_adv,
                season=season,
                season_type=config.season_type,
                team_abbr_to_id=team_abbr_to_id,
            )
            _merge_and_write_parquet(
                player_logs_df,
                settings.raw_dir / "player_game_logs" / f"season={season}.parquet",
                key_cols=["game_id", "player_id"],
            )
            summary.player_logs_rows = _upsert_dataframe(con, "player_game_logs", player_logs_df)

            player_max_date = (
                player_logs_df["game_date"].dropna().max()
                if not player_logs_df.empty
                else pl_max_date
            )
            _record_ingestion_log(
                con,
                "player_game_logs",
                season,
                "full",
                len(player_logs_df),
                player_max_date,
            )

            report.seasons.append(summary)

        report.api_requests = client.request_count
        return report
    finally:
        con.close()
