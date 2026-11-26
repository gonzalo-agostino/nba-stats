"""NBA slate fetcher supporting any date (today via live ScoreBoard, past via DuckDB, future via ScoreboardV2)."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from nba_api.stats.endpoints import ScoreboardV2
from nba_api.stats.library.http import STATS_HEADERS

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 15 * 60


class SlateService:
    """Fetches scheduled NBA games for any date and enriches them with B2B flags from DuckDB."""

    def __init__(self, duckdb_path: Path, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.duckdb_path = duckdb_path
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _fetch_live_scoreboard_dict(self) -> dict[str, Any]:
        sb = live_scoreboard.ScoreBoard(timeout=10)
        return sb.get_dict()

    def _fetch_scoreboard_v2_games(self, target_date: date) -> list[dict[str, Any]]:
        sb = ScoreboardV2(
            game_date=target_date.strftime("%Y-%m-%d"),
            headers=dict(STATS_HEADERS),
            timeout=15,
        )
        frames = sb.get_data_frames()
        if not frames or frames[0].empty:
            return []
        header_df = frames[0]
        team_abbr_map = self._team_id_to_abbr()
        games: list[dict[str, Any]] = []
        for _, row in header_df.iterrows():
            away_id = int(row["VISITOR_TEAM_ID"])
            home_id = int(row["HOME_TEAM_ID"])
            games.append(
                {
                    "game_id": str(row["GAME_ID"]),
                    "away_team_id": away_id,
                    "away_abbr": team_abbr_map.get(away_id, str(away_id)),
                    "home_team_id": home_id,
                    "home_abbr": team_abbr_map.get(home_id, str(home_id)),
                    "game_status": str(row.get("GAME_STATUS_TEXT") or "Scheduled").strip(),
                }
            )
        return games

    def _team_id_to_abbr(self) -> dict[int, str]:
        if not self.duckdb_path.exists():
            return {}
        try:
            with duckdb.connect(str(self.duckdb_path), read_only=True) as con:
                rows = con.execute("SELECT team_id, abbreviation FROM teams").fetchall()
            return {int(r[0]): str(r[1]) for r in rows}
        except duckdb.Error:
            return {}

    def _fetch_duckdb_games_for_date(self, target_date: date) -> list[dict[str, Any]]:
        if not self.duckdb_path.exists():
            return []
        try:
            with duckdb.connect(str(self.duckdb_path), read_only=True) as con:
                rows = con.execute(
                    """
                    SELECT
                        g.game_id,
                        g.away_team_id,
                        ta.abbreviation AS away_abbr,
                        g.home_team_id,
                        th.abbreviation AS home_abbr,
                        g.away_pts,
                        g.home_pts
                    FROM games g
                    JOIN teams ta ON g.away_team_id = ta.team_id
                    JOIN teams th ON g.home_team_id = th.team_id
                    WHERE g.game_date = ?
                    ORDER BY g.game_id
                    """,
                    [target_date],
                ).fetchall()
            return [
                {
                    "game_id": str(r[0]),
                    "away_team_id": int(r[1]),
                    "away_abbr": str(r[2]),
                    "home_team_id": int(r[3]),
                    "home_abbr": str(r[4]),
                    "game_status": f"Final ({int(r[5])}-{int(r[6])})"
                    if r[5] is not None and r[6] is not None
                    else "Final",
                }
                for r in rows
            ]
        except duckdb.Error as exc:
            logger.warning("Could not query games for %s from DuckDB: %s", target_date, exc)
            return []

    def _teams_played_yesterday(self, ref_date: date) -> set[int]:
        yesterday = ref_date - timedelta(days=1)
        if not self.duckdb_path.exists():
            return set()
        try:
            with duckdb.connect(str(self.duckdb_path), read_only=True) as con:
                rows = con.execute(
                    "SELECT DISTINCT team_id FROM team_game_logs WHERE game_date = ?",
                    [yesterday],
                ).fetchall()
            return {int(r[0]) for r in rows}
        except duckdb.Error as exc:
            logger.warning("Could not query yesterday's games from DuckDB: %s", exc)
            return set()

    def get_today_slate(
        self,
        game_date: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        today = date.today()
        target_date = today
        if game_date:
            try:
                target_date = date.fromisoformat(game_date[:10])
            except ValueError:
                target_date = today

        cache_key = game_date[:10] if game_date else "__today__"
        now = time.monotonic()
        if not force_refresh and cache_key in self._cache:
            ts, cached_payload = self._cache[cache_key]
            if (now - ts) < self.ttl_seconds:
                return cached_payload

        try:
            raw_games: list[dict[str, Any]] = []
            ref_date = target_date

            if game_date is None or target_date == today:
                raw = self._fetch_live_scoreboard_dict()
                sb_data = raw.get("scoreboard", {})
                gdate_str = str(sb_data.get("gameDate") or today.isoformat())
                try:
                    ref_date = date.fromisoformat(gdate_str[:10])
                except ValueError:
                    ref_date = today
                for g in sb_data.get("games") or []:
                    away = g.get("awayTeam") or {}
                    home = g.get("homeTeam") or {}
                    away_id = int(away.get("teamId") or 0)
                    home_id = int(home.get("teamId") or 0)
                    if away_id and home_id:
                        raw_games.append(
                            {
                                "game_id": str(g.get("gameId") or ""),
                                "away_team_id": away_id,
                                "away_abbr": str(away.get("teamTricode") or "").upper(),
                                "home_team_id": home_id,
                                "home_abbr": str(home.get("teamTricode") or "").upper(),
                                "game_status": str(g.get("gameStatusText") or "Scheduled").strip(),
                            }
                        )
            else:
                # For past dates, check local DuckDB cache first (0 HTTP calls!)
                if target_date < today:
                    raw_games = self._fetch_duckdb_games_for_date(target_date)
                # For future dates (or past dates not yet in DuckDB), query ScoreboardV2
                if not raw_games:
                    raw_games = self._fetch_scoreboard_v2_games(target_date)

            played_yesterday = self._teams_played_yesterday(ref_date)
            games = [
                {
                    **g,
                    "away_b2b": g["away_team_id"] in played_yesterday,
                    "home_b2b": g["home_team_id"] in played_yesterday,
                }
                for g in raw_games
            ]

            payload = {
                "game_date": ref_date.isoformat(),
                "games": games,
                "fallback": len(games) == 0,
                "cached": False,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Slate fetch failed for %s, returning fallback: %s", target_date, exc)
            payload = {
                "game_date": target_date.isoformat(),
                "games": [],
                "fallback": True,
                "cached": False,
            }

        self._cache[cache_key] = (now, {**payload, "cached": True})
        return payload
