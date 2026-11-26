import { Fragment, useState } from "react";
import { Check, ChevronRight, Loader2, Sparkles } from "lucide-react";
import type { AskResponse, TeamBoxscoreRow, TeamSimulationResponse } from "@/lib/api";
import { cn, formatSigned, humanizeFeature } from "@/lib/utils";

type EdgeStatKey = "pts" | "reb" | "ast" | "pra";

interface TeamBoxscoreTableProps {
  teamFullName: string;
  roleLabel: "Visitante" | "Local";
  data: TeamSimulationResponse;
  isB2B: boolean;
  onToggleB2B: () => void;
  onToggleOut: (playerId: number) => void;
  expandedPlayerId: number | null;
  onToggleExpand: (playerId: number) => void;
  aiLoadingId: number | null;
  aiByPlayer: Record<number, AskResponse>;
  aiError: string | null;
  onExplainWithAI: (row: TeamBoxscoreRow, teamData: TeamSimulationResponse) => Promise<void>;
}

function ProjCell({
  value,
  delta,
  bold,
}: {
  value: number;
  delta: number;
  bold?: boolean;
}) {
  const showDelta = Math.abs(delta) >= 0.2;
  return (
    <td className="num px-1.5 py-0 text-right align-middle">
      <span
        className={cn(
          "text-xs text-amber-600 dark:text-amber-400",
          bold && "font-semibold"
        )}
      >
        {value.toFixed(1)}
      </span>
      {showDelta ? (
        <span
          className={cn(
            "ml-0.5 text-[9px] font-medium",
            delta > 0
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400"
          )}
        >
          {formatSigned(delta, 1)}
        </span>
      ) : null}
    </td>
  );
}

export function TeamBoxscoreTable({
  teamFullName,
  roleLabel,
  data,
  isB2B,
  onToggleB2B,
  onToggleOut,
  expandedPlayerId,
  onToggleExpand,
  aiLoadingId,
  aiByPlayer,
  aiError,
  onExplainWithAI,
}: TeamBoxscoreTableProps) {
  const [edgeStat, setEdgeStat] = useState<EdgeStatKey>("pts");
  const [edgeLineInput, setEdgeLineInput] = useState<string>("");

  const hasOuts = data.rows.some((r) => r.is_out);

  return (
    <div className="min-w-0">
      {/* Team header */}
      <div className="mb-2 flex items-center justify-between gap-2 px-1">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-bold tracking-tight text-[var(--text-primary)]">
            {teamFullName}
          </h2>
          <span className="text-xs text-[var(--text-muted)]">{roleLabel}</span>
        </div>

        <button
          type="button"
          onClick={onToggleB2B}
          className={cn(
            "h-6 rounded border px-2 text-[11px] font-medium transition-colors cursor-pointer",
            isB2B
              ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
              : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          )}
        >
          B2B {isB2B ? "ON" : "OFF"}
        </button>
      </div>

      {/* Boxscore table: Base (10p) vs Proyectado (amber) */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-[10px] font-medium">
              <th colSpan={2} className="pb-1 text-left text-[var(--text-muted)]" />
              <th
                colSpan={3}
                className="border-r border-[var(--border-hairline)] pb-1 text-center text-[var(--text-muted)]"
              >
                Base
              </th>
              <th colSpan={5} className="pb-1 text-center text-amber-600 dark:text-amber-400">
                Proyectado{hasOuts ? " · con bajas" : ""}
              </th>
            </tr>
            <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
              <th className="w-7 px-1 py-1.5 text-center">OUT</th>
              <th className="px-1.5 py-1.5 text-left">Jugador</th>
              <th className="px-1.5 py-1.5 text-right">MIN</th>
              <th className="px-1.5 py-1.5 text-right">PTS</th>
              <th className="border-r border-[var(--border-hairline)] px-1.5 py-1.5 text-right">
                PRA
              </th>
              <th className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80">
                MIN
              </th>
              <th className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80">
                PTS
              </th>
              <th className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80">
                REB
              </th>
              <th className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80">
                AST
              </th>
              <th className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80">
                PRA
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border-hairline)]">
            {data.rows.map((row) => {
              const isExpanded = expandedPlayerId === row.player_id && !row.is_out;
              const aiResp = aiByPlayer[row.player_id];

              const parsedLine = Number.parseFloat(edgeLineInput);
              const hasValidLine = !Number.isNaN(parsedLine) && edgeLineInput.trim() !== "";
              const projectedForEdge = row.scenario ? row.scenario[edgeStat] : 0;
              const edgeValue = hasValidLine ? projectedForEdge - parsedLine : null;

              return (
                <Fragment key={row.player_id}>
                  <tr
                    className={cn(
                      "h-10 transition-colors",
                      row.is_out ? "opacity-45" : "hover:bg-[var(--bg-panel)]",
                      isExpanded && "bg-[var(--bg-panel)]"
                    )}
                  >
                    {/* OUT Checkbox */}
                    <td className="px-1 py-0 text-center align-middle">
                      <button
                        type="button"
                        onClick={() => onToggleOut(row.player_id)}
                        aria-label={`Marcar ${row.player_name} como baja`}
                        className={cn(
                          "inline-flex h-4 w-4 items-center justify-center rounded border transition-colors cursor-pointer",
                          row.is_out
                            ? "border-rose-500 bg-rose-500 text-white"
                            : "border-[var(--border-hairline)] bg-[var(--bg-panel)] hover:border-[var(--accent)]"
                        )}
                      >
                        {row.is_out ? <Check className="h-3 w-3 stroke-[3]" /> : null}
                      </button>
                    </td>

                    {/* Player Name + Position + Official Injury Badge */}
                    <td className="px-1.5 py-0 align-middle">
                      <div className="flex flex-wrap items-center gap-1">
                        <button
                          type="button"
                          disabled={row.is_out}
                          onClick={() => {
                            onToggleExpand(row.player_id);
                            setEdgeLineInput("");
                          }}
                          className={cn(
                            "inline-flex items-center gap-1 text-left text-xs font-medium text-[var(--text-primary)]",
                            row.is_out
                              ? "line-through cursor-default"
                              : "hover:text-amber-500 cursor-pointer"
                          )}
                        >
                          {!row.is_out ? (
                            <ChevronRight
                              className={cn(
                                "h-3 w-3 shrink-0 text-[var(--text-muted)] transition-transform",
                                isExpanded && "rotate-90 text-amber-500"
                              )}
                            />
                          ) : null}
                          <span className="truncate max-w-[130px]">{row.player_name}</span>
                        </button>
                        <span className="num text-[10px] text-[var(--text-muted)]">
                          {row.pos_group}
                        </span>
                        {row.injury_status === "OUT" ? (
                          <span
                            title={row.injury_note ?? "Fuera"}
                            className="rounded bg-rose-500/15 px-1 py-px text-[9px] font-semibold text-rose-500 dark:text-rose-400"
                          >
                            Fuera
                          </span>
                        ) : row.injury_status === "DTD" ? (
                          <span
                            title={row.injury_note ?? "Día a día"}
                            className="rounded bg-amber-500/15 px-1 py-px text-[9px] font-semibold text-amber-600 dark:text-amber-400"
                          >
                            Día a día
                          </span>
                        ) : null}
                      </div>
                    </td>

                    {/* Base (3 cols) + Proyectado (5 cols) */}
                    {row.is_out || !row.base || !row.scenario || !row.deltas ? (
                      <>
                        <td className="num px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-muted)]">
                          {row.ewma_min.toFixed(1)}
                        </td>
                        <td className="num px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-muted)]">
                          —
                        </td>
                        <td className="num border-r border-[var(--border-hairline)] px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-muted)]">
                          —
                        </td>
                        <td
                          colSpan={5}
                          className="px-2 py-0 text-left align-middle text-xs font-medium text-rose-500 dark:text-rose-400"
                        >
                          Baja
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="num px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-secondary)]">
                          {row.base.min.toFixed(1)}
                        </td>
                        <td className="num px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-secondary)]">
                          {row.base.pts.toFixed(1)}
                        </td>
                        <td className="num border-r border-[var(--border-hairline)] px-1.5 py-0 text-right align-middle text-[11px] text-[var(--text-secondary)]">
                          {row.base.pra.toFixed(1)}
                        </td>

                        <ProjCell value={row.scenario.min} delta={row.deltas.min} />
                        <ProjCell value={row.scenario.pts} delta={row.deltas.pts} />
                        <ProjCell value={row.scenario.reb} delta={row.deltas.reb} />
                        <ProjCell value={row.scenario.ast} delta={row.deltas.ast} />
                        <ProjCell value={row.scenario.pra} delta={row.deltas.pra} bold />
                      </>
                    )}
                  </tr>

                  {/* Expandable Detail Row: SHAP + WOWY + Edge Comparator + AI */}
                  {isExpanded && row.scenario ? (
                    <tr className="bg-[var(--bg-panel)]/80">
                      <td colSpan={10} className="px-3 py-2.5">
                        <div className="space-y-2.5 text-xs">
                          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                            {/* Top 3 SHAP */}
                            <div className="space-y-1">
                              <div className="text-[10px] font-semibold text-[var(--text-secondary)]">
                                Factores clave (PTS)
                              </div>
                              {row.top_drivers.slice(0, 3).map((d) => (
                                <div
                                  key={d.feature}
                                  className="flex items-center justify-between gap-2 text-[11px]"
                                >
                                  <span className="truncate text-[var(--text-primary)]">
                                    {humanizeFeature(d.feature)}
                                  </span>
                                  <span
                                    className={cn(
                                      "num shrink-0 font-medium",
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

                            {/* WOWY + Secondary stats */}
                            <div className="space-y-1">
                              <div className="text-[10px] font-semibold text-[var(--text-secondary)]">
                                WOWY y volumen
                              </div>
                              {row.wowy ? (
                                <div className="text-[11px]">
                                  <div className="text-[var(--text-muted)]">
                                    Sin {row.wowy.primary_out_player} ({row.wowy.sample_games_without}p)
                                  </div>
                                  <div className="num text-[var(--text-primary)]">
                                    MIN {formatSigned(row.wowy.raw_delta_min, 1)} · PTS{" "}
                                    {formatSigned(row.wowy.raw_delta_pts, 1)} · REB{" "}
                                    {formatSigned(row.wowy.raw_delta_reb, 1)} · AST{" "}
                                    {formatSigned(row.wowy.raw_delta_ast, 1)}
                                  </div>
                                </div>
                              ) : (
                                <div className="text-[11px] text-[var(--text-muted)]">
                                  Sin bajas propias marcadas.
                                </div>
                              )}
                              <div className="num text-[10px] text-[var(--text-muted)]">
                                STL {row.scenario.stl.toFixed(1)} · BLK {row.scenario.blk.toFixed(1)} · TOV{" "}
                                {row.scenario.tov.toFixed(1)} · FGA {row.scenario.fga.toFixed(1)} · FTM{" "}
                                {row.scenario.ftm.toFixed(1)}
                              </div>
                            </div>
                          </div>

                          {/* Line Edge Comparator + Explain with AI */}
                          <div className="flex flex-wrap items-center gap-1.5 border-t border-[var(--border-hairline)] pt-2">
                            <span className="text-[11px] text-[var(--text-muted)]">Línea:</span>
                            <select
                              aria-label="Stat para línea"
                              value={edgeStat}
                              onChange={(e) => setEdgeStat(e.target.value as EdgeStatKey)}
                              className="h-6 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-1 text-[11px] font-semibold uppercase text-[var(--text-primary)]"
                            >
                              <option value="pts">PTS</option>
                              <option value="reb">REB</option>
                              <option value="ast">AST</option>
                              <option value="pra">PRA</option>
                            </select>
                            <input
                              type="number"
                              step="0.5"
                              value={edgeLineInput}
                              onChange={(e) => setEdgeLineInput(e.target.value)}
                              placeholder={projectedForEdge.toFixed(1)}
                              className="num h-6 w-16 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-1.5 text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
                            />
                            {edgeValue !== null ? (
                              <span
                                className={cn(
                                  "num rounded px-1.5 py-0.5 text-[11px] font-semibold",
                                  edgeValue >= 0
                                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                    : "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                                )}
                              >
                                Edge {formatSigned(edgeValue, 1)}
                              </span>
                            ) : null}

                            <button
                              type="button"
                              disabled={aiLoadingId !== null}
                              onClick={() => void onExplainWithAI(row, data)}
                              className="ml-auto inline-flex h-6 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-45 cursor-pointer"
                            >
                              {aiLoadingId === row.player_id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <Sparkles className="h-3 w-3 text-[var(--accent)]" />
                              )}
                              Explicar con IA
                            </button>
                          </div>

                          {aiError && aiLoadingId === null ? (
                            <div className="text-[11px] text-rose-500">{aiError}</div>
                          ) : null}

                          {aiResp ? (
                            <div className="border-t border-[var(--border-hairline)] pt-2 text-xs leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
                              {aiResp.analysis || aiResp.final_response}
                            </div>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
