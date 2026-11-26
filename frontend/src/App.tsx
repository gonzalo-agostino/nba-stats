import { Fragment, useEffect, useState } from "react";
import { Calendar, Check, ChevronRight, Loader2, Moon, Sparkles, Sun } from "lucide-react";
import {
  askAgent,
  fetchTeams,
  fetchTodaySlate,
  simulateTeam,
  type AskResponse,
  type SlateGameItem,
  type TeamBoxscoreRow,
  type TeamItem,
  type TeamSimulationResponse,
} from "@/lib/api";
import { cn, formatSigned, humanizeFeature } from "@/lib/utils";

type BoxStatKey = "min" | "pts" | "reb" | "ast" | "pra";
type EdgeStatKey = "pts" | "reb" | "ast" | "pra";

const STAT_COLS: Array<{ key: BoxStatKey; label: string; bold?: boolean }> = [
  { key: "min", label: "MIN" },
  { key: "pts", label: "PTS" },
  { key: "reb", label: "REB" },
  { key: "ast", label: "AST" },
  { key: "pra", label: "PRA", bold: true },
];

function StatCell({
  value,
  delta,
  bold,
}: {
  value: number;
  delta: number;
  bold?: boolean;
}) {
  const showDelta = Math.abs(delta) >= 0.1;
  const isPos = delta > 0;
  return (
    <td className="num px-3 py-0 text-right align-middle">
      <span className={cn("text-xs text-[var(--text-primary)]", bold && "font-semibold")}>
        {value.toFixed(1)}
      </span>
      {showDelta ? (
        <span
          className={cn(
            "ml-1 text-[10px] font-medium",
            isPos
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

export function App() {
  const [darkMode, setDarkMode] = useState(true);
  const [teams, setTeams] = useState<TeamItem[]>([]);
  const [slateGames, setSlateGames] = useState<SlateGameItem[]>([]);
  const [slateDate, setSlateDate] = useState<string>("");
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null);
  const [manualMode, setManualMode] = useState(false);

  // Active matchup state
  const [teamId, setTeamId] = useState<number | null>(null);
  const [oppTeamId, setOppTeamId] = useState<number | null>(null);
  const [isHome, setIsHome] = useState(true);
  const [isB2B, setIsB2B] = useState(false);
  const [outIds, setOutIds] = useState<number[]>([]);

  // Boxscore simulation state
  const [boxscore, setBoxscore] = useState<TeamSimulationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Expandable player detail + Edge comparator + AI explanation
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [edgeStat, setEdgeStat] = useState<EdgeStatKey>("pts");
  const [edgeLineInput, setEdgeLineInput] = useState<string>("");
  const [aiLoadingId, setAiLoadingId] = useState<number | null>(null);
  const [aiByPlayer, setAiByPlayer] = useState<Record<number, AskResponse>>({});
  const [aiError, setAiError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  function selectSlateGame(game: SlateGameItem, side: "away" | "home" = "away") {
    setManualMode(false);
    setSelectedGameId(game.game_id);
    setOutIds([]);
    setExpandedId(null);
    setAiByPlayer({});
    if (side === "away") {
      setTeamId(game.away_team_id);
      setOppTeamId(game.home_team_id);
      setIsHome(false);
      setIsB2B(game.away_b2b);
    } else {
      setTeamId(game.home_team_id);
      setOppTeamId(game.away_team_id);
      setIsHome(true);
      setIsB2B(game.home_b2b);
    }
  }

  // Bootstrap teams + Today's Slate
  useEffect(() => {
    void Promise.all([fetchTeams(), fetchTodaySlate()])
      .then(([teamList, slate]) => {
        setTeams(teamList);
        setSlateGames(slate.games);
        setSlateDate(slate.game_date);

        if (!slate.fallback && slate.games.length > 0) {
          selectSlateGame(slate.games[0], "away");
        } else {
          setManualMode(true);
          const den = teamList.find((t) => t.abbreviation === "DEN") ?? teamList[0];
          const lal = teamList.find((t) => t.abbreviation === "LAL") ?? teamList[1];
          if (den) setTeamId(den.team_id);
          if (lal) setOppTeamId(lal.team_id);
        }
      })
      .catch(() => {
        setError(
          "No se pudo conectar con la API en http://127.0.0.1:8000 (uv run nba-stats serve --reload)."
        );
      });
  }, []);

  // Re-run boxscore simulation on every state change
  useEffect(() => {
    if (teamId === null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    void simulateTeam({
      team_id: teamId,
      opponent_team_id: oppTeamId,
      out_player_ids: outIds,
      is_back_to_back: isB2B,
      is_home: isHome,
      limit: 10,
    })
      .then((res) => {
        if (!cancelled) setBoxscore(res);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [teamId, oppTeamId, outIds, isB2B, isHome]);

  function toggleOut(playerId: number) {
    setOutIds((prev) =>
      prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId]
    );
    if (expandedId === playerId) setExpandedId(null);
    setAiByPlayer({});
  }

  async function handleExplainWithAI(row: TeamBoxscoreRow) {
    if (!boxscore || aiLoadingId !== null) return;
    setAiLoadingId(row.player_id);
    setAiError(null);

    const outNames = boxscore.rows.filter((r) => r.is_out).map((r) => r.player_name);
    const parts = [
      `Cómo proyecta ${row.player_name}`,
      isHome ? "de local" : "de visitante",
      boxscore.opponent_abbreviation !== "AVG" ? `contra ${boxscore.opponent_abbreviation}` : "",
      isB2B ? "en back-to-back" : "",
      outNames.length > 0 ? `sin ${outNames.join(" ni ")}` : "",
    ].filter(Boolean);

    try {
      const resp = await askAgent(`${parts.join(" ")}?`);
      setAiByPlayer((prev) => ({ ...prev, [row.player_id]: resp }));
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Error consultando al agente LangGraph.");
    } finally {
      setAiLoadingId(null);
    }
  }

  const activeGame = slateGames.find((g) => g.game_id === selectedGameId) ?? null;
  const oppDef = boxscore?.opponent_defense ?? null;

  return (
    <div className="min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)]">
      <div className="mx-auto max-w-5xl p-4 sm:py-5">
        {/* 1. Today's Slate horizontal strip */}
        <section className="mb-3 flex items-center gap-2 overflow-x-auto pb-1">
          <div className="flex shrink-0 items-center gap-1.5 pr-1 text-[11px] font-medium text-[var(--text-muted)]">
            <Calendar className="h-3.5 w-3.5 text-[var(--accent)]" />
            <span className="num">{slateDate || "Hoy"}</span>
          </div>

          {slateGames.map((g) => {
            const isActive = !manualMode && selectedGameId === g.game_id;
            const hasB2B = g.away_b2b || g.home_b2b;
            return (
              <button
                key={g.game_id}
                type="button"
                title={g.game_status}
                onClick={() => selectSlateGame(g, "away")}
                className={cn(
                  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
                  isActive
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                <span className="num font-semibold">
                  {g.away_abbr} @ {g.home_abbr}
                </span>
                {hasB2B ? (
                  <span className="rounded bg-amber-500/15 px-1 text-[9px] font-semibold text-amber-500">
                    B2B
                  </span>
                ) : null}
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => {
              setManualMode(true);
              setSelectedGameId(null);
            }}
            className={cn(
              "inline-flex h-7 shrink-0 items-center rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
              manualMode
                ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            )}
          >
            Manual
          </button>
        </section>

        {/* 2. Single-line Context Bar */}
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2 border-y border-[var(--border-hairline)] py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            {!manualMode && activeGame ? (
              /* Quick Away / Home tabs for the selected slate game */
              <div className="inline-flex rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => selectSlateGame(activeGame, "away")}
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2.5 py-1 font-medium transition-colors cursor-pointer",
                    teamId === activeGame.away_team_id
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  )}
                >
                  <span>{activeGame.away_abbr} (Visitante)</span>
                  {activeGame.away_b2b ? (
                    <span className="text-[9px] font-bold opacity-90">B2B</span>
                  ) : null}
                </button>
                <button
                  type="button"
                  onClick={() => selectSlateGame(activeGame, "home")}
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2.5 py-1 font-medium transition-colors cursor-pointer",
                    teamId === activeGame.home_team_id
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  )}
                >
                  <span>{activeGame.home_abbr} (Local)</span>
                  {activeGame.home_b2b ? (
                    <span className="text-[9px] font-bold opacity-90">B2B</span>
                  ) : null}
                </button>
              </div>
            ) : (
              /* Manual Team vs Opponent selectors */
              <div className="flex items-center gap-1.5">
                <select
                  aria-label="Equipo"
                  value={teamId ?? ""}
                  onChange={(e) => {
                    setTeamId(Number(e.target.value));
                    setOutIds([]);
                    setExpandedId(null);
                    setAiByPlayer({});
                  }}
                  className="h-8 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2 text-xs font-semibold text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
                >
                  {teams.map((t) => (
                    <option key={t.team_id} value={t.team_id}>
                      {t.abbreviation} — {t.full_name}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-[var(--text-muted)]">{isHome ? "vs" : "@"}</span>
                <select
                  aria-label="Rival"
                  value={oppTeamId ?? ""}
                  onChange={(e) => {
                    setOppTeamId(e.target.value ? Number(e.target.value) : null);
                    setAiByPlayer({});
                  }}
                  className="h-8 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2 text-xs font-medium text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
                >
                  <option value="">Promedio liga</option>
                  {teams
                    .filter((t) => t.team_id !== teamId)
                    .map((t) => (
                      <option key={t.team_id} value={t.team_id}>
                        {t.abbreviation} — {t.full_name}
                      </option>
                    ))}
                </select>
              </div>
            )}

            {/* Editable Local/Visitante & B2B toggles */}
            <button
              type="button"
              onClick={() => {
                setIsHome((h) => !h);
                setAiByPlayer({});
              }}
              className="h-8 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] cursor-pointer"
            >
              {isHome ? "Local" : "Visitante"}
            </button>

            <button
              type="button"
              onClick={() => {
                setIsB2B((b) => !b);
                setAiByPlayer({});
              }}
              className={cn(
                "h-8 rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
                isB2B
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              )}
            >
              B2B
            </button>

            {outIds.length > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setOutIds([]);
                  setAiByPlayer({});
                }}
                className="h-8 rounded-md border border-rose-500/35 bg-rose-500/10 px-2.5 text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-500/15 cursor-pointer"
              >
                Limpiar bajas ({outIds.length})
              </button>
            ) : null}
          </div>

          <div className="flex items-center gap-2.5">
            {oppDef && oppDef.def_rating_rank !== null ? (
              <span className="num hidden text-[11px] text-[var(--text-muted)] md:inline">
                Rival Def #{oppDef.def_rating_rank} · Pace #{oppDef.pace_rank ?? "-"}
              </span>
            ) : null}
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--accent)]" /> : null}
            <button
              type="button"
              onClick={() => setDarkMode((d) => !d)}
              aria-label="Cambiar tema"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] cursor-pointer"
            >
              {darkMode ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </button>
          </div>
        </header>

        {error ? (
          <div className="mb-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
            {error}
          </div>
        ) : null}

        {/* 3. Protagonist Boxscore Table */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-[var(--border-hairline)] text-[11px] font-medium text-[var(--text-muted)]">
                <th className="w-10 px-2 py-2 text-center">OUT</th>
                <th className="px-2 py-2 text-left">JUGADOR</th>
                <th className="w-12 px-2 py-2 text-center">POS</th>
                {STAT_COLS.map((col) => (
                  <th key={col.key} className="px-3 py-2 text-right">
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--border-hairline)]">
              {!boxscore ? (
                <tr>
                  <td colSpan={8} className="py-10 text-center text-xs text-[var(--text-muted)]">
                    {loading ? "Calculando boxscore..." : "Seleccioná un partido o equipo."}
                  </td>
                </tr>
              ) : (
                boxscore.rows.map((row) => {
                  const isExpanded = expandedId === row.player_id && !row.is_out;
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
                        <td className="px-2 py-0 text-center align-middle">
                          <button
                            type="button"
                            onClick={() => toggleOut(row.player_id)}
                            aria-label={`Marcar ${row.player_name} como OUT`}
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

                        <td className="px-2 py-0 align-middle">
                          <button
                            type="button"
                            disabled={row.is_out}
                            onClick={() => {
                              setExpandedId((curr) =>
                                curr === row.player_id ? null : row.player_id
                              );
                              setEdgeLineInput("");
                              setAiError(null);
                            }}
                            className={cn(
                              "inline-flex items-center gap-1.5 text-left text-xs font-medium text-[var(--text-primary)]",
                              row.is_out
                                ? "line-through cursor-default"
                                : "hover:text-[var(--accent)] cursor-pointer"
                            )}
                          >
                            {!row.is_out ? (
                              <ChevronRight
                                className={cn(
                                  "h-3 w-3 shrink-0 text-[var(--text-muted)] transition-transform",
                                  isExpanded && "rotate-90 text-[var(--accent)]"
                                )}
                              />
                            ) : null}
                            <span>{row.player_name}</span>
                          </button>
                        </td>

                        <td className="num px-2 py-0 text-center align-middle text-[11px] text-[var(--text-muted)]">
                          {row.pos_group}
                        </td>

                        {row.is_out || !row.scenario || !row.deltas ? (
                          <td
                            colSpan={STAT_COLS.length}
                            className="num px-3 py-0 text-right align-middle text-[11px] text-[var(--text-muted)]"
                          >
                            OUT ({row.ewma_min.toFixed(1)}m)
                          </td>
                        ) : (
                          STAT_COLS.map((col) => (
                            <StatCell
                              key={col.key}
                              value={row.scenario![col.key]}
                              delta={row.deltas![col.key]}
                              bold={col.bold}
                            />
                          ))
                        )}
                      </tr>

                      {/* 4. On-Demand Expandable Detail Panel (3 horizontal blocks) */}
                      {isExpanded && row.scenario ? (
                        <tr className="bg-[var(--bg-panel)]/75">
                          <td colSpan={8} className="px-4 py-3">
                            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                              {/* Block 1: Top 3 SHAP Drivers */}
                              <div className="space-y-1 text-xs">
                                <div className="text-[11px] font-semibold text-[var(--text-secondary)]">
                                  Factores clave (PTS)
                                </div>
                                <ul className="space-y-0.5 text-[11px]">
                                  {row.top_drivers.slice(0, 3).map((d) => (
                                    <li key={d.feature} className="flex items-center justify-between gap-2">
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
                                    </li>
                                  ))}
                                </ul>
                              </div>

                              {/* Block 2: Real WOWY History */}
                              <div className="space-y-1 text-xs">
                                <div className="text-[11px] font-semibold text-[var(--text-secondary)]">
                                  Historial WOWY
                                </div>
                                {row.wowy ? (
                                  <div className="space-y-0.5 text-[11px]">
                                    <div className="text-[var(--text-muted)]">
                                      Sin {row.wowy.primary_out_player} ({row.wowy.sample_games_without} sin /{" "}
                                      {row.wowy.sample_games_with} con)
                                    </div>
                                    <div className="num text-[var(--text-primary)]">
                                      MIN {formatSigned(row.wowy.raw_delta_min, 1)} · PTS{" "}
                                      {formatSigned(row.wowy.raw_delta_pts, 1)} · REB{" "}
                                      {formatSigned(row.wowy.raw_delta_reb, 1)} · AST{" "}
                                      {formatSigned(row.wowy.raw_delta_ast, 1)}
                                    </div>
                                  </div>
                                ) : (
                                  <p className="text-[11px] text-[var(--text-muted)]">
                                    Tildá un compañero como OUT para ver su historial WOWY real.
                                  </p>
                                )}
                              </div>

                              {/* Block 3: Line Edge Comparator + AI Agent */}
                              <div className="space-y-2 text-xs">
                                <div className="text-[11px] font-semibold text-[var(--text-secondary)]">
                                  Comparador de Línea (Edge)
                                </div>
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <select
                                    aria-label="Estadística para línea"
                                    value={edgeStat}
                                    onChange={(e) => setEdgeStat(e.target.value as EdgeStatKey)}
                                    className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-1.5 text-[11px] font-semibold uppercase text-[var(--text-primary)]"
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
                                    className="num h-7 w-18 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
                                  />
                                  {edgeValue !== null ? (
                                    <span
                                      className={cn(
                                        "num rounded px-1.5 py-0.5 text-xs font-semibold",
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
                                    onClick={() => void handleExplainWithAI(row)}
                                    className="ml-auto inline-flex h-7 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-45 cursor-pointer"
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
                            </div>

                            {aiError && aiLoadingId === null ? (
                              <div className="mt-2.5 text-xs text-rose-600 dark:text-rose-400">
                                {aiError}
                              </div>
                            ) : null}

                            {aiResp ? (
                              <div className="mt-3 border-t border-[var(--border-hairline)] pt-2.5 text-xs leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
                                {aiResp.analysis || aiResp.final_response}
                              </div>
                            ) : null}
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <footer className="mt-3 text-[11px] text-[var(--text-muted)]">
          Tildá OUT para recalcular el equipo en vivo · Clic en cualquier jugador para ver SHAP, WOWY, Edge e IA
        </footer>
      </div>
    </div>
  );
}

export default App;
