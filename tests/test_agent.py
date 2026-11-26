"""Unit tests for LangGraph agent flow, numerical verifier, retry loop, and fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from nba_stats.agent.evals import (
    GOLDEN_EVAL_CASES,
    score_numerical_faithfulness,
    score_parse_accuracy,
)
from nba_stats.agent.graph import build_prediction_graph
from nba_stats.agent.verifier import verify_analysis_numbers
from nba_stats.simulator.schemas import (
    ScenarioRequest,
    ShapDriver,
    SimulationResult,
    StatProjection,
    WowyContext,
)
from nba_stats.simulator.what_if import EntityResolutionError


def _sample_sim_result() -> SimulationResult:
    return SimulationResult(
        player_id=102,
        player_name="Jamal Murray",
        team_id=1,
        team_abbreviation="DEN",
        opponent_abbreviation="LAL",
        is_home=True,
        is_back_to_back=True,
        rest_days=0,
        out_players_resolved=["Nikola Jokić"],
        missing_team_minutes=36.0,
        missing_team_usage_pct=0.0465,
        star_teammate_out=True,
        base_projection=StatProjection(min=33.0, pts=21.0, reb=4.0, ast=6.5, pra=31.5),
        scenario_projection=StatProjection(min=35.5, pts=25.2, reb=4.6, ast=7.2, pra=37.0),
        deltas=StatProjection(min=2.5, pts=4.2, reb=0.6, ast=0.7, pra=5.5),
        shap_drivers={
            "min": [ShapDriver(feature="missing_team_minutes", value=36.0, shap_value=2.1, direction="+")],
            "pts": [ShapDriver(feature="pred_min", value=35.5, shap_value=3.4, direction="+")],
        },
        wowy_context=WowyContext(
            primary_out_player="Nikola Jokić",
            sample_games_without=6,
            sample_games_with=50,
            raw_delta_min=3.1,
            raw_delta_pts=5.0,
            raw_delta_reb=0.5,
            raw_delta_ast=1.1,
            shrunk_delta_min=1.33,
            shrunk_delta_pts_p36=2.1,
        ),
    )


class _DummyConn:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeSimulator:
    def __init__(self, fail_resolve: bool = False) -> None:
        self.fail_resolve = fail_resolve
        self.result = _sample_sim_result()

    def _connect(self) -> _DummyConn:
        return _DummyConn()

    def resolve_player(self, con: Any, name: str) -> Any:
        if self.fail_resolve:
            raise EntityResolutionError(f"El nombre '{name}' es ambiguo.")
        return name

    def resolve_team(self, con: Any, team: str | None) -> tuple[int | None, str]:
        return 2, team or "LAL"

    def simulate(self, request: ScenarioRequest) -> SimulationResult:
        return self.result


@dataclass
class _Msg:
    content: str


class FakeParserLLM:
    def __init__(self, scenario: ScenarioRequest) -> None:
        self.scenario = scenario

    def with_structured_output(self, schema: Any) -> Any:
        scenario = self.scenario

        class _Runner:
            def invoke(self, messages: Any) -> ScenarioRequest:
                return scenario

        return _Runner()


class SequenceAnalystLLM:
    """Returns a scripted sequence of responses to test verifier retry and fallback."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages: Any) -> _Msg:
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return _Msg(content=self.responses[idx])


def test_numerical_verifier_catches_hallucination() -> None:
    res = _sample_sim_result()
    good = "Murray sube a 25.2 PTS (+4.2 PTS) y 35.5 MIN gracias a la ausencia de Jokić (36.0 minutos)."
    ok, errs = verify_analysis_numbers(good, res)
    assert ok is True
    assert errs == []

    bad = "Murray proyecta 41.8 PTS y 12.0 REB en este partido."
    ok_bad, errs_bad = verify_analysis_numbers(bad, res)
    assert ok_bad is False
    assert len(errs_bad) == 2
    assert score_numerical_faithfulness(bad, res) == 0.0
    assert score_numerical_faithfulness(good, res) == 1.0


def test_agent_graph_retries_and_succeeds() -> None:
    req = ScenarioRequest(
        player_name="Jamal Murray",
        opponent_team="LAL",
        out_players=["Nikola Jokic"],
        is_back_to_back=True,
    )
    analyst = SequenceAnalystLLM(
        [
            "Murray proyecta 45.0 PTS en este escenario.",  # hallucination -> triggers retry
            "- Proyección de 25.2 PTS (+4.2 PTS) y 35.5 MIN por los 36.0 minutos ausentes de Jokić.",
        ]
    )
    graph = build_prediction_graph(
        simulator=FakeSimulator(),  # type: ignore[arg-type]
        parser_llm=FakeParserLLM(req),
        analyst_llm=analyst,
    )
    out = graph.invoke({"user_query": "Jamal Murray vs LAL B2B sin Jokic"})
    assert analyst.calls == 2
    assert out["verification_passed"] is True
    assert "25.20" in out["final_response"]


def test_agent_graph_falls_back_after_max_retries() -> None:
    req = ScenarioRequest(player_name="Jamal Murray", opponent_team="LAL")
    analyst = SequenceAnalystLLM(["Proyecta 99.9 PTS.", "Sigue proyectando 88.8 PTS."])
    graph = build_prediction_graph(
        simulator=FakeSimulator(),  # type: ignore[arg-type]
        parser_llm=FakeParserLLM(req),
        analyst_llm=analyst,
    )
    out = graph.invoke({"user_query": "Jamal Murray vs LAL"})
    assert analyst.calls == 2
    assert out["verification_passed"] is False
    assert "Drivers principales del modelo (SHAP):" in out["final_response"]
    assert "99.9" not in out["final_response"]


def test_agent_graph_clarifies_ambiguous_entity() -> None:
    req = ScenarioRequest(player_name="Murray", opponent_team="LAL")
    analyst = SequenceAnalystLLM(["No debería ejecutarse"])
    graph = build_prediction_graph(
        simulator=FakeSimulator(fail_resolve=True),  # type: ignore[arg-type]
        parser_llm=FakeParserLLM(req),
        analyst_llm=analyst,
    )
    out = graph.invoke({"user_query": "Murray vs LAL"})
    assert analyst.calls == 0
    assert "aclaración" in out["final_response"].lower()


def test_eval_helpers() -> None:
    case = GOLDEN_EVAL_CASES[0]
    parsed = ScenarioRequest(**case["expected"])
    assert score_parse_accuracy(parsed, case["expected"]) == 1.0
