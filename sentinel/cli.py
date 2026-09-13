"""Typer CLI: sentinel audit | index | search | bench | serve | replay | init-db."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from sentinel import __version__
from sentinel.config import get_settings

app = typer.Typer(
    name="sentinel",
    help="Autonomous codebase auditor: finds bugs, proves them with tests, fixes them, opens PRs.",
    no_args_is_help=True,
)
console = Console()

NOT_YET = 2  # exit code for commands whose phase has not landed


def _version(value: bool) -> None:
    if value:
        console.print(f"sentinel {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    """Sentinel CLI."""


@app.command()
def audit(
    repo: Annotated[str, typer.Argument(help="Git URL or local path")],
    pr: Annotated[bool, typer.Option(help="Open pull requests for fixed findings")] = False,
    review: Annotated[bool, typer.Option(help="Human review before PR creation")] = False,
    max_usd: Annotated[float | None, typer.Option(help="Budget cap in USD")] = None,
) -> None:
    """Run the full audit graph on a repository. (Phase 3+)"""
    settings = get_settings()
    budget = max_usd if max_usd is not None else settings.budget.max_usd
    console.print(f"[bold]audit[/] {repo} pr={pr} review={review} budget=${budget}")
    console.print("[yellow]Graph execution lands in Phase 3.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command()
def index(
    repo: Annotated[str, typer.Argument(help="Git URL or local path")],
) -> None:
    """Clone (if needed), extract symbols, chunk, embed, build call graph. (Phase 1)"""
    console.print(f"[bold]index[/] {repo}")
    console.print("[yellow]Indexing lands in Phase 1.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command()
def search(
    query: Annotated[str, typer.Argument()],
    k: Annotated[int, typer.Option(help="Results to return")] = 8,
    mode: Annotated[str, typer.Option(help="hybrid | bm25 | vector")] = "hybrid",
) -> None:
    """Hybrid search over the most recently indexed repo. (Phase 1)"""
    console.print(f"[bold]search[/] {query!r} k={k} mode={mode}")
    console.print("[yellow]Search lands in Phase 1.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command()
def bench(
    suite: Annotated[str, typer.Option(help="small | full")] = "small",
    arm: Annotated[str, typer.Option(help="analyzers | single_shot | no_triage | full")] = "full",
    out: Annotated[Path, typer.Option()] = Path("bench/out"),
) -> None:
    """Run the benchmark harness. (Phase 5)"""
    console.print(f"[bold]bench[/] suite={suite} arm={arm} out={out}")
    console.print("[yellow]Benchmark harness lands in Phase 5.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the FastAPI run viewer. (Phase 6)"""
    console.print(f"[bold]serve[/] {host}:{port}")
    console.print("[yellow]Dashboard lands in Phase 6.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument()],
    from_node: Annotated[str, typer.Option("--from", help="Node to resume from")] = "verify",
) -> None:
    """Resume a checkpointed run from a given node. (Phase 3)"""
    console.print(f"[bold]replay[/] {run_id} from={from_node}")
    console.print("[yellow]Replay lands in Phase 3.[/]")
    raise typer.Exit(code=NOT_YET)


@app.command("init-db")
def init_db_cmd() -> None:
    """Create database tables for the configured DATABASE_URL."""
    from sentinel.db.session import init_db

    init_db()
    console.print(f"[green]tables created[/] at {get_settings().database_url}")


if __name__ == "__main__":
    app()
