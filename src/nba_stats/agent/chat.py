"""Interactive multi-turn NBA Chat Copilot powered by Gemini (Vertex AI) + live platform tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from nba_stats.agent.nodes import build_vertex_llm, extract_text_content
from nba_stats.api.injuries import InjuryService
from nba_stats.api.odds import OddsService
from nba_stats.api.parlay import ParlayService, build_parlay_recommendations
from nba_stats.api.schemas import ParlayBuildRequest
from nba_stats.api.slate import SlateService
from nba_stats.config import Settings
from nba_stats.simulator.schemas import ScenarioRequest
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator

logger = logging.getLogger(__name__)

# Common NBA nicknames / abbreviations -> official names for smooth natural-language queries
PLAYER_ALIASES: dict[str, str] = {
    "kcp": "Kentavious Caldwell-Pope",
    "sga": "Shai Gilgeous-Alexander",
    "kat": "Karl-Anthony Towns",
    "ad": "Anthony Davis",
    "kd": "Kevin Durant",
    "pg": "Paul George",
    "pg13": "Paul George",
    "mpj": "Michael Porter Jr.",
    "og": "OG Anunoby",
    "wemby": "Victor Wembanyama",
    "ant": "Anthony Edwards",
    "dame": "Damian Lillard",
    "j-dub": "Jalen Williams",
    "jdub": "Jalen Williams",
    "cp3": "Chris Paul",
    "rj": "RJ Barrett",
    "banchero": "Paolo Banchero",
    "embiid": "Joel Embiid",
    "embid": "Joel Embiid",
    "lebron": "LeBron James",
    "jokic": "Nikola Jokic",
    "luka": "Luka Doncic",
    "giannis": "Giannis Antetokounmpo",
    "curry": "Stephen Curry",
    "steph": "Stephen Curry",
    "tatum": "Jayson Tatum",
    "brunson": "Jalen Brunson",
    "booker": "Devin Booker",
    "haliburton": "Tyrese Haliburton",
    "maxey": "Tyrese Maxey",
    "spida": "Donovan Mitchell",
    "bam": "Bam Adebayo",
    "sabonis": "Domantas Sabonis",
    "trae": "Trae Young",
    "ja": "Ja Morant",
    "zion": "Zion Williamson",
    "cade": "Cade Cunningham",
    "sengun": "Alperen Sengun",
    "chet": "Chet Holmgren",
    "jjj": "Jaren Jackson Jr.",
    "fvv": "Fred VanVleet",
}

CHAT_SYSTEM_PROMPT = """Eres el Copiloto IA de NBA Stats Terminal. Respondes en español rioplatense claro, directo y práctico (sin jerga técnica ni nombres internos de código).

Tienes herramientas conectadas EN VIVO a la plataforma:
1. `simulate_player_what_if`: simula cualquier jugador NBA con LightGBM + WOWY + SHAP bajo cualquier escenario (rival, bajas propias, bajas rivales, B2B, local/visitante).
2. `get_top_props_today`: consulta el tablero real de Props de hoy con líneas de mercado, cuotas, Hit Rate L5/L10/L20, proyección ML, Edge y riesgo de paliza.
3. `recommend_parlay`: arma combinadas recomendadas según cuota objetivo, picks fijados y mercados.
4. `get_today_slate_and_injuries`: consulta la cartelera de partidos de hoy y el reporte oficial de lesionados (OUT / DTD).
5. `get_win_rate_performance`: consulta el Win Rate, ROI, unidades netas y qué estadísticas/rangos de Edge están rindiendo mejor en el historial real del usuario.

REGLAS IMPORTANTES:
- SIEMPRE usá las herramientas para obtener datos reales antes de responder. NUNCA inventes proyecciones, cuotas, líneas ni estadísticas.
- Si el usuario menciona bajas de jugadores que pertenecen a OTRO equipo distinto al del jugador objetivo o al rival (por ejemplo, nombra a LeBron o Jaylen Brown al simular un jugador contra Philadelphia 76ers), igual corré la simulación con las bajas válidas y aclará de forma amable en qué equipo juega cada jugador mencionado.
- Cuando recomiendes picks o combinadas, citá siempre: jugador, stat, OVER/UNDER, línea, cuota, Hit Rate L10 y proyección ML.
- Sé conciso, bien formateado con viñetas o tablas cortas en Markdown, y cerrá con una recomendación concreta."""


def _normalize_alias(name: str) -> str:
    cleaned = name.strip()
    key = cleaned.lower().replace(".", "").strip()
    return PLAYER_ALIASES.get(key, cleaned)


class _SimulateInput(BaseModel):
    player_name: str = Field(description="Nombre o apodo del jugador a proyectar (ej. 'KCP', 'Nikola Jokic').")
    opponent_team: str | None = Field(
        default=None,
        description="Nombre o abreviatura del rival (ej. 'PHI', 'Philadelphia 76ers', 'LAL').",
    )
    out_players: list[str] = Field(
        default_factory=list,
        description="Lista de jugadores ausentes mencionados por el usuario (el tool clasificará automáticamente si son compañeros, rivales o de otro equipo).",
    )
    is_back_to_back: bool = Field(default=False, description="True si el jugador juega en Back-to-Back.")
    is_home: bool = Field(default=True, description="True si juega de local, False si es visitante.")


class _TopPropsInput(BaseModel):
    stat: str | None = Field(
        default=None,
        description="Filtrar por stat específica: pts, reb, ast, pra, pr, pa, ra, fg3m, blk, tov (o null para todas).",
    )
    side: str | None = Field(default=None, description="Filtrar por 'OVER' o 'UNDER' (o null para ambos).")
    team: str | None = Field(default=None, description="Abreviatura de equipo NBA (ej. 'DEN', 'PHI', 'LAL').")
    only_ml_agrees: bool = Field(default=True, description="True para priorizar picks donde LightGBM coincide.")
    limit: int = Field(default=8, ge=1, le=20)


class _ParlayInput(BaseModel):
    target_odds: float = Field(default=4.0, ge=1.5, le=50.0, description="Cuota objetivo de la combinada.")
    allowed_stats: list[str] | None = Field(
        default=None,
        description="Lista opcional de stats permitidas (ej. ['pts', 'ast', 'pr']).",
    )
    max_legs: int = Field(default=4, ge=2, le=8)
    one_per_game: bool = Field(default=False)


class _SlateInput(BaseModel):
    team: str | None = Field(default=None, description="Abreviatura opcional de equipo para filtrar lesiones/partido.")


class _EmptyInput(BaseModel):
    pass


def run_copilot_chat(
    messages: list[dict[str, str]],
    *,
    simulator: WhatIfSimulator,
    odds_service: OddsService,
    parlay_service: ParlayService,
    slate_service: SlateService,
    injury_service: InjuryService,
    settings: Settings,
    active_date: str | None = None,
) -> dict[str, Any]:
    """Run a multi-turn tool-calling chat session with live NBA Stats platform tools."""
    tools_used: list[str] = []

    def _tool_simulate(
        player_name: str,
        opponent_team: str | None = None,
        out_players: list[str] | None = None,
        is_back_to_back: bool = False,
        is_home: bool = True,
    ) -> str:
        tools_used.append("Simulador ML")
        resolved_player = _normalize_alias(player_name)
        raw_outs = [_normalize_alias(p) for p in (out_players or []) if p.strip()]

        # Try to classify out_players cleanly into teammate vs opponent vs unrelated
        team_outs: list[str] = []
        opp_outs: list[str] = []
        unrelated_notes: list[str] = []

        try:
            target_info = simulator._resolve_player(resolved_player)
            target_team_id = int(target_info["team_id"])
            target_team_abbr = str(target_info["team_abbreviation"])
            opp_team_id: int | None = None
            opp_team_abbr: str | None = None
            if opponent_team:
                try:
                    opp_info = simulator._resolve_team(opponent_team)
                    opp_team_id = int(opp_info["team_id"])
                    opp_team_abbr = str(opp_info["abbreviation"])
                except EntityResolutionError:
                    opp_team_abbr = opponent_team

            for out_name in raw_outs:
                try:
                    p_info = simulator._resolve_player(out_name)
                    p_tid = int(p_info["team_id"])
                    p_full = str(p_info["player_name"])
                    p_abbr = str(p_info["team_abbreviation"])
                    if p_tid == target_team_id:
                        team_outs.append(p_full)
                    elif opp_team_id is not None and p_tid == opp_team_id:
                        opp_outs.append(p_full)
                    else:
                        unrelated_notes.append(
                            f"{p_full} juega en {p_abbr} (ni en {target_team_abbr}"
                            + (f" ni en {opp_team_abbr}" if opp_team_abbr else "")
                            + ", así que no afecta directamente esta rotación)."
                        )
                except EntityResolutionError:
                    unrelated_notes.append(f"No se encontró a '{out_name}' en la base de jugadores.")

            req = ScenarioRequest(
                player_name=str(target_info["player_name"]),
                opponent_team=opp_team_abbr,
                out_players=team_outs,
                opponent_out_players=opp_outs,
                is_back_to_back=is_back_to_back,
                rest_days=0 if is_back_to_back else None,
                is_home=is_home,
            )
            sim = simulator.simulate(req)
            payload = {
                "player_name": sim.player_name,
                "team": sim.team_abbreviation,
                "opponent": sim.opponent_abbreviation,
                "is_home": sim.is_home,
                "is_back_to_back": sim.is_back_to_back,
                "teammates_out": sim.out_players_resolved,
                "opponents_out": sim.opp_out_players_resolved,
                "unrelated_players_notes": unrelated_notes,
                "base_projection": {
                    k: round(float(getattr(sim.base_projection, k)), 1)
                    for k in ("min", "pts", "reb", "ast", "pra", "pr", "pa", "ra", "stl", "blk", "tov")
                },
                "scenario_projection": {
                    k: round(float(getattr(sim.scenario_projection, k)), 1)
                    for k in ("min", "pts", "reb", "ast", "pra", "pr", "pa", "ra", "stl", "blk", "tov")
                },
                "deltas": {
                    k: round(float(getattr(sim.deltas, k)), 1)
                    for k in ("min", "pts", "reb", "ast", "pra", "pr", "pa")
                },
                "opponent_defense": sim.opponent_defense.model_dump() if sim.opponent_defense else None,
                "wowy_context": sim.wowy_context.model_dump() if sim.wowy_context else None,
            }
            return json.dumps(payload, ensure_ascii=False)
        except EntityResolutionError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"No se pudo simular: {exc}"}, ensure_ascii=False)

    def _tool_top_props(
        stat: str | None = None,
        side: str | None = None,
        team: str | None = None,
        only_ml_agrees: bool = True,
        limit: int = 8,
    ) -> str:
        tools_used.append("Props Board")
        board = odds_service.build_prop_board(
            simulator=simulator,
            slate_service=slate_service,
            injury_service=injury_service,
            game_date_str=active_date,
        )
        items = board.get("items") or []
        if stat:
            st = stat.strip().lower()
            items = [i for i in items if str(i["stat"]).lower() == st]
        if side:
            sd = side.strip().upper()
            items = [i for i in items if str(i["recommended_side"]).upper() == sd]
        if team:
            tm = team.strip().upper()
            items = [
                i
                for i in items
                if str(i["team_abbreviation"]).upper() == tm
                or str(i["opponent_abbreviation"]).upper() == tm
            ]
        if only_ml_agrees:
            agreed = [i for i in items if i.get("ml_agrees")]
            if agreed:
                items = agreed

        items.sort(key=lambda i: (i["conviction_score"], i["l10"]["pct"], abs(i["ml_edge"])), reverse=True)
        compact = [
            {
                "player": i["player_name"],
                "team": i["team_abbreviation"],
                "opp": i["opponent_abbreviation"],
                "stat": i["stat"].upper(),
                "side": i["recommended_side"],
                "line": i["line"],
                "odds": i["odds_decimal"],
                "ml_proj": i["ml_projection"],
                "ml_edge": i["ml_edge"],
                "ml_agrees": i["ml_agrees"],
                "l5": f"{i['l5']['hits']}/{i['l5']['total']} ({i['l5']['pct']}%)",
                "l10": f"{i['l10']['hits']}/{i['l10']['total']} ({i['l10']['pct']}%)",
                "conviction": i["conviction_score"],
                "blowout_risk": i["blowout_risk"],
                "b2b": i["is_back_to_back"],
                "injury": i["injury_status"],
            }
            for i in items[:limit]
        ]
        return json.dumps(
            {
                "game_date": board.get("game_date"),
                "total_props_available": len(board.get("items") or []),
                "top_props": compact,
            },
            ensure_ascii=False,
        )

    def _tool_recommend_parlay(
        target_odds: float = 4.0,
        allowed_stats: list[str] | None = None,
        max_legs: int = 4,
        one_per_game: bool = False,
    ) -> str:
        tools_used.append("Constructor de Combinadas")
        board = odds_service.build_prop_board(
            simulator=simulator,
            slate_service=slate_service,
            injury_service=injury_service,
            game_date_str=active_date,
        )
        req = ParlayBuildRequest(
            game_date=active_date,
            target_odds=target_odds,
            allowed_stats=allowed_stats,
            max_legs=max_legs,
            one_per_game=one_per_game,
            max_results=2,
        )
        res = build_parlay_recommendations(board.get("items") or [], req)
        return json.dumps(res, ensure_ascii=False)

    def _tool_slate_injuries(team: str | None = None) -> str:
        tools_used.append("Cartelera & Lesiones")
        slate = slate_service.get_today_slate(game_date=active_date)
        inj = injury_service.get_injuries()
        by_team = inj.get("by_team") or {}
        if team:
            tm = team.strip().upper()
            games = [
                g
                for g in (slate.get("games") or [])
                if g.get("away_abbr") == tm or g.get("home_abbr") == tm
            ]
            injuries = {tm: by_team.get(tm, [])}
        else:
            games = slate.get("games") or []
            # Only include teams playing today to keep prompt concise
            playing = {g.get("away_abbr") for g in games} | {g.get("home_abbr") for g in games}
            injuries = {t: v for t, v in by_team.items() if t in playing and v}
        return json.dumps(
            {
                "game_date": slate.get("game_date"),
                "games": [
                    {
                        "matchup": f"{g['away_abbr']} @ {g['home_abbr']}",
                        "away_b2b": g.get("away_b2b"),
                        "home_b2b": g.get("home_b2b"),
                        "status": g.get("game_status"),
                    }
                    for g in games
                ],
                "injuries": injuries,
            },
            ensure_ascii=False,
        )

    def _tool_win_rate() -> str:
        tools_used.append("Win Rate & ROI")
        perf = odds_service.get_performance_summary(limit=50)
        parlays = parlay_service.get_parlay_history(limit=20)
        return json.dumps(
            {
                "singles_overall": perf.get("overall"),
                "singles_by_stat": perf.get("by_stat"),
                "singles_by_edge": perf.get("by_edge_bucket"),
                "singles_by_date": perf.get("by_date"),
                "parlays_overall": parlays.get("overall"),
            },
            ensure_ascii=False,
        )

    tools = [
        StructuredTool.from_function(
            func=_tool_simulate,
            name="simulate_player_what_if",
            description="Simula las estadísticas proyectadas de un jugador NBA (MIN, PTS, REB, AST, PRA, etc.) con LightGBM + WOWY bajo un escenario específico (rival, bajas, B2B, localía).",
            args_schema=_SimulateInput,
        ),
        StructuredTool.from_function(
            func=_tool_top_props,
            name="get_top_props_today",
            description="Obtiene los mejores picks recomendados de hoy del Props Board con líneas reales, cuotas, Hit Rate L5/L10, proyección ML, Edge y riesgo de paliza.",
            args_schema=_TopPropsInput,
        ),
        StructuredTool.from_function(
            func=_tool_recommend_parlay,
            name="recommend_parlay",
            description="Arma combinadas (parlays) recomendadas para hoy según una cuota objetivo, máximo de patas y mercados.",
            args_schema=_ParlayInput,
        ),
        StructuredTool.from_function(
            func=_tool_slate_injuries,
            name="get_today_slate_and_injuries",
            description="Consulta los partidos de hoy, equipos en back-to-back y lesionados oficiales (OUT / DTD).",
            args_schema=_SlateInput,
        ),
        StructuredTool.from_function(
            func=_tool_win_rate,
            name="get_win_rate_performance",
            description="Consulta el historial real de Win Rate, ROI, unidades netas, mejores mercados por stat/edge y rendimiento de combinadas.",
            args_schema=_EmptyInput,
        ),
    ]
    tool_map = {t.name: t for t in tools}

    llm = build_vertex_llm(settings=settings, temperature=0.15)
    llm.max_output_tokens = 1400
    llm_with_tools = llm.bind_tools(tools)

    lc_messages: list[BaseMessage] = [SystemMessage(content=CHAT_SYSTEM_PROMPT)]
    # Keep last 12 turns of conversation history
    for m in messages[-12:]:
        role = str(m.get("role") or "user").lower()
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))

    reply_text = ""
    for _step in range(4):
        response = llm_with_tools.invoke(lc_messages)
        lc_messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            reply_text = extract_text_content(response.content)
            break
        for tc in tool_calls:
            t_name = str(tc.get("name") or "")
            t_args = tc.get("args") or {}
            t_id = str(tc.get("id") or t_name)
            tool_fn = tool_map.get(t_name)
            if tool_fn is None:
                out_str = json.dumps({"error": f"Herramienta desconocida: {t_name}"})
            else:
                try:
                    out_str = str(tool_fn.invoke(t_args))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Chat tool %s failed: %s", t_name, exc)
                    out_str = json.dumps({"error": str(exc)})
            lc_messages.append(ToolMessage(content=out_str, tool_call_id=t_id))
    else:
        final_resp = llm.invoke(lc_messages)
        reply_text = extract_text_content(final_resp.content)

    return {
        "reply": reply_text or "No pude generar una respuesta en este momento. Probá reformular la consulta.",
        "tools_used": list(dict.fromkeys(tools_used)),
    }
