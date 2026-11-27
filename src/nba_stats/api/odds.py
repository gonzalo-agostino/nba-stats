"""Automated NBA Player Props fetcher (The Odds API), pick tracker, and Win Rate/ROI grader."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

from nba_stats.api.injuries import ESPN_TO_NBA_ABBR, InjuryService, normalize_name
from nba_stats.api.slate import SlateService
from nba_stats.data.schema import init_schema
from nba_stats.simulator.what_if import WhatIfSimulator

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

MARKET_TO_STAT: dict[str, str] = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_rebounds_assists": "ra",
    "player_points_rebounds": "pr",
    "player_points_assists": "pa",
    "player_points_rebounds_assists": "pra",
    "player_threes": "fg3m",
    "player_blocks": "blk",
    "player_turnovers": "tov",
}

# Credit-saver default: only 2 markets (~16 credits/day -> lasts all month on 500 free credits)
SAVER_MARKETS: tuple[str, ...] = (
    "player_points",
    "player_points_rebounds_assists",
)

FULL_MARKETS: tuple[str, ...] = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_points_rebounds_assists",
    "player_threes",
    "player_blocks",
    "player_turnovers",
)

DEFAULT_MARKETS: tuple[str, ...] = SAVER_MARKETS

PREFERRED_BOOKMAKERS: tuple[str, ...] = (
    "fanduel",
    "draftkings",
    "betmgm",
    "caesars",
    "betrivers",
)

# Minimum |edge| per stat required to auto-register a pick
DEFAULT_MIN_EDGE_BY_STAT: dict[str, float] = {
    "pra": 2.5,
    "pr": 2.0,
    "pa": 2.0,
    "pts": 1.8,
    "ra": 1.5,
    "reb": 1.2,
    "ast": 1.0,
    "fg3m": 0.6,
    "blk": 0.4,
    "tov": 0.5,
}


class OddsService:
    """Manages The Odds API prop lines, DuckDB caching, auto-pick snapshots, and grading."""

    def __init__(
        self,
        duckdb_path: Path,
        api_key: str | None = None,
    ) -> None:
        self.duckdb_path = duckdb_path
        self.api_key = (api_key or "").strip() or None
        self.requests_remaining: int | None = None
        self.requests_used: int | None = None

    def _connect_rw(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(str(self.duckdb_path))
        init_schema(con)
        return con

    def _update_quota_headers(self, resp: requests.Response) -> None:
        rem = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if rem is not None:
            try:
                self.requests_remaining = int(float(rem))
            except ValueError:
                pass
        if used is not None:
            try:
                self.requests_used = int(float(used))
            except ValueError:
                pass

    def _fetch_events(self) -> list[dict[str, Any]]:
        """List upcoming NBA events from The Odds API (costs 0 credits)."""
        if not self.api_key:
            raise ValueError("Falta configurar THE_ODDS_API_KEY en el archivo .env.")
        resp = requests.get(
            f"{ODDS_API_BASE}/events",
            params={"apiKey": self.api_key},
            timeout=12,
        )
        self._update_quota_headers(resp)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def _fetch_event_props(
        self,
        event_id: str,
        markets: tuple[str, ...] = DEFAULT_MARKETS,
    ) -> dict[str, Any]:
        """Fetch player prop lines for a single NBA event (US books, decimal odds)."""
        if not self.api_key:
            raise ValueError("Falta configurar THE_ODDS_API_KEY en el archivo .env.")
        resp = requests.get(
            f"{ODDS_API_BASE}/events/{event_id}/odds",
            params={
                "apiKey": self.api_key,
                "regions": "us",
                "markets": ",".join(markets),
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        self._update_quota_headers(resp)
        resp.raise_for_status()
        return resp.json()

    def _fetch_espn_game_odds(self, target_date: date) -> list[tuple[Any, ...]]:
        """Fetch 100% FREE NBA game spreads & totals from ESPN public scoreboard JSON (0 credits)."""
        resp = requests.get(
            ESPN_SCOREBOARD_URL,
            params={"dates": target_date.strftime("%Y%m%d")},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        now_ts = datetime.now(UTC).replace(tzinfo=None)
        rows: list[tuple[Any, ...]] = []

        for ev in payload.get("events") or []:
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            competitors = comp.get("competitors") or []
            home_abbr = ""
            away_abbr = ""
            for c in competitors:
                raw_abbr = str((c.get("team") or {}).get("abbreviation") or "").upper()
                nba_abbr = ESPN_TO_NBA_ABBR.get(raw_abbr, raw_abbr)
                if c.get("homeAway") == "home":
                    home_abbr = nba_abbr
                elif c.get("homeAway") == "away":
                    away_abbr = nba_abbr
            if not home_abbr or not away_abbr:
                continue

            odds_list = comp.get("odds") or []
            if not odds_list:
                continue
            od = odds_list[0]
            provider = str((od.get("provider") or {}).get("name") or "ESPN").strip()
            total_val = od.get("overUnder")
            total_points = float(total_val) if total_val is not None else None

            spread_home: float | None = None
            spread_away: float | None = None
            raw_spread = od.get("spread")
            if raw_spread is not None:
                # ESPN `spread` is from the home team's perspective (negative = home favorite)
                spread_home = float(raw_spread)
                spread_away = round(-spread_home, 1)

            if spread_home is not None or total_points is not None:
                rows.append(
                    (
                        target_date,
                        f"{away_abbr}@{home_abbr}",
                        home_abbr,
                        away_abbr,
                        provider,
                        spread_home,
                        spread_away,
                        total_points,
                        now_ts,
                    )
                )
        return rows

    def fetch_and_cache_game_odds(
        self,
        target_date: date,
        force_refresh: bool = False,
    ) -> int:
        """Fetch and cache FREE Vegas spreads & totals from ESPN in DuckDB game_odds (0 credits)."""
        with self._connect_rw() as con:
            existing = int(
                con.execute(
                    "SELECT COUNT(*) FROM game_odds WHERE game_date = ?",
                    [target_date],
                ).fetchall()[0][0]
            )
            if existing > 0 and not force_refresh:
                return existing

        try:
            rows = self._fetch_espn_game_odds(target_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed fetching free ESPN game spreads/totals: %s", exc)
            return 0

        if rows:
            with self._connect_rw() as con:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO game_odds (
                        game_date, event_id, home_team, away_team, bookmaker,
                        spread_home, spread_away, total_points, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def get_game_odds_for_date(self, target_date: date) -> dict[str, dict[str, Any]]:
        """Return cached spreads & totals indexed by event_id AND 'AWAY@HOME' abbreviation key."""
        with self._connect_rw() as con:
            rows = con.execute(
                """
                SELECT event_id, home_team, away_team, bookmaker, spread_home, spread_away, total_points
                FROM game_odds
                WHERE game_date = ?
                """,
                [target_date],
            ).fetchall()
        by_key: dict[str, dict[str, Any]] = {}
        for r in rows:
            rec = {
                "event_id": str(r[0]),
                "home_team": str(r[1]),
                "away_team": str(r[2]),
                "bookmaker": str(r[3]),
                "spread_home": float(r[4]) if r[4] is not None else None,
                "spread_away": float(r[5]) if r[5] is not None else None,
                "total_points": float(r[6]) if r[6] is not None else None,
            }
            by_key[rec["event_id"]] = rec
            by_key[f"{rec['away_team']}@{rec['home_team']}"] = rec
        return by_key

    def _event_local_date(self, commence_time_iso: str) -> date | None:
        """Convert UTC ISO commence_time to US Eastern NBA schedule date (UTC-5)."""
        if not commence_time_iso:
            return None
        try:
            dt_utc = datetime.fromisoformat(commence_time_iso)
            dt_et = dt_utc - timedelta(hours=5)
            return dt_et.date()
        except ValueError:
            return None

    def fetch_and_cache_lines(
        self,
        target_date: date,
        markets: tuple[str, ...] = DEFAULT_MARKETS,
        force_refresh: bool = False,
    ) -> tuple[int, bool]:
        """Fetch prop lines for target_date from The Odds API and persist in DuckDB prop_lines.

        Returns (num_lines_available, used_cache).
        """
        with self._connect_rw() as con:
            existing_cnt = int(
                con.execute(
                    "SELECT COUNT(*) FROM prop_lines WHERE game_date = ?",
                    [target_date],
                ).fetchall()[0][0]
            )
            if existing_cnt > 0 and not force_refresh:
                return existing_cnt, True

        events = self._fetch_events()
        matching_events = [
            ev
            for ev in events
            if self._event_local_date(str(ev.get("commence_time") or "")) == target_date
        ]

        now_ts = datetime.now(UTC).replace(tzinfo=None)
        rows_to_insert: list[tuple[Any, ...]] = []

        for ev in matching_events:
            event_id = str(ev.get("id") or "")
            home_team = str(ev.get("home_team") or "")
            away_team = str(ev.get("away_team") or "")
            if not event_id:
                continue
            try:
                odds_payload = self._fetch_event_props(event_id, markets=markets)
            except requests.RequestException as exc:
                logger.warning("Failed fetching props for event %s: %s", event_id, exc)
                continue

            bookmakers = odds_payload.get("bookmakers") or []
            # Pick best available bookmaker per (player, stat)
            bookmakers_sorted = sorted(
                bookmakers,
                key=lambda b: (
                    PREFERRED_BOOKMAKERS.index(str(b.get("key") or "").lower())
                    if str(b.get("key") or "").lower() in PREFERRED_BOOKMAKERS
                    else 99
                ),
            )

            seen_player_stat: set[tuple[str, str]] = set()
            for bm in bookmakers_sorted:
                bm_key = str(bm.get("key") or "unknown").lower()
                for market in bm.get("markets") or []:
                    m_key = str(market.get("key") or "")
                    stat = MARKET_TO_STAT.get(m_key)
                    if not stat:
                        continue
                    # Group Over/Under outcomes by player description
                    by_player: dict[str, dict[str, Any]] = {}
                    for out in market.get("outcomes") or []:
                        p_name = str(out.get("description") or "").strip()
                        side = str(out.get("name") or "").strip().lower()
                        point = out.get("point")
                        price = out.get("price")
                        if not p_name or point is None or price is None:
                            continue
                        p_norm = normalize_name(p_name)
                        entry = by_player.setdefault(
                            p_norm,
                            {
                                "player_name": p_name,
                                "line": float(point),
                                "over_odds": None,
                                "under_odds": None,
                            },
                        )
                        if side == "over":
                            entry["over_odds"] = float(price)
                            entry["line"] = float(point)
                        elif side == "under":
                            entry["under_odds"] = float(price)

                    for p_norm, entry in by_player.items():
                        if (p_norm, stat) in seen_player_stat:
                            continue
                        seen_player_stat.add((p_norm, stat))
                        rows_to_insert.append(
                            (
                                target_date,
                                event_id,
                                home_team,
                                away_team,
                                bm_key,
                                entry["player_name"],
                                p_norm,
                                stat,
                                entry["line"],
                                entry["over_odds"],
                                entry["under_odds"],
                                now_ts,
                            )
                        )

        if rows_to_insert:
            with self._connect_rw() as con:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO prop_lines (
                        game_date, event_id, home_team, away_team, bookmaker,
                        player_name, player_name_norm, stat, line, over_odds, under_odds, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )

        return len(rows_to_insert), False

    def get_lines_for_date(self, target_date: date) -> list[dict[str, Any]]:
        """Return cached prop lines for a given date from DuckDB."""
        with self._connect_rw() as con:
            rows = con.execute(
                """
                SELECT
                    game_date, event_id, home_team, away_team, bookmaker,
                    player_name, player_name_norm, stat, line, over_odds, under_odds
                FROM prop_lines
                WHERE game_date = ?
                ORDER BY player_name ASC, stat ASC
                """,
                [target_date],
            ).fetchall()
        return [
            {
                "game_date": str(r[0])[:10],
                "event_id": str(r[1]),
                "home_team": str(r[2]),
                "away_team": str(r[3]),
                "bookmaker": str(r[4]),
                "player_name": str(r[5]),
                "player_name_norm": str(r[6]),
                "stat": str(r[7]),
                "line": float(r[8]),
                "over_odds": float(r[9]) if r[9] is not None else None,
                "under_odds": float(r[10]) if r[10] is not None else None,
            }
            for r in rows
        ]

    def snapshot_and_track_picks(
        self,
        simulator: WhatIfSimulator,
        slate_service: SlateService,
        injury_service: InjuryService,
        game_date_str: str | None = None,
        markets: tuple[str, ...] = DEFAULT_MARKETS,
        force_refresh: bool = False,
        min_edge_override: float | None = None,
    ) -> dict[str, Any]:
        """Automated daily workflow: fetch prop lines, run ML projections, register high-edge picks."""
        slate = slate_service.get_today_slate(game_date=game_date_str)
        ref_date = date.fromisoformat(str(slate["game_date"])[:10])
        games = slate.get("games") or []

        lines_count, from_cache = self.fetch_and_cache_lines(
            target_date=ref_date,
            markets=markets,
            force_refresh=force_refresh,
        )
        # Always cache FREE Vegas spreads & totals from ESPN (0 Odds API credits!)
        self.fetch_and_cache_game_odds(target_date=ref_date, force_refresh=force_refresh)
        lines = self.get_lines_for_date(ref_date)

        # Index lines by (player_name_norm) -> list[line_dict]
        lines_by_player: dict[str, list[dict[str, Any]]] = {}
        for ln in lines:
            lines_by_player.setdefault(ln["player_name_norm"], []).append(ln)

        inj_data = injury_service.get_injuries()
        by_name: dict[str, dict[str, str]] = inj_data.get("by_name") or {}

        now_ts = datetime.now(UTC).replace(tzinfo=None)
        picks_to_upsert: list[tuple[Any, ...]] = []
        matched_players = 0

        for g in games:
            away_id = int(g["away_team_id"])
            home_id = int(g["home_team_id"])
            game_id = str(g["game_id"])

            # Auto-detect official ESPN OUT players for both teams
            away_sim_init = simulator.simulate_team(
                team_id=away_id,
                opponent_team_id=home_id,
                out_player_ids=[],
                is_back_to_back=bool(g.get("away_b2b", False)),
                is_home=False,
                limit=10,
            )
            home_sim_init = simulator.simulate_team(
                team_id=home_id,
                opponent_team_id=away_id,
                out_player_ids=[],
                is_back_to_back=bool(g.get("home_b2b", False)),
                is_home=True,
                limit=10,
            )
            away_outs = [
                int(r["player_id"])
                for r in away_sim_init["rows"]
                if (by_name.get(normalize_name(str(r["player_name"]))) or {}).get("status") == "OUT"
            ]
            home_outs = [
                int(r["player_id"])
                for r in home_sim_init["rows"]
                if (by_name.get(normalize_name(str(r["player_name"]))) or {}).get("status") == "OUT"
            ]

            away_payload = simulator.simulate_team(
                team_id=away_id,
                opponent_team_id=home_id,
                out_player_ids=away_outs,
                opponent_out_player_ids=home_outs,
                is_back_to_back=bool(g.get("away_b2b", False)),
                is_home=False,
                limit=10,
            )
            home_payload = simulator.simulate_team(
                team_id=home_id,
                opponent_team_id=away_id,
                out_player_ids=home_outs,
                opponent_out_player_ids=away_outs,
                is_back_to_back=bool(g.get("home_b2b", False)),
                is_home=True,
                limit=10,
            )

            for payload in (away_payload, home_payload):
                team_abbr = str(payload["team_abbreviation"])
                opp_abbr = str(payload["opponent_abbreviation"])
                for row in payload["rows"]:
                    if row["is_out"] or row["scenario"] is None:
                        continue
                    p_name = str(row["player_name"])
                    p_norm = normalize_name(p_name)
                    p_lines = lines_by_player.get(p_norm)
                    if not p_lines:
                        continue
                    matched_players += 1
                    scen = row["scenario"]
                    for ln in p_lines:
                        stat = ln["stat"]
                        if hasattr(scen, stat):
                            proj_val = float(getattr(scen, stat))
                        elif stat == "fg3m":
                            ch = self.get_player_chart_data(
                                player_id=int(row["player_id"]),
                                stat="fg3m",
                                line=float(ln["line"]),
                                side="OVER",
                                limit=10,
                            )
                            avg_m10 = float(ch["l10"]["avg_min"] or 0.0)
                            avg_3m10 = float(ch["l10"]["avg"] or 0.0)
                            scale = (float(scen.min) / avg_m10) if avg_m10 > 0 else 1.0
                            proj_val = max(0.0, avg_3m10 * scale)
                        else:
                            continue
                        line_val = float(ln["line"])
                        edge = round(proj_val - line_val, 2)
                        threshold = (
                            min_edge_override
                            if min_edge_override is not None
                            else DEFAULT_MIN_EDGE_BY_STAT.get(stat, 1.5)
                        )
                        if abs(edge) < threshold:
                            continue

                        side = "OVER" if edge > 0 else "UNDER"
                        odds = (
                            ln["over_odds"]
                            if side == "OVER"
                            else ln["under_odds"]
                        )
                        if odds is None or odds <= 1.01:
                            odds = 1.87  # Standard fallback decimal odds (-115)

                        pick_id = f"{ref_date.isoformat()}_{row['player_id']}_{stat}"
                        picks_to_upsert.append(
                            (
                                pick_id,
                                ref_date,
                                game_id,
                                int(row["player_id"]),
                                p_name,
                                team_abbr,
                                opp_abbr,
                                stat,
                                ln["bookmaker"],
                                line_val,
                                side,
                                round(float(odds), 3),
                                round(proj_val, 2),
                                edge,
                                "PENDING",
                                None,
                                None,
                                now_ts,
                                None,
                            )
                        )

        if picks_to_upsert:
            with self._connect_rw() as con:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO tracked_picks (
                        pick_id, game_date, game_id, player_id, player_name,
                        team_abbreviation, opponent_abbreviation, stat, bookmaker,
                        line, side, odds_decimal, projected_value, edge,
                        status, actual_value, profit_units, created_at, graded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    picks_to_upsert,
                )

        return {
            "game_date": ref_date.isoformat(),
            "games_analyzed": len(games),
            "lines_available": lines_count,
            "lines_from_cache": from_cache,
            "matched_players": matched_players,
            "picks_registered": len(picks_to_upsert),
            "requests_remaining": self.requests_remaining,
            "requests_used": self.requests_used,
        }

    def grade_pending_picks(self) -> dict[str, Any]:
        """Automatically grade PENDING picks against actual NBA boxscores in player_game_logs."""
        now_ts = datetime.now(UTC).replace(tzinfo=None)
        graded = 0
        wins = 0
        losses = 0
        pushes = 0
        voids = 0

        with self._connect_rw() as con:
            pending = con.execute(
                """
                SELECT pick_id, game_date, player_id, stat, line, side, odds_decimal
                FROM tracked_picks
                WHERE status = 'PENDING'
                """
            ).fetchall()

            for pick_id, gdate, pid, stat, line, side, odds in pending:
                # Look up actual boxscore in +/- 1 day window (handles UTC/ET midnight offset)
                log_rows = con.execute(
                    """
                    SELECT min, pts, reb, ast, stl, blk, tov, fg3m
                    FROM player_game_logs
                    WHERE player_id = ?
                      AND game_date BETWEEN ? AND ?
                    ORDER BY ABS(DATEDIFF('day', game_date, ?)) ASC
                    LIMIT 1
                    """,
                    [pid, gdate - timedelta(days=1), gdate + timedelta(days=1), gdate],
                ).fetchall()

                if not log_rows:
                    # Check if ANY games exist in DuckDB for that date -> player was DNP (VOID)
                    day_games = int(
                        con.execute(
                            "SELECT COUNT(*) FROM games WHERE game_date = ?",
                            [gdate],
                        ).fetchall()[0][0]
                    )
                    if day_games > 0 and gdate < date.today():
                        con.execute(
                            """
                            UPDATE tracked_picks
                            SET status = 'VOID', actual_value = NULL, profit_units = 0.0, graded_at = ?
                            WHERE pick_id = ?
                            """,
                            [now_ts, pick_id],
                        )
                        graded += 1
                        voids += 1
                    continue

                m_min, pts, reb, ast, stl, blk, tov, fg3m = [float(x or 0.0) for x in log_rows[0]]
                if m_min <= 0.0:
                    con.execute(
                        """
                        UPDATE tracked_picks
                        SET status = 'VOID', actual_value = 0.0, profit_units = 0.0, graded_at = ?
                        WHERE pick_id = ?
                        """,
                        [now_ts, pick_id],
                    )
                    graded += 1
                    voids += 1
                    continue

                stat_map = {
                    "min": m_min,
                    "pts": pts,
                    "reb": reb,
                    "ast": ast,
                    "pra": pts + reb + ast,
                    "pr": pts + reb,
                    "pa": pts + ast,
                    "ra": reb + ast,
                    "stl": stl,
                    "blk": blk,
                    "tov": tov,
                    "fg3m": fg3m,
                }
                actual = round(stat_map.get(str(stat), 0.0), 2)

                if abs(actual - float(line)) < 1e-6:
                    status = "PUSH"
                    profit = 0.0
                    pushes += 1
                elif (side == "OVER" and actual > float(line)) or (
                    side == "UNDER" and actual < float(line)
                ):
                    status = "WIN"
                    profit = round(float(odds) - 1.0, 4)
                    wins += 1
                else:
                    status = "LOSS"
                    profit = -1.0
                    losses += 1

                con.execute(
                    """
                    UPDATE tracked_picks
                    SET status = ?, actual_value = ?, profit_units = ?, graded_at = ?
                    WHERE pick_id = ?
                    """,
                    [status, actual, profit, now_ts, pick_id],
                )
                graded += 1

        return {
            "graded": graded,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "voids": voids,
        }

    def get_performance_summary(self, limit: int = 100) -> dict[str, Any]:
        """Compute live Win Rate, ROI, net units, and breakdowns by stat and edge size."""
        with self._connect_rw() as con:
            rows = con.execute(
                """
                SELECT
                    pick_id, CAST(game_date AS VARCHAR), game_id, player_id, player_name,
                    team_abbreviation, opponent_abbreviation, stat, bookmaker,
                    line, side, odds_decimal, projected_value, edge,
                    status, actual_value, profit_units
                FROM tracked_picks
                ORDER BY game_date DESC, ABS(edge) DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()

        picks = [
            {
                "pick_id": str(r[0]),
                "game_date": str(r[1])[:10],
                "game_id": str(r[2]) if r[2] is not None else None,
                "player_id": int(r[3]),
                "player_name": str(r[4]),
                "team_abbreviation": str(r[5]),
                "opponent_abbreviation": str(r[6]),
                "stat": str(r[7]),
                "bookmaker": str(r[8]),
                "line": float(r[9]),
                "side": str(r[10]),
                "odds_decimal": float(r[11]),
                "projected_value": float(r[12]),
                "edge": float(r[13]),
                "status": str(r[14]),
                "actual_value": float(r[15]) if r[15] is not None else None,
                "profit_units": float(r[16]) if r[16] is not None else None,
            }
            for r in rows
        ]

        def _summarize(subset: list[dict[str, Any]]) -> dict[str, Any]:
            wins = sum(1 for p in subset if p["status"] == "WIN")
            losses = sum(1 for p in subset if p["status"] == "LOSS")
            pushes = sum(1 for p in subset if p["status"] == "PUSH")
            voids = sum(1 for p in subset if p["status"] == "VOID")
            pending = sum(1 for p in subset if p["status"] == "PENDING")
            decided = wins + losses
            staked = wins + losses + pushes
            net_units = round(sum(float(p["profit_units"] or 0.0) for p in subset), 2)
            win_rate = round((wins / decided) * 100.0, 1) if decided > 0 else None
            roi = round((net_units / staked) * 100.0, 1) if staked > 0 else None
            return {
                "total": len(subset),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "voids": voids,
                "pending": pending,
                "win_rate_pct": win_rate,
                "roi_pct": roi,
                "net_units": net_units,
            }

        overall = _summarize(picks)

        by_stat: list[dict[str, Any]] = []
        for st in sorted({p["stat"] for p in picks}):
            sub = [p for p in picks if p["stat"] == st]
            by_stat.append({"label": st.upper(), **_summarize(sub)})

        buckets = [
            ("Edge 1.0 – 2.5", lambda e: 1.0 <= abs(e) < 2.5),
            ("Edge 2.5 – 4.0", lambda e: 2.5 <= abs(e) < 4.0),
            ("Edge 4.0+", lambda e: abs(e) >= 4.0),
        ]
        by_edge_bucket: list[dict[str, Any]] = []
        for label, pred in buckets:
            sub = [p for p in picks if pred(float(p["edge"]))]
            if sub:
                by_edge_bucket.append({"label": label, **_summarize(sub)})

        return {
            "overall": overall,
            "by_stat": by_stat,
            "by_edge_bucket": by_edge_bucket,
            "requests_remaining": self.requests_remaining,
            "picks": picks,
        }

    @staticmethod
    def _extract_log_stat(row: tuple[Any, ...], stat: str) -> float:
        # row: (game_id, game_date, opp_abbr, is_home, wl, min, pts, reb, ast, stl, blk, tov, fg3m)
        pts = float(row[6] or 0.0)
        reb = float(row[7] or 0.0)
        ast = float(row[8] or 0.0)
        stl = float(row[9] or 0.0)
        blk = float(row[10] or 0.0)
        tov = float(row[11] or 0.0)
        fg3m = float(row[12] or 0.0) if len(row) > 12 else 0.0
        m_min = float(row[5] or 0.0)
        mapping = {
            "min": m_min,
            "pts": pts,
            "reb": reb,
            "ast": ast,
            "pra": pts + reb + ast,
            "pr": pts + reb,
            "pa": pts + ast,
            "ra": reb + ast,
            "stl": stl,
            "blk": blk,
            "tov": tov,
            "fg3m": fg3m,
        }
        return round(mapping.get(stat, 0.0), 1)

    def get_player_chart_data(
        self,
        player_id: int,
        stat: str,
        line: float,
        side: str = "OVER",
        without_player_id: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return recent game logs and exact Hit Rates for a player/stat/line with optional WOWY filter."""
        with self._connect_rw() as con:
            if without_player_id is not None:
                # Games played by target player where without_player_id did NOT play (>0 min) on same team
                rows = con.execute(
                    """
                    WITH played_other AS (
                        SELECT DISTINCT game_id
                        FROM player_game_logs
                        WHERE player_id = ? AND COALESCE(min, 0) > 0
                    )
                    SELECT
                        l.game_id,
                        CAST(l.game_date AS VARCHAR),
                        COALESCE(opp.abbreviation, 'OPP'),
                        COALESCE(l.is_home, false),
                        l.wl,
                        COALESCE(l.min, 0.0),
                        COALESCE(l.pts, 0.0),
                        COALESCE(l.reb, 0.0),
                        COALESCE(l.ast, 0.0),
                        COALESCE(l.stl, 0.0),
                        COALESCE(l.blk, 0.0),
                        COALESCE(l.tov, 0.0),
                        COALESCE(l.fg3m, 0.0)
                    FROM player_game_logs l
                    LEFT JOIN teams opp ON l.opp_team_id = opp.team_id
                    LEFT JOIN played_other po ON l.game_id = po.game_id
                    WHERE l.player_id = ?
                      AND COALESCE(l.min, 0) > 0
                      AND po.game_id IS NULL
                    ORDER BY l.game_date DESC, l.game_id DESC
                    LIMIT ?
                    """,
                    [without_player_id, player_id, limit],
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT
                        l.game_id,
                        CAST(l.game_date AS VARCHAR),
                        COALESCE(opp.abbreviation, 'OPP'),
                        COALESCE(l.is_home, false),
                        l.wl,
                        COALESCE(l.min, 0.0),
                        COALESCE(l.pts, 0.0),
                        COALESCE(l.reb, 0.0),
                        COALESCE(l.ast, 0.0),
                        COALESCE(l.stl, 0.0),
                        COALESCE(l.blk, 0.0),
                        COALESCE(l.tov, 0.0),
                        COALESCE(l.fg3m, 0.0)
                    FROM player_game_logs l
                    LEFT JOIN teams opp ON l.opp_team_id = opp.team_id
                    WHERE l.player_id = ?
                      AND COALESCE(l.min, 0) > 0
                    ORDER BY l.game_date DESC, l.game_id DESC
                    LIMIT ?
                    """,
                    [player_id, limit],
                ).fetchall()

        side_upper = side.upper()
        games: list[dict[str, Any]] = []
        for r in rows:
            val = self._extract_log_stat(r, stat)
            hit = (val > line) if side_upper == "OVER" else (val < line)
            games.append(
                {
                    "game_id": str(r[0]),
                    "game_date": str(r[1])[:10],
                    "opp_abbr": str(r[2]),
                    "is_home": bool(r[3]),
                    "wl": str(r[4]) if r[4] is not None else None,
                    "min": round(float(r[5] or 0.0), 1),
                    "value": val,
                    "hit": hit,
                }
            )

        def _hr(sub: list[dict[str, Any]]) -> dict[str, Any]:
            n = len(sub)
            hits = sum(1 for g in sub if g["hit"])
            avg_val = round(sum(g["value"] for g in sub) / n, 1) if n > 0 else 0.0
            avg_min = round(sum(g["min"] for g in sub) / n, 1) if n > 0 else 0.0
            return {
                "hits": hits,
                "total": n,
                "pct": round((hits / n) * 100.0, 1) if n > 0 else 0.0,
                "avg": avg_val,
                "avg_min": avg_min,
            }

        return {
            "player_id": player_id,
            "stat": stat,
            "line": line,
            "side": side_upper,
            "without_player_id": without_player_id,
            "l5": _hr(games[:5]),
            "l10": _hr(games[:10]),
            "l20": _hr(games[:20]),
            "home": _hr([g for g in games if g["is_home"]]),
            "away": _hr([g for g in games if not g["is_home"]]),
            # Chronological order (oldest -> newest) for left-to-right bar chart
            "games": list(reversed(games)),
        }

    def build_prop_board(
        self,
        simulator: WhatIfSimulator,
        slate_service: SlateService,
        injury_service: InjuryService,
        game_date_str: str | None = None,
    ) -> dict[str, Any]:
        """Build the visual Outlier-style Prop Board using cached lines, game logs, Spread/Total, and ML."""
        slate = slate_service.get_today_slate(game_date=game_date_str)
        ref_date = date.fromisoformat(str(slate["game_date"])[:10])
        games = slate.get("games") or []
        lines = self.get_lines_for_date(ref_date)
        game_odds_by_event = self.get_game_odds_for_date(ref_date)

        # Auto-fetch FREE ESPN spreads & totals if not cached yet (0 credits!)
        if not game_odds_by_event and games:
            self.fetch_and_cache_game_odds(target_date=ref_date, force_refresh=False)
            game_odds_by_event = self.get_game_odds_for_date(ref_date)

        # Index lines by player_name_norm
        lines_by_player: dict[str, list[dict[str, Any]]] = {}
        for ln in lines:
            lines_by_player.setdefault(ln["player_name_norm"], []).append(ln)

        inj_data = injury_service.get_injuries()
        by_name: dict[str, dict[str, str]] = inj_data.get("by_name") or {}

        # Fetch all recent 20 logs in one query for matched players after simulating teams
        board_items: list[dict[str, Any]] = []
        game_meta_list: list[dict[str, Any]] = []

        for g in games:
            away_id = int(g["away_team_id"])
            home_id = int(g["home_team_id"])
            away_abbr = str(g["away_abbr"])
            home_abbr = str(g["home_abbr"])
            game_id = str(g["game_id"])

            # Run fast cached team simulations with ESPN OUT players
            away_init = simulator.simulate_team(
                team_id=away_id,
                opponent_team_id=home_id,
                out_player_ids=[],
                is_back_to_back=bool(g.get("away_b2b", False)),
                is_home=False,
                limit=10,
            )
            home_init = simulator.simulate_team(
                team_id=home_id,
                opponent_team_id=away_id,
                out_player_ids=[],
                is_back_to_back=bool(g.get("home_b2b", False)),
                is_home=True,
                limit=10,
            )
            away_outs = [
                int(r["player_id"])
                for r in away_init["rows"]
                if (by_name.get(normalize_name(str(r["player_name"]))) or {}).get("status") == "OUT"
            ]
            home_outs = [
                int(r["player_id"])
                for r in home_init["rows"]
                if (by_name.get(normalize_name(str(r["player_name"]))) or {}).get("status") == "OUT"
            ]

            away_sim = simulator.simulate_team(
                team_id=away_id,
                opponent_team_id=home_id,
                out_player_ids=away_outs,
                opponent_out_player_ids=home_outs,
                is_back_to_back=bool(g.get("away_b2b", False)),
                is_home=False,
                limit=10,
            )
            home_sim = simulator.simulate_team(
                team_id=home_id,
                opponent_team_id=away_id,
                out_player_ids=home_outs,
                opponent_out_player_ids=away_outs,
                is_back_to_back=bool(g.get("home_b2b", False)),
                is_home=True,
                limit=10,
            )

            game_meta_list.append(
                {
                    "game_id": game_id,
                    "away_abbr": away_abbr,
                    "home_abbr": home_abbr,
                    "game_status": str(g.get("game_status") or ""),
                }
            )

            for payload, is_home_side in ((away_sim, False), (home_sim, True)):
                team_abbr = str(payload["team_abbreviation"])
                opp_abbr = str(payload["opponent_abbreviation"])
                opp_def = payload.get("opponent_defense")
                teammates = [
                    {
                        "player_id": int(r["player_id"]),
                        "player_name": str(r["player_name"]),
                        "is_out": bool(r["is_out"]),
                    }
                    for r in payload["rows"][:7]
                ]
                out_names = [t["player_name"] for t in teammates if t["is_out"]]

                for row in payload["rows"]:
                    if row["is_out"] or row["scenario"] is None:
                        continue
                    pid = int(row["player_id"])
                    p_name = str(row["player_name"])
                    p_norm = normalize_name(p_name)
                    p_lines = lines_by_player.get(p_norm)
                    if not p_lines:
                        continue

                    inj_rec = by_name.get(p_norm)
                    scen = row["scenario"]

                    for ln in p_lines:
                        stat = ln["stat"]
                        line_val = float(ln["line"])

                        # Compute real historical Hit Rates from DuckDB first
                        over_chart = self.get_player_chart_data(
                            player_id=pid,
                            stat=stat,
                            line=line_val,
                            side="OVER",
                            limit=20,
                        )

                        if hasattr(scen, stat):
                            ml_proj = round(float(getattr(scen, stat)), 1)
                        elif stat == "fg3m":
                            # Minutes-adjusted 3PM projection from real L10 rate
                            avg_m10 = float(over_chart["l10"]["avg_min"] or 0.0)
                            avg_3m10 = float(over_chart["l10"]["avg"] or 0.0)
                            scale = (float(scen.min) / avg_m10) if avg_m10 > 0 else 1.0
                            ml_proj = round(max(0.0, avg_3m10 * scale), 1)
                        else:
                            continue
                        ml_edge = round(ml_proj - line_val, 1)
                        l10_over_pct = float(over_chart["l10"]["pct"])
                        l10_under_pct = round(100.0 - l10_over_pct, 1) if over_chart["l10"]["total"] > 0 else 0.0

                        # Pick dominant recommended side based on real Hit Rate L10 (tie-break by ML edge)
                        if l10_over_pct > l10_under_pct:
                            rec_side = "OVER"
                        elif l10_under_pct > l10_over_pct:
                            rec_side = "UNDER"
                        else:
                            rec_side = "OVER" if ml_edge >= 0 else "UNDER"

                        chart = (
                            over_chart
                            if rec_side == "OVER"
                            else self.get_player_chart_data(
                                player_id=pid,
                                stat=stat,
                                line=line_val,
                                side="UNDER",
                                limit=20,
                            )
                        )

                        odds_dec = (
                            ln["over_odds"] if rec_side == "OVER" else ln["under_odds"]
                        )
                        if odds_dec is None or odds_dec <= 1.01:
                            odds_dec = 1.87

                        ml_agrees = (rec_side == "OVER" and ml_edge > 0.3) or (
                            rec_side == "UNDER" and ml_edge < -0.3
                        )

                        # Vegas game odds (spread & total from free ESPN feed or cached event)
                        ev_odds = game_odds_by_event.get(
                            f"{away_abbr}@{home_abbr}"
                        ) or game_odds_by_event.get(ln["event_id"])
                        team_spread: float | None = None
                        game_total: float | None = None
                        if ev_odds:
                            team_spread = (
                                ev_odds["spread_home"] if is_home_side else ev_odds["spread_away"]
                            )
                            game_total = ev_odds["total_points"]
                        blowout_risk = team_spread is not None and abs(team_spread) >= 11.5

                        # Transparent Conviction Score (0-100): real trend + ML agreement - blowout penalty
                        hr10 = float(chart["l10"]["pct"])
                        hr5 = float(chart["l5"]["pct"])
                        score = 0.55 * hr10 + 0.25 * hr5 + (15.0 if ml_agrees else 0.0)
                        if blowout_risk and rec_side == "OVER":
                            score -= 10.0
                        conviction = round(max(0.0, min(100.0, score)))

                        board_items.append(
                            {
                                "id": f"{ref_date.isoformat()}_{pid}_{stat}",
                                "game_id": game_id,
                                "game_date": ref_date.isoformat(),
                                "player_id": pid,
                                "player_name": p_name,
                                "pos_group": str(row["pos_group"]),
                                "team_abbreviation": team_abbr,
                                "opponent_abbreviation": opp_abbr,
                                "is_home": is_home_side,
                                "is_back_to_back": bool(payload["is_back_to_back"]),
                                "injury_status": inj_rec["status"] if inj_rec else None,
                                "injury_note": inj_rec["comment"] if inj_rec else None,
                                "team_outs": out_names,
                                "stat": stat,
                                "bookmaker": ln["bookmaker"],
                                "line": line_val,
                                "recommended_side": rec_side,
                                "odds_decimal": round(float(odds_dec), 2),
                                "over_odds": ln["over_odds"],
                                "under_odds": ln["under_odds"],
                                "ml_projection": ml_proj,
                                "ml_edge": ml_edge,
                                "ml_agrees": ml_agrees,
                                "conviction_score": conviction,
                                "team_spread": team_spread,
                                "game_total": game_total,
                                "blowout_risk": blowout_risk,
                                "opp_def_rank": opp_def.def_rating_rank if opp_def else None,
                                "opp_def_rating_10": opp_def.def_rating_10 if opp_def else None,
                                "opp_pace_rank": opp_def.pace_rank if opp_def else None,
                                "l5": chart["l5"],
                                "l10": chart["l10"],
                                "l20": chart["l20"],
                                "home_split": chart["home"],
                                "away_split": chart["away"],
                                "games": chart["games"],
                                "teammates": [t for t in teammates if t["player_id"] != pid],
                            }
                        )

        board_items.sort(
            key=lambda x: (x["conviction_score"], x["l10"]["pct"], abs(x["ml_edge"])),
            reverse=True,
        )

        return {
            "game_date": ref_date.isoformat(),
            "games": game_meta_list,
            "total_props": len(board_items),
            "requests_remaining": self.requests_remaining,
            "items": board_items,
        }
