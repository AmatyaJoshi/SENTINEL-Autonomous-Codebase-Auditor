"""Inject confirmed-killing mutants into copies of a clean repo and record a manifest (SPEC §8.2).

Constraints enforced here:
- only function-body lines (tree-sitter) are mutated;
- a mutant is kept only if ≥ 1 existing test fails on it (not an equivalent mutant);
- the killing tests are then skipped in the mutated copy so Sentinel must rediscover the bug;
- the original test source and the mutation are both stored for scoring.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bench.inject.operators import Candidate, apply_candidate, propose
from sentinel.graph.nodes.plan import is_test_file
from sentinel.indexing.pipeline import discover_files
from sentinel.sandbox.docker_runner import COPY_EXCLUDES
from sentinel.sandbox.test_runner import TestReport

# (repo_path, test_paths|None) -> TestReport. Production uses the Docker sandbox; tests use a host double.
TestExecutor = Callable[[Path, list[str] | None], TestReport]


@dataclass
class Injected:
    id: str
    repo: str
    commit_sha: str | None
    language: str
    operator: str
    category: str
    file: str
    line_start: int
    line_end: int
    original: str
    mutated: str
    description: str
    killed_tests: list[str]
    skipped_test_files: list[str]
    mutated_repo_path: str
    original_tests: dict[str, str] = field(default_factory=dict)


@dataclass
class Manifest:
    suite: str
    created_at: float
    instances: list[Injected] = field(default_factory=list)
    rejected_equivalent: int = 0
    rejected_broken: int = 0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Manifest:
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            suite=d["suite"],
            created_at=d["created_at"],
            instances=[Injected(**i) for i in d["instances"]],
            rejected_equivalent=d.get("rejected_equivalent", 0),
            rejected_broken=d.get("rejected_broken", 0),
        )


def copy_repo(src: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(
        src,
        dest,
        ignore=lambda _d, names: [n for n in names if n in COPY_EXCLUDES - {".git"}],
        symlinks=False,
    )
    return dest


_PY_TEST_DEF = re.compile(r"^(\s*)(async\s+)?def\s+(test_\w+)\s*\(", re.M)
_TS_TEST_CALL = re.compile(r"^(\s*)(it|test)\(\s*(['\"`])", re.M)


def skip_tests(repo: Path, killed: list[str], language: str) -> tuple[list[str], dict[str, str]]:
    """Mark the killing tests skipped in place. Returns (files touched, original file contents)."""
    touched: list[str] = []
    originals: dict[str, str] = {}
    names = {k.split("::")[-1].split("[")[0] for k in killed}
    files = {_test_file_for(repo, k) for k in killed}
    for rel in filter(None, files):
        p = repo / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        originals[rel] = src
        if language == "python":

            def repl(m: re.Match[str]) -> str:
                if m.group(3) in names:
                    return f"{m.group(1)}@pytest.mark.skip(reason='sentinel-bench: killing test hidden')\n{m.group(0)}"
                return m.group(0)

            new = _PY_TEST_DEF.sub(repl, src)
            if new != src and "import pytest" not in new:
                new = "import pytest\n" + new
        else:

            def repl_ts(m: re.Match[str]) -> str:
                return f"{m.group(1)}{m.group(2)}.skip({m.group(3)}"

            new = _TS_TEST_CALL.sub(
                repl_ts, src
            )  # coarse: skips the file's tests (jest/vitest names are free text)
        if new != src:
            p.write_text(new, encoding="utf-8")
            touched.append(rel)
    return touched, originals


def _test_file_for(repo: Path, case_id: str) -> str | None:
    classname = case_id.split("::")[0]
    cand = classname.replace(".", "/")
    for ext in (".py", ".ts", ".tsx", ".js"):
        for c in (cand + ext, classname + ext, classname):
            if (repo / c).exists() and (repo / c).is_file():
                return c
    # jest/vitest put the file path in classname; pytest uses dotted module path
    for p in repo.rglob("*"):
        if (
            p.is_file()
            and is_test_file(p.relative_to(repo).as_posix())
            and p.stem == cand.split("/")[-1]
        ):
            return p.relative_to(repo).as_posix()
    return None


def inject(
    clean_repo: Path,
    out_dir: Path,
    *,
    repo_name: str,
    commit_sha: str | None,
    language: str,
    n: int,
    seed: int,
    executor: TestExecutor,
    suite: str = "small",
    max_attempts: int | None = None,
    baseline_failures: set[str] | None = None,
) -> Manifest:
    rng = random.Random(seed)
    manifest = Manifest(suite=suite, created_at=time.time())
    files = [f for f in discover_files(clean_repo) if not is_test_file(f)]
    candidates: list[tuple[str, Candidate]] = []
    for rel in files:
        src = (clean_repo / rel).read_text(encoding="utf-8", errors="replace")
        candidates += [(rel, c) for c in propose(src, rel)]
    rng.shuffle(candidates)
    # balance operators: round-robin over categories
    by_cat: dict[str, list[tuple[str, Candidate]]] = {}
    for rel, c in candidates:
        by_cat.setdefault(c.category, []).append((rel, c))
    ordered: list[tuple[str, Candidate]] = []
    while any(by_cat.values()):
        for cat in sorted(by_cat):
            if by_cat[cat]:
                ordered.append(by_cat[cat].pop())
    baseline = (
        baseline_failures
        if baseline_failures is not None
        else executor(clean_repo, None).failing_ids()
    )
    attempts = 0
    limit = max_attempts or n * 8
    for rel, c in ordered:
        if len(manifest.instances) >= n or attempts >= limit:
            break
        attempts += 1
        mid = f"{repo_name.split('/')[-1]}-{len(manifest.instances) + 1:03d}-{c.operator}"
        dest = copy_repo(clean_repo, out_dir / mid)
        target = dest / rel
        try:
            target.write_text(
                apply_candidate(target.read_text(encoding="utf-8", errors="replace"), c),
                encoding="utf-8",
            )
        except ValueError:
            shutil.rmtree(dest, ignore_errors=True)
            continue
        report = executor(dest, None)
        killed = sorted(report.failing_ids() - baseline)
        if report.parse_error or report.total == 0:
            manifest.rejected_broken += 1
            shutil.rmtree(dest, ignore_errors=True)
            continue
        if not killed:
            manifest.rejected_equivalent += 1
            shutil.rmtree(dest, ignore_errors=True)
            continue
        # mutant must not break collection entirely: require most tests still run
        if report.errors > max(3, report.total // 2):
            manifest.rejected_broken += 1
            shutil.rmtree(dest, ignore_errors=True)
            continue
        touched, originals = skip_tests(dest, killed, language)
        manifest.instances.append(
            Injected(
                id=mid,
                repo=repo_name,
                commit_sha=commit_sha,
                language=language,
                operator=c.operator,
                category=c.category,
                file=rel,
                line_start=c.line,
                line_end=c.line,
                original=c.original,
                mutated=c.mutated,
                description=c.description,
                killed_tests=killed,
                skipped_test_files=touched,
                mutated_repo_path=str(dest),
                original_tests=originals,
            )
        )
    return manifest
