"""Rate-limited and retry-safe wrapper around nba_api endpoints."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import date
from json import JSONDecodeError
from typing import Any, TypeVar

import pandas as pd
import requests
from nba_api.stats.endpoints import PlayerGameLogs, PlayerIndex, TeamGameLogs
from nba_api.stats.library.http import STATS_HEADERS
from nba_api.stats.static import teams as static_teams
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from nba_stats.config import RateLimitConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

NBA_STATS_HEADERS: dict[str, str] = dict(STATS_HEADERS)

RETRYABLE_EXCEPTIONS = (
    requests.RequestException,
    TimeoutError,
    ConnectionError,
    JSONDecodeError,
    ValueError,
)


def _format_nba_date(value: date | None) -> str:
    if value is None:
        return ""
    return value.strftime("%m/%d/%Y")


class NBAClient:
    """Thin client over nba_api with jittered rate limiting and exponential backoff."""

    def __init__(
        self,
        rate_limit: RateLimitConfig | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> None:
        self.rate_limit = rate_limit or RateLimitConfig()
        self.headers = headers or dict(NBA_STATS_HEADERS)
        self.proxy = proxy
        self._last_request_ts: float = 0.0
        self.request_count: int = 0

    def _sleep_if_needed(self) -> None:
        if self._last_request_ts == 0.0:
            return
        delay = random.uniform(
            self.rate_limit.min_delay_seconds,
            self.rate_limit.max_delay_seconds,
        )
        elapsed = time.monotonic() - self._last_request_ts
        remaining = delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _call_with_retry(self, fn: Callable[[], T], label: str) -> T:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.rate_limit.max_retries),
            wait=wait_exponential_jitter(
                initial=self.rate_limit.backoff_base_seconds,
                max=self.rate_limit.backoff_max_seconds,
            ),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _wrapped() -> T:
            self._sleep_if_needed()
            logger.debug("Calling stats.nba.com endpoint: %s", label)
            self._last_request_ts = time.monotonic()
            self.request_count += 1
            return fn()

        return _wrapped()

    def get_static_teams(self) -> pd.DataFrame:
        """Return NBA teams from local nba_api static metadata (0 network calls)."""
        records: list[dict[str, Any]] = static_teams.get_teams()
        return pd.DataFrame(records)

    def fetch_player_index(self, season: str) -> pd.DataFrame:
        """Fetch league-wide player index (positions, team_id, active status) for a season."""
        label = f"PlayerIndex(season={season})"

        def _call() -> pd.DataFrame:
            ep = PlayerIndex(
                season=season,
                headers=self.headers,
                timeout=self.rate_limit.timeout_seconds,
                proxy=self.proxy,
            )
            frames = ep.get_data_frames()
            return frames[0] if frames else pd.DataFrame()

        return self._call_with_retry(_call, label)

    def fetch_team_game_logs(
        self,
        season: str,
        season_type: str = "Regular Season",
        measure_type: str = "Base",
        date_from: date | None = None,
    ) -> pd.DataFrame:
        """Fetch aggregated team game logs for an entire season (or from date_from)."""
        date_from_str = _format_nba_date(date_from)
        label = (
            f"TeamGameLogs(season={season}, type={season_type}, "
            f"measure={measure_type}, date_from={date_from_str or 'ALL'})"
        )

        def _call() -> pd.DataFrame:
            ep = TeamGameLogs(
                season_nullable=season,
                season_type_nullable=season_type,
                measure_type_player_game_logs_nullable=measure_type,
                date_from_nullable=date_from_str,
                headers=self.headers,
                timeout=self.rate_limit.timeout_seconds,
                proxy=self.proxy,
            )
            frames = ep.get_data_frames()
            return frames[0] if frames else pd.DataFrame()

        return self._call_with_retry(_call, label)

    def fetch_player_game_logs(
        self,
        season: str,
        season_type: str = "Regular Season",
        measure_type: str = "Base",
        date_from: date | None = None,
    ) -> pd.DataFrame:
        """Fetch aggregated player game logs for an entire season (or from date_from)."""
        date_from_str = _format_nba_date(date_from)
        label = (
            f"PlayerGameLogs(season={season}, type={season_type}, "
            f"measure={measure_type}, date_from={date_from_str or 'ALL'})"
        )

        def _call() -> pd.DataFrame:
            ep = PlayerGameLogs(
                season_nullable=season,
                season_type_nullable=season_type,
                measure_type_player_game_logs_nullable=measure_type,
                date_from_nullable=date_from_str,
                headers=self.headers,
                timeout=self.rate_limit.timeout_seconds,
                proxy=self.proxy,
            )
            frames = ep.get_data_frames()
            return frames[0] if frames else pd.DataFrame()

        return self._call_with_retry(_call, label)
