"""Benchmark harness (SPEC §8): inject → run 4 comparison arms → score → report.html/json/RESULTS.md.

    uv run python bench/run_bench.py --suite small
    uv run python bench/run_bench.py --suite small --arms analyzers,single_shot --inject-only

Arms:
  analyzers    static analyzers only (no LLM, no verify)
  single_shot  one LLM call per file, no tools, no verify  ← "everyone else's project"
  no_triage    Sentinel without the triage classifier
  full         Sentinel
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.inject.injector import Injected, Manifest, inject  # noqa: E402
from bench.score import ArmScore, Reported, from_findings, score_instance  # noqa: E402
from sentinel.analyzers.base import run_all  # noqa: E402
from sentinel.analyzers.registry import default_adapters  # noqa: E402
from sentinel.config import Settings, get_settings  # noqa: E402
from sentinel.graph.nodes.ingest import clone_or_update, ingest  # noqa: E402
from sentinel.sandbox.docker_runner import (  # noqa: E402
    DockerRunner,
    SandboxUnavailableError,
    make_workspace,
)
from sentinel.sandbox.test_runner import TestReport, run_tests  # noqa: E402

ARMS = ("analyzers", "single_shot", "no_triage", "full")
TEMPLATE = Path(__file__).parent / "report_template.html"


# --------------------------------------------------------------------------- executors
def docker_executor(settings: Settings, language: str, work: Path):  # type: ignore[no-untyped-def]
    runner = DockerRunner(settings.sandbox)
    if not runner.available():
        raise SandboxUnavailableError("Docker engine required to confirm mutants kill tests")

    def coverage(repo: Path) -> None:
        from bench.inject.coverage import node_coverage_command, python_coverage_command

        ing = ingest(str(repo), settings)
        image = runner.build_repo_image(
            repo, language, ing.lockfile_hash, list(ing.package_managers), work / "img"
        )
        ws = make_workspace(repo, work / "cov")
        fw = (
            ing.test_frameworks[0]
            if ing.test_frameworks and ing.test_frameworks[0] != "unknown"
            else "pytest"
        )
        cmd = python_coverage_command() if language == "python" else node_coverage_command(fw)
        runner.run(image, ws, cmd, timeout_s=settings.sandbox.suite_timeout_s)
        for rel in (".sentinel/coverage.json", "coverage/lcov.info"):
            src = ws / rel
            if src.exists():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_bytes(src.read_bytes())

    def run(repo: Path, paths: list[str] | None) -> TestReport:
        ing = ingest(str(repo), settings)
        image = runner.build_repo_image(
            repo, language, ing.lockfile_hash, list(ing.package_managers), work / "img"
        )
        ws = make_workspace(repo, work / "ws")
        fw = (
            ing.test_frameworks[0]
            if ing.test_frameworks and ing.test_frameworks[0] != "unknown"
            else ("pytest" if language == "python" else "vitest")
        )
        return run_tests(
            runner, image, ws, fw, paths, timeout_s=settings.sandbox.suite_timeout_s
        ).report  # type: ignore[arg-type]

    run.coverage = coverage  # type: ignore[attr-defined]
    return run


# --------------------------------------------------------------------------- arms
def arm_analyzers(
    repo: Path, language: str, settings: Settings
) -> tuple[list[Reported], dict[str, Any]]:
    t0 = time.time()
    results = run_all(repo, language, default_adapters(include_semgrep=settings.analyzers_semgrep))
    reported = [
        Reported(f.file, f.line_start, f.line_end, f.category_hint, "verified")
        for r in results
        for f in r.findings
        if f.category_hint is not None
    ]
    return reported, {
        "candidates": len(reported),
        "verified": 0,
        "fixed": 0,
        "cost_usd": 0.0,
        "wall_clock_s": time.time() - t0,
    }


def arm_single_shot(
    repo: Path, language: str, settings: Settings, backend: Any = None, max_files: int = 12
) -> tuple[list[Reported], dict[str, Any]]:
    from sentinel.graph.nodes.plan import is_test_file
    from sentinel.indexing.pipeline import discover_files
    from sentinel.llm.prompts import load_prompt
    from sentinel.llm.router import LLMRouter
    from sentinel.llm.schemas import HuntOutput

    t0 = time.time()
    router = LLMRouter(settings, backend=backend)
    prompt = load_prompt("single_shot_review")
    files = [f for f in discover_files(repo) if not is_test_file(f)]
    files.sort(key=lambda f: -(repo / f).stat().st_size)
    reported: list[Reported] = []
    for rel in files[:max_files]:
        lines = (repo / rel).read_text(encoding="utf-8", errors="replace").split("\n")
        numbered = "\n".join(f"{i:5d} | {ln}" for i, ln in enumerate(lines[:600], 1))
        try:
            out, _ = router.structured(
                HuntOutput,
                prompt.render(language=language, file=rel, source_numbered=numbered),
                tier="strong",
                prompt_name="single_shot_review",
                prompt_version=prompt.version,
            )
        except Exception:  # noqa: BLE001
            continue
        reported += [
            Reported(rel, h.line_start, h.line_end, h.category, "verified") for h in out.findings
        ]
    return reported, {
        "candidates": len(reported),
        "verified": 0,
        "fixed": 0,
        "cost_usd": router.total_cost_usd,
        "wall_clock_s": time.time() - t0,
    }


def arm_sentinel(
    repo: Path, arm: str, settings: Settings, manager: Any = None
) -> tuple[list[Reported], dict[str, Any]]:
    from sentinel.db.session import get_engine
    from sentinel.runner import RunManager

    mgr = manager or RunManager(settings, get_engine(settings))
    t0 = time.time()
    run = mgr.create(str(repo), arm=arm, created_by="bench")
    mgr.start(run, block=True)
    fs = [f.model_dump() for f in mgr.findings(run.id)]
    final = mgr.get(run.id)
    verified = sum(1 for f in fs if f["status"] in ("verified", "fixed", "pr_opened", "regressed"))
    fixed = sum(1 for f in fs if f["status"] in ("fixed", "pr_opened"))
    return from_findings(fs), {
        "candidates": len(fs),
        "verified": verified,
        "fixed": fixed,
        "cost_usd": final.cost_usd if final else 0.0,
        "wall_clock_s": time.time() - t0,
        "run_id": run.id,
    }


# --------------------------------------------------------------------------- report
def render_report(summary: dict[str, Any]) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    return tpl.replace("{{SUMMARY_JSON}}", json.dumps(summary).replace("</", "<\\/"))


def append_results_md(summary: dict[str, Any], commit: str, path: Path | None = None) -> None:
    path = path or ROOT / "bench" / "RESULTS.md"
    rows = []
    for arm in summary["arms"].values():
        rows.append(
            f"| {summary['date']} | {commit[:8]} | {summary['suite']} | {arm['arm']} | "
            f"{_pct(arm['precision'])} / {_pct(arm['precision_strict'])} | {_pct(arm['recall'])} | {_pct(arm['f1'])} | "
            f"{_pct(arm['verified_rate'])} | {_pct(arm['patch_pass_rate'])} | {arm['cost_per_repo'] or '-'} | {arm['minutes_per_repo'] or '-'} |"
        )
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = text.replace("| — | — | — | — | — | — | — | — | — | — | — |\n", "")
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=True
        ).stdout.strip()  # noqa: S603, S607
    except Exception:  # noqa: BLE001
        return "unknown"


# --------------------------------------------------------------------------- main
def run_suite(
    suite: str,
    arms: list[str],
    out: Path,
    settings: Settings,
    *,
    inject_only: bool = False,
    executor_factory: Any = None,
    backend: Any = None,
    manager_factory: Any = None,
    dataset: dict[str, Any] | None = None,
    results_md: Path | None = None,
) -> dict[str, Any]:
    dataset = dataset or yaml.safe_load(
        (ROOT / "bench" / "datasets" / f"{suite}.yaml").read_text(encoding="utf-8")
    )
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    all_instances: list[Injected] = []
    for repo_cfg in dataset["repos"]:
        name, language = repo_cfg["name"], repo_cfg["language"]
        if repo_cfg.get("path"):
            clean, sha = Path(repo_cfg["path"]), repo_cfg.get("sha")
        else:
            clean, sha = clone_or_update(repo_cfg["url"], settings.work_dir, repo_cfg.get("sha"))
        exec_fn = (executor_factory or docker_executor)(
            settings, language, out / "exec" / name.replace("/", "__")
        )
        manifest = inject(
            clean,
            out / "mutants",
            repo_name=name,
            commit_sha=sha,
            language=language,
            n=int(repo_cfg.get("mutations", dataset.get("mutations_per_repo", 6))),
            seed=int(dataset.get("seed", 0)),
            executor=exec_fn,
            suite=suite,
            coverage_runner=getattr(exec_fn, "coverage", None),
        )
        manifest.save(out / "manifests" / f"{name.replace('/', '__')}.json")
        all_instances += manifest.instances
        print(
            f"[inject] {name}: {len(manifest.instances)} mutants "
            f"(rejected equivalent={manifest.rejected_equivalent}, broken={manifest.rejected_broken})"
        )
    combined = Manifest(suite=suite, created_at=started, instances=all_instances)
    combined.save(out / "manifest.json")
    summary: dict[str, Any] = {
        "suite": suite,
        "date": time.strftime("%Y-%m-%d"),
        "commit": _git_head(),
        "instances": len(all_instances),
        "arms": {},
        "per_instance": [],
        "models": {
            "primary": settings.primary_model,
            "cheap": settings.cheap_model,
            "embedding": settings.embedding_model,
        },
        "seed": dataset.get("seed"),
    }
    if inject_only:
        (out / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    scores = {arm: ArmScore(arm) for arm in arms}
    for inst in all_instances:
        repo = Path(inst.mutated_repo_path)
        for arm in arms:
            if arm == "analyzers":
                reported, meta = arm_analyzers(repo, inst.language, settings)
            elif arm == "single_shot":
                reported, meta = arm_single_shot(repo, inst.language, settings, backend=backend)
            else:
                mgr = manager_factory(settings) if manager_factory else None
                reported, meta = arm_sentinel(repo, arm, settings, manager=mgr)
            row = score_instance(
                scores[arm],
                inst,
                reported,
                **{
                    k: meta[k]
                    for k in ("candidates", "verified", "fixed", "cost_usd", "wall_clock_s")
                },
            )
            row["arm"] = arm
            summary["per_instance"].append(row)
            print(
                f"[{arm:11s}] {inst.id:40s} found={row['found']} strict={row['strict']} fp={row['false_positives']}"
            )
    summary["arms"] = {arm: s.to_dict() for arm, s in scores.items()}
    summary["wall_clock_s"] = round(time.time() - started, 1)
    (out / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "report.html").write_text(render_report(summary), encoding="utf-8")
    append_results_md(summary, summary["commit"], results_md)
    _persist(summary, settings)
    return summary


def _persist(summary: dict[str, Any], settings: Settings) -> None:
    try:
        from sentinel.db.models import BenchResult
        from sentinel.db.session import get_engine, init_db, session_scope

        engine = get_engine(settings)
        init_db(engine)
        with session_scope(engine) as s:
            for arm in summary["arms"].values():
                s.add(
                    BenchResult(
                        suite=summary["suite"],
                        arm=arm["arm"],
                        repo="*",
                        commit_sha=summary["commit"],
                        precision=arm["precision"],
                        recall=arm["recall"],
                        f1=arm["f1"],
                        cost_usd=arm["cost_usd"],
                        wall_clock_s=arm["wall_clock_s"],
                        metrics={
                            **arm,
                            "arms": {
                                k: {
                                    "precision": v["precision"],
                                    "recall": v["recall"],
                                    "f1": v["f1"],
                                }
                                for k, v in summary["arms"].items()
                            },
                        },
                    )
                )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not persist bench results: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--suite", choices=["small", "full"], default="small")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--out", default="bench/out")
    parser.add_argument("--inject-only", action="store_true")
    args = parser.parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ARMS]
    if bad:
        parser.error(f"unknown arms {bad}; choose from {ARMS}")
    settings = get_settings()
    try:
        summary = run_suite(
            args.suite, arms, Path(args.out), settings, inject_only=args.inject_only
        )
    except SandboxUnavailableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not args.inject_only:
        for arm in summary["arms"].values():
            print(
                f"{arm['arm']:12s} P={_pct(arm['precision'])} R={_pct(arm['recall'])} F1={_pct(arm['f1'])} "
                f"strictP={_pct(arm['precision_strict'])} ${arm['cost_usd']:.2f} {arm['wall_clock_s']:.0f}s"
            )
        print(f"report: {Path(args.out) / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
