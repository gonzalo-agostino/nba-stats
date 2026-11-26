"""FastAPI application for nba-stats (Phase 4)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from nba_stats.agent.graph import build_prediction_graph, run_agent_query
from nba_stats.agent.nodes import AgentNodes
from nba_stats.api.injuries import InjuryService, normalize_name
from nba_stats.api.odds import DEFAULT_MARKETS, OddsService
from nba_stats.api.schemas import (
    AskRequest,
    AskResponse,
    ExplainResponse,
    GamePaceSummary,
    GameSimulationRequest,
    GameSimulationResponse,
    PlayerGameLogItem,
    PlayerProfileResponse,
    PlayerSearchItem,
    PlayerSplitsSummary,
    RosterPlayerItem,
    TeamItem,
    TeamSimulationRequest,
    TeamSimulationResponse,
    TodaySlateResponse,
)
from nba_stats.api.slate import SlateService
from nba_stats.config import Settings, load_ingestion_config
from nba_stats.data.ingest import run_ingestion
from nba_stats.simulator.schemas import ScenarioRequest, SimulationResult
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator


def create_app(
    settings: Settings | None = None,
    simulator: WhatIfSimulator | None = None,
    agent_graph: Any | None = None,
    agent_nodes: AgentNodes | None = None,
    slate_service: SlateService | None = None,
    injury_service: InjuryService | None = None,
    odds_service: OddsService | None = None,
    enable_scheduler: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = settings or Settings()

    state: dict[str, Any] = {
        "simulator": simulator,
        "agent_graph": agent_graph,
        "agent_nodes": agent_nodes,
        "slate_service": slate_service or SlateService(duckdb_path=cfg.duckdb_path),
        "injury_service": injury_service or InjuryService(),
        "odds_service": odds_service
        or OddsService(duckdb_path=cfg.duckdb_path, api_key=cfg.the_odds_api_key),
    }

    def _get_simulator() -> WhatIfSimulator:
        if state["simulator"] is None:
            state["simulator"] = WhatIfSimulator(settings=cfg)
        return state["simulator"]

    def _get_agent_nodes() -> AgentNodes:
        if state["agent_nodes"] is None:
            state["agent_nodes"] = AgentNodes(simulator=_get_simulator(), settings=cfg)
        return state["agent_nodes"]

    def _get_graph() -> Any:
        if state["agent_graph"] is None:
            state["agent_graph"] = build_prediction_graph(
                simulator=_get_simulator(),
                settings=cfg,
            )
        return state["agent_graph"]

    def _connect_db() -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(cfg.duckdb_path), read_only=True)

    def _get_latest_game_date() -> str | None:
        if not cfg.duckdb_path.exists():
            return None
        try:
            with _connect_db() as con:
                row = con.execute("SELECT MAX(game_date) FROM player_game_logs").fetchall()
            if row and row[0][0] is not None:
                return str(row[0][0])[:10]
        except duckdb.Error:
            return None
        return None

    async def _auto_scheduler_loop() -> None:
        """Zero-dependency background loop for daily sync/grading and pre-game prop snapshots."""
        last_sync_day: date | None = None
        last_snapshot_day: date | None = None

        while True:
            try:
                now_local = datetime.now()
                today = now_local.date()

                # 1. Morning auto-sync & auto-grade (after 09:00 local, once per day)
                if now_local.hour >= 9 and last_sync_day != today:
                    latest = _get_latest_game_date()
                    yesterday_iso = (today - timedelta(days=1)).isoformat()
                    if latest is None or latest < yesterday_iso:
                        logger.info("Auto-scheduler: running daily incremental sync & pick grading...")
                        ing_cfg = load_ingestion_config(cfg)
                        await asyncio.to_thread(
                            run_ingestion,
                            seasons=[ing_cfg.active_season],
                            force=False,
                            settings=cfg,
                            config=ing_cfg,
                        )
                        if state["simulator"] is not None:
                            state["simulator"].clear_caches()
                        state["slate_service"]._cache.clear()
                    grade_res = await asyncio.to_thread(state["odds_service"].grade_pending_picks)
                    if grade_res.get("graded", 0) > 0:
                        logger.info("Auto-scheduler: graded %d pending picks.", grade_res["graded"])
                    last_sync_day = today

                # 2. Pre-game auto-snapshot (between 18:00 and 22:00 local, once per day, 0 duplicate credits)
                if 18 <= now_local.hour <= 22 and last_snapshot_day != today and cfg.the_odds_api_key:
                    slate = await asyncio.to_thread(
                        state["slate_service"].get_today_slate,
                        today.isoformat(),
                    )
                    if slate.get("games"):
                        logger.info("Auto-scheduler: capturing daily prop lines & tracking picks for %s...", today)
                        snap = await asyncio.to_thread(
                            state["odds_service"].snapshot_and_track_picks,
                            _get_simulator(),
                            state["slate_service"],
                            state["injury_service"],
                            today.isoformat(),
                            DEFAULT_MARKETS,
                            False,
                        )
                        logger.info(
                            "Auto-scheduler: %d picks registered (from_cache=%s).",
                            snap.get("picks_registered", 0),
                            snap.get("lines_from_cache", False),
                        )
                    last_snapshot_day = today
            except Exception as exc:  # noqa: BLE001
                logger.warning("Auto-scheduler tick error: %s", exc)

            await asyncio.sleep(30 * 60)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if enable_scheduler and cfg.auto_sync_enabled:
            task = asyncio.create_task(_auto_scheduler_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(
        title="NBA Stats Analytics API",
        version="0.4.0",
        description="Real-time What-If NBA player stats simulator (LightGBM + SHAP + WOWY) and LangGraph agent.",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    def health() -> dict[str, str | None]:
        return {"status": "ok", "latest_game_date": _get_latest_game_date()}

    @app.post("/api/v1/sync")
    def sync_warehouse() -> dict[str, Any]:
        """Run incremental ingestion for the active season and invalidate in-memory caches."""
        ing_cfg = load_ingestion_config(cfg)
        try:
            report = run_ingestion(
                seasons=[ing_cfg.active_season],
                force=False,
                settings=cfg,
                config=ing_cfg,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error sincronizando datos: {exc}") from exc

        if state["simulator"] is not None:
            state["simulator"].clear_caches()
        state["slate_service"]._cache.clear()
        state["injury_service"].get_injuries(force_refresh=True)
        grade_summary = state["odds_service"].grade_pending_picks()

        summary = report.seasons[0] if report.seasons else None
        return {
            "status": "ok",
            "season": ing_cfg.active_season,
            "games_ingested": summary.games_rows if summary else 0,
            "player_logs_ingested": summary.player_logs_rows if summary else 0,
            "latest_game_date": _get_latest_game_date(),
            "graded_picks": grade_summary,
        }

    @app.get("/api/v1/props/lines")
    def get_prop_lines(
        game_date: str | None = Query(default=None, description="Fecha YYYY-MM-DD."),
    ) -> dict[str, Any]:
        from datetime import date as _date

        svc: OddsService = state["odds_service"]
        ref_date = _date.fromisoformat(game_date[:10]) if game_date else _date.today()
        lines = svc.get_lines_for_date(ref_date)
        return {
            "game_date": ref_date.isoformat(),
            "lines": lines,
            "requests_remaining": svc.requests_remaining,
        }

    @app.post("/api/v1/props/snapshot")
    def snapshot_daily_props(
        game_date: str | None = Query(default=None, description="Fecha YYYY-MM-DD opcional."),
        force_refresh: bool = Query(default=False),
        min_edge: float | None = Query(default=None, ge=0.5, le=10.0),
    ) -> dict[str, Any]:
        """Auto-fetch market prop lines, run ML projections, and register high-edge picks."""
        svc: OddsService = state["odds_service"]
        try:
            return svc.snapshot_and_track_picks(
                simulator=_get_simulator(),
                slate_service=state["slate_service"],
                injury_service=state["injury_service"],
                game_date_str=game_date,
                markets=DEFAULT_MARKETS,
                force_refresh=force_refresh,
                min_edge_override=min_edge,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error capturando líneas: {exc}") from exc

    @app.post("/api/v1/props/grade")
    def grade_picks() -> dict[str, Any]:
        """Grade all PENDING tracked picks against actual NBA game logs in DuckDB."""
        svc: OddsService = state["odds_service"]
        return svc.grade_pending_picks()

    @app.get("/api/v1/props/performance")
    def get_props_performance(
        limit: int = Query(default=150, ge=10, le=500),
    ) -> dict[str, Any]:
        """Return live Win Rate, ROI, net units, breakdowns, and tracked pick history."""
        svc: OddsService = state["odds_service"]
        return svc.get_performance_summary(limit=limit)

    @app.get("/api/v1/slate/today", response_model=TodaySlateResponse)
    def get_today_slate(
        game_date: str | None = Query(default=None, description="Fecha YYYY-MM-DD opcional."),
        refresh: bool = Query(default=False),
    ) -> TodaySlateResponse:
        svc: SlateService = state["slate_service"]
        payload = svc.get_today_slate(game_date=game_date, force_refresh=refresh)
        return TodaySlateResponse.model_validate(payload)

    @app.get("/api/v1/teams", response_model=list[TeamItem])
    def list_teams() -> list[TeamItem]:
        with _connect_db() as con:
            rows = con.execute(
                """
                SELECT team_id, abbreviation, full_name
                FROM teams
                ORDER BY abbreviation ASC
                """
            ).fetchall()
        return [TeamItem(team_id=int(r[0]), abbreviation=str(r[1]), full_name=str(r[2])) for r in rows]

    @app.get("/api/v1/players", response_model=list[PlayerSearchItem])
    def search_players(
        q: str = Query(default="", description="Búsqueda parcial por nombre (insensible a tildes)."),
        limit: int = Query(default=12, ge=1, le=50),
    ) -> list[PlayerSearchItem]:
        term = q.strip()
        with _connect_db() as con:
            rows = con.execute(
                """
                WITH ranked AS (
                    SELECT
                        player_id,
                        player_name,
                        team_id,
                        team_abbreviation,
                        min,
                        pts,
                        ROW_NUMBER() OVER (
                            PARTITION BY player_id
                            ORDER BY game_date DESC, game_id DESC
                        ) AS rn
                    FROM player_game_logs
                ),
                recent_10 AS (
                    SELECT
                        player_id,
                        AVG(min) AS avg_min_10,
                        AVG(pts) AS avg_pts_10
                    FROM ranked
                    WHERE rn <= 10
                    GROUP BY player_id
                )
                SELECT
                    l.player_id,
                    l.player_name,
                    l.team_id,
                    l.team_abbreviation,
                    COALESCE(p.pos_group, 'UNK') AS pos_group,
                    COALESCE(r10.avg_min_10, 0.0) AS avg_min_10,
                    COALESCE(r10.avg_pts_10, 0.0) AS avg_pts_10
                FROM ranked l
                LEFT JOIN players p ON l.player_id = p.player_id
                LEFT JOIN recent_10 r10 ON l.player_id = r10.player_id
                WHERE l.rn = 1
                  AND (? = '' OR strip_accents(LOWER(l.player_name)) LIKE strip_accents(LOWER(?)))
                ORDER BY
                    CASE WHEN ? <> '' AND strip_accents(LOWER(l.player_name)) LIKE strip_accents(LOWER(?)) THEN 0 ELSE 1 END,
                    r10.avg_min_10 DESC NULLS LAST,
                    l.player_name ASC
                LIMIT ?
                """,
                [term, f"%{term}%", term, f"{term}%", limit],
            ).fetchall()

        return [
            PlayerSearchItem(
                player_id=int(r[0]),
                full_name=str(r[1]),
                team_id=int(r[2]),
                team_abbreviation=str(r[3]),
                pos_group=str(r[4]),
                avg_min_10=round(float(r[5]), 1),
                avg_pts_10=round(float(r[6]), 1),
            )
            for r in rows
        ]

    @app.get("/api/v1/teams/{team_id}/roster", response_model=list[RosterPlayerItem])
    def get_team_roster(
        team_id: int,
        limit: int = Query(default=14, ge=1, le=25),
    ) -> list[RosterPlayerItem]:
        with _connect_db() as con:
            rows = con.execute(
                """
                WITH team_latest_season AS (
                    SELECT MAX(season) AS max_season
                    FROM team_game_logs
                    WHERE team_id = ?
                ),
                latest_player_team AS (
                    SELECT
                        player_id,
                        player_name,
                        team_id,
                        season,
                        ROW_NUMBER() OVER (
                            PARTITION BY player_id
                            ORDER BY game_date DESC, game_id DESC
                        ) AS rn
                    FROM player_game_logs
                ),
                active_on_team AS (
                    SELECT l.player_id, l.player_name, l.season
                    FROM latest_player_team l
                    CROSS JOIN team_latest_season tls
                    WHERE l.rn = 1
                      AND l.team_id = ?
                      AND (tls.max_season IS NULL OR l.season = tls.max_season)
                ),
                recent_logs AS (
                    SELECT
                        p.player_id,
                        a.player_name,
                        p.min,
                        COALESCE(p.usg_pct, 0.18) AS usg_pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY p.player_id
                            ORDER BY p.game_date DESC, p.game_id DESC
                        ) AS g_rn
                    FROM player_game_logs p
                    JOIN active_on_team a
                      ON p.player_id = a.player_id
                     AND p.team_id = ?
                ),
                weighted AS (
                    SELECT
                        player_id,
                        player_name,
                        COUNT(*) AS games_played,
                        SUM(min * POWER(0.85, g_rn - 1)) / NULLIF(SUM(POWER(0.85, g_rn - 1)), 0) AS ewma_min,
                        SUM(usg_pct * POWER(0.85, g_rn - 1)) / NULLIF(SUM(POWER(0.85, g_rn - 1)), 0) AS ewma_usg
                    FROM recent_logs
                    WHERE g_rn <= 15
                    GROUP BY player_id, player_name
                )
                SELECT
                    w.player_id,
                    w.player_name,
                    COALESCE(pl.pos_group, 'UNK') AS pos_group,
                    COALESCE(w.ewma_min, 0.0) AS ewma_min,
                    COALESCE(w.ewma_usg, 0.18) AS ewma_usg,
                    w.games_played
                FROM weighted w
                LEFT JOIN players pl ON w.player_id = pl.player_id
                ORDER BY w.ewma_min DESC
                LIMIT ?
                """,
                [team_id, team_id, team_id, limit],
            ).fetchall()

        return [
            RosterPlayerItem(
                player_id=int(r[0]),
                player_name=str(r[1]),
                pos_group=str(r[2]),
                ewma_min=round(float(r[3]), 1),
                ewma_usg=round(float(r[4]), 4),
                games_played=int(r[5]),
            )
            for r in rows
        ]

    @app.post("/api/v1/simulate", response_model=SimulationResult)
    def simulate_scenario(request: ScenarioRequest) -> SimulationResult:
        sim = _get_simulator()
        try:
            return sim.simulate(request)
        except EntityResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/v1/simulate-team", response_model=TeamSimulationResponse)
    def simulate_team_boxscore(request: TeamSimulationRequest) -> TeamSimulationResponse:
        sim = _get_simulator()
        try:
            payload = sim.simulate_team(
                team_id=request.team_id,
                opponent_team_id=request.opponent_team_id,
                out_player_ids=request.out_player_ids,
                is_back_to_back=request.is_back_to_back,
                is_home=request.is_home,
                limit=request.limit,
            )
            return TeamSimulationResponse.model_validate(payload)
        except EntityResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/injuries")
    def get_injuries(refresh: bool = Query(default=False)) -> dict[str, Any]:
        svc: InjuryService = state["injury_service"]
        data = svc.get_injuries(force_refresh=refresh)
        return {
            "updated_at": data["updated_at"],
            "by_team": data["by_team"],
            "total_out": data["total_out"],
            "total_dtd": data["total_dtd"],
            "fallback": data["fallback"],
        }

    @app.post("/api/v1/simulate-game", response_model=GameSimulationResponse)
    def simulate_full_game(request: GameSimulationRequest) -> GameSimulationResponse:
        sim = _get_simulator()
        inj_svc: InjuryService = state["injury_service"]
        inj_data = inj_svc.get_injuries()
        by_name: dict[str, dict[str, str]] = inj_data.get("by_name") or {}

        def _auto_out_ids_for_team(tid: int) -> list[int]:
            roster = get_team_roster(team_id=tid, limit=request.limit)
            out_ids: list[int] = []
            for item in roster:
                rec = by_name.get(normalize_name(item.player_name))
                if rec and rec.get("status") == "OUT":
                    out_ids.append(item.player_id)
            return out_ids

        away_outs = (
            _auto_out_ids_for_team(request.away_team_id)
            if request.auto_apply_injuries and request.away_out_player_ids is None
            else (request.away_out_player_ids or [])
        )
        home_outs = (
            _auto_out_ids_for_team(request.home_team_id)
            if request.auto_apply_injuries and request.home_out_player_ids is None
            else (request.home_out_player_ids or [])
        )

        try:
            away_payload = sim.simulate_team(
                team_id=request.away_team_id,
                opponent_team_id=request.home_team_id,
                out_player_ids=away_outs,
                opponent_out_player_ids=home_outs,
                is_back_to_back=request.away_b2b,
                is_home=False,
                limit=request.limit,
            )
            home_payload = sim.simulate_team(
                team_id=request.home_team_id,
                opponent_team_id=request.away_team_id,
                out_player_ids=home_outs,
                opponent_out_player_ids=away_outs,
                is_back_to_back=request.home_b2b,
                is_home=True,
                limit=request.limit,
            )
        except EntityResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        for payload in (away_payload, home_payload):
            for row in payload["rows"]:
                rec = by_name.get(normalize_name(str(row["player_name"])))
                if rec:
                    row["injury_status"] = rec["status"]
                    row["injury_note"] = rec["comment"]

        away_resp = TeamSimulationResponse.model_validate(away_payload)
        home_resp = TeamSimulationResponse.model_validate(home_payload)

        # home_resp.opponent_defense describes AWAY team; away_resp.opponent_defense describes HOME team
        away_profile = home_resp.opponent_defense
        home_profile = away_resp.opponent_defense
        away_pace = away_profile.pace_10 if away_profile else 99.0
        home_pace = home_profile.pace_10 if home_profile else 99.0
        expected_pace = round((away_pace + home_pace) / 2.0, 1)
        pace_label = (
            "Ritmo rápido"
            if expected_pace >= 101.0
            else ("Ritmo lento" if expected_pace <= 97.5 else "Ritmo normal")
        )

        pace_summary = GamePaceSummary(
            expected_pace=expected_pace,
            pace_label=pace_label,
            away_pace_10=round(away_pace, 1),
            away_pace_rank=away_profile.pace_rank if away_profile else None,
            away_def_rating_10=away_profile.def_rating_10 if away_profile else 113.0,
            away_def_rank=away_profile.def_rating_rank if away_profile else None,
            home_pace_10=round(home_pace, 1),
            home_pace_rank=home_profile.pace_rank if home_profile else None,
            home_def_rating_10=home_profile.def_rating_10 if home_profile else 113.0,
            home_def_rank=home_profile.def_rating_rank if home_profile else None,
        )

        return GameSimulationResponse(
            away=away_resp,
            home=home_resp,
            pace_summary=pace_summary,
            away_out_player_ids=away_outs,
            home_out_player_ids=home_outs,
            injuries_updated_at=inj_data.get("updated_at"),
            injuries_fallback=bool(inj_data.get("fallback", False)),
        )

    @app.get("/api/v1/players/{player_id}/profile", response_model=PlayerProfileResponse)
    def get_player_profile(
        player_id: int,
        limit: int = Query(default=15, ge=5, le=30),
    ) -> PlayerProfileResponse:
        with _connect_db() as con:
            meta = con.execute(
                """
                SELECT
                    l.player_id,
                    l.player_name,
                    l.team_id,
                    l.team_abbreviation,
                    COALESCE(p.pos_group, 'UNK') AS pos_group
                FROM player_game_logs l
                LEFT JOIN players p ON l.player_id = p.player_id
                WHERE l.player_id = ?
                ORDER BY l.game_date DESC, l.game_id DESC
                LIMIT 1
                """,
                [player_id],
            ).fetchall()
            if not meta:
                raise HTTPException(status_code=404, detail=f"Jugador {player_id} no encontrado.")

            total_games = int(
                con.execute(
                    "SELECT COUNT(*) FROM player_game_logs WHERE player_id = ?",
                    [player_id],
                ).fetchall()[0][0]
            )

            log_rows = con.execute(
                """
                SELECT
                    l.game_id,
                    CAST(l.game_date AS VARCHAR) AS game_date,
                    COALESCE(opp.abbreviation, 'OPP') AS opp_abbr,
                    COALESCE(l.is_home, false) AS is_home,
                    l.wl,
                    COALESCE(l.min, 0.0),
                    COALESCE(l.pts, 0.0),
                    COALESCE(l.reb, 0.0),
                    COALESCE(l.ast, 0.0),
                    COALESCE(l.stl, 0.0),
                    COALESCE(l.blk, 0.0),
                    COALESCE(l.tov, 0.0),
                    COALESCE(l.pf, 0.0),
                    COALESCE(l.fga, 0.0),
                    COALESCE(l.fg3a, 0.0),
                    COALESCE(l.ftm, 0.0),
                    COALESCE(l.fta, 0.0),
                    COALESCE(l.plus_minus, 0.0),
                    l.usg_pct,
                    l.ts_pct
                FROM player_game_logs l
                LEFT JOIN teams opp ON l.opp_team_id = opp.team_id
                WHERE l.player_id = ?
                ORDER BY l.game_date DESC, l.game_id DESC
                LIMIT ?
                """,
                [player_id, max(limit, 25)],
            ).fetchall()

        all_logs = [
            PlayerGameLogItem(
                game_id=str(r[0]),
                game_date=str(r[1])[:10],
                opponent_abbreviation=str(r[2]),
                is_home=bool(r[3]),
                wl=str(r[4]) if r[4] is not None else None,
                min=round(float(r[5]), 1),
                pts=round(float(r[6]), 1),
                reb=round(float(r[7]), 1),
                ast=round(float(r[8]), 1),
                pra=round(float(r[6]) + float(r[7]) + float(r[8]), 1),
                stl=round(float(r[9]), 1),
                blk=round(float(r[10]), 1),
                tov=round(float(r[11]), 1),
                pf=round(float(r[12]), 1),
                fga=round(float(r[13]), 1),
                fg3a=round(float(r[14]), 1),
                ftm=round(float(r[15]), 1),
                fta=round(float(r[16]), 1),
                plus_minus=round(float(r[17]), 1),
                usg_pct=round(float(r[18]), 3) if r[18] is not None else None,
                ts_pct=round(float(r[19]), 3) if r[19] is not None else None,
            )
            for r in log_rows
        ]

        def _make_split(label: str, subset: list[PlayerGameLogItem]) -> PlayerSplitsSummary | None:
            if not subset:
                return None
            n = len(subset)
            return PlayerSplitsSummary(
                label=label,
                games=n,
                min=round(sum(g.min for g in subset) / n, 1),
                pts=round(sum(g.pts for g in subset) / n, 1),
                reb=round(sum(g.reb for g in subset) / n, 1),
                ast=round(sum(g.ast for g in subset) / n, 1),
                pra=round(sum(g.pra for g in subset) / n, 1),
                stl=round(sum(g.stl for g in subset) / n, 1),
                blk=round(sum(g.blk for g in subset) / n, 1),
                tov=round(sum(g.tov for g in subset) / n, 1),
                fga=round(sum(g.fga for g in subset) / n, 1),
                ftm=round(sum(g.ftm for g in subset) / n, 1),
            )

        splits_candidates = [
            _make_split("Últ. 5", all_logs[:5]),
            _make_split("Últ. 10", all_logs[:10]),
            _make_split("Últ. 20", all_logs[:20]),
            _make_split("Local", [g for g in all_logs[:20] if g.is_home]),
            _make_split("Visitante", [g for g in all_logs[:20] if not g.is_home]),
        ]
        splits = [s for s in splits_candidates if s is not None]

        m = meta[0]
        return PlayerProfileResponse(
            player_id=int(m[0]),
            full_name=str(m[1]),
            team_id=int(m[2]),
            team_abbreviation=str(m[3]),
            pos_group=str(m[4]),
            total_games=total_games,
            splits=splits,
            recent_games=all_logs[:limit],
        )

    @app.post("/api/v1/explain", response_model=ExplainResponse)
    def explain_scenario_direct(request: ScenarioRequest) -> ExplainResponse:
        """Fast direct AI explanation for a structured ScenarioRequest (skips parse_query LLM call)."""
        sim = _get_simulator()
        try:
            sim_result = sim.simulate(request)
            nodes = _get_agent_nodes()
            analysis, passed = nodes.explain_simulation_direct(sim_result)
        except EntityResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error explicando escenario: {exc}") from exc

        return ExplainResponse(
            ml_result=sim_result,
            analysis=analysis,
            verification_passed=passed,
        )

    @app.post("/api/v1/ask", response_model=AskResponse)
    def ask_agent(request: AskRequest) -> AskResponse:
        try:
            graph = _get_graph()
            out = run_agent_query(query=request.query, settings=cfg, graph=graph)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Error ejecutando agente: {exc}") from exc

        return AskResponse(
            query=request.query,
            parsed_scenario=out.get("parsed_scenario"),
            ml_result=out.get("ml_result"),
            analysis=out.get("draft_analysis"),
            verification_passed=bool(out.get("verification_passed", False)),
            clarification_message=out.get("clarification_message"),
            final_response=str(out.get("final_response", "")),
        )

    return app


app = create_app(enable_scheduler=True)
