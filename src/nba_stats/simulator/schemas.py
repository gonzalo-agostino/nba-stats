"""Pydantic schemas for What-If simulation requests and outputs (all 13 NBA stats + opponent defense)."""

from __future__ import annotations

from pydantic import BaseModel, Field

ALL_STAT_KEYS: tuple[str, ...] = (
    "min",
    "pts",
    "reb",
    "ast",
    "pra",
    "pr",
    "pa",
    "stl",
    "blk",
    "tov",
    "pf",
    "fga",
    "ftm",
)


class ScenarioRequest(BaseModel):
    """Structured representation of a user's NBA prediction scenario."""

    player_name: str = Field(..., description="Nombre del jugador objetivo (ej. 'Jamal Murray').")
    opponent_team: str | None = Field(
        default=None,
        description="Abreviatura o nombre del equipo rival (ej. 'LAL', 'Lakers'). Opcional.",
    )
    out_players: list[str] = Field(
        default_factory=list,
        description="Lista de compañeros del jugador objetivo marcados como OUT por lesión o suspensión.",
    )
    opponent_out_players: list[str] = Field(
        default_factory=list,
        description="Lista de jugadores del equipo RIVAL marcados como OUT por lesión o suspensión.",
    )
    is_back_to_back: bool = Field(
        default=False,
        description="True si el equipo juega en back-to-back (0 días de descanso).",
    )
    rest_days: int | None = Field(
        default=None,
        ge=0,
        le=7,
        description="Días de descanso previos (0 si es B2B, 1-3+ en caso normal).",
    )
    is_home: bool = Field(
        default=True,
        description="True si el jugador juega de local, False si es visitante.",
    )
    target_stats: list[str] = Field(
        default_factory=lambda: list(ALL_STAT_KEYS),
        description="Estadísticas solicitadas para el análisis.",
    )


class StatProjection(BaseModel):
    """Predicted stats for a single scenario with exact composite consistency (PRA, PR, PA)."""

    min: float
    pts: float
    reb: float
    ast: float
    pra: float
    pr: float = 0.0
    pa: float = 0.0
    stl: float = 0.0
    blk: float = 0.0
    tov: float = 0.0
    pf: float = 0.0
    fga: float = 0.0
    ftm: float = 0.0


class ShapDriver(BaseModel):
    """Single SHAP feature attribution for a model prediction."""

    feature: str
    value: float | None
    shap_value: float
    direction: str


class WowyContext(BaseModel):
    """Real historical With-Or-Without-You stats for the primary missing teammate."""

    primary_out_player: str
    sample_games_without: int
    sample_games_with: int
    raw_delta_min: float
    raw_delta_pts: float
    raw_delta_reb: float
    raw_delta_ast: float
    shrunk_delta_min: float
    shrunk_delta_pts_p36: float


class OpponentDefenseContext(BaseModel):
    """Opponent defensive profile, league rankings (#1-#30), blowout risk, and opponent absences."""

    team_abbreviation: str
    def_rating_10: float
    def_rating_rank: int | None = None
    pace_10: float
    pace_rank: int | None = None
    pts_allowed_10: float
    reb_allowed_10: float
    ast_allowed_10: float
    efg_allowed_10: float
    fg3a_allowed_10: float
    tov_forced_10: float
    dvp_pts_10: float | None = None
    net_rating_diff: float = 0.0
    opp_missing_minutes: float = 0.0
    opp_missing_def_stocks: float = 0.0
    opp_star_out: bool = False


class SimulationResult(BaseModel):
    """Complete deterministic output of a Base vs What-If ML simulation."""

    player_id: int
    player_name: str
    team_id: int
    team_abbreviation: str
    opponent_abbreviation: str
    is_home: bool
    is_back_to_back: bool
    rest_days: int
    out_players_resolved: list[str] = Field(default_factory=list)
    out_players_unresolved: list[str] = Field(default_factory=list)
    opp_out_players_resolved: list[str] = Field(default_factory=list)
    opp_out_players_unresolved: list[str] = Field(default_factory=list)
    missing_team_minutes: float = 0.0
    missing_team_usage_pct: float = 0.0
    star_teammate_out: bool = False
    base_projection: StatProjection
    scenario_projection: StatProjection
    deltas: StatProjection
    shap_drivers: dict[str, list[ShapDriver]] = Field(default_factory=dict)
    wowy_context: WowyContext | None = None
    opponent_defense: OpponentDefenseContext | None = None
