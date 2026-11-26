"""Unit tests for Phase 1 ingestion pipeline, Parquet cache, and DuckDB repository."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from nba_stats.config import IngestionConfig, Settings
from nba_stats.data.ingest import normalize_pos_group, parse_matchup, run_ingestion
from nba_stats.data.repository import NBARepository


class FakeNBAClient:
    """Deterministic in-memory stand-in for NBAClient."""

    def __init__(self) -> None:
        self.request_count = 0
        self.calls: list[tuple[str, str, str, date | None]] = []
        self.extra_active_game = False

    def get_static_teams(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "id": 1610612743,
                    "abbreviation": "DEN",
                    "full_name": "Denver Nuggets",
                    "nickname": "Nuggets",
                    "city": "Denver",
                    "state": "Colorado",
                    "year_founded": 1976,
                },
                {
                    "id": 1610612747,
                    "abbreviation": "LAL",
                    "full_name": "Los Angeles Lakers",
                    "nickname": "Lakers",
                    "city": "Los Angeles",
                    "state": "California",
                    "year_founded": 1948,
                },
            ]
        )

    def fetch_player_index(self, season: str) -> pd.DataFrame:
        self.request_count += 1
        self.calls.append(("player_index", season, "all", None))
        return pd.DataFrame(
            [
                {
                    "PERSON_ID": 203999,
                    "PLAYER_FIRST_NAME": "Nikola",
                    "PLAYER_LAST_NAME": "Jokic",
                    "POSITION": "C",
                    "TEAM_ID": 1610612743,
                    "ROSTER_STATUS": 1,
                    "JERSEY_NUMBER": "15",
                },
                {
                    "PERSON_ID": 2544,
                    "PLAYER_FIRST_NAME": "LeBron",
                    "PLAYER_LAST_NAME": "James",
                    "POSITION": "F-G",
                    "TEAM_ID": 1610612747,
                    "ROSTER_STATUS": 1,
                    "JERSEY_NUMBER": "23",
                },
            ]
        )

    def fetch_team_game_logs(
        self,
        season: str,
        season_type: str = "Regular Season",
        measure_type: str = "Base",
        date_from: date | None = None,
    ) -> pd.DataFrame:
        self.request_count += 1
        self.calls.append(("team_game_logs", season, measure_type, date_from))
        g_date = "2025-11-10" if season == "2025-26" else "2026-11-20"
        rows = [
            {
                "GAME_ID": f"002{season[:4]}0001",
                "TEAM_ID": 1610612743,
                "GAME_DATE": g_date,
                "MATCHUP": "DEN vs. LAL",
                "WL": "W",
                "MIN": 240.0,
                "PTS": 115.0,
                "FGM": 44.0,
                "FGA": 88.0,
                "FG3M": 12.0,
                "FG3A": 32.0,
                "FTM": 15.0,
                "FTA": 18.0,
                "OREB": 10.0,
                "DREB": 36.0,
                "REB": 46.0,
                "AST": 30.0,
                "STL": 7.0,
                "BLK": 5.0,
                "TOV": 12.0,
                "PF": 18.0,
                "PLUS_MINUS": 8.0,
                "OFF_RATING": 118.5,
                "DEF_RATING": 110.2,
                "NET_RATING": 8.3,
                "PACE": 97.5,
                "POSS": 97.0,
                "TS_PCT": 0.60,
                "EFG_PCT": 0.568,
            },
            {
                "GAME_ID": f"002{season[:4]}0001",
                "TEAM_ID": 1610612747,
                "GAME_DATE": g_date,
                "MATCHUP": "LAL @ DEN",
                "WL": "L",
                "MIN": 240.0,
                "PTS": 107.0,
                "FGM": 40.0,
                "FGA": 85.0,
                "FG3M": 11.0,
                "FG3A": 30.0,
                "FTM": 16.0,
                "FTA": 20.0,
                "OREB": 8.0,
                "DREB": 32.0,
                "REB": 40.0,
                "AST": 24.0,
                "STL": 6.0,
                "BLK": 4.0,
                "TOV": 14.0,
                "PF": 19.0,
                "PLUS_MINUS": -8.0,
                "OFF_RATING": 110.2,
                "DEF_RATING": 118.5,
                "NET_RATING": -8.3,
                "PACE": 97.5,
                "POSS": 97.0,
                "TS_PCT": 0.57,
                "EFG_PCT": 0.535,
            },
        ]
        if self.extra_active_game and season == "2026-27":
            rows.extend(
                [
                    {
                        **rows[0],
                        "GAME_ID": "00220260002",
                        "GAME_DATE": "2026-11-22",
                        "PTS": 120.0,
                    },
                    {
                        **rows[1],
                        "GAME_ID": "00220260002",
                        "GAME_DATE": "2026-11-22",
                        "PTS": 112.0,
                    },
                ]
            )
        return pd.DataFrame(rows)

    def fetch_player_game_logs(
        self,
        season: str,
        season_type: str = "Regular Season",
        measure_type: str = "Base",
        date_from: date | None = None,
    ) -> pd.DataFrame:
        self.request_count += 1
        self.calls.append(("player_game_logs", season, measure_type, date_from))
        g_date = "2025-11-10" if season == "2025-26" else "2026-11-20"
        rows = [
            {
                "GAME_ID": f"002{season[:4]}0001",
                "PLAYER_ID": 203999,
                "PLAYER_NAME": "Nikola Jokic",
                "TEAM_ID": 1610612743,
                "TEAM_ABBREVIATION": "DEN",
                "GAME_DATE": g_date,
                "MATCHUP": "DEN vs. LAL",
                "WL": "W",
                "MIN": 36.5,
                "PTS": 31.0,
                "REB": 13.0,
                "OREB": 3.0,
                "DREB": 10.0,
                "AST": 10.0,
                "STL": 2.0,
                "BLK": 1.0,
                "TOV": 3.0,
                "PF": 2.0,
                "FGM": 12.0,
                "FGA": 20.0,
                "FG3M": 2.0,
                "FG3A": 4.0,
                "FTM": 5.0,
                "FTA": 6.0,
                "PLUS_MINUS": 12.0,
                "USG_PCT": 0.315,
                "TS_PCT": 0.684,
                "EFG_PCT": 0.65,
                "AST_PCT": 0.44,
                "REB_PCT": 0.21,
                "OFF_RATING": 124.0,
                "DEF_RATING": 108.0,
                "PACE": 98.0,
                "POSS": 74.0,
            }
        ]
        if self.extra_active_game and season == "2026-27":
            rows.append(
                {
                    **rows[0],
                    "GAME_ID": "00220260002",
                    "GAME_DATE": "2026-11-22",
                    "PTS": 28.0,
                    "REB": 11.0,
                    "AST": 9.0,
                }
            )
        return pd.DataFrame(rows)


def test_helpers() -> None:
    assert normalize_pos_group("G-F") == "G"
    assert normalize_pos_group("F-C") == "F"
    assert normalize_pos_group("C") == "C"
    assert normalize_pos_group("") is None

    abbr_map = {"DEN": 1, "LAL": 2}
    assert parse_matchup("DEN vs. LAL", abbr_map) == (True, 2)
    assert parse_matchup("DEN @ LAL", abbr_map) == (False, 2)


def test_ingestion_idempotent_and_incremental(tmp_path: Path) -> None:
    settings = Settings(nba_stats_data_dir=tmp_path / "data")
    cfg = IngestionConfig(default_seasons=["2025-26", "2026-27"], active_season="2026-27")
    fake = FakeNBAClient()

    # First run ingests both seasons
    report1 = run_ingestion(settings=settings, config=cfg, client=fake)
    assert report1.teams_rows == 2
    assert len(report1.seasons) == 2
    assert all(not s.skipped for s in report1.seasons)
    assert (settings.raw_dir / "player_game_logs" / "season=2025-26.parquet").exists()
    assert (settings.raw_dir / "player_game_logs" / "season=2026-27.parquet").exists()

    # Second run: 2025-26 is skipped (0 requests), 2026-27 refreshes incrementally from 2026-11-20
    fake.calls.clear()
    fake.extra_active_game = True
    report2 = run_ingestion(settings=settings, config=cfg, client=fake)

    by_season = {s.season: s for s in report2.seasons}
    assert by_season["2025-26"].skipped is True
    assert by_season["2026-27"].skipped is False
    assert by_season["2026-27"].date_from == date(2026, 11, 20)
    assert all(call[1] == "2026-27" for call in fake.calls)

    # Verify DuckDB contents through NBARepository
    repo = NBARepository(settings=settings)
    logs = repo.get_player_recent_logs("Jokic", limit=5)
    assert len(logs) == 3  # 1 in 2025-26 + 2 in 2026-27
    assert logs.iloc[0]["pra"] == 48.0  # 28 + 11 + 9
    assert logs.iloc[0]["fga"] == 20.0
    assert logs.iloc[0]["usg_pct"] == 0.315

    coverage = repo.get_season_coverage()
    assert list(coverage["season"]) == ["2025-26", "2026-27"]
