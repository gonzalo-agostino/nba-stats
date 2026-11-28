import { useEffect, useState } from "react";
import {
  AlertTriangle,
  BookmarkCheck,
  Check,
  Copy,
  Layers,
  Loader2,
  Lock,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  buildParlay,
  saveTrackedParlay,
  type ParlayBuildResponse,
  type ParlaySuggestion,
  type PropBoardItem,
} from "@/lib/api";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { cn, formatSigned } from "@/lib/utils";

interface ParlayBuilderModalProps {
  gameDate?: string;
  boardItems: PropBoardItem[];
  lockedPickIds: string[];
  onToggleLock: (pickId: string) => void;
  onClearLocks: () => void;
  onClose: () => void;
}

const ODDS_PRESETS = [
  { label: "x2.5", value: 2.5 },
  { label: "x4.0", value: 4.0 },
  { label: "x6.0", value: 6.0 },
  { label: "x10", value: 10.0 },
];

const TOP_ROI_STATS = ["pts", "ast", "pr"];

function formatStatLabel(stat: string): string {
  if (stat === "fg3m") return "3PM";
  return stat.toUpperCase();
}

export function ParlayBuilderModal({
  gameDate,
  boardItems,
  lockedPickIds,
  onToggleLock,
  onClearLocks,
  onClose,
}: ParlayBuilderModalProps) {
  const [targetOdds, setTargetOdds] = useState<number>(4.0);
  const [targetInput, setTargetInput] = useState<string>("4.0");
  const [maxLegs, setMaxLegs] = useState<number>(4);
  const [topRoiOnly, setTopRoiOnly] = useState<boolean>(false);
  const [requireMlAgrees, setRequireMlAgrees] = useState<boolean>(true);
  const [excludeBlowout, setExcludeBlowout] = useState<boolean>(true);
  const [excludeDtd, setExcludeDtd] = useState<boolean>(true);
  const [onePerPlayer, setOnePerPlayer] = useState<boolean>(false);
  const [onePerGame, setOnePerGame] = useState<boolean>(false);
  const [excludedIds, setExcludedIds] = useState<string[]>([]);

  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ParlayBuildResponse | null>(null);
  const [activeIdx, setActiveIdx] = useState<number>(0);

  const [stakeAmount, setStakeAmount] = useState<string>("10000");
  const [copied, setCopied] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);
  const [savedBanner, setSavedBanner] = useState<string | null>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function runBuild(customExcluded?: string[]) {
    setLoading(true);
    setError(null);
    setSavedBanner(null);
    const parsedTarget = Math.max(1.2, Math.min(100, Number.parseFloat(targetInput) || targetOdds));
    setTargetOdds(parsedTarget);
    try {
      const resp = await buildParlay({
        game_date: gameDate,
        target_odds: parsedTarget,
        locked_pick_ids: lockedPickIds,
        excluded_pick_ids: customExcluded ?? excludedIds,
        min_legs: Math.max(2, lockedPickIds.length),
        max_legs: Math.max(maxLegs, lockedPickIds.length),
        allowed_stats: topRoiOnly ? TOP_ROI_STATS : null,
        require_ml_agrees: requireMlAgrees,
        exclude_blowout: excludeBlowout,
        exclude_dtd: excludeDtd,
        one_per_player: onePerPlayer,
        one_per_game: onePerGame,
        max_results: 3,
      });
      setResult(resp);
      setActiveIdx(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error armando la combinada.");
    } finally {
      setLoading(false);
    }
  }

  // Auto-build when modal opens or core settings change
  useEffect(() => {
    void runBuild();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    lockedPickIds.join(","),
    targetOdds,
    maxLegs,
    topRoiOnly,
    requireMlAgrees,
    excludeBlowout,
    excludeDtd,
    onePerPlayer,
    onePerGame,
  ]);

  const activeParlay: ParlaySuggestion | null = result?.parlays?.[activeIdx] ?? null;
  const stakeNum = Number.parseFloat(stakeAmount) || 0;
  const payout = activeParlay ? Math.round(stakeNum * activeParlay.combined_odds) : 0;

  const lockedItems = lockedPickIds
    .map((id) => boardItems.find((i) => i.id === id))
    .filter((i): i is PropBoardItem => Boolean(i));

  function handleRerollLeg(pickId: string) {
    const nextExcluded = [...excludedIds, pickId];
    setExcludedIds(nextExcluded);
    void runBuild(nextExcluded);
  }

  async function handleCopySlip() {
    if (!activeParlay) return;
    const lines = [
      `🏀 Combinada NBA (${activeParlay.strategy}) — Cuota x${activeParlay.combined_odds.toFixed(2)}`,
      ...activeParlay.legs.map(
        (lg) =>
          `• ${lg.player_name} (${lg.team_abbreviation} vs ${lg.opponent_abbreviation}): ${lg.side} ${lg.line.toFixed(
            1
          )} ${formatStatLabel(lg.stat)} @${lg.odds_decimal.toFixed(2)} (ML ${
            lg.ml_projection?.toFixed(1) ?? "-"
          } · L10 ${lg.l10_pct?.toFixed(0) ?? "-"}%)`
      ),
      `Prob. estimada: ${activeParlay.estimated_prob_pct.toFixed(1)}% · EV: ${formatSigned(
        activeParlay.ev_pct,
        1
      )}%`,
    ];
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore clipboard errors
    }
  }

  async function handleSaveParlay() {
    if (!activeParlay || saving) return;
    setSaving(true);
    setError(null);
    setSavedBanner(null);
    try {
      await saveTrackedParlay({
        game_date: result?.game_date || gameDate || new Date().toISOString().slice(0, 10),
        label: `${activeParlay.strategy} (${activeParlay.num_legs} patas)`,
        target_odds: activeParlay.target_odds,
        combined_odds: activeParlay.combined_odds,
        estimated_prob: activeParlay.estimated_prob_pct,
        stake_units: 1.0,
        legs: activeParlay.legs,
      });
      setSavedBanner("Combinada guardada en tu historial de Win Rate (se calificará automáticamente).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error guardando la combinada.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/65 p-0 sm:items-center sm:p-5 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl border border-[var(--border-hairline)] bg-[var(--bg-canvas)] text-[var(--text-primary)] shadow-2xl sm:max-h-[90vh] sm:rounded-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-3">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-amber-500" />
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)]">
                Constructor de Combinada Recomendada
              </h2>
              <p className="text-[11px] text-[var(--text-muted)]">
                Elegí tu cuota objetivo y fijá picks si querés; el motor completa el resto.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 space-y-3.5 overflow-y-auto p-4">
          {/* Target Odds & Legs Controls */}
          <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3 space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-semibold text-[var(--text-secondary)]">
                  Cuota objetivo:
                </span>
                {ODDS_PRESETS.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => {
                      setTargetOdds(p.value);
                      setTargetInput(String(p.value));
                    }}
                    className={cn(
                      "num h-7 rounded px-2 text-xs font-semibold cursor-pointer transition-colors",
                      Math.abs(targetOdds - p.value) < 0.05
                        ? "bg-amber-500 text-slate-950"
                        : "border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-secondary)]"
                    )}
                  >
                    {p.label}
                  </button>
                ))}
                <div className="inline-flex h-7 items-center rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2">
                  <span className="text-[11px] text-[var(--text-muted)] mr-1">x</span>
                  <input
                    type="number"
                    step="0.5"
                    min="1.2"
                    max="100"
                    value={targetInput}
                    onChange={(e) => setTargetInput(e.target.value)}
                    onBlur={() => {
                      const v = Number.parseFloat(targetInput);
                      if (!Number.isNaN(v) && v >= 1.2) setTargetOdds(v);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        const v = Number.parseFloat(targetInput);
                        if (!Number.isNaN(v) && v >= 1.2) setTargetOdds(v);
                      }
                    }}
                    className="num w-14 bg-transparent text-xs font-semibold text-[var(--text-primary)] focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-[var(--text-muted)]">Máx. patas:</span>
                <select
                  value={maxLegs}
                  onChange={(e) => setMaxLegs(Number(e.target.value))}
                  className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-xs font-semibold text-[var(--text-primary)]"
                >
                  {[2, 3, 4, 5, 6, 8].map((n) => (
                    <option key={n} value={n}>
                      {n} patas
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Quality & Diversification Toggles */}
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {(
                [
                  {
                    label: "Top ROI (PTS/AST/PR)",
                    active: topRoiOnly,
                    toggle: () => setTopRoiOnly((v) => !v),
                  },
                  {
                    label: "Solo ML ✓",
                    active: requireMlAgrees,
                    toggle: () => setRequireMlAgrees((v) => !v),
                  },
                  {
                    label: "Sin Paliza",
                    active: excludeBlowout,
                    toggle: () => setExcludeBlowout((v) => !v),
                  },
                  {
                    label: "Sin DTD",
                    active: excludeDtd,
                    toggle: () => setExcludeDtd((v) => !v),
                  },
                  {
                    label: "1 por jugador",
                    active: onePerPlayer,
                    toggle: () => setOnePerPlayer((v) => !v),
                  },
                  {
                    label: "1 por partido",
                    active: onePerGame,
                    toggle: () => setOnePerGame((v) => !v),
                  },
                ] as const
              ).map((t) => (
                <button
                  key={t.label}
                  type="button"
                  onClick={t.toggle}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer",
                    t.active
                      ? "border-amber-500/50 bg-amber-500/15 text-amber-600 dark:text-amber-400"
                      : "border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-[var(--text-muted)]"
                  )}
                >
                  {t.label}
                </button>
              ))}
              {excludedIds.length > 0 ? (
                <button
                  type="button"
                  onClick={() => {
                    setExcludedIds([]);
                    void runBuild([]);
                  }}
                  className="ml-auto text-[11px] font-medium text-amber-500 hover:underline cursor-pointer"
                >
                  Restaurar descartados ({excludedIds.length})
                </button>
              ) : null}
            </div>

            {/* Locked picks summary */}
            {lockedItems.length > 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-hairline)] pt-2 text-[11px]">
                <span className="inline-flex items-center gap-1 font-medium text-amber-600 dark:text-amber-400">
                  <Lock className="h-3 w-3" />
                  {lockedItems.length} {lockedItems.length === 1 ? "pick fijado" : "picks fijados"} por vos
                </span>
                <button
                  type="button"
                  onClick={onClearLocks}
                  className="inline-flex items-center gap-1 text-[var(--text-muted)] hover:text-rose-500 cursor-pointer"
                >
                  <Trash2 className="h-3 w-3" />
                  Limpiar fijados
                </button>
              </div>
            ) : null}
          </div>

          {error ? (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-500">
              {error}
            </div>
          ) : null}

          {savedBanner ? (
            <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400">
              {savedBanner}
            </div>
          ) : null}

          {result?.note ? (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
              {result.note}
            </div>
          ) : null}

          {/* Results Area */}
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-xs text-[var(--text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
              Calculando mejores combinadas...
            </div>
          ) : !activeParlay ? (
            <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-10 text-center text-xs text-[var(--text-muted)]">
              No se encontraron suficientes picks con estos filtros. Probá desactivar{" "}
              <strong>Solo ML ✓</strong> o subir el máximo de patas.
            </div>
          ) : (
            <div className="space-y-3">
              {/* Strategy Selector Tabs */}
              {result && result.parlays.length > 1 ? (
                <div className="grid grid-cols-3 gap-1.5">
                  {result.parlays.map((p, idx) => (
                    <button
                      key={p.strategy}
                      type="button"
                      onClick={() => {
                        setActiveIdx(idx);
                        setSavedBanner(null);
                      }}
                      className={cn(
                        "rounded-md border px-2.5 py-2 text-left transition-colors cursor-pointer",
                        activeIdx === idx
                          ? "border-amber-500 bg-amber-500/10"
                          : "border-[var(--border-hairline)] bg-[var(--bg-panel)] hover:bg-[var(--bg-subtle)]"
                      )}
                    >
                      <div className="truncate text-[11px] font-semibold text-[var(--text-primary)]">
                        {p.strategy}
                      </div>
                      <div className="mt-0.5 flex items-center justify-between text-[11px] num">
                        <span className="font-bold text-amber-500">x{p.combined_odds.toFixed(2)}</span>
                        <span className="text-[var(--text-muted)]">{p.estimated_prob_pct.toFixed(0)}%</span>
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}

              {/* Active Parlay Summary Strip */}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 py-2">
                  <div className="text-[10px] text-[var(--text-muted)]">Cuota Total</div>
                  <div className="num text-lg font-bold text-amber-500">
                    x{activeParlay.combined_odds.toFixed(2)}
                  </div>
                </div>
                <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 py-2">
                  <div className="text-[10px] text-[var(--text-muted)]">Prob. Estimada</div>
                  <div className="num text-lg font-bold text-[var(--text-primary)]">
                    {activeParlay.estimated_prob_pct.toFixed(1)}%
                  </div>
                </div>
                <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 py-2">
                  <div className="text-[10px] text-[var(--text-muted)]">EV Estimado</div>
                  <div
                    className={cn(
                      "num text-lg font-bold",
                      activeParlay.ev_pct >= 0
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-rose-500"
                    )}
                  >
                    {formatSigned(activeParlay.ev_pct, 1)}%
                  </div>
                </div>
                <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 py-2">
                  <div className="text-[10px] text-[var(--text-muted)]">Simulador apuesta</div>
                  <div className="mt-0.5 flex items-center gap-1 num text-xs">
                    <span className="text-[var(--text-muted)]">$</span>
                    <input
                      type="number"
                      value={stakeAmount}
                      onChange={(e) => setStakeAmount(e.target.value)}
                      className="w-16 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-1 py-0.5 text-xs font-semibold text-[var(--text-primary)] focus:outline-none"
                    />
                    <span className="font-bold text-emerald-500">→ ${payout.toLocaleString("es-AR")}</span>
                  </div>
                </div>
              </div>

              {/* Warnings if any */}
              {activeParlay.warnings.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  <span>{activeParlay.warnings.join(" · ")}</span>
                </div>
              ) : null}

              {/* Legs List */}
              <div className="divide-y divide-[var(--border-hairline)] rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)]">
                {activeParlay.legs.map((lg) => (
                  <div
                    key={lg.pick_id}
                    className="flex items-center justify-between gap-2 px-3 py-2.5"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <PlayerAvatar playerId={lg.player_id} name={lg.player_name} size="sm" />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate text-xs font-semibold text-[var(--text-primary)]">
                            {lg.player_name}
                          </span>
                          <span className="num text-[10px] text-[var(--text-muted)]">
                            {lg.team_abbreviation} vs {lg.opponent_abbreviation}
                          </span>
                          {lg.locked ? (
                            <span className="inline-flex items-center gap-0.5 rounded bg-amber-500/15 px-1.5 py-px text-[9px] font-semibold text-amber-600 dark:text-amber-400">
                              <Lock className="h-2.5 w-2.5" />
                              Tuyo
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-0.5 rounded bg-emerald-500/15 px-1.5 py-px text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
                              <Sparkles className="h-2.5 w-2.5" />
                              Sugerido
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] num">
                          <span
                            className={cn(
                              "font-bold",
                              lg.side === "OVER"
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-sky-600 dark:text-sky-400"
                            )}
                          >
                            {lg.side} {lg.line.toFixed(1)} {formatStatLabel(lg.stat)}
                          </span>
                          <span className="text-[var(--text-muted)]">·</span>
                          <span className="font-semibold text-[var(--text-primary)]">
                            @{lg.odds_decimal.toFixed(2)}
                          </span>
                          <span className="text-[var(--text-muted)]">·</span>
                          <span className="text-amber-600 dark:text-amber-400">
                            ML {lg.ml_projection?.toFixed(1)}
                          </span>
                          <span className="text-[var(--text-muted)]">
                            L10 {lg.l10_hits}/{lg.l10_total}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {lg.locked ? (
                        <button
                          type="button"
                          onClick={() => onToggleLock(lg.pick_id)}
                          title="Quitar pick fijado"
                          className="inline-flex h-7 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] text-[var(--text-muted)] hover:text-rose-500 cursor-pointer"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleRerollLeg(lg.pick_id)}
                          title="Cambiar esta pata por otra opción"
                          className="inline-flex h-7 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-secondary)] hover:border-amber-500 hover:text-[var(--text-primary)] cursor-pointer"
                        >
                          <RefreshCw className="h-3 w-3" />
                          <span className="hidden sm:inline">Cambiar</span>
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <button
            type="button"
            disabled={!activeParlay}
            onClick={() => void handleCopySlip()}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-3 text-xs font-medium text-[var(--text-primary)] hover:border-amber-500 disabled:opacity-40 cursor-pointer"
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copiado!" : "Copiar boleto"}
          </button>

          <button
            type="button"
            disabled={!activeParlay || saving}
            onClick={() => void handleSaveParlay()}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-amber-500 px-4 text-xs font-bold text-slate-950 hover:opacity-90 disabled:opacity-40 cursor-pointer"
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <BookmarkCheck className="h-4 w-4" />
            )}
            Guardar en Historial
          </button>
        </div>
      </div>
    </div>
  );
}
