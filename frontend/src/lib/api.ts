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
  ra: number;
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
  injury_status: "OUT" | "DTD" | null;
  injury_note: string | null;
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

export interface GameSimulationRequest {
  away_team_id: number;
  home_team_id: number;
  away_out_player_ids?: number[] | null;
  home_out_player_ids?: number[] | null;
  auto_apply_injuries?: boolean;
  away_b2b?: boolean;
  home_b2b?: boolean;
  limit?: number;
}

export interface GamePaceSummary {
  expected_pace: number;
  pace_label: string;
  away_pace_10: number;
  away_pace_rank: number | null;
  away_def_rating_10: number;
  away_def_rank: number | null;
  home_pace_10: number;
  home_pace_rank: number | null;
  home_def_rating_10: number;
  home_def_rank: number | null;
}

export interface GameSimulationResponse {
  away: TeamSimulationResponse;
  home: TeamSimulationResponse;
  pace_summary: GamePaceSummary;
  away_out_player_ids: number[];
  home_out_player_ids: number[];
  injuries_updated_at: string | null;
  injuries_fallback: boolean;
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

export interface ExplainResponse {
  ml_result: SimulationResult;
  analysis: string;
  verification_passed: boolean;
}

export interface PlayerGameLogItem {
  game_id: string;
  game_date: string;
  opponent_abbreviation: string;
  is_home: boolean;
  wl: string | null;
  min: number;
  pts: number;
  reb: number;
  ast: number;
  pra: number;
  stl: number;
  blk: number;
  tov: number;
  pf: number;
  fga: number;
  fg3a: number;
  ftm: number;
  fta: number;
  plus_minus: number;
  usg_pct: number | null;
  ts_pct: number | null;
}

export interface PlayerSplitsSummary {
  label: string;
  games: number;
  min: number;
  pts: number;
  reb: number;
  ast: number;
  pra: number;
  stl: number;
  blk: number;
  tov: number;
  fga: number;
  ftm: number;
}

export interface PlayerProfileResponse {
  player_id: number;
  full_name: string;
  team_id: number;
  team_abbreviation: string;
  pos_group: string;
  total_games: number;
  splits: PlayerSplitsSummary[];
  recent_games: PlayerGameLogItem[];
}

export function playerHeadshotUrl(playerId: number): string {
  return `https://cdn.nba.com/headshots/nba/latest/260x190/${playerId}.png`;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

const RETRYABLE_HTTP_STATUS = new Set([429, 502, 503, 504]);

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isIdempotentRead =
    method === "GET" || url.startsWith("/api/v1/simulate");
  const maxAttempts = isIdempotentRead ? 4 : 2;
  let lastErr: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(`${API_BASE}${url}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(init?.headers ?? {}),
        },
      });
      if (!res.ok) {
        if (RETRYABLE_HTTP_STATUS.has(res.status) && attempt < maxAttempts) {
          await new Promise((r) => setTimeout(r, attempt * 1800));
          continue;
        }
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          // ignore non-json error body
        }
        throw new Error(detail);
      }
      return (await res.json()) as T;
    } catch (err) {
      lastErr = err;
      const msg = err instanceof Error ? err.message : "";
      const isNetworkErr =
        err instanceof TypeError || /load failed|failed to fetch|network/i.test(msg);
      if (attempt < maxAttempts && isNetworkErr) {
        await new Promise((r) => setTimeout(r, attempt * 1800));
        continue;
      }
      throw err;
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("Error de conexión con el servidor.");
}

export interface HealthResponse {
  status: string;
  latest_game_date: string | null;
}

export interface SyncResponse {
  status: string;
  season: string;
  games_ingested: number;
  player_logs_ingested: number;
  latest_game_date: string | null;
}

export function fetchHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/v1/health");
}

export function syncWarehouse(): Promise<SyncResponse> {
  return requestJson<SyncResponse>("/api/v1/sync", { method: "POST" });
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

export function simulateGame(payload: GameSimulationRequest): Promise<GameSimulationResponse> {
  return requestJson<GameSimulationResponse>("/api/v1/simulate-game", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchTodaySlate(gameDate?: string): Promise<TodaySlateResponse> {
  const query = gameDate ? `?game_date=${encodeURIComponent(gameDate)}` : "";
  return requestJson<TodaySlateResponse>(`/api/v1/slate/today${query}`);
}

export function explainScenario(payload: ScenarioRequest): Promise<ExplainResponse> {
  return requestJson<ExplainResponse>("/api/v1/explain", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface TrackedPickItem {
  pick_id: string;
  game_date: string;
  game_id: string | null;
  player_id: number;
  player_name: string;
  team_abbreviation: string;
  opponent_abbreviation: string;
  stat: string;
  bookmaker: string;
  line: number;
  side: "OVER" | "UNDER";
  odds_decimal: number;
  projected_value: number;
  edge: number;
  status: "PENDING" | "WIN" | "LOSS" | "PUSH" | "VOID";
  actual_value: number | null;
  profit_units: number | null;
}

export interface PerformanceBucket {
  label?: string;
  total: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  pending: number;
  win_rate_pct: number | null;
  roi_pct: number | null;
  net_units: number;
}

export interface PerformanceSummaryResponse {
  overall: PerformanceBucket;
  by_stat: PerformanceBucket[];
  by_edge_bucket: PerformanceBucket[];
  by_date?: PerformanceBucket[];
  requests_remaining: number | null;
  picks: TrackedPickItem[];
}

export interface PropsSnapshotResponse {
  game_date: string;
  games_analyzed: number;
  lines_available: number;
  lines_from_cache: boolean;
  matched_players: number;
  picks_registered: number;
  requests_remaining: number | null;
  requests_used: number | null;
}

export function fetchPropsPerformance(limit = 150): Promise<PerformanceSummaryResponse> {
  return requestJson<PerformanceSummaryResponse>(`/api/v1/props/performance?limit=${limit}`);
}

export function snapshotDailyProps(
  gameDate?: string,
  forceRefresh = false,
  mode: "saver" | "full" = "saver"
): Promise<PropsSnapshotResponse> {
  const params = new URLSearchParams();
  if (gameDate) params.set("game_date", gameDate);
  if (forceRefresh) params.set("force_refresh", "true");
  params.set("mode", mode);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return requestJson<PropsSnapshotResponse>(`/api/v1/props/snapshot${qs}`, {
    method: "POST",
  });
}

export function gradeTrackedPicks(): Promise<{
  graded: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
}> {
  return requestJson(`/api/v1/props/grade`, { method: "POST" });
}

export interface HitRateSummary {
  hits: number;
  total: number;
  pct: number;
  avg: number;
  avg_min: number;
}

export interface PropGamePoint {
  game_id: string;
  game_date: string;
  opp_abbr: string;
  is_home: boolean;
  wl: string | null;
  min: number;
  value: number;
  hit: boolean;
}

export interface PropTeammateOption {
  player_id: number;
  player_name: string;
  is_out: boolean;
}

export interface PropBoardItem {
  id: string;
  game_id: string;
  game_date: string;
  player_id: number;
  player_name: string;
  pos_group: string;
  team_abbreviation: string;
  opponent_abbreviation: string;
  is_home: boolean;
  is_back_to_back: boolean;
  injury_status: "OUT" | "DTD" | null;
  injury_note: string | null;
  team_outs: string[];
  stat: string;
  bookmaker: string;
  line: number;
  recommended_side: "OVER" | "UNDER";
  odds_decimal: number;
  over_odds: number | null;
  under_odds: number | null;
  ml_projection: number;
  ml_edge: number;
  ml_agrees: boolean;
  conviction_score: number;
  team_spread: number | null;
  game_total: number | null;
  blowout_risk: boolean;
  opp_def_rank: number | null;
  opp_def_rating_10: number | null;
  opp_pace_rank: number | null;
  l5: HitRateSummary;
  l10: HitRateSummary;
  l20: HitRateSummary;
  home_split: HitRateSummary;
  away_split: HitRateSummary;
  games: PropGamePoint[];
  teammates: PropTeammateOption[];
}

export interface PropBoardResponse {
  game_date: string;
  games: { game_id: string; away_abbr: string; home_abbr: string; game_status: string }[];
  total_props: number;
  requests_remaining: number | null;
  items: PropBoardItem[];
}

export interface PlayerChartResponse {
  player_id: number;
  stat: string;
  line: number;
  side: "OVER" | "UNDER";
  without_player_id: number | null;
  l5: HitRateSummary;
  l10: HitRateSummary;
  l20: HitRateSummary;
  home: HitRateSummary;
  away: HitRateSummary;
  games: PropGamePoint[];
}

export function fetchPropBoard(gameDate?: string): Promise<PropBoardResponse> {
  const qs = gameDate ? `?game_date=${encodeURIComponent(gameDate)}` : "";
  return requestJson<PropBoardResponse>(`/api/v1/props/board${qs}`);
}

export function fetchPlayerChart(params: {
  playerId: number;
  stat: string;
  line: number;
  side: "OVER" | "UNDER";
  withoutPlayerId?: number | null;
  limit?: number;
}): Promise<PlayerChartResponse> {
  const sp = new URLSearchParams({
    player_id: String(params.playerId),
    stat: params.stat,
    line: String(params.line),
    side: params.side,
    limit: String(params.limit ?? 20),
  });
  if (params.withoutPlayerId) {
    sp.set("without_player_id", String(params.withoutPlayerId));
  }
  return requestJson<PlayerChartResponse>(`/api/v1/props/player-chart?${sp.toString()}`);
}

export function fetchPlayerProfile(playerId: number, limit = 15): Promise<PlayerProfileResponse> {
  return requestJson<PlayerProfileResponse>(`/api/v1/players/${playerId}/profile?limit=${limit}`);
}

export interface ParlayLeg {
  pick_id: string;
  game_id?: string;
  player_id: number;
  player_name: string;
  team_abbreviation: string;
  opponent_abbreviation: string;
  is_home?: boolean;
  is_back_to_back?: boolean;
  injury_status?: string | null;
  blowout_risk?: boolean;
  stat: string;
  bookmaker?: string;
  line: number;
  side: "OVER" | "UNDER" | string;
  odds_decimal: number;
  ml_projection: number | null;
  ml_edge: number | null;
  ml_agrees?: boolean;
  l10_pct: number | null;
  l10_hits?: number;
  l10_total?: number;
  conviction_score: number | null;
  est_prob?: number;
  locked: boolean;
  status?: "PENDING" | "WIN" | "LOSS" | "VOID" | string;
  actual_value?: number | null;
}

export interface ParlaySuggestion {
  strategy: string;
  legs: ParlayLeg[];
  num_legs: number;
  combined_odds: number;
  target_odds: number;
  odds_diff: number;
  estimated_prob_pct: number;
  implied_prob_pct: number;
  ev_pct: number;
  avg_conviction: number;
  warnings: string[];
}

export interface ParlayBuildRequest {
  game_date?: string;
  target_odds: number;
  locked_pick_ids: string[];
  excluded_pick_ids?: string[];
  min_legs?: number;
  max_legs?: number;
  allowed_stats?: string[] | null;
  require_ml_agrees?: boolean;
  exclude_blowout?: boolean;
  exclude_dtd?: boolean;
  one_per_player?: boolean;
  one_per_game?: boolean;
  max_results?: number;
}

export interface ParlayBuildResponse {
  game_date: string;
  target_odds: number;
  locked_count: number;
  candidates_evaluated: number;
  parlays: ParlaySuggestion[];
  note: string | null;
}

export interface TrackedParlayItem {
  parlay_id: string;
  game_date: string;
  label: string;
  target_odds: number | null;
  combined_odds: number;
  estimated_prob: number | null;
  stake_units: number;
  legs: ParlayLeg[];
  status: "PENDING" | "WIN" | "LOSS" | "VOID" | string;
  legs_won: number;
  legs_lost: number;
  legs_void: number;
  legs_total: number;
  effective_odds: number;
  profit_units: number | null;
  created_at: string;
}

export interface ParlayHistoryResponse {
  overall: {
    total: number;
    wins: number;
    losses: number;
    voids: number;
    pending: number;
    win_rate_pct: number | null;
    roi_pct: number | null;
    net_units: number;
    avg_odds: number | null;
  };
  parlays: TrackedParlayItem[];
}

export function buildParlay(req: ParlayBuildRequest): Promise<ParlayBuildResponse> {
  return requestJson<ParlayBuildResponse>("/api/v1/props/build-parlay", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function saveTrackedParlay(payload: {
  game_date: string;
  label: string;
  target_odds?: number | null;
  combined_odds: number;
  estimated_prob?: number | null;
  stake_units?: number;
  legs: ParlayLeg[];
}): Promise<{ parlay_id: string; status: string }> {
  return requestJson("/api/v1/props/parlays", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchTrackedParlays(limit = 100): Promise<ParlayHistoryResponse> {
  return requestJson<ParlayHistoryResponse>(`/api/v1/props/parlays?limit=${limit}`);
}

export function deleteTrackedParlay(parlayId: string): Promise<{ status: string }> {
  return requestJson(`/api/v1/props/parlays/${encodeURIComponent(parlayId)}`, {
    method: "DELETE",
  });
}
