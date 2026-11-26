"""Deterministic numerical faithfulness verifier for LLM analyst explanations."""

from __future__ import annotations

import re

from nba_stats.simulator.schemas import SimulationResult

# Matches stat-anchored numbers like "28.4 PTS", "PTS: 28.4", "+3.2 MIN", "45.1 PRA"
STAT_PATTERN = re.compile(
    r"(?:(?P<sign1>[+-])?\s*(?P<num1>\d+(?:\.\d+)?)\s*(?P<stat1>pts|puntos|reb|rebotes|ast|asistencias|pra|pr|pa|min|minutos|stl|robos|blk|bloqueos|tapones|tov|pérdidas|perdidas|pf|faltas|fga|tiros|ftm|libres)\b)"
    r"|(?:(?P<stat2>pts|puntos|reb|rebotes|ast|asistencias|pra|pr|pa|min|minutos|stl|robos|blk|bloqueos|tapones|tov|pérdidas|perdidas|pf|faltas|fga|tiros|ftm|libres)\s*[:=de]+\s*(?P<sign2>[+-])?\s*(?P<num2>\d+(?:\.\d+)?))",
    flags=re.IGNORECASE,
)

STAT_NORMALIZE: dict[str, str] = {
    "pts": "pts",
    "puntos": "pts",
    "reb": "reb",
    "rebotes": "reb",
    "ast": "ast",
    "asistencias": "ast",
    "pra": "pra",
    "pr": "pr",
    "pa": "pa",
    "min": "min",
    "minutos": "min",
    "stl": "stl",
    "robos": "stl",
    "blk": "blk",
    "bloqueos": "blk",
    "tapones": "blk",
    "tov": "tov",
    "pérdidas": "tov",
    "perdidas": "tov",
    "pf": "pf",
    "faltas": "pf",
    "fga": "fga",
    "tiros": "fga",
    "ftm": "ftm",
    "libres": "ftm",
}


def _allowed_values_for_stat(result: SimulationResult, stat: str) -> list[float]:
    allowed = [
        getattr(result.base_projection, stat),
        getattr(result.scenario_projection, stat),
        getattr(result.deltas, stat),
        abs(getattr(result.deltas, stat)),
    ]
    if stat == "min":
        allowed.append(result.missing_team_minutes)
    if result.opponent_defense is not None:
        od = result.opponent_defense
        opp_map = {
            "min": [od.opp_missing_minutes],
            "pts": [od.pts_allowed_10, od.def_rating_10]
            + ([od.dvp_pts_10] if od.dvp_pts_10 is not None else []),
            "reb": [od.reb_allowed_10],
            "ast": [od.ast_allowed_10],
            "tov": [od.tov_forced_10],
            "fga": [od.fg3a_allowed_10],
            "stl": [od.opp_missing_def_stocks],
            "blk": [od.opp_missing_def_stocks],
        }
        for val in opp_map.get(stat, []):
            allowed.extend([val, abs(val)])
    if result.wowy_context is not None:
        wowy_map = {
            "min": [result.wowy_context.raw_delta_min, result.wowy_context.shrunk_delta_min],
            "pts": [result.wowy_context.raw_delta_pts, result.wowy_context.shrunk_delta_pts_p36],
            "reb": [result.wowy_context.raw_delta_reb],
            "ast": [result.wowy_context.raw_delta_ast],
        }
        for val in wowy_map.get(stat, []):
            allowed.extend([val, abs(val)])
    for drivers in result.shap_drivers.values():
        for d in drivers:
            if d.value is not None:
                allowed.extend([d.value, abs(d.value)])
            allowed.extend([d.shap_value, abs(d.shap_value)])
    return allowed


def verify_analysis_numbers(
    text: str,
    result: SimulationResult,
    tolerance: float = 0.2,
) -> tuple[bool, list[str]]:
    """Verify that every stat-linked number cited in text matches ml_result within tolerance."""
    errors: list[str] = []
    if not text or not text.strip():
        return False, ["El análisis generado está vacío."]

    for match in STAT_PATTERN.finditer(text):
        raw_stat = (match.group("stat1") or match.group("stat2") or "").lower()
        raw_num = match.group("num1") or match.group("num2")
        sign = match.group("sign1") or match.group("sign2") or ""
        if not raw_num or raw_stat not in STAT_NORMALIZE:
            continue

        stat = STAT_NORMALIZE[raw_stat]
        value = float(f"{sign}{raw_num}")
        allowed = _allowed_values_for_stat(result, stat)

        if not any(abs(value - ref) <= tolerance for ref in allowed):
            expected_scen = getattr(result.scenario_projection, stat)
            expected_base = getattr(result.base_projection, stat)
            expected_delta = getattr(result.deltas, stat)
            errors.append(
                f"Cifra inconsistente para {stat.upper()}: se citó '{sign}{raw_num}', "
                f"pero el modelo proyecta escenario={expected_scen}, base={expected_base}, "
                f"delta={expected_delta:+.2f}."
            )

    return len(errors) == 0, errors
