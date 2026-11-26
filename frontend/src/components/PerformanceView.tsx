import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, TrendingUp } from "lucide-react";
import {
  fetchPropsPerformance,
  gradeTrackedPicks,
  snapshotDailyProps,
  type PerformanceSummaryResponse,
  type PropsSnapshotResponse,
} from "@/lib/api";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { cn, formatSigned } from "@/lib/utils";

interface PerformanceViewProps {
  activeDate?: string;
}

export function PerformanceView({ activeDate }: PerformanceViewProps) {
  const [data, setData] = useState<PerformanceSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [snapLoading, setSnapLoading] = useState(false);
  const [gradeLoading, setGradeLoading] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<"ALL" | "PENDING" | "GRADED">("ALL");
  const [sideFilter, setSideFilter] = useState<"ALL" | "OVER" | "UNDER">("ALL");
  const [statFilter, setStatFilter] = useState<string>("ALL");

  async function loadPerformance() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchPropsPerformance(200);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error cargando historial de Win Rate.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPerformance();
  }, []);

  async function handleSnapshot(forceRefresh = false) {
    if (snapLoading) return;
    setSnapLoading(true);
    setError(null);
    setBanner(null);
    try {
      const res: PropsSnapshotResponse = await snapshotDailyProps(
        activeDate || undefined,
        forceRefresh
      );
      setBanner(
        `Fecha ${res.game_date}: ${res.lines_available} líneas ${
          res.lines_from_cache ? "(caché local, 0 créditos)" : "descargadas"
        } · ${res.picks_registered} picks con Edge registrados automáticamente.`
      );
      await loadPerformance();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error capturando líneas de The Odds API.");
    } finally {
      setSnapLoading(false);
    }
  }

  async function handleGrade() {
    if (gradeLoading) return;
    setGradeLoading(true);
    setError(null);
    setBanner(null);
    try {
      const res = await gradeTrackedPicks();
      setBanner(
        `Calificación completada: ${res.graded} picks actualizados (${res.wins}G - ${res.losses}P - ${res.pushes}E - ${res.voids} anulados).`
      );
      await loadPerformance();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error calificando picks.");
    } finally {
      setGradeLoading(false);
    }
  }

  const overall = data?.overall;
  const allPicks = data?.picks ?? [];
  const overCount = allPicks.filter((p) => p.side === "OVER").length;
  const underCount = allPicks.filter((p) => p.side === "UNDER").length;
  const STAT_ORDER = ["pts", "reb", "ast", "ra", "pr", "pa", "pra", "fg3m", "blk", "tov", "stl"];
  const availableStats = Array.from(new Set(allPicks.map((p) => p.stat))).sort((a, b) => {
    const ia = STAT_ORDER.indexOf(a);
    const ib = STAT_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  const filteredPicks = allPicks
    .filter((p) => {
      if (statusFilter === "PENDING" && p.status !== "PENDING") return false;
      if (statusFilter === "GRADED" && p.status === "PENDING") return false;
      if (sideFilter !== "ALL" && p.side !== sideFilter) return false;
      if (statFilter !== "ALL" && p.stat !== statFilter) return false;
      return true;
    })
    .sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge));

  return (
    <div className="space-y-4">
      {/* Action Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-3">
        <div>
          <h2 className="text-sm font-bold text-[var(--text-primary)]">
            Paper Trading Automático · Win Rate & ROI
          </h2>
          <p className="text-xs text-[var(--text-muted)]">
            Descarga líneas reales de casas (The Odds API), registra picks con Edge automáticamente y los califica contra los boxscores reales de la NBA.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {data?.requests_remaining !== null && data?.requests_remaining !== undefined ? (
            <span className="num rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 py-1 text-[11px] text-[var(--text-muted)]">
              Créditos Odds API: <strong className="text-[var(--text-primary)]">{data.requests_remaining}</strong>
            </span>
          ) : null}

          <button
            type="button"
            disabled={snapLoading}
            onClick={() => void handleSnapshot(false)}
            className="inline-flex h-8 items-center gap-1.5 rounded bg-[var(--accent)] px-3 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
          >
            {snapLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <TrendingUp className="h-3.5 w-3.5" />
            )}
            Capturar líneas y picks{activeDate ? ` (${activeDate.slice(5)})` : ""}
          </button>

          <button
            type="button"
            disabled={gradeLoading}
            onClick={() => void handleGrade()}
            className="inline-flex h-8 items-center gap-1.5 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-3 text-xs font-medium text-[var(--text-primary)] hover:border-[var(--accent)] disabled:opacity-50 cursor-pointer"
          >
            {gradeLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            )}
            Calificar pendientes
          </button>

          <button
            type="button"
            onClick={() => void loadPerformance()}
            aria-label="Recargar"
            className="inline-flex h-8 w-8 items-center justify-center rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      {banner ? (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2 text-xs text-emerald-600 dark:text-emerald-400">
          {banner}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3.5 py-2 text-xs text-rose-600 dark:text-rose-400">
          {error}
        </div>
      ) : null}

      {/* Top KPI Cards */}
      {overall ? (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-5">
          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
            <div className="text-[11px] text-[var(--text-muted)]">Win Rate</div>
            <div className="num mt-0.5 text-xl font-bold text-[var(--text-primary)]">
              {overall.win_rate_pct !== null ? `${overall.win_rate_pct.toFixed(1)}%` : "—"}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">Meta rentable &gt; 53%</div>
          </div>

          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
            <div className="text-[11px] text-[var(--text-muted)]">ROI Simulado</div>
            <div
              className={cn(
                "num mt-0.5 text-xl font-bold",
                overall.roi_pct === null
                  ? "text-[var(--text-primary)]"
                  : overall.roi_pct >= 0
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400"
              )}
            >
              {overall.roi_pct !== null ? `${formatSigned(overall.roi_pct, 1)}%` : "—"}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">Sobre cuota decimal real</div>
          </div>

          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
            <div className="text-[11px] text-[var(--text-muted)]">Unidades Netas</div>
            <div
              className={cn(
                "num mt-0.5 text-xl font-bold",
                overall.net_units >= 0
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400"
              )}
            >
              {formatSigned(overall.net_units, 2)}u
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">1 unidad plana por pick</div>
          </div>

          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
            <div className="text-[11px] text-[var(--text-muted)]">Record (G - P - E)</div>
            <div className="num mt-0.5 text-xl font-bold text-[var(--text-primary)]">
              {overall.wins}-{overall.losses}-{overall.pushes}
            </div>
            <div className="text-[10px] text-[var(--text-muted)]">{overall.voids} anulados (DNP)</div>
          </div>

          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
            <div className="text-[11px] text-[var(--text-muted)]">Pendientes</div>
            <div className="num mt-0.5 text-xl font-bold text-amber-500">{overall.pending}</div>
            <div className="text-[10px] text-[var(--text-muted)]">{overall.total} picks totales</div>
          </div>
        </div>
      ) : null}

      {/* Breakdowns: By Stat & By Edge */}
      {data && (data.by_stat.length > 0 || data.by_edge_bucket.length > 0) ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5">
            <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">
              Rendimiento por estadística
            </h3>
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-[var(--border-hairline)] text-[10px] text-[var(--text-muted)]">
                  <th className="py-1.5 text-left">Stat</th>
                  <th className="py-1.5 text-right">Record</th>
                  <th className="py-1.5 text-right">Pend.</th>
                  <th className="py-1.5 text-right">Win Rate</th>
                  <th className="py-1.5 text-right">ROI</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-hairline)] num">
                {data.by_stat.map((b) => (
                  <tr key={b.label}>
                    <td className="py-1.5 font-sans font-semibold">{b.label}</td>
                    <td className="py-1.5 text-right">
                      {b.wins}-{b.losses}
                    </td>
                    <td className="py-1.5 text-right text-[var(--text-muted)]">{b.pending}</td>
                    <td className="py-1.5 text-right font-semibold">
                      {b.win_rate_pct !== null ? `${b.win_rate_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 text-right font-semibold",
                        b.roi_pct === null
                          ? "text-[var(--text-muted)]"
                          : b.roi_pct >= 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      )}
                    >
                      {b.roi_pct !== null ? `${formatSigned(b.roi_pct, 1)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5">
            <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">
              Rendimiento por tamaño de Edge
            </h3>
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-[var(--border-hairline)] text-[10px] text-[var(--text-muted)]">
                  <th className="py-1.5 text-left">Rango</th>
                  <th className="py-1.5 text-right">Record</th>
                  <th className="py-1.5 text-right">Pend.</th>
                  <th className="py-1.5 text-right">Win Rate</th>
                  <th className="py-1.5 text-right">ROI</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-hairline)] num">
                {data.by_edge_bucket.map((b) => (
                  <tr key={b.label}>
                    <td className="py-1.5 font-sans font-medium">{b.label}</td>
                    <td className="py-1.5 text-right">
                      {b.wins}-{b.losses}
                    </td>
                    <td className="py-1.5 text-right text-[var(--text-muted)]">{b.pending}</td>
                    <td className="py-1.5 text-right font-semibold">
                      {b.win_rate_pct !== null ? `${b.win_rate_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td
                      className={cn(
                        "py-1.5 text-right font-semibold",
                        b.roi_pct === null
                          ? "text-[var(--text-muted)]"
                          : b.roi_pct >= 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      )}
                    >
                      {b.roi_pct !== null ? `${formatSigned(b.roi_pct, 1)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Picks Table */}
      <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)]">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-hairline)] px-4 py-2.5">
          <h3 className="text-xs font-semibold text-[var(--text-secondary)]">
            Top oportunidades por mayor Edge ({filteredPicks.length})
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            {availableStats.length > 1 ? (
              <div className="inline-flex h-6 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[10px]">
                <button
                  type="button"
                  onClick={() => setStatFilter("ALL")}
                  className={cn(
                    "rounded px-2 font-medium cursor-pointer",
                    statFilter === "ALL"
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--text-secondary)]"
                  )}
                >
                  Todas
                </button>
                {availableStats.map((st) => (
                  <button
                    key={st}
                    type="button"
                    onClick={() => setStatFilter(st)}
                    className={cn(
                      "rounded px-1.5 font-medium uppercase cursor-pointer",
                      statFilter === st
                        ? "bg-[var(--accent)] text-white"
                        : "text-[var(--text-secondary)]"
                    )}
                  >
                    {st === "fg3m" ? "3PM" : st.toUpperCase()}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="inline-flex h-6 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[10px]">
              {(
                [
                  { id: "ALL", label: "Over + Under" },
                  { id: "OVER", label: `OVER (${overCount})` },
                  { id: "UNDER", label: `UNDER (${underCount})` },
                ] as const
              ).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setSideFilter(f.id)}
                  className={cn(
                    "rounded px-2 font-medium cursor-pointer",
                    sideFilter === f.id
                      ? f.id === "OVER"
                        ? "bg-emerald-600 text-white"
                        : f.id === "UNDER"
                        ? "bg-sky-600 text-white"
                        : "bg-[var(--accent)] text-white"
                      : "text-[var(--text-secondary)]"
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div className="inline-flex h-6 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[10px]">
              {(
                [
                  { id: "ALL", label: "Todos" },
                  { id: "PENDING", label: "Pendientes" },
                  { id: "GRADED", label: "Calificados" },
                ] as const
              ).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setStatusFilter(f.id)}
                  className={cn(
                    "rounded px-2 font-medium cursor-pointer",
                    statusFilter === f.id
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--text-secondary)]"
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredPicks.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs text-[var(--text-muted)]">
            Todavía no hay picks registrados. Hacé clic en{" "}
            <strong className="text-[var(--text-primary)]">Capturar líneas y picks</strong> para analizar la cartelera con líneas reales de mercado.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-[var(--border-hairline)] text-[10px] font-medium text-[var(--text-muted)]">
                  <th className="px-3 py-2 text-left">Fecha</th>
                  <th className="px-2 py-2 text-left">Jugador</th>
                  <th className="px-2 py-2 text-left">Partido</th>
                  <th className="px-2 py-2 text-center">Stat</th>
                  <th className="px-2 py-2 text-center">Pick</th>
                  <th className="px-2 py-2 text-right">Línea</th>
                  <th className="px-2 py-2 text-right">Proy. ML</th>
                  <th className="px-2 py-2 text-right">Edge</th>
                  <th className="px-2 py-2 text-right">Cuota</th>
                  <th className="px-2 py-2 text-right">Real</th>
                  <th className="px-3 py-2 text-center">Estado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-hairline)] num">
                {filteredPicks.map((p) => (
                  <tr key={p.pick_id} className="hover:bg-[var(--bg-subtle)]">
                    <td className="px-3 py-2 text-[var(--text-muted)]">{p.game_date}</td>
                    <td className="px-2 py-2 font-sans">
                      <div className="flex items-center gap-1.5">
                        <PlayerAvatar playerId={p.player_id} name={p.player_name} size="sm" />
                        <span className="font-medium text-[var(--text-primary)]">{p.player_name}</span>
                      </div>
                    </td>
                    <td className="px-2 py-2 text-[var(--text-secondary)]">
                      {p.team_abbreviation} vs {p.opponent_abbreviation}
                    </td>
                    <td className="px-2 py-2 text-center font-semibold uppercase">{p.stat}</td>
                    <td className="px-2 py-2 text-center">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                          p.side === "OVER"
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                            : "bg-sky-500/15 text-sky-600 dark:text-sky-400"
                        )}
                      >
                        {p.side}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-right">{p.line.toFixed(1)}</td>
                    <td className="px-2 py-2 text-right font-semibold text-amber-600 dark:text-amber-400">
                      {p.projected_value.toFixed(1)}
                    </td>
                    <td
                      className={cn(
                        "px-2 py-2 text-right font-semibold",
                        p.edge >= 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400"
                      )}
                    >
                      {formatSigned(p.edge, 1)}
                    </td>
                    <td className="px-2 py-2 text-right text-[var(--text-secondary)]">
                      {p.odds_decimal.toFixed(2)}
                    </td>
                    <td className="px-2 py-2 text-right font-semibold">
                      {p.actual_value !== null ? p.actual_value.toFixed(0) : "—"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-semibold",
                          p.status === "WIN"
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                            : p.status === "LOSS"
                            ? "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                            : p.status === "PENDING"
                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                            : "bg-[var(--bg-subtle)] text-[var(--text-muted)]"
                        )}
                      >
                        {p.status === "WIN"
                          ? "GANADO"
                          : p.status === "LOSS"
                          ? "PERDIDO"
                          : p.status === "PENDING"
                          ? "PENDIENTE"
                          : p.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
