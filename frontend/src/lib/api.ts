export interface TeamItem {
  team_id: number;
  abbreviation: string;
  full_name: string;
}

export interface PlayerSearchItem {
  player_id: number;
  full_name: string;
  team_id: number;
  team_abbreviation: string;
  pos_group: string;
  avg_min_10: number;
  avg_pts_10: number;
}

export interface RosterPlayerItem {
  player_id: number;
  player_name: string;
  pos_group: string;
  ewma_min: number;
  ewma_usg: number;
  games_played: number;
}

export interface ScenarioRequest {
  player_name: string;
  opponent_team: string | null;
  out_players: string[];
  opponent_out_players: string[];
  is_back_to_back: boolean;
  rest_days: number | null;
  is_home: boolean;
  target_stats?: string[];
}

export interface StatProjection {
  min: number;
  pts: number;
  reb: number;
  ast: number;
  pra: number;
  pr: number;
  pa: number;
  stl: number;
  blk: number;
  tov: number;
  pf: number;
  fga: number;
  ftm: number;
}

export interface OpponentDefenseContext {
  team_abbreviation: string;
  def_rating_10: number;
  def_rating_rank: number | null;
  pace_10: number;
  pace_rank: number | null;
  pts_allowed_10: number;
  reb_allowed_10: number;
  ast_allowed_10: number;
  efg_allowed_10: number;
  fg3a_allowed_10: number;
  tov_forced_10: number;
  dvp_pts_10: number | null;
  net_rating_diff: number;
  opp_missing_minutes: number;
  opp_missing_def_stocks: number;
  opp_star_out: boolean;
}

export interface ShapDriver {
  feature: string;
  value: number | null;
  shap_value: number;
  direction: "+" | "-";
}

export interface WowyContext {
  primary_out_player: string;
  sample_games_without: number;
  sample_games_with: number;
  raw_delta_min: number;
  raw_delta_pts: number;
  raw_delta_reb: number;
  raw_delta_ast: number;
  shrunk_delta_min: number;
  shrunk_delta_pts_p36: number;
}

export interface SimulationResult {
  player_id: number;
  player_name: string;
  team_id: number;
  team_abbreviation: string;
  opponent_abbreviation: string;
  is_home: boolean;
  is_back_to_back: boolean;
  rest_days: number;
  out_players_resolved: string[];
  out_players_unresolved: string[];
  opp_out_players_resolved: string[];
  opp_out_players_unresolved: string[];
  missing_team_minutes: number;
  missing_team_usage_pct: number;
  star_teammate_out: boolean;
  base_projection: StatProjection;
  scenario_projection: StatProjection;
  deltas: StatProjection;
  shap_drivers: Record<string, ShapDriver[]>;
  wowy_context: WowyContext | null;
  opponent_defense: OpponentDefenseContext | null;
}

export interface AskResponse {
  query: string;
  parsed_scenario: ScenarioRequest | null;
  ml_result: SimulationResult | null;
  analysis: string | null;
  verification_passed: boolean;
  clarification_message: string | null;
  final_response: string;
}

export interface TeamSimulationRequest {
  team_id: number;
  opponent_team_id: number | null;
  out_player_ids: number[];
  is_back_to_back: boolean;
  is_home: boolean;
  limit?: number;
}

export interface TeamBoxscoreRow {
  player_id: number;
  player_name: string;
  pos_group: string;
  ewma_min: number;
  is_out: boolean;
  base: StatProjection | null;
  scenario: StatProjection | null;
  deltas: StatProjection | null;
  top_drivers: ShapDriver[];
  wowy: WowyContext | null;
}

export interface TeamSimulationResponse {
  team_id: number;
  team_abbreviation: string;
  opponent_team_id: number | null;
  opponent_abbreviation: string;
  is_home: boolean;
  is_back_to_back: boolean;
  opponent_defense: OpponentDefenseContext | null;
  rows: TeamBoxscoreRow[];
}

export interface SlateGameItem {
  game_id: string;
  away_team_id: number;
  away_abbr: string;
  home_team_id: number;
  home_abbr: string;
  game_status: string;
  away_b2b: boolean;
  home_b2b: boolean;
}

export interface TodaySlateResponse {
  game_date: string;
  games: SlateGameItem[];
  fallback: boolean;
  cached: boolean;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // ignore non-json error body
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function fetchHealth(): Promise<{ status: string }> {
  return requestJson<{ status: string }>("/api/v1/health");
}

export function fetchTeams(): Promise<TeamItem[]> {
  return requestJson<TeamItem[]>("/api/v1/teams");
}

export function searchPlayers(q: string, limit = 10): Promise<PlayerSearchItem[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return requestJson<PlayerSearchItem[]>(`/api/v1/players?${params.toString()}`);
}

export function fetchTeamRoster(teamId: number): Promise<RosterPlayerItem[]> {
  return requestJson<RosterPlayerItem[]>(`/api/v1/teams/${teamId}/roster`);
}

export function runSimulation(payload: ScenarioRequest): Promise<SimulationResult> {
  return requestJson<SimulationResult>("/api/v1/simulate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function askAgent(query: string): Promise<AskResponse> {
  return requestJson<AskResponse>("/api/v1/ask", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

export function simulateTeam(payload: TeamSimulationRequest): Promise<TeamSimulationResponse> {
  return requestJson<TeamSimulationResponse>("/api/v1/simulate-team", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchTodaySlate(): Promise<TodaySlateResponse> {
  return requestJson<TodaySlateResponse>("/api/v1/slate/today");
}
