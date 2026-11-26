"""Today's NBA slate fetcher with 15-minute in-memory cache and DuckDB B2B detection."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 15 * 60


class SlateService:
    """Fetches today's scheduled NBA games and enriches them with B2B flags from DuckDB."""

    def __init__(self, duckdb_path: Path, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.duckdb_path = duckdb_path
        self.ttl_seconds = ttl_seconds
        self._cache_ts: float = 0.0
        self._cache_payload: dict[str, Any] | None = None

    def _fetch_live_scoreboard_dict(self) -> dict[str, Any]:
        sb = live_scoreboard.ScoreBoard(timeout=10)
        return sb.get_dict()

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

    def get_today_slate(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._cache_payload is not None
            and (now - self._cache_ts) < self.ttl_seconds
        ):
            return self._cache_payload

        today = date.today()
        try:
            raw = self._fetch_live_scoreboard_dict()
            sb_data = raw.get("scoreboard", {})
            game_date_str = str(sb_data.get("gameDate") or today.isoformat())
            try:
                ref_date = date.fromisoformat(game_date_str[:10])
            except ValueError:
                ref_date = today

            played_yesterday = self._teams_played_yesterday(ref_date)
            raw_games = sb_data.get("games") or []

            games: list[dict[str, Any]] = []
            for g in raw_games:
                away = g.get("awayTeam") or {}
                home = g.get("homeTeam") or {}
                away_id = int(away.get("teamId") or 0)
                home_id = int(home.get("teamId") or 0)
                if away_id == 0 or home_id == 0:
                    continue
                games.append(
                    {
                        "game_id": str(g.get("gameId") or ""),
                        "away_team_id": away_id,
                        "away_abbr": str(away.get("teamTricode") or "").upper(),
                        "home_team_id": home_id,
                        "home_abbr": str(home.get("teamTricode") or "").upper(),
                        "game_status": str(g.get("gameStatusText") or "Scheduled").strip(),
                        "away_b2b": away_id in played_yesterday,
                        "home_b2b": home_id in played_yesterday,
                    }
                )

            payload = {
                "game_date": ref_date.isoformat(),
                "games": games,
                "fallback": len(games) == 0,
                "cached": False,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live scoreboard fetch failed, returning fallback slate: %s", exc)
            payload = {
                "game_date": today.isoformat(),
                "games": [],
                "fallback": True,
                "cached": False,
            }

        self._cache_payload = {**payload, "cached": True}
        self._cache_ts = now
        return payload
