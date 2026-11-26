import { Fragment, useState } from "react";
import { Check, ChevronRight, Loader2, Sparkles } from "lucide-react";
import type {
  ExplainResponse,
  StatProjection,
  TeamBoxscoreRow,
  TeamSimulationResponse,
} from "@/lib/api";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { cn, formatSigned, humanizeFeature } from "@/lib/utils";

export type StatViewMode = "main" | "extras" | "all";
type EdgeStatKey = "pts" | "reb" | "ast" | "pra" | "ra" | "stl" | "blk" | "tov";

interface TeamBoxscoreTableProps {
  teamFullName: string;
  roleLabel: "Visitante" | "Local";
  data: TeamSimulationResponse;
  statView: StatViewMode;
  isB2B: boolean;
  onToggleB2B: () => void;
  onToggleOut: (playerId: number) => void;
  expandedPlayerId: number | null;
  onToggleExpand: (playerId: number) => void;
  onOpenPlayer: (row: TeamBoxscoreRow) => void;
  aiLoadingId: number | null;
  aiByPlayer: Record<number, ExplainResponse>;
  aiError: string | null;
  onExplainWithAI: (row: TeamBoxscoreRow, teamData: TeamSimulationResponse) => Promise<void>;
}

const EXTRAS_COLS: { key: keyof StatProjection; label: string; bold?: boolean }[] = [
  { key: "min", label: "MIN" },
  { key: "ra", label: "R+A", bold: true },
  { key: "pr", label: "P+R" },
  { key: "pa", label: "P+A" },
  { key: "stl", label: "STL" },
  { key: "blk", label: "BLK" },
  { key: "tov", label: "TOV" },
  { key: "pf", label: "PF" },
  { key: "fga", label: "FGA" },
  { key: "ftm", label: "FTM" },
];

const ALL_COLS: { key: keyof StatProjection; label: string; bold?: boolean }[] = [
  { key: "min", label: "MIN" },
  { key: "pts", label: "PTS" },
  { key: "reb", label: "REB" },
  { key: "ast", label: "AST" },
  { key: "pra", label: "PRA", bold: true },
  { key: "pr", label: "P+R" },
  { key: "pa", label: "P+A" },
  { key: "ra", label: "R+A" },
  { key: "stl", label: "STL" },
  { key: "blk", label: "BLK" },
  { key: "tov", label: "TOV" },
  { key: "pf", label: "PF" },
  { key: "fga", label: "FGA" },
  { key: "ftm", label: "FTM" },
];

function ProjCell({
  value,
  baseValue,
  delta,
  bold,
}: {
  value: number;
  baseValue?: number;
  delta: number;
  bold?: boolean;
}) {
  const showDelta = Math.abs(delta) >= 0.2;
  const tooltip =
    baseValue !== undefined
      ? `Base (sin bajas/B2B): ${baseValue.toFixed(1)} → Proyectado: ${value.toFixed(1)} (${formatSigned(delta, 1)})`
      : undefined;
  return (
    <td title={tooltip} className="num px-2 py-0 text-right align-middle whitespace-nowrap">
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
            "ml-1 text-[10px] font-semibold",
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
  statView,
  isB2B,
  onToggleB2B,
  onToggleOut,
  expandedPlayerId,
  onToggleExpand,
  onOpenPlayer,
  aiLoadingId,
  aiByPlayer,
  aiError,
  onExplainWithAI,
}: TeamBoxscoreTableProps) {
  const [edgeStat, setEdgeStat] = useState<EdgeStatKey>("pts");
  const [edgeLineInput, setEdgeLineInput] = useState<string>("");
  const [sortBy, setSortBy] = useState<keyof StatProjection | null>(null);
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  function handleSort(key: keyof StatProjection) {
    if (sortBy === key) {
      if (sortDir === "desc") {
        setSortDir("asc");
      } else {
        setSortBy(null);
        setSortDir("desc");
      }
    } else {
      setSortBy(key);
      setSortDir("desc");
    }
  }

  const sortedRows = sortBy
    ? [...data.rows].sort((a, b) => {
        if (a.is_out !== b.is_out) return a.is_out ? 1 : -1;
        const av = a.scenario ? a.scenario[sortBy] : -1;
        const bv = b.scenario ? b.scenario[sortBy] : -1;
        return sortDir === "desc" ? bv - av : av - bv;
      })
    : data.rows;

  const outPlayers = data.rows.filter((r) => r.is_out);
  const hasOuts = outPlayers.length > 0;
  const topBeneficiaries = hasOuts
    ? [...data.rows]
        .filter((r) => !r.is_out && r.deltas && (r.deltas.min >= 0.3 || r.deltas.pra >= 0.5))
        .sort((a, b) => (b.deltas?.min ?? 0) - (a.deltas?.min ?? 0))
        .slice(0, 3)
    : [];
  const totalCols = statView === "main" ? 7 : statView === "extras" ? 12 : 16;

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

      {/* Plain-Spanish Rotation Impact Callout when 1+ players are OUT */}
      {hasOuts && topBeneficiaries.length > 0 ? (
        <div className="mb-2 rounded-md border border-emerald-500/25 bg-emerald-500/8 px-3 py-1.5 text-[11px] leading-snug text-[var(--text-secondary)]">
          <span className="font-semibold text-emerald-600 dark:text-emerald-400">
            Rotación sin {outPlayers.map((p) => p.player_name).join(", ")}:
          </span>{" "}
          absorben más carga{" "}
          {topBeneficiaries.map((p, idx) => (
            <span key={p.player_id}>
              {idx > 0 ? ", " : ""}
              <span className="font-medium text-[var(--text-primary)]">{p.player_name}</span>{" "}
              <span className="num text-emerald-600 dark:text-emerald-400">
                ({formatSigned(p.deltas!.min, 1)}m / {formatSigned(p.deltas!.pra, 1)} PRA)
              </span>
            </span>
          ))}
          .
        </div>
      ) : null}

      {/* Boxscore table */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            {statView === "main" ? (
              <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
                <th className="w-7 px-1 py-1.5 text-center">OUT</th>
                <th className="px-1.5 py-1.5 text-left">
                  Jugador
                  {hasOuts ? (
                    <span className="ml-1.5 font-normal text-amber-500">· con bajas</span>
                  ) : null}
                </th>
                {(
                  [
                    { key: "min", label: "MIN" },
                    { key: "pts", label: "PTS" },
                    { key: "reb", label: "REB" },
                    { key: "ast", label: "AST" },
                    { key: "pra", label: "PRA" },
                  ] as const
                ).map((col) => (
                  <th
                    key={col.key}
                    onClick={() => handleSort(col.key)}
                    title={`Ordenar por ${col.label}`}
                    className="px-2 py-1.5 text-right text-amber-600/90 dark:text-amber-400/90 hover:text-amber-500 cursor-pointer select-none whitespace-nowrap"
                  >
                    {col.label}
                    {sortBy === col.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
                  </th>
                ))}
              </tr>
            ) : (
              <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
                <th className="w-7 px-1 py-1.5 text-center">OUT</th>
                <th className="px-1.5 py-1.5 text-left">Jugador</th>
                {(statView === "extras" ? EXTRAS_COLS : ALL_COLS).map((col) => (
                  <th
                    key={col.key}
                    onClick={() => handleSort(col.key)}
                    title={`Ordenar por ${col.label}`}
                    className="px-1.5 py-1.5 text-right text-amber-600/80 dark:text-amber-400/80 hover:text-amber-500 cursor-pointer select-none"
                  >
                    {col.label}
                    {sortBy === col.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
                  </th>
                ))}
              </tr>
            )}
          </thead>

          <tbody className="divide-y divide-[var(--border-hairline)]">
            {sortedRows.map((row) => {
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

                    {/* Player Avatar + Name (opens full profile) + quick-expand chevron */}
                    <td className="px-1.5 py-0 align-middle">
                      <div className="flex items-center gap-1.5">
                        {!row.is_out ? (
                          <button
                            type="button"
                            onClick={() => {
                              onToggleExpand(row.player_id);
                              setEdgeLineInput("");
                            }}
                            aria-label={`Desplegar resumen rápido de ${row.player_name}`}
                            className="inline-flex h-4 w-4 shrink-0 items-center justify-center text-[var(--text-muted)] hover:text-amber-500 cursor-pointer"
                          >
                            <ChevronRight
                              className={cn(
                                "h-3 w-3 transition-transform",
                                isExpanded && "rotate-90 text-amber-500"
                              )}
                            />
                          </button>
                        ) : (
                          <span className="w-4 shrink-0" />
                        )}

                        <button
                          type="button"
                          onClick={() => onOpenPlayer(row)}
                          className={cn(
                            "inline-flex items-center gap-1.5 text-left text-xs font-medium text-[var(--text-primary)] cursor-pointer group",
                            row.is_out && "line-through"
                          )}
                        >
                          <PlayerAvatar playerId={row.player_id} name={row.player_name} size="sm" />
                          <span className="truncate max-w-[115px] group-hover:text-amber-500 transition-colors">
                            {row.player_name}
                          </span>
                        </button>

                        <span className="num text-[10px] text-[var(--text-muted)]">
                          {row.pos_group}
                        </span>

                        {row.injury_status === "OUT" ? (
                          <span
                            title={row.injury_note ?? "Fuera"}
                            className="shrink-0 whitespace-nowrap rounded bg-rose-500/15 px-1 py-px text-[9px] font-semibold text-rose-500 dark:text-rose-400"
                          >
                            OUT
                          </span>
                        ) : row.injury_status === "DTD" ? (
                          <span
                            title={`Día a día${row.injury_note ? ` — ${row.injury_note}` : ""}`}
                            className="shrink-0 whitespace-nowrap rounded bg-amber-500/15 px-1 py-px text-[9px] font-semibold text-amber-600 dark:text-amber-400"
                          >
                            DTD
                          </span>
                        ) : null}
                      </div>
                    </td>

                    {/* Stat Columns */}
                    {row.is_out || !row.base || !row.scenario || !row.deltas ? (
                      <td
                        colSpan={
                          statView === "main"
                            ? 5
                            : statView === "extras"
                            ? EXTRAS_COLS.length
                            : ALL_COLS.length
                        }
                        className="px-2 py-0 text-left align-middle text-xs font-medium text-rose-500 dark:text-rose-400"
                      >
                        Baja · promedio habitual {row.ewma_min.toFixed(1)} min
                      </td>
                    ) : statView === "main" ? (
                      <>
                        <ProjCell
                          value={row.scenario.min}
                          baseValue={row.base.min}
                          delta={row.deltas.min}
                        />
                        <ProjCell
                          value={row.scenario.pts}
                          baseValue={row.base.pts}
                          delta={row.deltas.pts}
                        />
                        <ProjCell
                          value={row.scenario.reb}
                          baseValue={row.base.reb}
                          delta={row.deltas.reb}
                        />
                        <ProjCell
                          value={row.scenario.ast}
                          baseValue={row.base.ast}
                          delta={row.deltas.ast}
                        />
                        <ProjCell
                          value={row.scenario.pra}
                          baseValue={row.base.pra}
                          delta={row.deltas.pra}
                          bold
                        />
                      </>
                    ) : (
                      (statView === "extras" ? EXTRAS_COLS : ALL_COLS).map((col) => (
                        <ProjCell
                          key={col.key}
                          value={row.scenario![col.key]}
                          baseValue={row.base![col.key]}
                          delta={row.deltas![col.key]}
                          bold={col.bold}
                        />
                      ))
                    )}
                  </tr>

                  {/* Quick Expandable Detail Row */}
                  {isExpanded && row.scenario ? (
                    <tr className="bg-[var(--bg-panel)]/80">
                      <td colSpan={totalCols} className="px-3 py-2.5">
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
                                {row.scenario.tov.toFixed(1)} · PF {row.scenario.pf.toFixed(1)} · FGA{" "}
                                {row.scenario.fga.toFixed(1)} · FTM {row.scenario.ftm.toFixed(1)}
                              </div>
                            </div>
                          </div>

                          {/* Line Edge Comparator + Open Profile + Explain with AI */}
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
                              <option value="ra">R+A</option>
                              <option value="stl">STL</option>
                              <option value="blk">BLK</option>
                              <option value="tov">TOV</option>
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

                            <div className="ml-auto flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() => onOpenPlayer(row)}
                                className="inline-flex h-6 items-center rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
                              >
                                Ver perfil
                              </button>
                              <button
                                type="button"
                                disabled={aiLoadingId !== null}
                                onClick={() => void onExplainWithAI(row, data)}
                                className="inline-flex h-6 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-45 cursor-pointer"
                              >
                                {aiLoadingId === row.player_id ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <Sparkles className="h-3 w-3 text-[var(--accent)]" />
                                )}
                                Explicar con IA
                              </button>
                            </div>
                          </div>

                          {aiError && aiLoadingId === null ? (
                            <div className="text-[11px] text-rose-500">{aiError}</div>
                          ) : null}

                          {aiResp ? (
                            <div className="border-t border-[var(--border-hairline)] pt-2 text-xs leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
                              {aiResp.analysis}
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
