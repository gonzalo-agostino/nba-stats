"""Command-line interface for nba-stats."""

from __future__ import annotations

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from nba_stats.agent.graph import run_agent_query
from nba_stats.config import (
    Settings,
    load_features_config,
    load_ingestion_config,
    load_training_config,
)
from nba_stats.data.ingest import run_ingestion
from nba_stats.data.repository import NBARepository
from nba_stats.features.pipeline import build_and_save_features
from nba_stats.ml.train import train_and_backtest
from nba_stats.simulator.schemas import ScenarioRequest
from nba_stats.simulator.what_if import EntityResolutionError, WhatIfSimulator

app = typer.Typer(
    name="nba-stats",
    help="NBA player stats prediction pipeline (DuckDB + LightGBM + LangGraph).",
    no_args_is_help=True,
)
console = Console()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@app.command()
def ingest(
    seasons: Annotated[
        list[str] | None,
        typer.Option(
            "--seasons",
            "-s",
            help="Temporadas a ingestar (ej: --seasons 2024-25 --seasons 2025-26).",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Forzar re-descarga incluso en temporadas ya cacheadas."),
    ] = False,
    skip_advanced: Annotated[
        bool,
        typer.Option("--skip-advanced", help="Omitir llamadas de MeasureType=Advanced."),
    ] = False,
) -> None:
    """Descarga incrementalmente game logs y rosters desde nba_api hacia Parquet + DuckDB."""
    settings = Settings()
    _configure_logging(settings.nba_stats_log_level)
    cfg = load_ingestion_config(settings)
    target_seasons = seasons or cfg.default_seasons

    console.print(
        f"[bold cyan]Iniciando ingesta NBA[/bold cyan] | temporadas={target_seasons} | "
        f"force={force} | advanced={not skip_advanced}"
    )

    report = run_ingestion(
        seasons=target_seasons,
        force=force,
        include_advanced=not skip_advanced,
        settings=settings,
        config=cfg,
    )

    table = Table(title="Resumen de Ingesta")
    table.add_column("Temporada", style="cyan")
    table.add_column("Estado")
    table.add_column("Desde", justify="center")
    table.add_column("Players", justify="right")
    table.add_column("Team Logs", justify="right")
    table.add_column("Player Logs", justify="right")
    table.add_column("Games", justify="right")

    for s in report.seasons:
        status_label = "[yellow]Skipped (caché)[/yellow]" if s.skipped else "[green]OK[/green]"
        from_label = str(s.date_from) if s.date_from else "full"
        table.add_row(
            s.season,
            status_label,
            from_label,
            str(s.players_rows),
            str(s.team_logs_rows),
            str(s.player_logs_rows),
            str(s.games_rows),
        )

    console.print(table)
    console.print(
        f"Equipos cargados: [bold]{report.teams_rows}[/bold] | "
        f"Requests HTTP a stats.nba.com: [bold]{report.api_requests}[/bold] | "
        f"DuckDB: [dim]{settings.duckdb_path}[/dim]"
    )


@app.command()
def status() -> None:
    """Muestra el estado actual de tablas y cobertura de fechas en DuckDB."""
    settings = Settings()
    repo = NBARepository(settings=settings)

    counts_df = repo.get_table_counts()
    counts_table = Table(title=f"Warehouse DuckDB ({settings.duckdb_path})")
    counts_table.add_column("Tabla", style="cyan")
    counts_table.add_column("Filas", justify="right")
    for _, row in counts_df.iterrows():
        counts_table.add_row(str(row["table"]), f"{int(row['rows']):,}")
    console.print(counts_table)

    coverage_df = repo.get_season_coverage()
    if coverage_df.empty:
        console.print("[yellow]No hay player_game_logs cargados todavía. Ejecuta `nba-stats ingest`.[/yellow]")
        return

    cov_table = Table(title="Cobertura por Temporada")
    cov_table.add_column("Temporada", style="cyan")
    cov_table.add_column("Partidos", justify="right")
    cov_table.add_column("Player Logs", justify="right")
    cov_table.add_column("Fecha Min", justify="center")
    cov_table.add_column("Fecha Max", justify="center")
    for _, row in coverage_df.iterrows():
        cov_table.add_row(
            str(row["season"]),
            f"{int(row['games']):,}",
            f"{int(row['player_logs']):,}",
            str(row["min_date"])[:10],
            str(row["max_date"])[:10],
        )
    console.print(cov_table)


@app.command()
def player(
    name: Annotated[str, typer.Argument(help="Nombre (o parte del nombre) del jugador.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Cantidad de partidos recientes.")] = 10,
    season: Annotated[str | None, typer.Option("--season", "-s", help="Filtrar por temporada.")] = None,
) -> None:
    """Consulta los últimos partidos de un jugador desde la caché local de DuckDB."""
    repo = NBARepository()
    logs = repo.get_player_recent_logs(name_query=name, limit=limit, season=season)
    if logs.empty:
        console.print(f"[yellow]No se encontraron game logs para '{name}'.[/yellow]")
        return

    table = Table(title=f"Últimos {len(logs)} partidos — '{name}'")
    for col in ["game_date", "player_name", "matchup", "wl", "min", "pts", "reb", "ast", "pra", "fga", "usg_pct"]:
        table.add_column(col.upper(), justify="right" if col not in {"game_date", "player_name", "matchup", "wl"} else "left")

    for _, row in logs.iterrows():
        usg = f"{row['usg_pct'] * 100:.1f}%" if row["usg_pct"] == row["usg_pct"] else "-"
        table.add_row(
            str(row["game_date"])[:10],
            str(row["player_name"]),
            str(row["matchup"]),
            str(row["wl"]),
            f"{row['min']:.1f}",
            f"{row['pts']:.0f}",
            f"{row['reb']:.0f}",
            f"{row['ast']:.0f}",
            f"{row['pra']:.0f}",
            f"{row['fga']:.0f}",
            usg,
        )
    console.print(table)


@app.command(name="build-features")
def build_features() -> None:
    """Genera el dataset de features anti-leakage (fatiga, rolling, matchup, WOWY) en Parquet."""
    settings = Settings()
    _configure_logging(settings.nba_stats_log_level)
    feat_cfg = load_features_config(settings)

    console.print("[bold cyan]Construyendo features anti-leakage desde DuckDB...[/bold cyan]")
    df, out_path = build_and_save_features(settings=settings, config=feat_cfg)
    console.print(
        f"[green]OK[/green] | Filas: [bold]{len(df):,}[/bold] | "
        f"Columnas: [bold]{len(df.columns)}[/bold] | Archivo: [dim]{out_path}[/dim]"
    )


@app.command()
def train(
    rebuild_features: Annotated[
        bool,
        typer.Option("--rebuild-features", help="Reconstruir features.parquet antes de entrenar."),
    ] = False,
) -> None:
    """Entrena el modelo LightGBM en 2 etapas con backtesting temporal vs baseline de 10 partidos."""
    settings = Settings()
    _configure_logging(settings.nba_stats_log_level)
    train_cfg = load_training_config(settings)

    feat_path = settings.processed_dir / "features.parquet"
    if rebuild_features or not feat_path.exists():
        console.print("[cyan]Generando dataset de features...[/cyan]")
        feat_cfg = load_features_config(settings)
        build_and_save_features(settings=settings, config=feat_cfg)

    console.print("[bold cyan]Entrenando LightGBM en 2 etapas (MIN -> PTS, REB, AST -> PRA)...[/bold cyan]")
    result = train_and_backtest(settings=settings, config=train_cfg)

    for slice_name, label in [
        ("all", "Backtesting Temporal — Todos los partidos"),
        ("b2b", "Slice: Back-to-Back (is_back_to_back = 1)"),
        ("star_out", "Slice: Compañero Estrella Ausente (star_teammate_out = 1)"),
    ]:
        table = Table(title=label)
        table.add_column("Target", style="cyan")
        table.add_column("N", justify="right")
        table.add_column("Model MAE", justify="right", style="green")
        table.add_column("Base10 MAE", justify="right")
        table.add_column("Mejora MAE", justify="right", style="bold")
        table.add_column("Model RMSE", justify="right")
        table.add_column("Base10 RMSE", justify="right")

        for target, m in result.metrics[slice_name].items():
            imp = m["mae_improvement_pct"]
            imp_str = f"[green]+{imp:.2f}%[/green]" if imp >= 0 else f"[red]{imp:.2f}%[/red]"
            table.add_row(
                target.upper(),
                f"{int(m['n']):,}",
                f"{m['model_mae']:.3f}",
                f"{m['baseline_mae']:.3f}",
                imp_str,
                f"{m['model_rmse']:.3f}",
                f"{m['baseline_rmse']:.3f}",
            )
        console.print(table)

    console.print(
        f"Modelos guardados en: [dim]{result.models_dir}[/dim] | "
        f"OOF backtest: [dim]{result.oof_path}[/dim]"
    )


@app.command()
def simulate(
    player_name: Annotated[str, typer.Argument(help="Nombre del jugador a proyectar.")],
    opp: Annotated[str | None, typer.Option("--opp", "-o", help="Equipo rival (ej. LAL, BOS).")] = None,
    out: Annotated[
        list[str] | None,
        typer.Option("--out", help="Compañero OUT por lesión/suspensión (repetible)."),
    ] = None,
    opp_out: Annotated[
        list[str] | None,
        typer.Option("--opp-out", help="Jugador del RIVAL OUT por lesión/suspensión (repetible)."),
    ] = None,
    b2b: Annotated[bool, typer.Option("--b2b", help="Activar escenario back-to-back.")] = False,
    away: Annotated[bool, typer.Option("--away", help="Marcar partido como visitante.")] = False,
    rest_days: Annotated[int | None, typer.Option("--rest", help="Días de descanso (0-3+).")] = None,
) -> None:
    """Simulador What-If determinista (puro ML + SHAP + WOWY + Defensa Rival, costo LLM $0)."""
    settings = Settings()
    sim = WhatIfSimulator(settings=settings)
    req = ScenarioRequest(
        player_name=player_name,
        opponent_team=opp,
        out_players=out or [],
        opponent_out_players=opp_out or [],
        is_back_to_back=b2b,
        rest_days=rest_days,
        is_home=not away,
    )
    try:
        res = sim.simulate(req)
    except EntityResolutionError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1) from exc

    flags = ["B2B" if res.is_back_to_back else f"Rest={res.rest_days}d", "Local" if res.is_home else "Visitante"]
    if res.out_players_resolved:
        flags.append(f"OUT: {', '.join(res.out_players_resolved)}")
    if res.opp_out_players_resolved:
        flags.append(f"Rival OUT: {', '.join(res.opp_out_players_resolved)}")

    table = Table(
        title=f"{res.player_name} ({res.team_abbreviation}) vs {res.opponent_abbreviation} — {' | '.join(flags)}"
    )
    table.add_column("Stat", style="cyan")
    table.add_column("Base", justify="right")
    table.add_column("Escenario", justify="right", style="bold")
    table.add_column("Delta", justify="right")

    for stat in ["min", "pts", "reb", "ast", "pra", "pr", "pa", "stl", "blk", "tov", "pf", "fga", "ftm"]:
        b = getattr(res.base_projection, stat)
        s = getattr(res.scenario_projection, stat)
        d = getattr(res.deltas, stat)
        d_str = f"[green]{d:+.2f}[/green]" if d > 0 else (f"[red]{d:+.2f}[/red]" if d < 0 else "0.00")
        table.add_row(stat.upper(), f"{b:.2f}", f"{s:.2f}", d_str)
    console.print(table)

    if res.opponent_defense is not None:
        od = res.opponent_defense
        def_rank_str = f"#{od.def_rating_rank}" if od.def_rating_rank else "-"
        pace_rank_str = f"#{od.pace_rank}" if od.pace_rank else "-"
        console.print(
            f"[cyan]Defensa rival ({od.team_abbreviation}):[/cyan] "
            f"DefRtg {od.def_rating_10:.1f} ({def_rank_str}) | Pace {od.pace_10:.1f} ({pace_rank_str}) | "
            f"eFG% perm. {od.efg_allowed_10 * 100:.1f}% | NetRtg diff {od.net_rating_diff:+.1f} | "
            f"Rival OUT: {od.opp_missing_minutes:.1f} min ({od.opp_missing_def_stocks:.1f} stocks)"
        )

    if res.out_players_unresolved:
        console.print(f"[yellow]No resueltos:[/yellow] {', '.join(res.out_players_unresolved)}")

    console.print(
        f"Contexto ausencias: [bold]{res.missing_team_minutes:.1f} min[/bold] | "
        f"USG ausente: [bold]{res.missing_team_usage_pct * 100:.1f}%[/bold] | "
        f"Star OUT: [bold]{res.star_teammate_out}[/bold]"
    )

    if res.wowy_context is not None:
        w = res.wowy_context
        console.print(
            f"[cyan]Historial WOWY sin {w.primary_out_player}[/cyan] "
            f"(N={w.sample_games_without} sin / {w.sample_games_with} con): "
            f"MIN {w.raw_delta_min:+.2f} | PTS {w.raw_delta_pts:+.2f} | "
            f"REB {w.raw_delta_reb:+.2f} | AST {w.raw_delta_ast:+.2f}"
        )

    for stat in ["min", "pts"]:
        drivers = res.shap_drivers.get(stat, [])[:4]
        if drivers:
            drv_str = ", ".join(f"{d.feature} ({d.shap_value:+.2f})" for d in drivers)
            console.print(f"[dim]Top SHAP {stat.upper()}:[/dim] {drv_str}")


@app.command()
def ask(
    query: Annotated[str, typer.Argument(help="Consulta en lenguaje natural sobre un escenario NBA.")],
) -> None:
    """Ejecuta el agente completo LangGraph + Vertex AI (parse -> simulate -> explain -> verify)."""
    settings = Settings()
    _configure_logging(settings.nba_stats_log_level)
    state = run_agent_query(query=query, settings=settings)
    console.print(Markdown(state.get("final_response", "Sin respuesta.")))


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Host de escucha para FastAPI.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Puerto HTTP.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Recarga automática en desarrollo.")] = False,
) -> None:
    """Levanta la API FastAPI (uvicorn) para el frontend web."""
    import uvicorn

    console.print(f"[bold cyan]Levantando NBA Stats API[/bold cyan] en http://{host}:{port}")
    uvicorn.run("nba_stats.api.app:app", host=host, port=port, reload=reload)
