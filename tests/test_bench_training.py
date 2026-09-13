"""Phase 5/7: mutation operators, injector (with host executor on the fixture), scoring, a full
mini benchmark run (analyzers + full arms) and the training data/baseline/eval scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import create_engine

from bench.inject.injector import Injected, inject, skip_tests
from bench.inject.operators import ALL_OPERATORS, apply_candidate, propose
from bench.run_bench import run_suite
from bench.score import ArmScore, Reported, from_findings, score_instance
from sentinel.config import Settings
from sentinel.runner import RunManager
from sentinel.sandbox.test_runner import parse_junit
from tests.fakes import PYREPO, HostPytestSandbox, make_scripted_backend


def host_executor(repo: Path, paths: list[str] | None):  # type: ignore[no-untyped-def]
    junit = repo / ".sentinel" / "junit.xml"
    junit.parent.mkdir(exist_ok=True)
    junit.unlink(missing_ok=True)
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            *(paths or []),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return parse_junit(junit.read_text()) if junit.exists() else parse_junit("<x/>")


# --------------------------------------------------------------------------- operators
def test_operators_propose_known_mutations_on_fixture() -> None:
    src = (PYREPO / "app/auth.py").read_text()
    cands = propose(src, "app/auth.py")
    ops = {c.operator for c in cands}
    assert {"OffByOne", "WrongOperator", "ReturnMutation"} <= ops
    off = [c for c in cands if c.operator == "OffByOne" and c.line == 35]
    assert off and "range(n - 1 - 1)" in off[0].mutated
    for c in cands:  # every candidate is inside a function body, never a signature/import
        assert (
            not src.split("\n")[c.line - 1]
            .lstrip()
            .startswith(("def ", "import ", "from ", "class "))
        )
    mutated = apply_candidate(src, off[0])
    assert mutated.count("\n") == src.count("\n") and mutated != src
    with pytest.raises(ValueError):
        apply_candidate(mutated, off[0])


def test_operators_typescript() -> None:
    src = (Path(__file__).parent / "fixtures/tsrepo/src/auth.ts").read_text()
    cands = propose(src, "src/auth.ts")
    ops = {c.operator for c in cands}
    assert "OffByOne" in ops and "WrongOperator" in ops
    assert any(
        c.operator == "AsyncMisuse"
        for c in propose("async function f(){\n  await g();\n}\n", "x.ts")
    )
    assert any(
        c.operator == "NullCheckRemoval"
        for c in propose("function f(x){\n  if (!x) return;\n  return x.y;\n}\n", "x.js")
    )


def test_each_operator_has_a_positive_example() -> None:
    samples = {
        "OffByOne": ("def f(n):\n    for i in range(n):\n        pass\n", "python"),
        "NullCheckRemoval": (
            "def f(x):\n    if x is None:\n        return 0\n    return x\n",
            "python",
        ),
        "ExceptionSwallow": (
            "def f():\n    try:\n        g()\n    except E:\n        raise\n",
            "python",
        ),
        "ResourceLeak": ("def f(p):\n    fh = open(p)\n    fh.close()\n", "python"),
        "WrongOperator": ("def f(a, b):\n    return a and b\n", "python"),
        "TypeConfusion": ("def f(s):\n    return int(s) + 1\n", "python"),
        "AsyncMisuse": ("async def f():\n    x = await g()\n    return x\n", "python"),
        "ReturnMutation": ("def f():\n    return True\n", "python"),
        "SecuritySmell": ("import os\ndef f():\n    return os.environ['API_TOKEN']\n", "python"),
    }
    for op in ALL_OPERATORS:
        src, _ = samples[op.name]
        assert any(c.operator == op.name for c in propose(src, "m.py", [op])), op.name


# --------------------------------------------------------------------------- injector
@pytest.mark.timeout(300)
def test_inject_keeps_only_killed_mutants_and_hides_killing_tests(tmp_path: Path) -> None:
    fixed = tmp_path / "clean"
    import shutil

    shutil.copytree(PYREPO, fixed)
    # make the fixture's own test pass on the clean repo so the baseline is green
    (fixed / "app/auth.py").write_text(
        (fixed / "app/auth.py").read_text().replace("range(n - 1):  # off-by-one", "range(n):")
    )
    manifest = inject(
        fixed,
        tmp_path / "mut",
        repo_name="fixture/pyrepo",
        commit_sha=None,
        language="python",
        n=2,
        seed=1,
        executor=host_executor,
        max_attempts=12,
    )
    assert manifest.instances, (manifest.rejected_equivalent, manifest.rejected_broken)
    for inst in manifest.instances:
        assert inst.killed_tests and Path(inst.mutated_repo_path).exists()
        # killing test skipped in the mutated copy, original preserved for scoring
        assert inst.skipped_test_files and inst.original_tests
        skipped_src = (Path(inst.mutated_repo_path) / inst.skipped_test_files[0]).read_text()
        assert "pytest.mark.skip" in skipped_src
        rerun = host_executor(Path(inst.mutated_repo_path), None)
        assert not rerun.failing_ids(), "hidden tests must no longer fail"
    assert manifest.rejected_equivalent + len(manifest.instances) >= 2


def test_skip_tests_python(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(
        "def test_a():\n    assert 1\n\ndef test_b():\n    assert 2\n"
    )
    touched, originals = skip_tests(tmp_path, ["tests.test_x::test_b"], "python")
    assert touched == ["tests/test_x.py"] and "def test_b" in originals["tests/test_x.py"]
    new = (tmp_path / "tests/test_x.py").read_text()
    assert new.count("pytest.mark.skip") == 1 and new.index("skip") < new.index("def test_b")


# --------------------------------------------------------------------------- scoring
def _inj(**kw: object) -> Injected:
    base = dict(
        id="i",
        repo="r",
        commit_sha=None,
        language="python",
        operator="OffByOne",
        category="off_by_one",
        file="app/auth.py",
        line_start=35,
        line_end=35,
        original="a",
        mutated="b",
        description="d",
        killed_tests=["t"],
        skipped_test_files=[],
        mutated_repo_path="/x",
    )
    base.update(kw)
    return Injected(**base)  # type: ignore[arg-type]


def test_scoring_lenient_vs_strict_and_fp() -> None:
    s = ArmScore("full")
    row = score_instance(
        s,
        _inj(),
        [
            Reported("app/auth.py", 33, 37, "logic_error"),
            Reported("app/util.py", 1, 2, "dead_code"),
        ],
        candidates=3,
        verified=2,
        fixed=1,
        cost_usd=0.5,
        wall_clock_s=10,
    )
    assert row["found"] and not row["strict"] and row["false_positives"] == 1
    score_instance(
        s,
        _inj(id="j", file="app/util.py", line_start=9, line_end=9, category="resource_leak"),
        [],
        candidates=1,
        verified=0,
        fixed=0,
        cost_usd=0.1,
        wall_clock_s=5,
    )
    d = s.to_dict()
    assert (d["tp"], d["fp"], d["fn"]) == (1, 1, 1)
    assert d["precision"] == 0.5 and d["recall"] == 0.5 and d["f1"] == 0.5
    assert (
        d["precision_strict"] == 0.0 and d["verified_rate"] == 0.5 and d["patch_pass_rate"] == 0.5
    )
    assert (
        d["per_category"]["off_by_one"]["tp"] == 1 and d["per_category"]["resource_leak"]["fn"] == 1
    )
    assert d["confusion"]["off_by_one"]["logic_error"] == 1
    assert d["cost_per_repo"] == 0.3
    assert (
        from_findings(
            [{"file": "a", "line_start": 1, "line_end": 1, "status": "refuted", "category": "x"}]
        )
        == []
    )


# --------------------------------------------------------------------------- mini benchmark
@pytest.mark.timeout(600)
def test_run_suite_end_to_end_on_fixture(tmp_path: Path) -> None:
    import shutil

    clean = tmp_path / "clean"
    shutil.copytree(PYREPO, clean)
    (clean / "app/auth.py").write_text(
        (clean / "app/auth.py").read_text().replace("range(n - 1):  # off-by-one", "range(n):")
    )
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'b.sqlite').as_posix()}",  # type: ignore[call-arg]
        work_dir=tmp_path / "w",
        analyzers_semgrep=False,
    )
    dataset = {
        "suite": "small",
        "seed": 3,
        "mutations_per_repo": 1,
        "repos": [
            {"name": "fixture/pyrepo", "path": str(clean), "language": "python", "mutations": 1}
        ],
    }

    def manager_factory(s: Settings) -> RunManager:
        return RunManager(
            s,
            create_engine(s.database_url),
            backend=make_scripted_backend(),
            sandbox_factory=lambda: HostPytestSandbox(),
        )

    summary = run_suite(
        "small",
        ["analyzers", "full"],
        tmp_path / "out",
        settings,
        dataset=dataset,
        executor_factory=lambda *_: host_executor,
        manager_factory=manager_factory,
        results_md=tmp_path / "RESULTS.md",
    )
    assert summary["instances"] == 1
    arms = summary["arms"]
    assert set(arms) == {"analyzers", "full"}
    for a in arms.values():
        assert a["tp"] + a["fn"] == 1
    assert (tmp_path / "out" / "report.html").exists() and (
        tmp_path / "out" / "manifest.json"
    ).exists()
    rep = json.loads((tmp_path / "out" / "report.json").read_text())
    assert rep["per_instance"] and {r["arm"] for r in rep["per_instance"]} == {"analyzers", "full"}
    # RESULTS.md gained rows for this run
    results_md = (tmp_path / "RESULTS.md").read_text()
    assert "| small | full |" in results_md


# --------------------------------------------------------------------------- training
def test_training_scripts_on_synthetic_data(tmp_path: Path) -> None:
    import random

    from sentinel.triage.model import FEATURE_NAMES
    from training.eval_triage import main as eval_main
    from training.train_triage_baseline import main as train_main

    rng = random.Random(0)
    data = tmp_path / "data"
    data.mkdir()
    for split, n in (("train", 120), ("val", 30), ("test", 40)):
        with (data / f"{split}.jsonl").open("w") as fh:
            for i in range(n):
                conf = rng.random()
                label = int(rng.random() < 0.2 + 0.7 * conf)
                feats = [
                    1.0,
                    conf,
                    rng.random(),
                    rng.random(),
                    float(rng.random() < 0.5),
                    0.5,
                    0,
                    1,
                    0,
                ] + [0.0] * (len(FEATURE_NAMES) - 9)
                feats[9] = 1.0  # category one-hot: null_deref
                fh.write(
                    json.dumps(
                        {
                            "id": f"{split}{i}",
                            "repo": "r",
                            "label": label,
                            "category": "null_deref",
                            "features": feats,
                            "text": f"description: d {i}\nhypothesis: h {i} = 1\n",
                        }
                    )
                    + "\n"
                )
    out_model = tmp_path / "m.json"
    assert train_main(["--data", str(data), "--out", str(out_model), "--epochs", "50"]) == 0
    weights = json.loads(out_model.read_text())
    assert (
        weights["features"] == list(FEATURE_NAMES) and weights["weights"][1] > 0
    )  # confidence is predictive
    out_eval = tmp_path / "eval.json"
    assert eval_main(["--data", str(data), "--out", str(out_eval), "--logreg", str(out_model)]) == 0
    ev = json.loads(out_eval.read_text())
    assert set(ev["arms"]) == {"hunter_confidence", "heuristic", "logreg"}
    assert ev["arms"]["logreg"]["auroc"] is not None and ev["arms"]["logreg"]["auroc"] > 0.6
    assert "sandbox_runs_avoided" in ev["cost_impact"]["logreg"]
