import type { ReactNode } from "react";
import { CheckCircle2, Sparkles } from "lucide-react";
import type { SimulationResult } from "@/lib/api";
import { cn, formatSigned } from "@/lib/utils";

interface AiAnalysisBlockProps {
  text: string;
  verified?: boolean;
  mlResult?: SimulationResult | null;
  compact?: boolean;
}

function renderInlineFormatted(line: string): ReactNode[] {
  // Parse **bold**, `code`, and signed deltas like (+1.23) or (-2.36)
  const tokenRegex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  const parts = line.split(tokenRegex);

  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={idx} className="font-semibold text-[var(--text-primary)]">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={idx}
          className="num rounded bg-[var(--bg-canvas)] px-1 py-px text-[11px] font-medium text-amber-500"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={idx}>{part}</span>;
  });
}

function parseBullets(raw: string): { title?: string; body: string }[] {
  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith("|") && !l.startsWith("###"));

  const items: { title?: string; body: string }[] = [];
  for (const line of lines) {
    const cleaned = line.replace(/^[-*•]\s+/, "");
    // Detect leading "**Title:** rest of paragraph"
    const match = cleaned.match(/^\*\*([^*]+?)(?::)?\*\*:?\s*(.*)$/);
    if (match && match[2]) {
      items.push({ title: match[1].replace(/:$/, "").trim(), body: match[2].trim() });
    } else {
      items.push({ body: cleaned });
    }
  }
  return items;
}

export function AiAnalysisBlock({
  text,
  verified,
  mlResult,
  compact = false,
}: AiAnalysisBlockProps) {
  const bullets = parseBullets(text);

  return (
    <div className={cn("space-y-2.5", compact ? "text-[11px]" : "text-xs")}>
      {/* Optional ML Projection Summary Strip (e.g. from top LangGraph query bar) */}
      {mlResult ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-3 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-amber-500" />
            <span className="font-bold text-[var(--text-primary)]">{mlResult.player_name}</span>
            <span className="num text-[11px] text-[var(--text-muted)]">
              ({mlResult.team_abbreviation} {mlResult.is_home ? "vs" : "@"}{" "}
              {mlResult.opponent_abbreviation})
            </span>
            {mlResult.is_back_to_back ? (
              <span className="rounded bg-amber-500/15 px-1 py-px text-[9px] font-semibold text-amber-500">
                B2B
              </span>
            ) : null}
            {verified ? (
              <span className="inline-flex items-center gap-0.5 rounded bg-emerald-500/15 px-1.5 py-px text-[10px] font-semibold text-emerald-500">
                <CheckCircle2 className="h-2.5 w-2.5" />
                Verificado
              </span>
            ) : null}
          </div>

          <div className="num flex flex-wrap items-center gap-3 text-[11px]">
            {(
              [
                { k: "min", label: "MIN" },
                { k: "pts", label: "PTS" },
                { k: "reb", label: "REB" },
                { k: "ast", label: "AST" },
                { k: "pra", label: "PRA" },
              ] as const
            ).map((st) => {
              const val = mlResult.scenario_projection[st.k];
              const delta = mlResult.deltas[st.k];
              return (
                <div key={st.k} className="flex items-baseline gap-1">
                  <span className="text-[10px] text-[var(--text-muted)]">{st.label}</span>
                  <span className="font-bold text-amber-500">{val.toFixed(1)}</span>
                  {Math.abs(delta) >= 0.1 ? (
                    <span
                      className={cn(
                        "text-[10px] font-semibold",
                        delta > 0 ? "text-emerald-500" : "text-rose-500"
                      )}
                    >
                      {formatSigned(delta, 1)}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Styled Bullet Points (with highlighted Conclusion card) */}
      <div className="space-y-2">
        {bullets.map((item, idx) => {
          const isConclusion =
            item.title?.toLowerCase().includes("conclusi") ?? false;
          return (
            <div
              key={idx}
              className={cn(
                "flex items-start gap-2.5 rounded-md border px-3 py-2",
                isConclusion
                  ? "border-amber-500/40 bg-amber-500/10"
                  : "border-[var(--border-hairline)]/70 bg-[var(--bg-subtle)]/65"
              )}
            >
              <span
                className={cn(
                  "mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full",
                  isConclusion ? "bg-emerald-500" : "bg-amber-500"
                )}
              />
              <div
                className={cn(
                  "leading-relaxed",
                  isConclusion
                    ? "text-[var(--text-primary)]"
                    : "text-[var(--text-secondary)]"
                )}
              >
                {item.title ? (
                  <span
                    className={cn(
                      "font-semibold",
                      isConclusion
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-[var(--text-primary)]"
                    )}
                  >
                    {item.title}:{" "}
                  </span>
                ) : null}
                <span>{renderInlineFormatted(item.body)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
