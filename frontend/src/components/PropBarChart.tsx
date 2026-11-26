import { useState } from "react";
import type { PropGamePoint } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PropBarChartProps {
  games: PropGamePoint[];
  line: number;
  side: "OVER" | "UNDER";
  windowSize: 5 | 10 | 15 | 20;
}

export function PropBarChart({ games, line, side, windowSize }: PropBarChartProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  // Take the most recent `windowSize` games (games array is chronological oldest -> newest)
  const visible = games.slice(-windowSize);
  if (visible.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] text-xs text-[var(--text-muted)]">
        Sin partidos históricos disponibles para este filtro.
      </div>
    );
  }

  const maxVal = Math.max(line * 1.25, ...visible.map((g) => g.value), 5);
  const lineBottomPct = Math.min(92, Math.max(8, (line / maxVal) * 100));
  const hovered = hoveredIdx !== null ? visible[hoveredIdx] : null;

  return (
    <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] p-3.5">
      {/* Top micro-legend / hovered game detail */}
      <div className="mb-2 flex h-5 items-center justify-between text-[11px]">
        {hovered ? (
          <div className="flex items-center gap-2 text-[var(--text-primary)]">
            <span className="num text-[var(--text-muted)]">{hovered.game_date}</span>
            <span className="font-semibold">
              {hovered.is_home ? "vs" : "@"} {hovered.opp_abbr}
            </span>
            {hovered.wl ? (
              <span
                className={cn(
                  "rounded px-1 py-px text-[10px] font-semibold",
                  hovered.wl === "W"
                    ? "bg-emerald-500/15 text-emerald-500"
                    : "bg-rose-500/15 text-rose-500"
                )}
              >
                {hovered.wl}
              </span>
            ) : null}
            <span className="num text-[var(--text-secondary)]">{hovered.min.toFixed(0)} min</span>
            <span
              className={cn(
                "num font-bold",
                hovered.hit ? "text-emerald-500" : "text-rose-500"
              )}
            >
              Valor: {hovered.value.toFixed(0)} ({hovered.hit ? "HIT" : "MISS"})
            </span>
          </div>
        ) : (
          <div className="text-[var(--text-muted)]">
            Últimos {visible.length} partidos ·{" "}
            <span className="text-emerald-500 font-medium">Verde = cumple {side}</span> ·{" "}
            <span className="text-rose-500 font-medium">Rojo = no cumple</span>
          </div>
        )}
        <span className="num text-[11px] font-semibold text-amber-500">Línea {line.toFixed(1)}</span>
      </div>

      {/* Bar chart area */}
      <div className="relative mt-4 h-44 w-full">
        {/* Dashed market line */}
        <div
          style={{ bottom: `${lineBottomPct}%` }}
          className="pointer-events-none absolute left-0 right-0 z-10 border-t-2 border-dashed border-amber-400/80"
        />

        {/* Bars */}
        <div className="flex h-full items-end gap-1.5 sm:gap-2">
          {visible.map((g, idx) => {
            const heightPct = Math.max(4, Math.min(100, (g.value / maxVal) * 100));
            const isHovered = hoveredIdx === idx;
            return (
              <div
                key={`${g.game_id}-${idx}`}
                onMouseEnter={() => setHoveredIdx(idx)}
                onMouseLeave={() => setHoveredIdx(null)}
                className="flex h-full flex-1 flex-col items-center justify-end cursor-pointer"
              >
                <span
                  className={cn(
                    "num mb-1 text-[10px] font-bold transition-colors",
                    g.hit
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-rose-600 dark:text-rose-400"
                  )}
                >
                  {Number.isInteger(g.value) ? g.value : g.value.toFixed(1)}
                </span>
                <div
                  style={{ height: `${heightPct}%` }}
                  className={cn(
                    "w-full max-w-[34px] rounded-t transition-all",
                    g.hit
                      ? isHovered
                        ? "bg-emerald-400"
                        : "bg-emerald-500/85"
                      : isHovered
                      ? "bg-rose-400"
                      : "bg-rose-500/80"
                  )}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* X-axis labels: Opponent + Date */}
      <div className="mt-1.5 flex gap-1.5 sm:gap-2 border-t border-[var(--border-hairline)] pt-1.5">
        {visible.map((g, idx) => (
          <div
            key={`lbl-${g.game_id}-${idx}`}
            className="flex-1 text-center leading-tight overflow-hidden"
          >
            <div className="truncate text-[9px] font-semibold text-[var(--text-secondary)]">
              {g.is_home ? "vs" : "@"}
              {g.opp_abbr}
            </div>
            <div className="num text-[8px] text-[var(--text-muted)]">
              {g.game_date.slice(5)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
