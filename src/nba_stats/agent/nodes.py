"""LangGraph node implementations for parsing, simulation, explanation, and verification."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_vertexai import ChatVertexAI

from nba_stats.agent.state import PredictionGraphState
from nba_stats.agent.verifier import verify_analysis_numbers
from nba_stats.config import Settings
from nba_stats.simulator.schemas import ScenarioRequest, SimulationResult
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator

PARSER_SYSTEM_PROMPT = """Eres un parser estricto de consultas de estadísticas de la NBA.
Extrae la información del usuario en el esquema ScenarioRequest:
- player_name: nombre completo o parcial del jugador a proyectar.
- opponent_team: abreviatura NBA o nombre del rival si se menciona (si no se menciona, null).
- out_players: lista de compañeros DEL MISMO EQUIPO del jugador objetivo que están OUT (lesión/suspensión).
- opponent_out_players: lista de jugadores DEL EQUIPO RIVAL que están OUT (ej. "contra SAS sin Wembanyama" -> opponent_out_players=["Victor Wembanyama"]).
- is_back_to_back: true si el usuario menciona B2B, back-to-back o que jugó ayer.
- rest_days: entero 0..7 si se menciona explícitamente (0 si es B2B).
- is_home: false si menciona visitante/away/@; true por defecto.
No inventes jugadores ni rivales que no estén en la consulta."""

ANALYST_SYSTEM_PROMPT = """Eres un analista cuantitativo de NBA.
Tu tarea es explicar de forma concisa (3 a 5 viñetas en español) POR QUÉ suben o bajan las proyecciones del escenario respecto a la proyección base.
REGLAS ESTRICTAS:
1. Cita ÚNICAMENTE números que aparezcan exactamente en el JSON de `ml_result` (proyecciones base, escenario, deltas, SHAP drivers, `opponent_defense` e historial WOWY).
2. NUNCA inventes estadísticas, promedios ni porcentajes que no estén en el JSON.
3. Menciona el perfil defensivo del rival (DefRtg y su ranking #1-#30 si está disponible), bajas del rival si existen, principales SHAP drivers y el historial WOWY real.
4. Si la diferencia entre base y escenario es pequeña, explícalo con honestidad."""


def build_vertex_llm(settings: Settings | None = None, temperature: float = 0.0) -> ChatVertexAI:
    cfg = settings or Settings()
    kwargs: dict[str, Any] = {
        "model": cfg.vertex_model_name,
        "location": cfg.google_cloud_location,
        "temperature": temperature,
    }
    if cfg.google_cloud_project:
        kwargs["project"] = cfg.google_cloud_project
    return ChatVertexAI(**kwargs)


def format_projection_table(result: SimulationResult) -> str:
    """Render deterministic markdown summary table from SimulationResult."""
    flags = []
    if result.is_back_to_back:
        flags.append("B2B")
    flags.append("Local" if result.is_home else "Visitante")
    if result.out_players_resolved:
        flags.append(f"Equipo OUT: {', '.join(result.out_players_resolved)}")
    if result.opp_out_players_resolved:
        flags.append(f"Rival OUT: {', '.join(result.opp_out_players_resolved)}")

    header = (
        f"### {result.player_name} ({result.team_abbreviation}) vs {result.opponent_abbreviation} "
        f"— {' | '.join(flags)}\n\n"
    )
    rows = [
        "| Stat | Base | Escenario | Delta |",
        "| :--- | :---: | :---: | :---: |",
    ]
    for stat in ["min", "pts", "reb", "ast", "pra", "pr", "pa", "stl", "blk", "tov", "pf", "fga", "ftm"]:
        b = getattr(result.base_projection, stat)
        s = getattr(result.scenario_projection, stat)
        d = getattr(result.deltas, stat)
        rows.append(f"| **{stat.upper()}** | {b:.2f} | **{s:.2f}** | {d:+.2f} |")

    extra = ""
    if result.out_players_unresolved:
        extra += f"\n> Aviso: no se pudo resolver en caché: {', '.join(result.out_players_unresolved)}.\n"
    return header + "\n".join(rows) + "\n" + extra


def format_deterministic_fallback(result: SimulationResult) -> str:
    """Zero-LLM fallback combining table + top SHAP drivers + WOWY summary."""
    table = format_projection_table(result)
    lines = ["\n**Drivers principales del modelo (SHAP):**"]
    for stat in ["min", "pts"]:
        top = result.shap_drivers.get(stat, [])[:3]
        if top:
            summary = ", ".join(f"`{d.feature}` ({d.shap_value:+.2f})" for d in top)
            lines.append(f"- **{stat.upper()}**: {summary}")
    if result.wowy_context is not None:
        w = result.wowy_context
        lines.append(
            f"- **WOWY sin {w.primary_out_player}** ({w.sample_games_without} partidos previos): "
            f"delta histórico {w.raw_delta_min:+.2f} MIN, {w.raw_delta_pts:+.2f} PTS, "
            f"{w.raw_delta_reb:+.2f} REB, {w.raw_delta_ast:+.2f} AST."
        )
    return table + "\n".join(lines)


class AgentNodes:
    """Container of LangGraph nodes with injectable simulator and LLMs for testability."""

    def __init__(
        self,
        simulator: WhatIfSimulator,
        parser_llm: Any | None = None,
        analyst_llm: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.simulator = simulator
        self.settings = settings or Settings()
        self._parser_llm = parser_llm
        self._analyst_llm = analyst_llm

    @property
    def parser_llm(self) -> Any:
        if self._parser_llm is None:
            self._parser_llm = build_vertex_llm(self.settings, temperature=0.0)
        return self._parser_llm

    @property
    def analyst_llm(self) -> Any:
        if self._analyst_llm is None:
            self._analyst_llm = build_vertex_llm(self.settings, temperature=0.1)
        return self._analyst_llm

    def parse_query(self, state: PredictionGraphState) -> dict[str, Any]:
        structured = self.parser_llm.with_structured_output(ScenarioRequest)
        scenario = structured.invoke(
            [
                SystemMessage(content=PARSER_SYSTEM_PROMPT),
                HumanMessage(content=state["user_query"]),
            ]
        )
        return {"parsed_scenario": scenario, "retry_count": 0, "verification_errors": []}

    def resolve_entities(self, state: PredictionGraphState) -> dict[str, Any]:
        scenario = state.get("parsed_scenario")
        if scenario is None:
            return {
                "resolved_ok": False,
                "clarification_message": "No pude interpretar la consulta. Indicá al menos el nombre del jugador.",
            }
        try:
            with self.simulator._connect() as con:
                self.simulator.resolve_player(con, scenario.player_name)
                self.simulator.resolve_team(con, scenario.opponent_team)
            return {"resolved_ok": True, "clarification_message": None}
        except EntityResolutionError as exc:
            return {"resolved_ok": False, "clarification_message": str(exc)}

    def clarify_or_error(self, state: PredictionGraphState) -> dict[str, Any]:
        msg = state.get("clarification_message") or "Necesito más precisión sobre el jugador o el rival."
        return {"final_response": f"Necesito una aclaración antes de simular:\n\n{msg}"}

    def run_ml_simulation(self, state: PredictionGraphState) -> dict[str, Any]:
        scenario = state["parsed_scenario"]
        assert scenario is not None
        result = self.simulator.simulate(scenario)
        return {"ml_result": result}

    def explain_projection(self, state: PredictionGraphState) -> dict[str, Any]:
        result = state["ml_result"]
        assert result is not None
        errors = state.get("verification_errors") or []
        retry_hint = ""
        if errors:
            retry_hint = (
                "\n\nATENCIÓN — tu borrador anterior tuvo errores numéricos. Corrígelos estrictamente:\n- "
                + "\n- ".join(errors)
            )
        prompt = (
            f"Consulta original: {state['user_query']}\n\n"
            f"Salida exacta del modelo ML (JSON):\n{result.model_dump_json(indent=2)}"
            f"{retry_hint}"
        )
        resp = self.analyst_llm.invoke(
            [
                SystemMessage(content=ANALYST_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return {"draft_analysis": text.strip()}

    def verify_output(self, state: PredictionGraphState) -> dict[str, Any]:
        result = state["ml_result"]
        draft = state.get("draft_analysis") or ""
        assert result is not None
        passed, errors = verify_analysis_numbers(draft, result)
        retry_count = int(state.get("retry_count", 0))
        if not passed:
            retry_count += 1
        return {
            "verification_passed": passed,
            "verification_errors": errors,
            "retry_count": retry_count,
        }

    def format_final_output(self, state: PredictionGraphState) -> dict[str, Any]:
        result = state["ml_result"]
        assert result is not None
        table = format_projection_table(result)
        analysis = state.get("draft_analysis") or ""
        return {"final_response": f"{table}\n**Análisis del modelo:**\n{analysis}"}

    def fallback_deterministic(self, state: PredictionGraphState) -> dict[str, Any]:
        result = state["ml_result"]
        assert result is not None
        return {"final_response": format_deterministic_fallback(result)}
