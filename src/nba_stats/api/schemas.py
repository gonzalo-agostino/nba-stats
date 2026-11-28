"""API-specific Pydantic request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nba_stats.simulator.schemas import (
    OpponentDefenseContext,
    ScenarioRequest,
    ShapDriver,
    SimulationResult,
    StatProjection,
    WowyContext,
)


class TeamItem(BaseModel):
    team_id: int
    abbreviation: str
    full_name: str


class PlayerSearchItem(BaseModel):
    player_id: int
    full_name: str
    team_id: int
    team_abbreviation: str
    pos_group: str
    avg_min_10: float
    avg_pts_10: float


class RosterPlayerItem(BaseModel):
    player_id: int
    player_name: str
    pos_group: str
    ewma_min: float
    ewma_usg: float
    games_played: int


class AskRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Consulta en lenguaje natural sobre un escenario NBA.")


class AskResponse(BaseModel):
    query: str
    parsed_scenario: ScenarioRequest | None = None
    ml_result: SimulationResult | None = None
    analysis: str | None = None
    verification_passed: bool = False
    clarification_message: str | None = None
    final_response: str


class TeamSimulationRequest(BaseModel):
    team_id: int
    opponent_team_id: int | None = None
    out_player_ids: list[int] = Field(default_factory=list)
    is_back_to_back: bool = False
    is_home: bool = True
    limit: int = Field(default=10, ge=1, le=15)


class TeamBoxscoreRow(BaseModel):
    player_id: int
    player_name: str
    pos_group: str
    ewma_min: float
    is_out: bool
    injury_status: str | None = None
    injury_note: str | None = None
    base: StatProjection | None = None
    scenario: StatProjection | None = None
    deltas: StatProjection | None = None
    top_drivers: list[ShapDriver] = Field(default_factory=list)
    wowy: WowyContext | None = None


class TeamSimulationResponse(BaseModel):
    team_id: int
    team_abbreviation: str
    opponent_team_id: int | None = None
    opponent_abbreviation: str
    is_home: bool
    is_back_to_back: bool
    opponent_defense: OpponentDefenseContext | None = None
    rows: list[TeamBoxscoreRow] = Field(default_factory=list)


class GameSimulationRequest(BaseModel):
    away_team_id: int
    home_team_id: int
    away_out_player_ids: list[int] | None = None
    home_out_player_ids: list[int] | None = None
    auto_apply_injuries: bool = False
    away_b2b: bool = False
    home_b2b: bool = False
    limit: int = Field(default=10, ge=1, le=15)


class GamePaceSummary(BaseModel):
    expected_pace: float
    pace_label: str
    away_pace_10: float
    away_pace_rank: int | None = None
    away_def_rating_10: float
    away_def_rank: int | None = None
    home_pace_10: float
    home_pace_rank: int | None = None
    home_def_rating_10: float
    home_def_rank: int | None = None


class GameSimulationResponse(BaseModel):
    away: TeamSimulationResponse
    home: TeamSimulationResponse
    pace_summary: GamePaceSummary
    away_out_player_ids: list[int] = Field(default_factory=list)
    home_out_player_ids: list[int] = Field(default_factory=list)
    injuries_updated_at: str | None = None
    injuries_fallback: bool = False


class SlateGameItem(BaseModel):
    game_id: str
    away_team_id: int
    away_abbr: str
    home_team_id: int
    home_abbr: str
    game_status: str
    away_b2b: bool = False
    home_b2b: bool = False


class TodaySlateResponse(BaseModel):
    game_date: str
    games: list[SlateGameItem] = Field(default_factory=list)
    fallback: bool = False
    cached: bool = False


class ExplainResponse(BaseModel):
    ml_result: SimulationResult
    analysis: str
    verification_passed: bool = False


class PlayerGameLogItem(BaseModel):
    game_id: str
    game_date: str
    opponent_abbreviation: str
    is_home: bool
    wl: str | None = None
    min: float
    pts: float
    reb: float
    ast: float
    pra: float
    stl: float
    blk: float
    tov: float
    pf: float
    fga: float
    fg3a: float
    ftm: float
    fta: float
    plus_minus: float
    usg_pct: float | None = None
    ts_pct: float | None = None


class PlayerSplitsSummary(BaseModel):
    label: str
    games: int
    min: float
    pts: float
    reb: float
    ast: float
    pra: float
    stl: float
    blk: float
    tov: float
    fga: float
    ftm: float


class PlayerProfileResponse(BaseModel):
    player_id: int
    full_name: str
    team_id: int
    team_abbreviation: str
    pos_group: str
    total_games: int
    splits: list[PlayerSplitsSummary] = Field(default_factory=list)
    recent_games: list[PlayerGameLogItem] = Field(default_factory=list)


class ParlayBuildRequest(BaseModel):
    game_date: str | None = None
    target_odds: float = Field(default=3.5, ge=1.2, le=100.0)
    locked_pick_ids: list[str] = Field(default_factory=list)
    excluded_pick_ids: list[str] = Field(default_factory=list)
    min_legs: int = Field(default=2, ge=1, le=10)
    max_legs: int = Field(default=5, ge=1, le=10)
    allowed_stats: list[str] | None = None
    require_ml_agrees: bool = True
    exclude_blowout: bool = True
    exclude_dtd: bool = True
    one_per_player: bool = False
    one_per_game: bool = False
    max_results: int = Field(default=3, ge=1, le=5)


class SaveParlayLegInput(BaseModel):
    pick_id: str
    player_id: int
    player_name: str
    team_abbreviation: str
    opponent_abbreviation: str
    stat: str
    side: str
    line: float
    odds_decimal: float
    ml_projection: float | None = None
    ml_edge: float | None = None
    l10_pct: float | None = None
    conviction_score: int | None = None
    locked: bool = False


class SaveParlayRequest(BaseModel):
    game_date: str
    label: str = "Combinada"
    target_odds: float | None = None
    combined_odds: float = Field(gt=1.0)
    estimated_prob: float | None = None
    stake_units: float = Field(default=1.0, gt=0.0, le=100.0)
    legs: list[SaveParlayLegInput] = Field(min_length=1, max_length=15)


class ChatMessageItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessageItem] = Field(min_length=1, max_length=24)
    active_date: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
