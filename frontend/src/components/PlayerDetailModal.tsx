import { useEffect, useState } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import {
  fetchPlayerProfile,
  type ExplainResponse,
  type PlayerProfileResponse,
  type StatProjection,
  type TeamBoxscoreRow,
  type TeamSimulationResponse,
} from "@/lib/api";
import { AiAnalysisBlock } from "@/components/AiAnalysisBlock";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { cn, formatSigned, humanizeFeature } from "@/lib/utils";

type StatKey = keyof StatProjection;

const ALL_STATS_META: { key: StatKey; label: string; group: "primary" | "combo" | "extra" }[] = [
  { key: "min", label: "MIN", group: "primary" },
  { key: "pts", label: "PTS", group: "primary" },
  { key: "reb", label: "REB", group: "primary" },
  { key: "ast", label: "AST", group: "primary" },
  { key: "pra", label: "PRA", group: "combo" },
  { key: "pr", label: "P+R", group: "combo" },
  { key: "pa", label: "P+A", group: "combo" },
  { key: "ra", label: "R+A", group: "combo" },
  { key: "stl", label: "STL", group: "extra" },
  { key: "blk", label: "BLK", group: "extra" },
  { key: "tov", label: "TOV", group: "extra" },
  { key: "pf", label: "PF", group: "extra" },
  { key: "fga", label: "FGA", group: "extra" },
  { key: "ftm", label: "FTM", group: "extra" },
];

const EDGE_STAT_OPTIONS: StatKey[] = [
  "pts",
  "reb",
  "ast",
  "pra",
  "pr",
  "pa",
  "ra",
  "stl",
  "blk",
  "tov",
  "min",
];

interface PlayerDetailModalProps {
  row: TeamBoxscoreRow;
  teamData: TeamSimulationResponse;
  teamFullName: string;
  aiLoading: boolean;
  aiResponse?: ExplainResponse;
  aiError: string | null;
  onExplainWithAI: (row: TeamBoxscoreRow, teamData: TeamSimulationResponse) => Promise<void>;
  onClose: () => void;
}

export function PlayerDetailModal({
  row,
  teamData,
  teamFullName,
  aiLoading,
  aiResponse,
  aiError,
  onExplainWithAI,
  onClose,
}: PlayerDetailModalProps) {
  const [profile, setProfile] = useState<PlayerProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [edgeStat, setEdgeStat] = useState<StatKey>("pts");
  const [edgeLineInput, setEdgeLineInput] = useState("");

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setProfileLoading(true);
    setProfileError(null);
    void fetchPlayerProfile(row.player_id, 15)
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setProfileError(err.message);
      })
      .finally(() => {
        if (!cancelled) setProfileLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [row.player_id]);

  const parsedLine = Number.parseFloat(edgeLineInput);
  const hasValidLine = !Number.isNaN(parsedLine) && edgeLineInput.trim() !== "";
  const projectedForEdge = row.scenario ? row.scenario[edgeStat] : 0;
  const edgeValue = hasValidLine ? projectedForEdge - parsedLine : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 sm:p-6 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="relative max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-lg border border-[var(--border-hairline)] bg-[var(--bg-canvas)] text-[var(--text-primary)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-[var(--border-hairline)] bg-[var(--bg-panel)] px-5 py-3.5">
          <div className="flex items-center gap-3.5 min-w-0">
            <PlayerAvatar playerId={row.player_id} name={row.player_name} size="lg" />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-lg font-bold tracking-tight text-[var(--text-primary)]">
                  {row.player_name}
                </h2>
                <span className="num rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--text-secondary)]">
                  {row.pos_group}
                </span>
                {row.injury_status === "OUT" ? (
                  <span
                    title={row.injury_note ?? "Fuera"}
                    className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-rose-500 dark:text-rose-400"
                  >
                    Fuera
                  </span>
                ) : row.injury_status === "DTD" ? (
                  <span
                    title={row.injury_note ?? "Día a día"}
                    className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-amber-600 dark:text-amber-400"
                  >
                    Día a día
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">{teamFullName}</span>
                <span>
                  {teamData.is_home ? "vs" : "@"} {teamData.opponent_abbreviation}
                </span>
                <span>·</span>
                <span>{teamData.is_home ? "Local" : "Visitante"}</span>
                {teamData.is_back_to_back ? (
                  <span className="rounded bg-amber-500/15 px-1.5 py-px text-[10px] font-semibold text-amber-500">
                    B2B
                  </span>
                ) : null}
                {row.injury_note ? (
                  <span className="text-[11px] text-[var(--text-muted)]">· {row.injury_note}</span>
                ) : null}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar perfil"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5 p-5">
          {/* 1. Full 13-Stat Projection Grid */}
          {row.base && row.scenario && row.deltas ? (
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                  Proyección completa del modelo (14 estadísticas)
                </h3>
                <span className="text-[11px] text-[var(--text-muted)]">
                  Base vs Escenario actual
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 md:grid-cols-7">
                {ALL_STATS_META.map((stat) => {
                  const baseVal = row.base![stat.key];
                  const scenVal = row.scenario![stat.key];
                  const deltaVal = row.deltas![stat.key];
                  const showDelta = Math.abs(deltaVal) >= 0.1;
                  return (
                    <div
                      key={stat.key}
                      className={cn(
                        "rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2.5 py-2",
                        stat.key === "pra" && "border-amber-500/40"
                      )}
                    >
                      <div className="flex items-center justify-between text-[10px] font-semibold text-[var(--text-muted)]">
                        <span>{stat.label}</span>
                        {showDelta ? (
                          <span
                            className={cn(
                              "num",
                              deltaVal > 0
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-rose-600 dark:text-rose-400"
                            )}
                          >
                            {formatSigned(deltaVal, 1)}
                          </span>
                        ) : null}
                      </div>
                      <div className="num mt-0.5 text-base font-bold text-amber-600 dark:text-amber-400">
                        {scenVal.toFixed(1)}
                      </div>
                      <div className="num text-[10px] text-[var(--text-muted)]">
                        Base {baseVal.toFixed(1)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-500">
              Este jugador está marcado como BAJA (OUT) en el escenario actual.
            </div>
          )}

          {/* 2. Line Edge Comparator + Fast AI Explanation */}
          {!row.is_out && row.scenario ? (
            <section className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  Comparar línea:
                </span>
                <select
                  aria-label="Stat para línea"
                  value={edgeStat}
                  onChange={(e) => setEdgeStat(e.target.value as StatKey)}
                  className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-xs font-semibold uppercase text-[var(--text-primary)]"
                >
                  {EDGE_STAT_OPTIONS.map((k) => (
                    <option key={k} value={k}>
                      {k.toUpperCase()}
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  step="0.5"
                  value={edgeLineInput}
                  onChange={(e) => setEdgeLineInput(e.target.value)}
                  placeholder={projectedForEdge.toFixed(1)}
                  className="num h-7 w-20 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
                />
                {edgeValue !== null ? (
                  <span
                    className={cn(
                      "num rounded px-2 py-0.5 text-xs font-semibold",
                      edgeValue >= 0
                        ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                        : "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                    )}
                  >
                    {edgeValue >= 0 ? "OVER" : "UNDER"} · Edge {formatSigned(edgeValue, 1)}
                  </span>
                ) : null}

                <button
                  type="button"
                  disabled={aiLoading}
                  onClick={() => void onExplainWithAI(row, teamData)}
                  className="ml-auto inline-flex h-7 items-center gap-1.5 rounded bg-[var(--accent)] px-3 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50 cursor-pointer"
                >
                  {aiLoading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  Explicar con IA
                </button>
              </div>

              {aiError ? <div className="text-xs text-rose-500">{aiError}</div> : null}

              {aiResponse ? (
                <div className="border-t border-[var(--border-hairline)] pt-2.5">
                  <AiAnalysisBlock
                    text={aiResponse.analysis}
                    verified={aiResponse.verification_passed}
                    compact
                  />
                </div>
              ) : null}
            </section>
          ) : null}

          {/* 3. SHAP Drivers + WOWY History */}
          {!row.is_out ? (
            <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5">
                <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">
                  Factores clave del modelo (SHAP · PTS)
                </h3>
                <div className="space-y-1.5">
                  {row.top_drivers.map((d) => (
                    <div key={d.feature} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-[var(--text-primary)]">
                        {humanizeFeature(d.feature)}
                      </span>
                      <span
                        className={cn(
                          "num shrink-0 font-semibold",
                          d.shap_value >= 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-rose-600 dark:text-rose-400"
                        )}
                      >
                        {formatSigned(d.shap_value, 2)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5">
                <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">
                  Historial WOWY (con vs. sin compañero OUT)
                </h3>
                {row.wowy ? (
                  <div className="space-y-1 text-xs">
                    <div className="text-[var(--text-muted)]">
                      Sin <span className="font-medium text-[var(--text-primary)]">{row.wowy.primary_out_player}</span>{" "}
                      ({row.wowy.sample_games_without} partidos sin vs. {row.wowy.sample_games_with} con)
                    </div>
                    <div className="num grid grid-cols-4 gap-2 pt-1">
                      <div>
                        <div className="text-[10px] text-[var(--text-muted)]">MIN</div>
                        <div className="font-semibold">{formatSigned(row.wowy.raw_delta_min, 1)}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-[var(--text-muted)]">PTS</div>
                        <div className="font-semibold">{formatSigned(row.wowy.raw_delta_pts, 1)}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-[var(--text-muted)]">REB</div>
                        <div className="font-semibold">{formatSigned(row.wowy.raw_delta_reb, 1)}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-[var(--text-muted)]">AST</div>
                        <div className="font-semibold">{formatSigned(row.wowy.raw_delta_ast, 1)}</div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-[var(--text-muted)]">
                    No hay compañeros marcados como OUT o no existe muestra histórica WOWY previa.
                  </p>
                )}
              </div>
            </section>
          ) : null}

          {/* 4. Historical Splits & Recent 15 Game Logs */}
          <section className="space-y-4">
            {profileLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-[var(--text-muted)]">
                <Loader2 className="h-4 w-4 animate-spin text-[var(--accent)]" />
                Cargando historial y splits de {row.player_name}...
              </div>
            ) : profileError ? (
              <div className="text-xs text-rose-500">{profileError}</div>
            ) : profile ? (
              <>
                {/* Splits */}
                {profile.splits.length > 0 ? (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                      Promedios recientes y splits ({profile.total_games} partidos en base)
                    </h3>
                    <div className="overflow-x-auto rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)]">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
                            <th className="px-2.5 py-2 text-left">Split</th>
                            <th className="px-2 py-2 text-right">PJ</th>
                            <th className="px-2 py-2 text-right">MIN</th>
                            <th className="px-2 py-2 text-right">PTS</th>
                            <th className="px-2 py-2 text-right">REB</th>
                            <th className="px-2 py-2 text-right">AST</th>
                            <th className="px-2 py-2 text-right">PRA</th>
                            <th className="px-2 py-2 text-right">STL</th>
                            <th className="px-2 py-2 text-right">BLK</th>
                            <th className="px-2 py-2 text-right">TOV</th>
                            <th className="px-2 py-2 text-right">FGA</th>
                            <th className="px-2 py-2 text-right">FTM</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-hairline)] num">
                          {profile.splits.map((s) => (
                            <tr key={s.label} className="hover:bg-[var(--bg-subtle)]">
                              <td className="px-2.5 py-1.5 font-sans font-medium text-[var(--text-primary)]">
                                {s.label}
                              </td>
                              <td className="px-2 py-1.5 text-right text-[var(--text-muted)]">{s.games}</td>
                              <td className="px-2 py-1.5 text-right">{s.min.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right font-semibold">{s.pts.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.reb.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.ast.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right font-semibold text-amber-600 dark:text-amber-400">
                                {s.pra.toFixed(1)}
                              </td>
                              <td className="px-2 py-1.5 text-right">{s.stl.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.blk.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.tov.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.fga.toFixed(1)}</td>
                              <td className="px-2 py-1.5 text-right">{s.ftm.toFixed(1)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                {/* Recent Game Logs */}
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                    Últimos {profile.recent_games.length} partidos
                  </h3>
                  <div className="overflow-x-auto rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)]">
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
                          <th className="px-2.5 py-2 text-left">Fecha</th>
                          <th className="px-2 py-2 text-left">Rival</th>
                          <th className="px-1.5 py-2 text-center">Res</th>
                          <th className="px-2 py-2 text-right">MIN</th>
                          <th className="px-2 py-2 text-right">PTS</th>
                          <th className="px-2 py-2 text-right">REB</th>
                          <th className="px-2 py-2 text-right">AST</th>
                          <th className="px-2 py-2 text-right">PRA</th>
                          <th className="px-2 py-2 text-right">STL</th>
                          <th className="px-2 py-2 text-right">BLK</th>
                          <th className="px-2 py-2 text-right">TOV</th>
                          <th className="px-2 py-2 text-right">FGA</th>
                          <th className="px-2 py-2 text-right">FTM</th>
                          <th className="px-2 py-2 text-right">+/-</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border-hairline)] num">
                        {profile.recent_games.map((g) => (
                          <tr key={g.game_id} className="hover:bg-[var(--bg-subtle)]">
                            <td className="px-2.5 py-1.5 text-[var(--text-muted)]">{g.game_date}</td>
                            <td className="px-2 py-1.5 font-sans font-medium">
                              {g.is_home ? "vs" : "@"} {g.opponent_abbreviation}
                            </td>
                            <td className="px-1.5 py-1.5 text-center">
                              {g.wl ? (
                                <span
                                  className={cn(
                                    "rounded px-1 py-px text-[10px] font-semibold",
                                    g.wl === "W"
                                      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                      : "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                                  )}
                                >
                                  {g.wl}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="px-2 py-1.5 text-right">{g.min.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right font-semibold">{g.pts.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.reb.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.ast.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right font-semibold text-amber-600 dark:text-amber-400">
                              {g.pra.toFixed(0)}
                            </td>
                            <td className="px-2 py-1.5 text-right">{g.stl.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.blk.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.tov.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.fga.toFixed(0)}</td>
                            <td className="px-2 py-1.5 text-right">{g.ftm.toFixed(0)}</td>
                            <td
                              className={cn(
                                "px-2 py-1.5 text-right",
                                g.plus_minus > 0
                                  ? "text-emerald-600 dark:text-emerald-400"
                                  : g.plus_minus < 0
                                  ? "text-rose-600 dark:text-rose-400"
                                  : "text-[var(--text-muted)]"
                              )}
                            >
                              {formatSigned(g.plus_minus, 0)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
