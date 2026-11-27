import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ChevronLeft,
  ChevronRight,
  CornerDownLeft,
  Loader2,
  Moon,
  RefreshCw,
  Search,
  Sparkles,
  Sun,
  X,
} from "lucide-react";
import {
  askAgent,
  explainScenario,
  fetchHealth,
  fetchTeams,
  fetchTodaySlate,
  searchPlayers,
  simulateGame,
  syncWarehouse,
  type AskResponse,
  type ExplainResponse,
  type GameSimulationResponse,
  type PlayerSearchItem,
  type SlateGameItem,
  type TeamBoxscoreRow,
  type TeamItem,
  type TeamSimulationResponse,
} from "@/lib/api";
import { AiAnalysisBlock } from "@/components/AiAnalysisBlock";
import { PlayerAvatar } from "@/components/PlayerAvatar";
import { PerformanceView } from "@/components/PerformanceView";
import { PlayerDetailModal } from "@/components/PlayerDetailModal";
import { PropsBoardView } from "@/components/PropsBoardView";
import { TeamBoxscoreTable, type StatViewMode } from "@/components/TeamBoxscoreTable";
import { cn } from "@/lib/utils";

function shiftIsoDate(isoDate: string, deltaDays: number): string {
  const base = isoDate ? new Date(`${isoDate}T12:00:00`) : new Date();
  base.setDate(base.getDate() + deltaDays);
  return base.toISOString().slice(0, 10);
}

export function App() {
  const [darkMode, setDarkMode] = useState(true);
  const [activeTab, setActiveTab] = useState<"props" | "games" | "performance">("props");
  const [teams, setTeams] = useState<TeamItem[]>([]);
  const [latestGameDate, setLatestGameDate] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  // Slate & date navigation
  const [slateDate, setSlateDate] = useState<string>("");
  const [slateGames, setSlateGames] = useState<SlateGameItem[]>([]);
  const [slateLoading, setSlateLoading] = useState(false);
  const [selectedGameId, setSelectedGameId] = useState<string | null>(null);
  const [manualMode, setManualMode] = useState(false);
  const slateReqSeqRef = useRef(0);

  // Unified top search / LangGraph AI query bar
  const [searchMode, setSearchMode] = useState<"player" | "ai">("player");
  const [topInput, setTopInput] = useState("");
  const [playerMatches, setPlayerMatches] = useState<PlayerSearchItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [globalAiLoading, setGlobalAiLoading] = useState(false);
  const [globalAiResp, setGlobalAiResp] = useState<AskResponse | null>(null);
  const [globalAiError, setGlobalAiError] = useState<string | null>(null);
  const topSearchRef = useRef<HTMLDivElement>(null);

  // Active matchup (Away @ Home)
  const [awayTeamId, setAwayTeamId] = useState<number | null>(null);
  const [homeTeamId, setHomeTeamId] = useState<number | null>(null);
  const [awayB2B, setAwayB2B] = useState(false);
  const [homeB2B, setHomeB2B] = useState(false);
  // null = auto-apply official ESPN injuries on first load of a matchup
  const [awayOutIds, setAwayOutIds] = useState<number[] | null>(null);
  const [homeOutIds, setHomeOutIds] = useState<number[] | null>(null);
  const [autoApplyInjuries, setAutoApplyInjuries] = useState(true);

  // Full game simulation result
  const [gameData, setGameData] = useState<GameSimulationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Per-row expandable details, stat view, player detail modal & fast AI
  const [statView, setStatView] = useState<StatViewMode>("main");
  const [expandedPlayerId, setExpandedPlayerId] = useState<number | null>(null);
  const [modalPlayer, setModalPlayer] = useState<{
    playerId: number;
    side: "away" | "home";
  } | null>(null);
  const [aiLoadingId, setAiLoadingId] = useState<number | null>(null);
  const [aiByPlayer, setAiByPlayer] = useState<Record<number, ExplainResponse>>({});
  const [aiError, setAiError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (topSearchRef.current && !topSearchRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function selectSlateGame(game: SlateGameItem) {
    setManualMode(false);
    setSelectedGameId(game.game_id);
    setAwayTeamId(game.away_team_id);
    setHomeTeamId(game.home_team_id);
    setAwayB2B(game.away_b2b);
    setHomeB2B(game.home_b2b);
    setAwayOutIds(null);
    setHomeOutIds(null);
    setAutoApplyInjuries(true);
    setExpandedPlayerId(null);
    setAiByPlayer({});
  }

  async function loadSlateForDate(targetDate?: string, fallbackTeams?: TeamItem[]) {
    const reqId = ++slateReqSeqRef.current;
    if (targetDate) {
      setSlateDate(targetDate);
    }
    setSlateLoading(true);
    try {
      const slate = await fetchTodaySlate(targetDate);
      if (reqId !== slateReqSeqRef.current) return;
      setSlateGames(slate.games);
      setSlateDate(slate.game_date);

      if (!slate.fallback && slate.games.length > 0) {
        selectSlateGame(slate.games[0]);
      } else {
        setManualMode(true);
        setSelectedGameId(null);
        const list = fallbackTeams ?? teams;
        if ((awayTeamId === null || homeTeamId === null) && list.length > 1) {
          const den = list.find((t) => t.abbreviation === "DEN") ?? list[0];
          const lal = list.find((t) => t.abbreviation === "LAL") ?? list[1];
          setAwayTeamId(den.team_id);
          setHomeTeamId(lal.team_id);
          setAwayOutIds(null);
          setHomeOutIds(null);
          setAutoApplyInjuries(true);
        }
      }
    } catch {
      if (reqId !== slateReqSeqRef.current) return;
      setError(
        "No se pudo conectar con la API en http://127.0.0.1:8000 (uv run nba-stats serve --reload)."
      );
    } finally {
      if (reqId === slateReqSeqRef.current) {
        setSlateLoading(false);
      }
    }
  }

  // Initial bootstrap: health + teams + today's slate
  useEffect(() => {
    void fetchHealth()
      .then((h) => setLatestGameDate(h.latest_game_date))
      .catch(() => setLatestGameDate(null));

    void fetchTeams()
      .then((teamList) => {
        setTeams(teamList);
        return loadSlateForDate(undefined, teamList);
      })
      .catch(() => {
        setError(
          "No se pudo conectar con la API en http://127.0.0.1:8000 (uv run nba-stats serve --reload)."
        );
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSyncWarehouse() {
    if (syncing) return;
    setSyncing(true);
    setError(null);
    try {
      const res = await syncWarehouse();
      setLatestGameDate(res.latest_game_date);
      await loadSlateForDate(slateDate || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error sincronizando datos.");
    } finally {
      setSyncing(false);
    }
  }

  // Debounced player autocomplete
  useEffect(() => {
    if (searchMode !== "player") {
      setPlayerMatches([]);
      return;
    }
    const q = topInput.trim();
    if (q.length < 2) {
      setPlayerMatches([]);
      return;
    }
    const timer = setTimeout(() => {
      void searchPlayers(q, 8)
        .then(setPlayerMatches)
        .catch(() => setPlayerMatches([]));
    }, 150);
    return () => clearTimeout(timer);
  }, [topInput, searchMode]);

  // Simulate full game (Away + Home with cross-team absences & optional official injury pre-load)
  useEffect(() => {
    if (awayTeamId === null || homeTeamId === null || awayTeamId === homeTeamId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    void simulateGame({
      away_team_id: awayTeamId,
      home_team_id: homeTeamId,
      away_out_player_ids: awayOutIds,
      home_out_player_ids: homeOutIds,
      auto_apply_injuries: autoApplyInjuries,
      away_b2b: awayB2B,
      home_b2b: homeB2B,
      limit: 10,
    })
      .then((res) => {
        if (!cancelled) {
          setGameData(res);
          // Sync resolved auto-injuries into controlled state so user toggles work seamlessly
          if (awayOutIds === null) setAwayOutIds(res.away_out_player_ids);
          if (homeOutIds === null) setHomeOutIds(res.home_out_player_ids);
          if (autoApplyInjuries) setAutoApplyInjuries(false);
        }
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
  }, [awayTeamId, homeTeamId, awayOutIds, homeOutIds, autoApplyInjuries, awayB2B, homeB2B]);

  function handleSelectPlayerFromSearch(player: PlayerSearchItem) {
    setDropdownOpen(false);
    setTopInput(player.full_name);

    const slateMatch = slateGames.find(
      (g) => g.away_team_id === player.team_id || g.home_team_id === player.team_id
    );
    if (slateMatch) {
      selectSlateGame(slateMatch);
    } else {
      setManualMode(true);
      setSelectedGameId(null);
      setAwayTeamId(player.team_id);
      const fallbackOpp = teams.find((t) => t.team_id !== player.team_id);
      if (fallbackOpp && (homeTeamId === null || homeTeamId === player.team_id)) {
        setHomeTeamId(fallbackOpp.team_id);
      }
      setAwayOutIds(null);
      setHomeOutIds(null);
      setAutoApplyInjuries(true);
    }
    setExpandedPlayerId(player.player_id);
  }

  async function handleTopFormSubmit(e: FormEvent) {
    e.preventDefault();
    const q = topInput.trim();
    if (!q) return;

    if (searchMode === "player") {
      if (playerMatches.length > 0) handleSelectPlayerFromSearch(playerMatches[0]);
      return;
    }

    if (globalAiLoading || q.length < 3) return;
    setGlobalAiLoading(true);
    setGlobalAiError(null);
    try {
      const resp = await askAgent(q);
      setGlobalAiResp(resp);
      if (resp.ml_result) {
        const res = resp.ml_result;
        const oppTeam = teams.find((t) => t.abbreviation === res.opponent_abbreviation);
        if (oppTeam) {
          setManualMode(true);
          setSelectedGameId(null);
          if (res.is_home) {
            setHomeTeamId(res.team_id);
            setAwayTeamId(oppTeam.team_id);
            setHomeB2B(res.is_back_to_back);
          } else {
            setAwayTeamId(res.team_id);
            setHomeTeamId(oppTeam.team_id);
            setAwayB2B(res.is_back_to_back);
          }
          setAwayOutIds(null);
          setHomeOutIds(null);
          setAutoApplyInjuries(true);
          setExpandedPlayerId(res.player_id);
        }
      }
    } catch (err) {
      setGlobalAiError(err instanceof Error ? err.message : "Error consultando al agente LangGraph.");
    } finally {
      setGlobalAiLoading(false);
    }
  }

  function toggleOut(side: "away" | "home", playerId: number) {
    setAutoApplyInjuries(false);
    setAiByPlayer({});
    if (expandedPlayerId === playerId) setExpandedPlayerId(null);

    if (side === "away") {
      const curr = awayOutIds ?? [];
      setAwayOutIds(
        curr.includes(playerId) ? curr.filter((id) => id !== playerId) : [...curr, playerId]
      );
    } else {
      const curr = homeOutIds ?? [];
      setHomeOutIds(
        curr.includes(playerId) ? curr.filter((id) => id !== playerId) : [...curr, playerId]
      );
    }
  }

  async function handleExplainWithAI(row: TeamBoxscoreRow, teamData: TeamSimulationResponse) {
    if (!gameData || aiLoadingId !== null) return;
    setAiLoadingId(row.player_id);
    setAiError(null);

    const ownOuts = teamData.rows.filter((r) => r.is_out).map((r) => r.player_name);
    const otherTeam = teamData.is_home ? gameData.away : gameData.home;
    const oppOuts = otherTeam.rows.filter((r) => r.is_out).map((r) => r.player_name);

    try {
      const resp = await explainScenario({
        player_name: row.player_name,
        opponent_team: teamData.opponent_abbreviation,
        out_players: ownOuts,
        opponent_out_players: oppOuts,
        is_back_to_back: teamData.is_back_to_back,
        rest_days: teamData.is_back_to_back ? 0 : 1,
        is_home: teamData.is_home,
      });
      setAiByPlayer((prev) => ({ ...prev, [row.player_id]: resp }));
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Error explicando con IA.");
    } finally {
      setAiLoadingId(null);
    }
  }

  const activeGame = slateGames.find((g) => g.game_id === selectedGameId) ?? null;
  const awayTeamObj = teams.find((t) => t.team_id === awayTeamId);
  const homeTeamObj = teams.find((t) => t.team_id === homeTeamId);

  const allOutLabels: string[] = [];
  if (gameData) {
    for (const r of gameData.away.rows) {
      if (r.is_out) allOutLabels.push(`${r.player_name} (${gameData.away.team_abbreviation})`);
    }
    for (const r of gameData.home.rows) {
      if (r.is_out) allOutLabels.push(`${r.player_name} (${gameData.home.team_abbreviation})`);
    }
  }

  return (
    <div className="min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)]">
      <div className="mx-auto max-w-7xl p-4 sm:py-5">
        {/* 1. Top Unified Bar: Brand + Player Search / LangGraph AI + Theme */}
        <div ref={topSearchRef} className="relative mb-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setActiveTab("props")}
              className="inline-flex shrink-0 items-center gap-2 cursor-pointer group"
              title="NBA Stats"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 32 32"
                fill="none"
                className="h-7 w-7 shrink-0 transition-transform duration-200 group-hover:rotate-12"
                aria-hidden="true"
              >
                <defs>
                  <linearGradient id="nbaLogoBall" x1="4" y1="3" x2="28" y2="29" gradientUnits="userSpaceOnUse">
                    <stop offset="0%" stopColor="#F59E0B" />
                    <stop offset="100%" stopColor="#EA580C" />
                  </linearGradient>
                  <clipPath id="nbaLogoClip">
                    <circle cx="16" cy="16" r="14" />
                  </clipPath>
                </defs>
                <circle cx="16" cy="16" r="14" fill="url(#nbaLogoBall)" />
                <g clipPath="url(#nbaLogoClip)" stroke="#0B0E14" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M2 16 H30" />
                  <path d="M16 2 V30" />
                  <path d="M6.2 5.2 C11.5 10.5 11.5 21.5 6.2 26.8" />
                  <path d="M25.8 5.2 C20.5 10.5 20.5 21.5 25.8 26.8" />
                </g>
              </svg>
              <span className="hidden text-sm font-bold tracking-tight text-[var(--text-primary)] sm:inline">
                NBA<span className="text-amber-500">Stats</span>
              </span>
            </button>

            <div className="inline-flex h-9 shrink-0 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setActiveTab("props")}
                className={cn(
                  "rounded px-2.5 font-medium transition-colors cursor-pointer",
                  activeTab === "props"
                    ? "bg-amber-500 text-slate-950 font-semibold"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                Props
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("games")}
                className={cn(
                  "rounded px-2.5 font-medium transition-colors cursor-pointer",
                  activeTab === "games"
                    ? "bg-amber-500 text-slate-950 font-semibold"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                Boxscore
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("performance")}
                className={cn(
                  "rounded px-2.5 font-medium transition-colors cursor-pointer",
                  activeTab === "performance"
                    ? "bg-amber-500 text-slate-950 font-semibold"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                Win Rate
              </button>
            </div>

            <div className="inline-flex h-9 shrink-0 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-0.5 text-xs">
              <button
                type="button"
                onClick={() => {
                  setSearchMode("player");
                  setGlobalAiError(null);
                }}
                className={cn(
                  "rounded px-2.5 font-medium transition-colors cursor-pointer",
                  searchMode === "player"
                    ? "bg-[var(--accent)] text-white"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                Jugador
              </button>
              <button
                type="button"
                onClick={() => {
                  setSearchMode("ai");
                  setDropdownOpen(false);
                }}
                className={cn(
                  "inline-flex items-center gap-1 rounded px-2.5 font-medium transition-colors cursor-pointer",
                  searchMode === "ai"
                    ? "bg-[var(--accent)] text-white"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                <Sparkles className="h-3 w-3" />
                Consultar IA
              </button>
            </div>

            <form
              onSubmit={handleTopFormSubmit}
              className="flex h-9 flex-1 items-center gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2.5 focus-within:border-[var(--accent)]"
            >
              {searchMode === "player" ? (
                <Search className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted)]" />
              ) : (
                <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--accent)]" />
              )}
              <input
                type="text"
                value={topInput}
                onFocus={() => searchMode === "player" && setDropdownOpen(true)}
                onChange={(e) => {
                  setTopInput(e.target.value);
                  if (searchMode === "player") setDropdownOpen(true);
                }}
                placeholder={
                  searchMode === "player"
                    ? "Buscar jugador (ej. Jokic, Brunson, Tatum)..."
                    : "Consultar al Agente LangGraph (ej. Jamal Murray vs LAL en B2B sin Jokic)..."
                }
                className="h-full flex-1 bg-transparent text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none"
              />
              {topInput ? (
                <button
                  type="button"
                  onClick={() => {
                    setTopInput("");
                    setPlayerMatches([]);
                  }}
                  aria-label="Limpiar"
                  className="text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
              {searchMode === "ai" ? (
                <button
                  type="submit"
                  disabled={globalAiLoading || topInput.trim().length < 3}
                  className="inline-flex h-6 items-center gap-1 rounded bg-[var(--accent)] px-2 text-[11px] font-medium text-white disabled:opacity-40 cursor-pointer"
                >
                  {globalAiLoading ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <CornerDownLeft className="h-3 w-3" />
                  )}
                  Enviar
                </button>
              ) : null}
            </form>

            <button
              type="button"
              onClick={() => void handleSyncWarehouse()}
              disabled={syncing}
              title={
                latestGameDate
                  ? `Datos locales al ${latestGameDate} · Clic para sincronizar últimos partidos`
                  : "Sincronizar últimos partidos NBA"
              }
              className="hidden h-9 shrink-0 items-center gap-1.5 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-2.5 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] disabled:opacity-50 sm:inline-flex cursor-pointer"
            >
              <RefreshCw className={cn("h-3 w-3", syncing && "animate-spin text-[var(--accent)]")} />
              <span className="num">
                {syncing
                  ? "Sincronizando..."
                  : latestGameDate
                  ? `Datos ${latestGameDate.slice(5)}`
                  : "Sincronizar"}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setDarkMode((d) => !d)}
              aria-label="Cambiar tema"
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] cursor-pointer"
            >
              {darkMode ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </button>
          </div>

          {searchMode === "player" && dropdownOpen && playerMatches.length > 0 ? (
            <div className="absolute left-0 right-0 z-30 mt-1 max-h-64 overflow-y-auto rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] py-1">
              {playerMatches.map((p) => (
                <button
                  key={p.player_id}
                  type="button"
                  onClick={() => handleSelectPlayerFromSearch(p)}
                  className="flex h-10 w-full items-center justify-between px-3 text-left text-xs transition-colors hover:bg-[var(--bg-subtle)] cursor-pointer"
                >
                  <span className="flex items-center gap-2 font-medium text-[var(--text-primary)]">
                    <PlayerAvatar playerId={p.player_id} name={p.full_name} size="sm" />
                    <span>{p.full_name}</span>
                    <span className="num text-[11px] text-[var(--text-muted)]">
                      {p.team_abbreviation} · {p.pos_group}
                    </span>
                  </span>
                  <span className="num text-[11px] text-[var(--text-secondary)]">
                    {p.avg_min_10.toFixed(1)}m · {p.avg_pts_10.toFixed(1)} pts
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          {globalAiError ? (
            <div className="mt-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
              {globalAiError}
            </div>
          ) : null}
          {globalAiResp ? (
            <div className="mt-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-3.5 text-xs">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-semibold text-[var(--text-secondary)]">
                  Análisis del Agente LangGraph
                </span>
                <button
                  type="button"
                  onClick={() => setGlobalAiResp(null)}
                  aria-label="Cerrar respuesta IA"
                  className="text-[var(--text-muted)] hover:text-[var(--text-primary)] cursor-pointer"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <AiAnalysisBlock
                text={
                  globalAiResp.clarification_message ||
                  globalAiResp.analysis ||
                  globalAiResp.final_response
                }
                verified={globalAiResp.verification_passed}
                mlResult={globalAiResp.ml_result}
              />
            </div>
          ) : null}
        </div>

        {/* Main Content: Keep Props mounted for instant tab switching without re-fetching */}
        <div className={activeTab === "props" ? "" : "hidden"}>
          <PropsBoardView activeDate={slateDate || undefined} isActive={activeTab === "props"} />
        </div>

        {activeTab === "performance" ? (
          <PerformanceView activeDate={slateDate || undefined} />
        ) : activeTab === "games" ? (
        <>
        {/* 2. Date Picker + Slate Games Strip */}
        <section className="mb-4 flex items-center gap-2 overflow-x-auto pb-1">
          <div className="inline-flex h-7 shrink-0 items-center rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)]">
            <button
              type="button"
              aria-label="Día anterior"
              onClick={() => void loadSlateForDate(shiftIsoDate(slateDate, -1))}
              className="flex h-full w-6 items-center justify-center text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <input
              type="date"
              aria-label="Fecha de partidos"
              value={slateDate}
              onChange={(e) => {
                if (e.target.value) void loadSlateForDate(e.target.value);
              }}
              className="num h-full bg-transparent px-1 text-[11px] font-medium text-[var(--text-primary)] focus:outline-none cursor-pointer"
            />
            <button
              type="button"
              aria-label="Día siguiente"
              onClick={() => void loadSlateForDate(shiftIsoDate(slateDate, 1))}
              className="flex h-full w-6 items-center justify-center text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {slateLoading ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--accent)]" />
          ) : slateGames.length === 0 ? (
            <span className="shrink-0 text-[11px] text-[var(--text-muted)]">
              Sin partidos programados
            </span>
          ) : (
            slateGames.map((g) => {
              const isActive = !manualMode && selectedGameId === g.game_id;
              const hasB2B = g.away_b2b || g.home_b2b;
              return (
                <button
                  key={g.game_id}
                  type="button"
                  title={g.game_status}
                  onClick={() => selectSlateGame(g)}
                  className={cn(
                    "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
                    isActive
                      ? "border-amber-500 bg-amber-500/12 text-amber-600 dark:text-amber-400"
                      : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  )}
                >
                  <span className="num font-semibold">
                    {g.away_abbr} @ {g.home_abbr}
                  </span>
                  {isActive && loading ? (
                    <Loader2 className="h-3 w-3 animate-spin text-amber-500" />
                  ) : hasB2B ? (
                    <span className="rounded bg-amber-500/15 px-1 text-[9px] font-semibold text-amber-500">
                      B2B
                    </span>
                  ) : null}
                </button>
              );
            })
          )}

          <button
            type="button"
            onClick={() => {
              setManualMode(true);
              setSelectedGameId(null);
            }}
            className={cn(
              "inline-flex h-7 shrink-0 items-center rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
              manualMode
                ? "border-amber-500 bg-amber-500/12 text-amber-600 dark:text-amber-400"
                : "border-[var(--border-hairline)] bg-[var(--bg-panel)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            )}
          >
            Manual
          </button>
        </section>

        {/* Manual Matchup Selector (only when Manual is active) */}
        {manualMode ? (
          <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 py-2 text-xs">
            <span className="text-[var(--text-muted)]">Visitante:</span>
            <select
              value={awayTeamId ?? ""}
              onChange={(e) => {
                setAwayTeamId(Number(e.target.value));
                setAwayOutIds(null);
                setAutoApplyInjuries(true);
              }}
              className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 font-medium text-[var(--text-primary)]"
            >
              {teams.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  {t.abbreviation} — {t.full_name}
                </option>
              ))}
            </select>
            <span className="text-[var(--text-muted)]">@ Local:</span>
            <select
              value={homeTeamId ?? ""}
              onChange={(e) => {
                setHomeTeamId(Number(e.target.value));
                setHomeOutIds(null);
                setAutoApplyInjuries(true);
              }}
              className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 font-medium text-[var(--text-primary)]"
            >
              {teams
                .filter((t) => t.team_id !== awayTeamId)
                .map((t) => (
                  <option key={t.team_id} value={t.team_id}>
                    {t.abbreviation} — {t.full_name}
                  </option>
                ))}
            </select>
          </div>
        ) : null}

        {/* 3. Matchup Headline (LudoProps style: AWAY | time | HOME) */}
        {awayTeamObj && homeTeamObj ? (
          <div className="mb-3 flex items-center justify-between px-1">
            <div>
              <div className="num text-xl font-bold tracking-tight text-[var(--text-primary)]">
                {awayTeamObj.abbreviation}
              </div>
              <div className="text-xs text-[var(--text-muted)]">{awayTeamObj.full_name}</div>
            </div>

            <div className="flex flex-col items-center">
              <span className="num text-xs font-medium text-[var(--text-secondary)]">
                {activeGame?.game_status ?? "Simulación manual"}
              </span>
              {loading ? (
                <span className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-amber-500">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Actualizando
                </span>
              ) : null}
            </div>

            <div className="text-right">
              <div className="num text-xl font-bold tracking-tight text-[var(--text-primary)]">
                {homeTeamObj.abbreviation}
              </div>
              <div className="text-xs text-[var(--text-muted)]">{homeTeamObj.full_name}</div>
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="mb-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
            {error}
          </div>
        ) : null}

        {/* 4. Pace & Defense Summary Card + Unified "Si no juegan" Bar */}
        {gameData ? (
          <div className="relative mb-4">
            {loading &&
            (gameData.away.team_id !== awayTeamId || gameData.home.team_id !== homeTeamId) ? (
              <div className="absolute inset-0 z-20 flex items-start justify-center pt-16 bg-[var(--bg-canvas)]/55 backdrop-blur-[1px]">
                <div className="inline-flex items-center gap-2.5 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-2.5 text-xs font-medium text-[var(--text-primary)] shadow-lg">
                  <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
                  <span>
                    Cargando{" "}
                    <span className="num font-semibold">
                      {awayTeamObj?.abbreviation ?? ""} @ {homeTeamObj?.abbreviation ?? ""}
                    </span>
                    ...
                  </span>
                </div>
              </div>
            ) : null}
            <div
              className={cn(
                "space-y-2.5 transition-opacity duration-150",
                loading &&
                  (gameData.away.team_id !== awayTeamId || gameData.home.team_id !== homeTeamId) &&
                  "opacity-35 pointer-events-none select-none"
              )}
            >
            {/* Ritmo y Defensa del partido */}
            <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-3">
              <div className="mb-2 flex flex-wrap items-baseline gap-2">
                <span className="text-xs text-[var(--text-muted)]">Ritmo del partido</span>
                <span className="text-sm font-bold text-[var(--text-primary)]">
                  {gameData.pace_summary.pace_label}
                </span>
                <span className="num text-xs text-[var(--text-secondary)]">
                  {gameData.pace_summary.expected_pace.toFixed(1)} posesiones esperadas
                </span>
              </div>

              <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                <div className="num text-[var(--text-secondary)]">
                  <span className="font-semibold text-[var(--text-primary)]">
                    {gameData.away.team_abbreviation}
                  </span>{" "}
                  · Pace {gameData.pace_summary.away_pace_10.toFixed(1)}{" "}
                  {gameData.pace_summary.away_pace_rank
                    ? `(#${gameData.pace_summary.away_pace_rank})`
                    : ""}{" "}
                  · DefRtg {gameData.pace_summary.away_def_rating_10.toFixed(1)}{" "}
                  {gameData.pace_summary.away_def_rank
                    ? `(#${gameData.pace_summary.away_def_rank})`
                    : ""}
                </div>
                <div className="num text-[var(--text-secondary)] sm:text-right">
                  <span className="font-semibold text-[var(--text-primary)]">
                    {gameData.home.team_abbreviation}
                  </span>{" "}
                  · Pace {gameData.pace_summary.home_pace_10.toFixed(1)}{" "}
                  {gameData.pace_summary.home_pace_rank
                    ? `(#${gameData.pace_summary.home_pace_rank})`
                    : ""}{" "}
                  · DefRtg {gameData.pace_summary.home_def_rating_10.toFixed(1)}{" "}
                  {gameData.pace_summary.home_def_rank
                    ? `(#${gameData.pace_summary.home_def_rank})`
                    : ""}
                </div>
              </div>
            </div>

            {/* Si no juegan (unified absences bar across both teams) */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-4 py-2.5">
              <div className="space-y-0.5 text-xs">
                <div className="font-semibold text-[var(--text-primary)]">Si no juegan</div>
                <div className="text-[var(--text-secondary)]">
                  {allOutLabels.length > 0 ? (
                    <>Sin {allOutLabels.join(", ")}. Ambas tablas ya recalculan las bajas propias y rivales.</>
                  ) : (
                    <>Sin bajas activas. Tildá OUT en cualquier jugador de ambos equipos para simular.</>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setAwayOutIds(null);
                    setHomeOutIds(null);
                    setAutoApplyInjuries(true);
                  }}
                  className="inline-flex h-7 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2.5 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] cursor-pointer"
                >
                  <RefreshCw className="h-3 w-3" />
                  Pre-cargar lesiones
                </button>
                {allOutLabels.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      setAutoApplyInjuries(false);
                      setAwayOutIds([]);
                      setHomeOutIds([]);
                      setAiByPlayer({});
                    }}
                    className="h-7 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2.5 text-[11px] font-medium text-rose-500 transition-colors hover:border-rose-500/40 cursor-pointer"
                  >
                    Quitar bajas
                  </button>
                ) : null}
              </div>
            </div>

            {/* Stat Columns View Toggle */}
            <div className="flex flex-wrap items-center justify-between gap-2 px-1 pt-1">
              <span className="text-[11px] text-[var(--text-muted)]">
                Hacé clic en cualquier jugador para ver su foto, todas las estadísticas y últimos 15 partidos.
              </span>
              <div className="inline-flex h-7 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] p-0.5 text-[11px]">
                {(
                  [
                    { id: "main", label: "Principales" },
                    { id: "extras", label: "Combos y Defensa" },
                    { id: "all", label: "Todas (14)" },
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setStatView(tab.id)}
                    className={cn(
                      "rounded px-2.5 font-medium transition-colors cursor-pointer",
                      statView === tab.id
                        ? "bg-[var(--accent)] text-white"
                        : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 5. Side-by-Side Boxscore Tables (Visitante | Local) */}
            <div
              className={cn(
                "grid grid-cols-1 gap-6",
                statView === "all" ? "xl:grid-cols-1" : "xl:grid-cols-2"
              )}
            >
              <TeamBoxscoreTable
                teamFullName={awayTeamObj?.full_name ?? gameData.away.team_abbreviation}
                roleLabel="Visitante"
                data={gameData.away}
                statView={statView}
                isB2B={awayB2B}
                onToggleB2B={() => setAwayB2B((v) => !v)}
                onToggleOut={(pid) => toggleOut("away", pid)}
                expandedPlayerId={expandedPlayerId}
                onToggleExpand={(pid) =>
                  setExpandedPlayerId((curr) => (curr === pid ? null : pid))
                }
                onOpenPlayer={(row) =>
                  setModalPlayer({ playerId: row.player_id, side: "away" })
                }
                aiLoadingId={aiLoadingId}
                aiByPlayer={aiByPlayer}
                aiError={aiError}
                onExplainWithAI={handleExplainWithAI}
              />

              <TeamBoxscoreTable
                teamFullName={homeTeamObj?.full_name ?? gameData.home.team_abbreviation}
                roleLabel="Local"
                data={gameData.home}
                statView={statView}
                isB2B={homeB2B}
                onToggleB2B={() => setHomeB2B((v) => !v)}
                onToggleOut={(pid) => toggleOut("home", pid)}
                expandedPlayerId={expandedPlayerId}
                onToggleExpand={(pid) =>
                  setExpandedPlayerId((curr) => (curr === pid ? null : pid))
                }
                onOpenPlayer={(row) =>
                  setModalPlayer({ playerId: row.player_id, side: "home" })
                }
                aiLoadingId={aiLoadingId}
                aiByPlayer={aiByPlayer}
                aiError={aiError}
                onExplainWithAI={handleExplainWithAI}
              />
            </div>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center gap-2 py-12 text-xs text-[var(--text-muted)]">
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
                <span>Cargando partido y reporte oficial de lesiones...</span>
              </>
            ) : (
              "Seleccioná un partido."
            )}
          </div>
        )}
        </>
        ) : null}
      </div>

      {/* Dedicated Player Detail Modal (headshot, 13 stats, splits, 15 game logs, AI) */}
      {gameData && modalPlayer
        ? (() => {
            const teamData = modalPlayer.side === "away" ? gameData.away : gameData.home;
            const row = teamData.rows.find((r) => r.player_id === modalPlayer.playerId);
            if (!row) return null;
            const teamFullName =
              modalPlayer.side === "away"
                ? awayTeamObj?.full_name ?? gameData.away.team_abbreviation
                : homeTeamObj?.full_name ?? gameData.home.team_abbreviation;
            return (
              <PlayerDetailModal
                row={row}
                teamData={teamData}
                teamFullName={teamFullName}
                aiLoading={aiLoadingId === row.player_id}
                aiResponse={aiByPlayer[row.player_id]}
                aiError={aiError}
                onExplainWithAI={handleExplainWithAI}
                onClose={() => setModalPlayer(null)}
              />
            );
          })()
        : null}
    </div>
  );
}

export default App;
