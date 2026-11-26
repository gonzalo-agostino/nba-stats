import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  explainScenario,
  fetchPlayerChart,
  fetchPropBoard,
  snapshotDailyProps,
  type ExplainResponse,
  type PlayerChartResponse,
  type PropBoardItem,
  type PropBoardResponse,
} from "@/lib/api";
import { AiAnalysisBlock } from "@/components/AiAnalysisBlock";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { PropBarChart } from "@/components/PropBarChart";
import { cn, formatSigned } from "@/lib/utils";

const STAT_DISPLAY_ORDER = [
  "pts",
  "reb",
  "ast",
  "ra",
  "pr",
  "pa",
  "pra",
  "fg3m",
  "blk",
  "tov",
  "stl",
];

function statLabel(stat: string): string {
  if (stat === "ra") return "RA";
  if (stat === "pr") return "PR";
  if (stat === "pa") return "PA";
  if (stat === "fg3m") return "3PM";
  return stat.toUpperCase();
}

function hitRateColor(pct: number): string {
  if (pct >= 70) return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30";
  if (pct >= 50) return "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30";
  return "bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30";
}

interface PropsBoardViewProps {
  activeDate?: string;
  isActive?: boolean;
}

export function PropsBoardView({ activeDate, isActive = true }: PropsBoardViewProps) {
  const [board, setBoard] = useState<PropBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statFilter, setStatFilter] = useState<string>("ALL");
  const [sideFilter, setSideFilter] = useState<"ALL" | "OVER" | "UNDER">("ALL");
  const [gameFilter, setGameFilter] = useState<string>("ALL");
  const [sortBy, setSortBy] = useState<"conviction" | "l10" | "edge">("conviction");

  // Selected Prop & Interactive Evidence State
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const itemRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [windowSize, setWindowSize] = useState<5 | 10 | 15 | 20>(10);
  const [customLine, setCustomLine] = useState<number | null>(null);
  const [customSide, setCustomSide] = useState<"OVER" | "UNDER" | null>(null);
  const [withoutPlayerId, setWithoutPlayerId] = useState<number | null>(null);
  const [customChart, setCustomChart] = useState<PlayerChartResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);

  // AI Explanation
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResp, setAiResp] = useState<ExplainResponse | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);

  async function loadBoard(targetDate?: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchPropBoard(targetDate);
      setBoard(res);
      if (res.items.length > 0) {
        setSelectedId((prev) =>
          prev && res.items.some((i) => i.id === prev) ? prev : res.items[0].id
        );
      } else {
        setSelectedId(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error cargando tablero de props.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadBoard(activeDate);
  }, [activeDate]);

  async function handleCaptureLines(forceRefresh = false) {
    if (capturing) return;
    setCapturing(true);
    setError(null);
    try {
      await snapshotDailyProps(activeDate || undefined, forceRefresh);
      await loadBoard(activeDate);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error capturando líneas de The Odds API.");
    } finally {
      setCapturing(false);
    }
  }

  const allItems = board?.items ?? [];
  const availableStats = Array.from(new Set(allItems.map((i) => i.stat))).sort((a, b) => {
    const ia = STAT_DISPLAY_ORDER.indexOf(a);
    const ib = STAT_DISPLAY_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  const filteredItems = allItems
    .filter((i) => {
      if (statFilter !== "ALL" && i.stat !== statFilter) return false;
      if (sideFilter !== "ALL" && i.recommended_side !== sideFilter) return false;
      if (gameFilter !== "ALL" && i.game_id !== gameFilter) return false;
      return true;
    })
    .sort((a, b) => {
      if (sortBy === "l10") return b.l10.pct - a.l10.pct || b.conviction_score - a.conviction_score;
      if (sortBy === "edge") return Math.abs(b.ml_edge) - Math.abs(a.ml_edge);
      return b.conviction_score - a.conviction_score || b.l10.pct - a.l10.pct;
    });

  const selected: PropBoardItem | null =
    filteredItems.find((i) => i.id === selectedId) ?? filteredItems[0] ?? null;

  // Keyboard navigation (ArrowUp / ArrowDown) through the filtered props list
  useEffect(() => {
    if (!isActive) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (filteredItems.length === 0) return;

      e.preventDefault();
      const currIdx = selected ? filteredItems.findIndex((i) => i.id === selected.id) : -1;
      const nextIdx =
        e.key === "ArrowDown"
          ? Math.min(filteredItems.length - 1, currIdx + 1)
          : Math.max(0, currIdx - 1);
      const nextItem = filteredItems[nextIdx];
      if (nextItem) {
        setSelectedId(nextItem.id);
        itemRefs.current[nextItem.id]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filteredItems, selected, isActive]);

  // Reset interactive overrides when selecting a different prop
  useEffect(() => {
    setCustomLine(null);
    setCustomSide(null);
    setWithoutPlayerId(null);
    setCustomChart(null);
    setAiResp(null);
    setAiError(null);
  }, [selected?.id]);

  // Recalculate chart from backend whenever customLine, customSide, or withoutPlayerId changes
  useEffect(() => {
    if (!selected) return;
    const isModified =
      customLine !== null || customSide !== null || withoutPlayerId !== null;
    if (!isModified) {
      setCustomChart(null);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    void fetchPlayerChart({
      playerId: selected.player_id,
      stat: selected.stat,
      line: customLine ?? selected.line,
      side: customSide ?? selected.recommended_side,
      withoutPlayerId,
      limit: 20,
    })
      .then((res) => {
        if (!cancelled) setCustomChart(res);
      })
      .catch(() => {
        if (!cancelled) setCustomChart(null);
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, customLine, customSide, withoutPlayerId]);

  async function handleExplain(item: PropBoardItem) {
    if (aiLoading) return;
    setAiLoading(true);
    setAiError(null);
    try {
      const resp = await explainScenario({
        player_name: item.player_name,
        opponent_team: item.opponent_abbreviation,
        out_players: item.team_outs,
        opponent_out_players: [],
        is_back_to_back: item.is_back_to_back,
        rest_days: item.is_back_to_back ? 0 : 1,
        is_home: item.is_home,
      });
      setAiResp(resp);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Error explicando con IA.");
    } finally {
      setAiLoading(false);
    }
  }

  const effectiveLine = selected ? customLine ?? selected.line : 0;
  const effectiveSide = selected ? customSide ?? selected.recommended_side : "OVER";
  const effectiveL5 = customChart?.l5 ?? selected?.l5;
  const effectiveL10 = customChart?.l10 ?? selected?.l10;
  const effectiveL20 = customChart?.l20 ?? selected?.l20;
  const effectiveLocSplit = selected
    ? selected.is_home
      ? customChart?.home ?? selected.home_split
      : customChart?.away ?? selected.away_split
    : null;
  const effectiveGames = customChart?.games ?? selected?.games ?? [];
  const effectiveEdge = selected ? round1(selected.ml_projection - effectiveLine) : 0;

  return (
    <div className="space-y-3">
      {/* Top Filter & Capture Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3.5 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          {/* Stat filter */}
          <div className="inline-flex h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setStatFilter("ALL")}
              className={cn(
                "rounded px-2 font-medium cursor-pointer",
                statFilter === "ALL"
                  ? "bg-amber-500 text-slate-950 font-semibold"
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
                  "rounded px-2 font-medium cursor-pointer",
                  statFilter === st
                    ? "bg-amber-500 text-slate-950 font-semibold"
                    : "text-[var(--text-secondary)]"
                )}
              >
                {statLabel(st)}
              </button>
            ))}
          </div>

          {/* Side filter */}
          <div className="inline-flex h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[11px]">
            {(
              [
                { id: "ALL", label: "Over + Under" },
                { id: "OVER", label: "OVER" },
                { id: "UNDER", label: "UNDER" },
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

          {/* Game filter */}
          {board && board.games.length > 0 ? (
            <select
              aria-label="Filtrar por partido"
              value={gameFilter}
              onChange={(e) => setGameFilter(e.target.value)}
              className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-primary)]"
            >
              <option value="ALL">Todos los partidos ({board.games.length})</option>
              {board.games.map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {g.away_abbr} @ {g.home_abbr}
                </option>
              ))}
            </select>
          ) : null}

          {/* Sort selector */}
          <select
            aria-label="Ordenar props"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "conviction" | "l10" | "edge")}
            className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-primary)]"
          >
            <option value="conviction">Orden: Mayor Convicción</option>
            <option value="l10">Orden: Mejor Hit Rate L10</option>
            <option value="edge">Orden: Mayor Edge ML</option>
          </select>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={capturing}
            onClick={() => void handleCaptureLines(allItems.length > 0)}
            title="Traer líneas reales y Spread/Total desde The Odds API"
            className="inline-flex h-7 items-center gap-1.5 rounded bg-[var(--accent)] px-2.5 text-[11px] font-medium text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
          >
            {capturing ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <TrendingUp className="h-3 w-3" />
            )}
            {allItems.length > 0 ? "Refrescar líneas" : "Capturar líneas"}
          </button>
          <button
            type="button"
            onClick={() => void loadBoard(activeDate)}
            aria-label="Recargar tablero"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-500">
          {error}
        </div>
      ) : null}

      {/* Loading / Empty State */}
      {loading && !board ? (
        <div className="flex items-center justify-center gap-2 py-16 text-xs text-[var(--text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
          Cargando tablero visual de props...
        </div>
      ) : allItems.length === 0 ? (
        <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-6 py-12 text-center">
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            No hay líneas de mercado cacheadas para {board?.game_date ?? activeDate ?? "hoy"}.
          </p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Hacé clic en el botón para descargar las líneas reales (PTS, REB, AST, PRA, P+R, P+A, R+A) y Spread/Total de Vegas.
          </p>
          <button
            type="button"
            disabled={capturing}
            onClick={() => void handleCaptureLines(false)}
            className="mt-4 inline-flex h-8 items-center gap-1.5 rounded bg-amber-500 px-4 text-xs font-semibold text-slate-950 hover:opacity-90 disabled:opacity-50 cursor-pointer"
          >
            {capturing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TrendingUp className="h-3.5 w-3.5" />}
            Capturar líneas de {board?.game_date ?? "hoy"}
          </button>
        </div>
      ) : (
        /* Main Outlier-Style Split Screen */
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[370px_1fr]">
          {/* LEFT: Compact Props Feed */}
          <div className="max-h-[76vh] overflow-y-auto rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] divide-y divide-[var(--border-hairline)]">
            {filteredItems.length === 0 ? (
              <div className="p-6 text-center text-xs text-[var(--text-muted)]">
                Ningún prop coincide con estos filtros.
              </div>
            ) : (
              filteredItems.map((item) => {
                const isSelected = selected?.id === item.id;
                return (
                  <button
                    key={item.id}
                    ref={(el) => {
                      itemRefs.current[item.id] = el;
                    }}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-colors cursor-pointer",
                      isSelected
                        ? "bg-amber-500/10 border-l-2 border-l-amber-500"
                        : "hover:bg-[var(--bg-subtle)]"
                    )}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <PlayerAvatar playerId={item.player_id} name={item.player_name} size="md" />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-xs font-semibold text-[var(--text-primary)]">
                            {item.player_name}
                          </span>
                          {item.injury_status === "DTD" ? (
                            <span className="rounded bg-amber-500/15 px-1 py-px text-[9px] font-semibold text-amber-500">
                              DTD
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5 text-[11px]">
                          <span
                            className={cn(
                              "font-semibold",
                              item.recommended_side === "OVER"
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-sky-600 dark:text-sky-400"
                            )}
                          >
                            {item.recommended_side === "OVER" ? "O" : "U"} {item.line.toFixed(1)}{" "}
                            {statLabel(item.stat)}
                          </span>
                          <span className="text-[var(--text-muted)]">·</span>
                          <span className="num text-[10px] text-[var(--text-muted)]">
                            {item.team_abbreviation} {item.is_home ? "vs" : "@"}{" "}
                            {item.opponent_abbreviation}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <div className="flex items-center gap-1">
                        {item.ml_agrees ? (
                          <span
                            title={`LightGBM coincide: proyecta ${item.ml_projection.toFixed(1)}`}
                            className="inline-flex items-center gap-0.5 rounded bg-emerald-500/15 px-1 py-px text-[9px] font-semibold text-emerald-500"
                          >
                            <CheckCircle2 className="h-2.5 w-2.5" />
                            ML
                          </span>
                        ) : null}
                        <span
                          className={cn(
                            "num rounded border px-1.5 py-0.5 text-[11px] font-bold",
                            hitRateColor(item.l10.pct)
                          )}
                        >
                          {item.l10.hits}/{item.l10.total}
                        </span>
                      </div>
                      <span className="num text-[10px] text-[var(--text-muted)]">
                        Cuota {item.odds_decimal.toFixed(2)} · Proy {item.ml_projection.toFixed(1)}
                      </span>
                    </div>
                  </button>
                );
              })
            )}
          </div>

          {/* RIGHT: Visual Evidence Panel */}
          {selected ? (
            <div className="space-y-3.5 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-4">
              {/* Header: Avatar + Matchup + Vegas Spread/Total + Defense */}
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--border-hairline)] pb-3">
                <div className="flex items-center gap-3">
                  <PlayerAvatar playerId={selected.player_id} name={selected.player_name} size="lg" />
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-bold text-[var(--text-primary)]">
                        {selected.player_name}
                      </h2>
                      <span className="num rounded bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--text-secondary)]">
                        {selected.team_abbreviation} · {selected.pos_group}
                      </span>
                      {selected.is_back_to_back ? (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-500">
                          B2B
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                      <span>
                        {selected.is_home ? "Local vs" : "Visitante @"}{" "}
                        <strong className="text-[var(--text-primary)]">
                          {selected.opponent_abbreviation}
                        </strong>
                      </span>
                      {selected.opp_def_rank ? (
                        <span className="num text-[11px] text-[var(--text-muted)]">
                          · Defensa rival #{selected.opp_def_rank} (DefRtg{" "}
                          {selected.opp_def_rating_10?.toFixed(1)})
                        </span>
                      ) : null}
                      {selected.opp_pace_rank ? (
                        <span className="num text-[11px] text-[var(--text-muted)]">
                          · Ritmo #{selected.opp_pace_rank}
                        </span>
                      ) : null}
                    </div>

                    {/* Vegas Spread / Total + Blowout warning */}
                    {selected.team_spread !== null || selected.game_total !== null ? (
                      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="num rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 py-0.5 text-[var(--text-secondary)]">
                          Vegas:{" "}
                          {selected.team_spread !== null
                            ? `Spread ${formatSigned(selected.team_spread, 1)}`
                            : ""}
                          {selected.team_spread !== null && selected.game_total !== null ? " · " : ""}
                          {selected.game_total !== null ? `Total ${selected.game_total.toFixed(1)}` : ""}
                        </span>
                        {selected.blowout_risk ? (
                          <span className="inline-flex items-center gap-1 rounded bg-rose-500/15 px-2 py-0.5 font-semibold text-rose-500">
                            <AlertTriangle className="h-3 w-3" />
                            Riesgo de paliza (titulares pueden descansar 4Q)
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>

                {/* Interactive Line Adjuster (Match Betano exact line!) */}
                <div className="flex flex-col items-end gap-1.5">
                  <div className="flex items-center gap-1.5">
                    <div className="inline-flex h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[11px]">
                      {(["OVER", "UNDER"] as const).map((s) => (
                        <button
                          key={s}
                          type="button"
                          onClick={() => setCustomSide(s)}
                          className={cn(
                            "rounded px-2 font-semibold cursor-pointer",
                            effectiveSide === s
                              ? s === "OVER"
                                ? "bg-emerald-600 text-white"
                                : "bg-sky-600 text-white"
                              : "text-[var(--text-secondary)]"
                          )}
                        >
                          {s}
                        </button>
                      ))}
                    </div>

                    <div className="inline-flex h-7 items-center rounded border border-amber-500/50 bg-[var(--bg-subtle)]">
                      <button
                        type="button"
                        aria-label="Bajar línea 0.5"
                        onClick={() => setCustomLine(Math.max(0.5, effectiveLine - 0.5))}
                        className="flex h-full w-6 items-center justify-center text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
                      >
                        <Minus className="h-3 w-3" />
                      </button>
                      <span className="num px-2 text-xs font-bold text-amber-500">
                        {effectiveLine.toFixed(1)} {statLabel(selected.stat)}
                      </span>
                      <button
                        type="button"
                        aria-label="Subir línea 0.5"
                        onClick={() => setCustomLine(effectiveLine + 0.5)}
                        className="flex h-full w-6 items-center justify-center text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                  <div className="num text-[10px] text-[var(--text-muted)]">
                    Ajustá ±0.5 para igualar la línea exacta de Betano · Casa: {selected.bookmaker}
                  </div>
                </div>
              </div>

              {/* Hit Rate & ML Confirmation Strip */}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {[
                  { label: "Últimos 5", data: effectiveL5 },
                  { label: "Últimos 10", data: effectiveL10 },
                  { label: "Últimos 20", data: effectiveL20 },
                  {
                    label: selected.is_home ? "De Local" : "De Visitante",
                    data: effectiveLocSplit,
                  },
                ].map((card) => (
                  <div
                    key={card.label}
                    className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-3 py-2"
                  >
                    <div className="text-[10px] text-[var(--text-muted)]">{card.label}</div>
                    <div className="mt-0.5 flex items-baseline justify-between">
                      <span className="num text-base font-bold text-[var(--text-primary)]">
                        {card.data ? `${card.data.hits}/${card.data.total}` : "—"}
                      </span>
                      <span
                        className={cn(
                          "num text-xs font-semibold",
                          (card.data?.pct ?? 0) >= 70
                            ? "text-emerald-500"
                            : (card.data?.pct ?? 0) >= 50
                            ? "text-amber-500"
                            : "text-rose-500"
                        )}
                      >
                        {card.data ? `${card.data.pct.toFixed(0)}%` : ""}
                      </span>
                    </div>
                    <div className="num text-[10px] text-[var(--text-muted)]">
                      Prom: {card.data ? card.data.avg.toFixed(1) : "—"} ·{" "}
                      {card.data ? `${card.data.avg_min.toFixed(0)}m` : ""}
                    </div>
                  </div>
                ))}

                {/* ML Projection Card */}
                <div className="rounded-md border border-amber-500/35 bg-amber-500/8 px-3 py-2">
                  <div className="text-[10px] text-amber-600 dark:text-amber-400 font-semibold">
                    Proyección ML
                  </div>
                  <div className="mt-0.5 flex items-baseline justify-between">
                    <span className="num text-base font-bold text-amber-500">
                      {selected.ml_projection.toFixed(1)}
                    </span>
                    <span
                      className={cn(
                        "num text-xs font-semibold",
                        effectiveEdge >= 0 ? "text-emerald-500" : "text-rose-500"
                      )}
                    >
                      {formatSigned(effectiveEdge, 1)}
                    </span>
                  </div>
                  <div className="text-[10px] text-[var(--text-muted)]">
                    Convicción: <strong>{selected.conviction_score}/100</strong>
                  </div>
                </div>
              </div>

              {/* Window Selector + Visual WOWY Filter ("Sin compañero") */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-medium text-[var(--text-muted)]">
                    Filtrar partidos reales:
                  </span>
                  <button
                    type="button"
                    onClick={() => setWithoutPlayerId(null)}
                    className={cn(
                      "h-6 rounded border px-2 text-[11px] font-medium cursor-pointer",
                      withoutPlayerId === null
                        ? "border-amber-500 bg-amber-500/15 text-amber-500"
                        : "border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)]"
                    )}
                  >
                    Todos
                  </button>
                  {selected.teammates.slice(0, 5).map((tm) => (
                    <button
                      key={tm.player_id}
                      type="button"
                      onClick={() =>
                        setWithoutPlayerId((curr) =>
                          curr === tm.player_id ? null : tm.player_id
                        )
                      }
                      className={cn(
                        "h-6 rounded border px-2 text-[11px] font-medium cursor-pointer",
                        withoutPlayerId === tm.player_id
                          ? "border-rose-500 bg-rose-500/15 text-rose-500"
                          : tm.is_out
                          ? "border-rose-500/40 bg-rose-500/5 text-rose-400"
                          : "border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                      )}
                    >
                      Sin {tm.player_name.split(" ").slice(-1)[0]}
                      {tm.is_out ? " (OUT)" : ""}
                    </button>
                  ))}
                  {chartLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" /> : null}
                </div>

                <div className="inline-flex h-6 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-0.5 text-[10px]">
                  {([5, 10, 15, 20] as const).map((w) => (
                    <button
                      key={w}
                      type="button"
                      onClick={() => setWindowSize(w)}
                      className={cn(
                        "rounded px-2 font-semibold cursor-pointer",
                        windowSize === w
                          ? "bg-[var(--accent)] text-white"
                          : "text-[var(--text-secondary)]"
                      )}
                    >
                      L{w}
                    </button>
                  ))}
                </div>
              </div>

              {/* HERO: Interactive Bar Chart vs Market Line */}
              <PropBarChart
                games={effectiveGames}
                line={effectiveLine}
                side={effectiveSide}
                windowSize={windowSize}
              />

              {/* Bottom On-Demand AI Explanation */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-hairline)] pt-2.5">
                <span className="text-[11px] text-[var(--text-muted)]">
                  {selected.team_outs.length > 0
                    ? `Bajas confirmadas en ${selected.team_abbreviation}: ${selected.team_outs.join(", ")}`
                    : `Sin bajas confirmadas en ${selected.team_abbreviation}`}
                </span>
                <button
                  type="button"
                  disabled={aiLoading}
                  onClick={() => void handleExplain(selected)}
                  className="inline-flex h-7 items-center gap-1.5 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2.5 text-xs font-medium text-[var(--text-primary)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-50 cursor-pointer"
                >
                  {aiLoading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5 text-[var(--accent)]" />
                  )}
                  Explicar este spot con IA
                </button>
              </div>

              {aiError ? <div className="text-xs text-rose-500">{aiError}</div> : null}
              {aiResp ? (
                <AiAnalysisBlock
                  text={aiResp.analysis}
                  verified={aiResp.verification_passed}
                  compact
                />
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function round1(val: number): number {
  return Math.round(val * 10) / 10;
}
