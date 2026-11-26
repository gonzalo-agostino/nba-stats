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
Tu tarea es explicar de forma concisa (3 a 4 viñetas en español claro, sin nombres técnicos de variables en inglés ni snake_case) POR QUÉ suben o bajan las proyecciones del escenario respecto a la proyección base.
REGLAS ESTRICTAS:
1. Cita ÚNICAMENTE números que aparezcan exactamente en el JSON de `ml_result` (proyecciones base, escenario, deltas, factores SHAP, `opponent_defense` e historial WOWY).
2. NUNCA inventes estadísticas, promedios ni porcentajes que no estén en el JSON.
3. Menciona el perfil defensivo del rival (DefRtg y su ranking #1-#30 si está disponible), bajas del rival si existen, principales factores SHAP y el historial WOWY real si existe.
4. Si la diferencia entre base y escenario es pequeña, explícalo con honestidad."""

FEATURE_LABELS_ES: dict[str, str] = {
    "pred_min": "Minutos proyectados",
    "min_ewma_10": "Tendencia de minutos (10p)",
    "min_ewma_3": "Minutos muy recientes (3p)",
    "min_avg_5": "Promedio de minutos (5p)",
    "min_avg_10": "Promedio de minutos (10p)",
    "min_avg_20": "Promedio de minutos (20p)",
    "min_last_game": "Minutos en el último partido",
    "pts_ewma_10": "Tendencia de puntos (10p)",
    "pts_avg_5": "Promedio de puntos (5p)",
    "pts_avg_10": "Promedio de puntos (10p)",
    "pts_avg_20": "Promedio de puntos (20p)",
    "pts_per36_10": "Ritmo de puntos por 36 min",
    "reb_ewma_10": "Tendencia de rebotes (10p)",
    "reb_avg_10": "Promedio de rebotes (10p)",
    "reb_avg_20": "Promedio de rebotes (20p)",
    "reb_per36_10": "Rebotes por 36 min",
    "ast_ewma_10": "Tendencia de asistencias (10p)",
    "ast_avg_10": "Promedio de asistencias (10p)",
    "ast_avg_20": "Promedio de asistencias (20p)",
    "ast_per36_10": "Asistencias por 36 min",
    "fga_ewma_10": "Volumen de tiros (10p)",
    "fga_avg_10": "Tiros de campo promedio (10p)",
    "fga_per36_10": "Tiros por 36 min",
    "usg_pct_ewma_10": "Uso ofensivo reciente (USG%)",
    "usg_pct_avg_10": "Uso ofensivo promedio (10p)",
    "ts_pct_avg_10": "Eficiencia de tiro (TS%)",
    "missing_team_minutes": "Minutos ausentes en el equipo",
    "missing_team_usage_pct": "Uso ofensivo ausente en el equipo",
    "missing_same_pos_minutes": "Minutos ausentes en su misma posición",
    "star_teammate_out": "Estrella del equipo ausente",
    "opp_def_rating_10": "Rating defensivo del rival (10p)",
    "opp_pace_10": "Ritmo del rival (10p)",
    "opp_pts_allowed_10": "Puntos permitidos por el rival",
    "opp_missing_team_minutes": "Minutos ausentes en el rival",
    "opp_missing_center_minutes": "Minutos de pivotes ausentes en el rival",
    "opp_missing_def_stocks": "Stocks defensivos ausentes en el rival",
    "is_back_to_back": "Back-to-back (fatiga)",
    "is_home": "Localía",
    "player_rest_days": "Días de descanso del jugador",
    "is_returning_from_absence": "Vuelta tras ausencia prolongada",
    "wowy_delta_min": "Historial WOWY en minutos",
    "wowy_delta_pts_p36": "Historial WOWY en puntos/36",
}


def _humanize_feature_es(name: str) -> str:
    return FEATURE_LABELS_ES.get(name, name.replace("_", " "))


def extract_text_content(content: Any) -> str:
    """Normalize LangChain/Gemini response content (str or list of content blocks) to plain text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block)
            elif isinstance(block, dict):
                # Skip thought/reasoning blocks; keep only visible text blocks
                block_type = block.get("type")
                if block_type in ("text", None) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        if parts:
            return "\n".join(parts).strip()
    return str(content).strip()


def build_vertex_llm(settings: Settings | None = None, temperature: float = 0.0) -> ChatVertexAI:
    cfg = settings or Settings()
    kwargs: dict[str, Any] = {
        "model": cfg.vertex_model_name,
        "location": cfg.google_cloud_location,
        "temperature": temperature,
        "thinking_budget": 0,
        "max_output_tokens": 600,
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
    for stat in ["min", "pts", "reb", "ast", "pra", "pr", "pa", "ra", "stl", "blk", "tov", "pf", "fga", "ftm"]:
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

    def _compact_ml_payload(self, result: SimulationResult) -> str:
        """Build a compact JSON summary for the analyst LLM to minimize tokens and latency."""
        compact = {
            "player_name": result.player_name,
            "team_abbreviation": result.team_abbreviation,
            "opponent_abbreviation": result.opponent_abbreviation,
            "is_home": result.is_home,
            "is_back_to_back": result.is_back_to_back,
            "out_players_resolved": result.out_players_resolved,
            "opp_out_players_resolved": result.opp_out_players_resolved,
            "missing_team_minutes": result.missing_team_minutes,
            "base_projection": result.base_projection.model_dump(),
            "scenario_projection": result.scenario_projection.model_dump(),
            "deltas": result.deltas.model_dump(),
            "opponent_defense": (
                result.opponent_defense.model_dump() if result.opponent_defense else None
            ),
            "wowy_context": result.wowy_context.model_dump() if result.wowy_context else None,
            "shap_drivers": {
                k: [
                    {
                        "factor": _humanize_feature_es(d.feature),
                        "value": d.value,
                        "shap_value": d.shap_value,
                    }
                    for d in result.shap_drivers.get(k, [])[:3]
                ]
                for k in ("min", "pts", "reb", "ast")
                if k in result.shap_drivers
            },
        }
        import json

        return json.dumps(compact, ensure_ascii=False)

    def explain_simulation_direct(self, result: SimulationResult) -> tuple[str, bool]:
        """Fast path: explain a pre-computed SimulationResult without running parse_query."""
        payload_json = self._compact_ml_payload(result)
        errors: list[str] = []
        draft = ""
        for _attempt in range(2):
            retry_hint = ""
            if errors:
                retry_hint = (
                    "\n\nATENCIÓN — tu borrador anterior tuvo errores numéricos. Corrígelos estrictamente:\n- "
                    + "\n- ".join(errors)
                )
            prompt = (
                f"Explica de forma concisa la proyección de {result.player_name} vs "
                f"{result.opponent_abbreviation}.\n\n"
                f"Salida exacta del modelo ML (JSON):\n{payload_json}"
                f"{retry_hint}"
            )
            resp = self.analyst_llm.invoke(
                [
                    SystemMessage(content=ANALYST_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            draft = extract_text_content(resp.content)
            passed, errors = verify_analysis_numbers(draft, result)
            if passed:
                return draft, True
        return format_deterministic_fallback(result), False

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
            f"Salida exacta del modelo ML (JSON):\n{self._compact_ml_payload(result)}"
            f"{retry_hint}"
        )
        resp = self.analyst_llm.invoke(
            [
                SystemMessage(content=ANALYST_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        text = extract_text_content(resp.content)
        return {"draft_analysis": text}

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
