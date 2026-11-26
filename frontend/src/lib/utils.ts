import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatSigned(value: number, decimals = 2): string {
  const fixed = Math.abs(value).toFixed(decimals);
  if (value > 0.004) return `+${fixed}`;
  if (value < -0.004) return `-${fixed}`;
  return `0.${"0".repeat(decimals)}`;
}

const FEATURE_LABELS: Record<string, string> = {
  // Stage 1 / volume
  pred_min: "Minutos proyectados",
  min_ewma_10: "Minutos recientes (EWMA 10p)",
  min_avg_5: "Minutos promedio (5p)",
  min_avg_10: "Minutos promedio (10p)",
  min_avg_20: "Minutos promedio (20p)",
  min_last_game: "Minutos último partido",
  min_std_10: "Volatilidad de minutos (10p)",
  min_trend_5_20: "Tendencia de minutos (5p vs 20p)",
  // Scoring & shooting
  pts_ewma_10: "Puntos recientes (EWMA 10p)",
  pts_avg_5: "Puntos promedio (5p)",
  pts_avg_10: "Puntos promedio (10p)",
  pts_avg_20: "Puntos promedio (20p)",
  pts_per36_10: "Puntos por 36 min (10p)",
  pts_std_10: "Volatilidad de puntos (10p)",
  pts_trend_5_20: "Tendencia de puntos (5p vs 20p)",
  fga_ewma_10: "Tiros de campo (EWMA 10p)",
  fga_avg_5: "Tiros de campo (5p)",
  fga_avg_10: "Tiros de campo (10p)",
  fga_avg_20: "Tiros de campo (20p)",
  fga_per36_10: "Tiros por 36 min (10p)",
  fg3a_avg_5: "Triples intentados (5p)",
  fg3a_avg_10: "Triples intentados (10p)",
  fta_avg_5: "Tiros libres intentados (5p)",
  fta_avg_10: "Tiros libres intentados (10p)",
  ftm_avg_10: "Tiros libres anotados (10p)",
  ts_pct_avg_5: "Eficiencia real TS% (5p)",
  ts_pct_avg_10: "Eficiencia real TS% (10p)",
  ts_pct_avg_20: "Eficiencia real TS% (20p)",
  usg_pct_ewma_10: "Uso ofensivo USG% (EWMA)",
  usg_pct_avg_5: "Uso ofensivo USG% (5p)",
  usg_pct_avg_10: "Uso ofensivo USG% (10p)",
  usg_pct_avg_20: "Uso ofensivo USG% (20p)",
  // Rebounds, assists, defense
  reb_ewma_10: "Rebotes recientes (EWMA 10p)",
  reb_avg_5: "Rebotes promedio (5p)",
  reb_avg_10: "Rebotes promedio (10p)",
  reb_avg_20: "Rebotes promedio (20p)",
  reb_per36_10: "Rebotes por 36 min (10p)",
  reb_pct_avg_10: "% de rebote (10p)",
  oreb_avg_10: "Rebotes ofensivos (10p)",
  dreb_avg_10: "Rebotes defensivos (10p)",
  ast_ewma_10: "Asistencias recientes (EWMA 10p)",
  ast_avg_5: "Asistencias promedio (5p)",
  ast_avg_10: "Asistencias promedio (10p)",
  ast_avg_20: "Asistencias promedio (20p)",
  ast_per36_10: "Asistencias por 36 min (10p)",
  ast_pct_avg_10: "% de asistencias (10p)",
  stl_avg_10: "Robos promedio (10p)",
  blk_avg_10: "Bloqueos promedio (10p)",
  tov_avg_10: "Pérdidas promedio (10p)",
  pf_avg_10: "Faltas personales (10p)",
  plus_minus_avg_10: "Plus/Minus promedio (10p)",
  // Fatigue & context
  is_home: "Condición de local",
  is_back_to_back: "Partido en Back-to-Back",
  rest_days: "Días de descanso del equipo",
  player_rest_days: "Días de descanso del jugador",
  games_in_last_7_days: "Partidos últimos 7 días",
  games_in_last_14_days: "Partidos últimos 14 días",
  road_trip_game_num: "Partido consecutivo de visita",
  games_played_prior: "Partidos jugados previos",
  pos_group_code: "Posición del jugador",
  // Own-team absences & WOWY
  missing_team_minutes: "Minutos ausentes del equipo",
  missing_team_usage_pct: "Uso ofensivo ausente del equipo",
  missing_same_pos_minutes: "Minutos ausentes misma posición",
  missing_same_pos_usage: "Uso ausente misma posición",
  star_teammate_out: "Compañero estrella ausente",
  num_rotation_players_out: "Jugadores de rotación OUT",
  wowy_sample_games: "Muestra histórica WOWY",
  wowy_delta_min: "Impacto histórico WOWY (MIN)",
  wowy_delta_usg: "Impacto histórico WOWY (USG%)",
  wowy_delta_pts_p36: "Impacto histórico WOWY (PTS/36)",
  wowy_delta_reb_p36: "Impacto histórico WOWY (REB/36)",
  wowy_delta_ast_p36: "Impacto histórico WOWY (AST/36)",
  // Opponent defense & opponent absences
  team_pace_10: "Ritmo del equipo (10p)",
  team_off_rating_10: "Rating ofensivo propio (10p)",
  team_def_rating_10: "Rating defensivo propio (10p)",
  team_net_rating_10: "Net Rating propio (10p)",
  opp_pace_10: "Ritmo del rival (10p)",
  opp_off_rating_10: "Rating ofensivo rival (10p)",
  opp_def_rating_10: "Rating defensivo rival (10p)",
  opp_net_rating_10: "Net Rating rival (10p)",
  expected_game_pace: "Ritmo esperado del partido",
  expected_margin_proxy: "Diferencial esperado (NetRtg)",
  abs_expected_margin: "Riesgo de blowout",
  opp_pts_allowed_10: "Puntos permitidos por rival",
  opp_reb_allowed_10: "Rebotes permitidos por rival",
  opp_ast_allowed_10: "Asistencias permitidas rival",
  opp_fta_allowed_10: "Tiros libres concedidos rival",
  opp_efg_allowed_10: "Eficiencia eFG% permitida rival",
  opp_fg3a_allowed_10: "Triples permitidos por rival",
  opp_tov_forced_10: "Pérdidas forzadas por rival",
  opp_oreb_allowed_10: "Rebotes ofensivos concedidos",
  opp_dvp_pts_10: "Defensa rival vs posición (PTS)",
  opp_dvp_reb_10: "Defensa rival vs posición (REB)",
  opp_dvp_ast_10: "Defensa rival vs posición (AST)",
  opp_missing_team_minutes: "Minutos ausentes del rival",
  opp_missing_team_usage_pct: "Uso ausente del rival",
  opp_star_out: "Estrella del rival ausente",
  opp_missing_center_minutes: "Pívot rival ausente (min)",
  opp_missing_def_stocks: "Defensa rival perdida (STL+BLK)",
  opp_missing_same_pos_minutes: "Defensor rival misma pos. OUT",
};

export function humanizeFeature(name: string): string {
  if (FEATURE_LABELS[name]) return FEATURE_LABELS[name];
  return name
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
