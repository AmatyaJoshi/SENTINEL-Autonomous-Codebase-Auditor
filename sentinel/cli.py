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
    """Ingest + tree-sitter symbols + chunks + embeddings + call graph -> index store."""
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
            err.print("[red]nothing indexed yet[/] - run `sentinel index <repo>` first")
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
        console.print(f"[green]SARIF written[/] -> {sarif}")


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


# --------------------------------------------------------------------------- audit / replay / serve


def _manager(settings: Settings):  # type: ignore[no-untyped-def]
    from sentinel.db.session import get_engine
    from sentinel.logging_setup import configure_logging
    from sentinel.runner import RunManager

    configure_logging(settings)
    _boot(settings)
    return RunManager(settings, get_engine(settings))


def _print_run_summary(mgr, run_id: str) -> None:  # type: ignore[no-untyped-def]
    from sentinel.runner import run_to_dict

    run = mgr.get(run_id)
    if run is None:
        return
    d = run_to_dict(run)
    t = Table(title=f"run {run_id}", show_header=False)
    for k in ("status", "repo_url", "commit_sha", "language", "arm", "cost_usd", "counts", "error"):
        t.add_row(k, str(d.get(k)))
    console.print(t)
    findings = mgr.findings(run_id)
    if findings:
        ft = Table(title="findings")
        for c in ("#", "status", "severity", "category", "location", "conf"):
            ft.add_column(c)
        for f in findings:
            ft.add_row(
                str(f.rank or ""),
                f.status,
                f.severity,
                f.category,
                f"{f.file}:{f.line_start}",
                f"{f.confidence:.2f}",
            )
        console.print(ft)
    report = settings_path(mgr.settings) / "runs" / run_id / "report.json"
    if report.exists():
        import json as _json

        errors = _json.loads(report.read_text(encoding="utf-8")).get("errors", [])
        for e in errors[:8]:
            err.print(f"[yellow]error:[/] {e[:300]}")
        if len(errors) > 8:
            err.print(f"[yellow]... {len(errors) - 8} more errors in report.json[/]")
    console.print(f"report: {settings_path(mgr.settings) / 'runs' / run_id / 'report.html'}")


def settings_path(settings: Settings) -> Path:
    return settings.work_dir


@app.command()
def audit(
    repo: Annotated[str, typer.Argument(help="Git URL or local path")],
    pr: Annotated[bool, typer.Option(help="Open pull requests for fixed findings")] = False,
    review: Annotated[bool, typer.Option(help="Pause before report/PR for human review")] = False,
    max_usd: Annotated[float | None, typer.Option(help="Budget cap in USD")] = None,
    max_minutes: Annotated[int | None, typer.Option(help="Wall-clock budget")] = None,
    arm: Annotated[str, typer.Option(help="full | no_triage | single_shot | analyzers")] = "full",
    sha: Annotated[str | None, typer.Option(help="Pin to a commit")] = None,
) -> None:
    """Run the full audit graph: ingest -> index -> analyze -> plan -> hunt -> triage -> verify -> fix ->
    regress -> rank -> report (-> PR)."""
    from sentinel.graph.state import Budget

    settings = get_settings()
    mgr = _manager(settings)
    budget = Budget(
        max_usd=max_usd if max_usd is not None else settings.budget.max_usd,
        max_minutes=max_minutes or settings.budget.max_minutes,
        max_findings=settings.budget.max_findings,
    )
    run = mgr.create(
        repo, budget=budget, arm=arm, open_pr=pr, review=review, created_by="cli", sha=sha
    )
    console.print(f"[bold]audit[/] {repo} run_id={run.id} arm={arm} budget=${budget.max_usd}")
    mgr.buses  # noqa: B018 - ensure manager initialised
    mgr.start(run, block=True)
    _print_run_summary(mgr, run.id)
    final = mgr.get(run.id)
    if final and final.status == "awaiting_review":
        console.print(
            "[yellow]awaiting review[/]: approve/reject findings in the dashboard or via "
            f"`sentinel replay {run.id}` after deciding"
        )
    if final and final.status == "failed":
        raise typer.Exit(code=1)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument()],
    from_node: Annotated[
        str | None, typer.Option("--from", help="Rewind to before this node")
    ] = None,
) -> None:
    """Resume a checkpointed run (optionally rewinding to before a node) and finish it."""
    settings = get_settings()
    mgr = _manager(settings)
    try:
        mgr.resume(run_id, from_node=from_node, block=True)
    except (KeyError, ValueError) as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e
    _print_run_summary(mgr, run_id)


@app.command()
def runs(limit: int = 20) -> None:
    """List recent runs."""
    settings = get_settings()
    mgr = _manager(settings)
    items, total = mgr.list_runs(limit=limit)
    t = Table(title=f"runs ({total})")
    for c in ("id", "status", "repo", "arm", "cost", "started"):
        t.add_column(c)
    for r in items:
        t.add_row(
            r.id[:12],
            r.status,
            r.repo_url[-50:],
            r.arm,
            f"${r.cost_usd:.2f}",
            r.started_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(t)


@app.command()
def bench(
    suite: Annotated[str, typer.Option(help="small | full")] = "small",
    arms: Annotated[
        str, typer.Option(help="comma list: analyzers,single_shot,no_triage,full")
    ] = "analyzers,single_shot,no_triage,full",
    out: Annotated[Path, typer.Option()] = Path("bench/out"),
    inject_only: Annotated[
        bool, typer.Option(help="Only generate mutated repos + manifest")
    ] = False,
) -> None:
    """Run the injected-bug benchmark (SPEC §8) and write report.html/json."""
    from bench.run_bench import main as bench_main

    code = bench_main(
        ["--suite", suite, "--arms", arms, "--out", str(out)]
        + (["--inject-only"] if inject_only else [])
    )
    raise typer.Exit(code=code)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
    reload: bool = False,
) -> None:
    """Start the FastAPI server (API + dashboard)."""
    import uvicorn

    settings = get_settings()
    from sentinel.logging_setup import configure_logging

    configure_logging(settings)
    _boot(settings)
    uvicorn.run(
        "sentinel.api.app:app_factory",
        factory=True,
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command("init-db")
def init_db_cmd() -> None:
    """Create database tables for the configured DATABASE_URL."""
    from sentinel.db.session import init_db

    init_db()
    console.print(f"[green]tables created[/] at {get_settings().database_url}")


if __name__ == "__main__":
    app()
