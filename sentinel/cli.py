"""Typer CLI: sentinel audit | ingest | index | search | analyze | bench | serve | replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sentinel import __version__
from sentinel.config import Settings, get_settings

app = typer.Typer(
    name="sentinel",
    help="Autonomous codebase auditor: finds bugs, proves them with tests, fixes them, opens PRs.",
    no_args_is_help=True,
)
console = Console()
err = Console(stderr=True)

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


def _boot(settings: Settings) -> None:
    from sentinel.telemetry.otel import init_telemetry

    init_telemetry(settings)


# --------------------------------------------------------------------------- phase 1


@app.command()
def ingest(
    repo: Annotated[str, typer.Argument(help="Git URL or local path")],
    sha: Annotated[str | None, typer.Option(help="Pin to a commit")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Clone (if URL) and detect language, package manager and test framework."""
    from sentinel.graph.nodes.ingest import ingest as _ingest

    settings = get_settings()
    _boot(settings)
    res = _ingest(repo, settings, sha)
    if as_json:
        console.print_json(res.model_dump_json(exclude={"repo_config"}))
        return
    t = Table(title="ingest", show_header=False)
    for k in (
        "repo_path",
        "commit_sha",
        "language",
        "package_managers",
        "test_frameworks",
        "test_command",
        "lockfiles",
        "file_counts",
    ):
        t.add_row(k, str(getattr(res, k)))
    console.print(t)


@app.command()
def index(
    repo: Annotated[str, typer.Argument(help="Git URL or local path")],
    sha: Annotated[str | None, typer.Option(help="Pin to a commit")] = None,
) -> None:
    """Ingest + tree-sitter symbols + chunks + embeddings + call graph → index store."""
    from sentinel.graph.nodes.ingest import ingest as _ingest
    from sentinel.indexing.embeddings import HashEmbedder, make_embedder
    from sentinel.indexing.pipeline import index_repo

    settings = get_settings()
    _boot(settings)
    ing = _ingest(repo, settings, sha)
    embedder = make_embedder(settings)
    if isinstance(embedder, HashEmbedder):
        err.print(
            "[yellow]warning:[/] no embedding API key configured "
            "(SENTINEL_OPENAI_API_KEY / SENTINEL_VOYAGE_API_KEY); using the offline lexical "
            "hash embedder. Vector search quality will be lexical only."
        )
    res = index_repo(Path(ing.repo_path), settings, embedder=embedder, commit_sha=ing.commit_sha)
    t = Table(title=f"index {res.repo_id}", show_header=False)
    t.add_row("repo", res.repo_path)
    t.add_row("language", ing.language)
    t.add_row("files", str(res.files))
    t.add_row("symbols", str(res.symbols))
    t.add_row("chunks", str(res.chunks))
    t.add_row("call edges", str(res.edges))
    t.add_row("embedder", res.embedder)
    t.add_row("duration", f"{res.duration_s:.1f}s")
    console.print(t)
    if res.skipped:
        err.print(f"[yellow]{len(res.skipped)} files skipped[/] (first: {res.skipped[0]})")


@app.command()
def search(
    query: Annotated[str, typer.Argument()],
    k: Annotated[int, typer.Option("--k", "-k", help="Results to return")] = 8,
    mode: Annotated[str, typer.Option(help="hybrid | bm25 | vector")] = "hybrid",
    repo_id: Annotated[str | None, typer.Option(help="Defaults to last indexed repo")] = None,
    full: Annotated[bool, typer.Option(help="Print whole chunks")] = False,
) -> None:
    """Hybrid (BM25 + vector, RRF) search over an indexed repo."""
    from sentinel.indexing.embeddings import make_embedder
    from sentinel.indexing.pipeline import read_last_index
    from sentinel.indexing.store import hybrid_search, open_store

    if mode not in ("hybrid", "bm25", "vector"):
        raise typer.BadParameter("mode must be hybrid | bm25 | vector")
    settings = get_settings()
    _boot(settings)
    if repo_id is None:
        last = read_last_index(settings.work_dir)
        if last is None:
            err.print("[red]nothing indexed yet[/] — run `sentinel index <repo>` first")
            raise typer.Exit(code=1)
        repo_id = last[0]
    embedder = make_embedder(settings)
    store = open_store(settings.database_url, settings.work_dir, embedder.dim or 256)
    meta = store.get_meta(repo_id)
    if meta is None:
        err.print(f"[red]unknown repo_id {repo_id}[/]")
        raise typer.Exit(code=1)
    if meta.get("embedder") != embedder.name and mode != "bm25":
        err.print(
            f"[yellow]warning:[/] index built with {meta.get('embedder')} but current embedder "
            f"is {embedder.name}; falling back to bm25"
        )
        mode = "bm25"
    hits = hybrid_search(store, embedder, repo_id, query, k=k, mode=mode)  # type: ignore[arg-type]
    store.close()
    if not hits:
        console.print("[yellow]no results[/]")
        return
    for i, h in enumerate(hits, 1):
        console.rule(
            f"[bold]{i}. {h.location}[/]  {h.symbol or ''}  "
            f"[dim]{'+'.join(h.sources)} {h.score:.4f}[/]"
        )
        text = h.text if full else "\n".join(h.text.split("\n")[:12])
        console.print(text, markup=False, highlight=False)


@app.command()
def analyze(
    repo: Annotated[str, typer.Argument(help="Local path (or URL, cloned first)")],
    sarif: Annotated[Path | None, typer.Option(help="Write SARIF 2.1.0 to this file")] = None,
    no_semgrep: Annotated[bool, typer.Option("--no-semgrep")] = False,
    tools: Annotated[str | None, typer.Option(help="Comma list, e.g. ruff,bandit")] = None,
) -> None:
    """Run static analyzers in parallel and print SARIF-normalised findings."""
    from sentinel.analyzers.base import run_all, to_sarif
    from sentinel.analyzers.registry import default_adapters
    from sentinel.graph.nodes.ingest import ingest as _ingest

    settings = get_settings()
    _boot(settings)
    ing = _ingest(repo, settings)
    adapters = default_adapters(include_semgrep=not no_semgrep)
    if tools:
        wanted = {t.strip() for t in tools.split(",")}
        adapters = [a for a in adapters if a.name in wanted]
    results = run_all(Path(ing.repo_path), ing.language, adapters)

    t = Table(title=f"analyze {ing.repo_path} ({ing.language})")
    t.add_column("tool")
    t.add_column("status")
    t.add_column("findings", justify="right")
    t.add_column("time", justify="right")
    for r in results:
        status = (
            r.skipped_reason
            and f"[dim]skipped: {r.skipped_reason}[/]"
            or ("[green]ok[/]" if r.ok else f"[red]error: {r.error}[/]")
        )
        t.add_row(r.tool, status, str(len(r.findings)), f"{r.duration_s:.1f}s")
    console.print(t)

    by_cat: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            by_cat[f.category_hint or "uncategorised"] = (
                by_cat.get(f.category_hint or "uncategorised", 0) + 1
            )
    if by_cat:
        console.print("category hints:", dict(sorted(by_cat.items(), key=lambda kv: -kv[1])))

    if sarif:
        sarif.parent.mkdir(parents=True, exist_ok=True)
        sarif.write_text(json.dumps(to_sarif(results), indent=2), encoding="utf-8")
        console.print(f"[green]SARIF written[/] → {sarif}")


@app.command()
def symbol(
    name: Annotated[str, typer.Argument(help="Symbol name, e.g. Auth.parse_token")],
    repo_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Show a symbol's definition, plus callers/callees from the call graph."""
    from sentinel.indexing.callgraph import CallGraph
    from sentinel.indexing.pipeline import read_last_index
    from sentinel.indexing.store import open_store

    settings = get_settings()
    if repo_id is None:
        last = read_last_index(settings.work_dir)
        if last is None:
            err.print("[red]nothing indexed yet[/]")
            raise typer.Exit(code=1)
        repo_id = last[0]
    store = open_store(settings.database_url, settings.work_dir, 256)
    meta = store.get_meta(repo_id) or {}
    recs = store.get_symbol(repo_id, name)
    store.close()
    if not recs:
        console.print("[yellow]no such symbol[/]")
        raise typer.Exit(code=1)
    cg = CallGraph.load(Path(meta["callgraph_path"])) if meta.get("callgraph_path") else None
    for r in recs:
        console.rule(f"[bold]{r.kind} {r.name}[/]  {r.file}:{r.line_start}-{r.line_end}")
        if r.docstring:
            console.print(f"[dim]{r.docstring}[/]")
        console.print(r.text, markup=False, highlight=False)
        if cg:
            qid = f"{r.file}::{r.name}"
            console.print(f"callers ({cg.blast_radius(qid)} transitive): {cg.callers(qid)}")
            console.print(f"callees: {cg.callees(qid)}")


# --------------------------------------------------------------------------- later phases


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
