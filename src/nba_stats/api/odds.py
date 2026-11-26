"""Automated NBA Player Props fetcher (The Odds API), pick tracker, and Win Rate/ROI grader."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import requests

from nba_stats.api.injuries import InjuryService, normalize_name
from nba_stats.api.slate import SlateService
from nba_stats.data.schema import init_schema
from nba_stats.simulator.what_if import WhatIfSimulator

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/basketball_nba"

MARKET_TO_STAT: dict[str, str] = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_points_rebounds_assists": "pra",
    "player_points_rebounds": "pr",
    "player_points_assists": "pa",
    "player_rebounds_assists": "ra",
}

DEFAULT_MARKETS: tuple[str, ...] = (
    "player_points_rebounds_assists",
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
)

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

    def _connect_ro(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.duckdb_path), read_only=True)

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
                        if not hasattr(scen, stat):
                            continue
                        proj_val = float(getattr(scen, stat))
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
                    SELECT min, pts, reb, ast, stl, blk, tov
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

                m_min, pts, reb, ast, stl, blk, tov = [float(x or 0.0) for x in log_rows[0]]
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
