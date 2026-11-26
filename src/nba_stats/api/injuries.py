"""Live NBA injury report fetcher (ESPN public feed) with 15-minute in-memory cache."""

from __future__ import annotations

import logging
import time
import unicodedata
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
CACHE_TTL_SECONDS = 15 * 60

# Map ESPN team abbreviations to NBA stats standard abbreviations where they differ
ESPN_TO_NBA_ABBR: dict[str, str] = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}


def normalize_name(name: str) -> str:
    """Lowercase and strip accents/diacritics for reliable cross-source player matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    cleaned = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(cleaned.lower().replace(".", "").split())


def classify_injury_status(raw_status: str) -> str | None:
    """Normalize ESPN injury status into 'OUT', 'DTD', or None."""
    s = raw_status.strip().lower()
    if not s:
        return None
    if any(k in s for k in ("out", "suspended", "suspension", "doubtful")):
        return "OUT"
    if any(k in s for k in ("day-to-day", "questionable", "probable", "gtd")):
        return "DTD"
    return None


class InjuryService:
    """Fetches and caches current NBA injuries from ESPN's public API."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache_ts: float = 0.0
        self._cache_payload: dict[str, Any] | None = None

    def _fetch_raw_json(self) -> dict[str, Any]:
        resp = requests.get(ESPN_INJURIES_URL, timeout=8)
        resp.raise_for_status()
        return resp.json()

    def get_injuries(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._cache_payload is not None
            and (now - self._cache_ts) < self.ttl_seconds
        ):
            return self._cache_payload

        updated_at = datetime.now(UTC).isoformat()
        try:
            raw = self._fetch_raw_json()
            by_name: dict[str, dict[str, str]] = {}
            by_team: dict[str, list[dict[str, str]]] = {}
            total_out = 0
            total_dtd = 0

            for team_block in raw.get("injuries") or []:
                team_info = team_block.get("team") or {}
                raw_abbr = str(team_info.get("abbreviation") or "").upper()
                nba_abbr = ESPN_TO_NBA_ABBR.get(raw_abbr, raw_abbr)
                entries: list[dict[str, str]] = []

                for item in team_block.get("injuries") or []:
                    athlete = item.get("athlete") or {}
                    full_name = str(athlete.get("displayName") or "").strip()
                    raw_status = str(item.get("status") or "").strip()
                    status = classify_injury_status(raw_status)
                    if not full_name or status is None:
                        continue

                    short_comment = str(item.get("shortComment") or raw_status).strip()
                    record = {
                        "player_name": full_name,
                        "status": status,
                        "raw_status": raw_status,
                        "comment": short_comment,
                    }
                    entries.append(record)
                    by_name[normalize_name(full_name)] = record
                    if status == "OUT":
                        total_out += 1
                    elif status == "DTD":
                        total_dtd += 1

                if nba_abbr and entries:
                    by_team[nba_abbr] = entries

            payload = {
                "updated_at": updated_at,
                "by_team": by_team,
                "by_name": by_name,
                "total_out": total_out,
                "total_dtd": total_dtd,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch ESPN injuries feed: %s", exc)
            payload = {
                "updated_at": updated_at,
                "by_team": {},
                "by_name": {},
                "total_out": 0,
                "total_dtd": 0,
                "fallback": True,
            }

        self._cache_payload = payload
        self._cache_ts = now
        return payload
