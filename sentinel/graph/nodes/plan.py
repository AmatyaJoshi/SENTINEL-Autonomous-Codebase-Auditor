"""Plan node: repo map + analyzer seeds + churn → ranked HuntTargets (LLM with heuristic fallback)."""

from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from sentinel.graph.state import AuditState, HuntTarget
from sentinel.indexing.pipeline import discover_files
from sentinel.indexing.treesitter import parse_file
from sentinel.llm.prompts import load_prompt
from sentinel.llm.schemas import PlanOutput
from sentinel.tools.context import RunContext

STRONG = {
    "security_smell": 3.0,
    "null_deref": 2.5,
    "race_condition": 2.5,
    "unhandled_exception": 2.0,
    "resource_leak": 2.0,
    "logic_error": 1.5,
    "type_error": 1.2,
    "api_misuse": 1.2,
    "off_by_one": 1.5,
    "perf": 0.8,
    "dead_code": 0.3,
    None: 1.0,
}
TEST_MARKERS = (
    "test_",
    "_test.",
    ".test.",
    ".spec.",
    "/tests/",
    "/test/",
    "/__tests__/",
    "conftest",
)
SKIP_MARKERS = ("migrations/", "/generated/", "/vendor/", "__init__.py", ".d.ts", "/fixtures/")


def git_churn(repo_path: Path, days: int = 180) -> dict[str, int]:
    try:
        out = subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(repo_path),
                "log",
                f"--since={days}.days",
                "--name-only",
                "--pretty=format:",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    return dict(Counter(ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()))


def is_test_file(path: str) -> bool:
    p = "/" + path.lower()
    return any(m in p for m in TEST_MARKERS)


def heuristic_scores(
    ctx: RunContext, files: list[str]
) -> list[tuple[str, float, dict[str, float]]]:
    """risk = churn × complexity × analyzer hits × (no test) — SPEC §3.2 plan heuristics."""
    churn = git_churn(ctx.repo_path)
    hits: dict[str, float] = defaultdict(float)
    for f in ctx.analyzer_findings:
        hits[f.file] += STRONG.get(f.category_hint, 1.0)
    test_stems = {
        Path(f)
        .stem.replace("test_", "")
        .replace("_test", "")
        .replace(".test", "")
        .replace(".spec", "")
        for f in files
        if is_test_file(f)
    }
    scored = []
    for rel in files:
        if is_test_file(rel) or any(m in "/" + rel for m in SKIP_MARKERS):
            continue
        pf = parse_file(ctx.repo_path, rel)
        if pf is None or not pf.symbols:
            continue
        loc = pf.n_lines
        fn_lines = [
            s.line_end - s.line_start + 1 for s in pf.symbols if s.kind in ("function", "method")
        ]
        complexity = 1.0 + (max(fn_lines) / 40.0 if fn_lines else 0.0) + len(pf.symbols) / 25.0
        churn_f = 1.0 + min(churn.get(rel, 0), 20) / 5.0
        hit_f = 1.0 + min(hits.get(rel, 0.0), 15.0) / 3.0
        untested = 1.6 if Path(rel).stem not in test_stems else 1.0
        blast = 1.0
        if ctx.callgraph is not None:
            callers = sum(len(ctx.callgraph.callers(s.qualified_id)) for s in pf.symbols[:30])
            blast = 1.0 + min(callers, 30) / 15.0
        score = churn_f * complexity * hit_f * untested * blast * min(1.0, loc / 60.0 + 0.2)
        scored.append(
            (
                rel,
                score,
                {
                    "churn": churn.get(rel, 0),
                    "hits": hits.get(rel, 0.0),
                    "complexity": round(complexity, 2),
                    "untested": untested != 1.0,
                    "blast": round(blast, 2),
                    "loc": loc,
                },
            )
        )
    scored.sort(key=lambda t: -t[1])
    return scored


def build_repo_map(
    ctx: RunContext, scored: list[tuple[str, float, dict[str, float]]], limit: int
) -> str:
    rows = []
    for rel, score, meta in scored[:limit]:
        pf = parse_file(ctx.repo_path, rel)
        syms = ", ".join(s.name for s in (pf.symbols if pf else [])[:12] if s.parent is None)
        rows.append(f"{rel} | {int(meta['loc'])} lines | risk≈{score:.1f} | {syms}")
    return "\n".join(rows)


def plan_node(state: AuditState, ctx: RunContext) -> AuditState:
    files = discover_files(ctx.repo_path)
    scored = heuristic_scores(ctx, files)
    max_targets = ctx.settings.plan_max_targets
    if not scored:
        return {"plan": [], "errors": ["plan: no analysable source files"]}

    analyzer_summary = defaultdict(Counter)  # type: ignore[var-annotated]
    for f in ctx.analyzer_findings:
        analyzer_summary[f.file][f.category_hint or "other"] += 1
    summary_lines = [
        f"{p}: " + ", ".join(f"{c}={n}" for c, n in cnt.most_common(6))
        for p, cnt in list(analyzer_summary.items())[:80]
    ]
    churn = git_churn(ctx.repo_path)
    churn_lines = [f"{p}: {n}" for p, n in sorted(churn.items(), key=lambda kv: -kv[1])[:60]]

    prompt = load_prompt("plan")
    text = prompt.render(
        language=ctx.language,
        max_targets=max_targets,
        repo_map=build_repo_map(ctx, scored, 120),
        analyzer_summary="\n".join(summary_lines) or "none",
        churn_table="\n".join(churn_lines) or "no git history",
    )
    targets: list[HuntTarget] = []
    try:
        out, _ = ctx.router.structured(
            PlanOutput, text, tier="strong", prompt_name="plan", prompt_version=prompt.version
        )
        valid = {rel for rel, _, _ in scored}
        for t in out.targets:
            rel = t.file.replace("\\", "/").removeprefix("./")
            if rel in valid:
                hits = sum(1 for f in ctx.analyzer_findings if f.file == rel)
                targets.append(
                    HuntTarget(
                        file=rel,
                        symbol=t.symbol,
                        reason=t.reason,
                        risk_score=t.risk_score,
                        analyzer_hits=hits,
                    )
                )
    except Exception as e:  # noqa: BLE001 - fall back to heuristics; never fail silently
        ctx.log(f"plan LLM failed ({type(e).__name__}: {e}); using heuristic plan", level="warning")
    if not targets:
        for rel, score, meta in scored[:max_targets]:
            targets.append(
                HuntTarget(
                    file=rel,
                    reason=f"heuristic: churn={meta['churn']} hits={meta['hits']:.0f} "
                    f"complexity={meta['complexity']} untested={meta['untested']}",
                    risk_score=min(10.0, score),
                    analyzer_hits=int(meta["hits"]),
                )
            )
    targets = targets[:max_targets]
    ctx.log(f"plan: {len(targets)} targets → " + ", ".join(t.file for t in targets[:6]))
    return {"plan": targets}
