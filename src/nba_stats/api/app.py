"""FastAPI application for nba-stats (Phase 4)."""

from __future__ import annotations

from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from nba_stats.agent.graph import build_prediction_graph, run_agent_query
from nba_stats.api.schemas import (
    AskRequest,
    AskResponse,
    PlayerSearchItem,
    RosterPlayerItem,
    TeamItem,
    TeamSimulationRequest,
    TeamSimulationResponse,
    TodaySlateResponse,
)
from nba_stats.api.slate import SlateService
from nba_stats.config import Settings
from nba_stats.simulator.schemas import ScenarioRequest, SimulationResult
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator


def create_app(
    settings: Settings | None = None,
    simulator: WhatIfSimulator | None = None,
    agent_graph: Any | None = None,
    slate_service: SlateService | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = settings or Settings()

    app = FastAPI(
        title="NBA Stats Analytics API",
        version="0.4.0",
        description="Real-time What-If NBA player stats simulator (LightGBM + SHAP + WOWY) and LangGraph agent.",
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

    state: dict[str, Any] = {
        "simulator": simulator,
        "agent_graph": agent_graph,
        "slate_service": slate_service or SlateService(duckdb_path=cfg.duckdb_path),
    }

    def _get_simulator() -> WhatIfSimulator:
        if state["simulator"] is None:
            state["simulator"] = WhatIfSimulator(settings=cfg)
        return state["simulator"]

    def _get_graph() -> Any:
        if state["agent_graph"] is None:
            state["agent_graph"] = build_prediction_graph(
                simulator=_get_simulator(),
                settings=cfg,
            )
        return state["agent_graph"]

    def _connect_db() -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(cfg.duckdb_path), read_only=True)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/slate/today", response_model=TodaySlateResponse)
    def get_today_slate(refresh: bool = Query(default=False)) -> TodaySlateResponse:
        svc: SlateService = state["slate_service"]
        payload = svc.get_today_slate(force_refresh=refresh)
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
                WITH latest_player_team AS (
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
                    SELECT player_id, player_name, season
                    FROM latest_player_team
                    WHERE rn = 1 AND team_id = ?
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
                [team_id, team_id, limit],
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


app = create_app()
