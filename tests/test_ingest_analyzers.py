"""Phase 1: ingest detection and analyzer adapters (SARIF-normalised output)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sentinel.analyzers.bandit import BanditAdapter
from sentinel.analyzers.base import AnalyzerFinding, AnalyzerResult, run_all, to_sarif
from sentinel.analyzers.eslint import EslintAdapter
from sentinel.analyzers.mypy import MypyAdapter
from sentinel.analyzers.registry import default_adapters
from sentinel.analyzers.ruff import RuffAdapter
from sentinel.analyzers.tsc import TscAdapter
from sentinel.cli import app
from sentinel.config import Settings
from sentinel.graph.nodes.ingest import (
    detect_language,
    detect_package_managers,
    detect_test_frameworks,
    ingest,
    is_url,
)

FIX = Path(__file__).parent / "fixtures"
PYREPO = FIX / "pyrepo"
TSREPO = FIX / "tsrepo"
runner = CliRunner()


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", work_dir=tmp_path)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- ingest


def test_is_url() -> None:
    assert is_url("https://github.com/org/repo")
    assert is_url("git@github.com:org/repo.git")
    assert not is_url("C:/CODES/Sentinel")
    assert not is_url("./local")


def test_detect_python_repo(tmp_path: Path) -> None:
    res = ingest(str(PYREPO), _settings(tmp_path))
    assert res.language == "python"
    assert res.package_managers == ["pip"]
    assert res.test_frameworks == ["pytest"]
    assert res.test_command == "pytest -q"
    assert res.file_counts == {"python": 4}
    assert res.repo_url.startswith("file://")


def test_detect_ts_repo(tmp_path: Path) -> None:
    res = ingest(str(TSREPO), _settings(tmp_path))
    assert res.language == "typescript"
    assert res.package_managers == ["npm"]
    assert res.test_frameworks == ["vitest"]
    assert res.test_command == "npm test"


def test_detect_mixed_and_lockfiles(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / "b.ts").write_text("export const x = 1;\n")
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "pnpm-lock.yaml").write_text("lock")
    lang, counts = detect_language(tmp_path)
    assert lang == "mixed" and counts == {"python": 1, "typescript": 1}
    pms, locks = detect_package_managers(tmp_path)
    assert pms == ["uv", "pnpm"] and locks == ["uv.lock", "pnpm-lock.yaml"]
    assert detect_test_frameworks(tmp_path) == (["unknown"], None)


def test_repo_config_overrides(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / "sentinel.yaml").write_text("language: mixed\ntest_command: make test\n")
    res = ingest(str(tmp_path), _settings(tmp_path / ".w"))
    assert res.language == "mixed" and res.test_command == "make test"


def test_ingest_missing_path(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        ingest(str(tmp_path / "nope"), _settings(tmp_path))


# --------------------------------------------------------------------------- analyzers


def test_ruff_finds_seeded_smells() -> None:
    findings = RuffAdapter().run(PYREPO)
    rules = {(f.file, f.rule_id) for f in findings}
    assert ("app/util.py", "SIM115") in rules  # bare open() -> resource_leak
    assert ("app/auth.py", "S608") in rules  # SQL string formatting -> security_smell
    assert ("app/util.py", "BLE001") in rules or ("app/util.py", "S110") in rules
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["SIM115"].category_hint == "resource_leak"
    assert by_rule["S608"].category_hint == "security_smell"
    assert all(not f.file.startswith(("/", "C:")) for f in findings)


def test_bandit_finds_hardcoded_secret_and_sql() -> None:
    findings = BanditAdapter().run(PYREPO)
    rules = {f.rule_id for f in findings}
    assert "B105" in rules and "B608" in rules
    sec = next(f for f in findings if f.rule_id == "B105")
    assert sec.file == "app/auth.py" and sec.line_start == 8
    assert sec.category_hint == "security_smell"
    assert sec.raw["cwe"] == 259


def test_mypy_runs_and_normalises() -> None:
    findings = MypyAdapter().run(PYREPO)
    assert all(f.tool == "mypy" and f.level == "error" for f in findings)
    # `read_config` returns None implicitly on the except path -> mypy flags return type
    assert isinstance(findings, list)


def test_ts_adapters_skip_without_node_modules() -> None:
    assert EslintAdapter().available(TSREPO) is not None
    assert TscAdapter().available(TSREPO) is not None


def test_run_all_parallel_and_language_filter() -> None:
    adapters = [a for a in default_adapters(include_semgrep=False)]
    results = run_all(PYREPO, "python", adapters)
    assert {r.tool for r in results} == {"ruff", "bandit", "mypy"}
    assert all(r.ok for r in results)
    ts_results = run_all(TSREPO, "typescript", adapters)
    assert {r.tool for r in ts_results} == {"eslint", "tsc"}
    assert all(r.skipped_reason for r in ts_results)


def test_adapter_error_is_captured() -> None:
    class Boom:
        name = "boom"
        languages = frozenset({"python"})

        def available(self, repo_path: Path) -> str | None:
            return None

        def run(self, repo_path: Path) -> list[AnalyzerFinding]:
            raise RuntimeError("kaboom")

    [r] = run_all(PYREPO, "python", [Boom()])
    assert r.ok is False and r.error == "RuntimeError: kaboom"


def test_sarif_shape() -> None:
    f = AnalyzerFinding(
        tool="ruff",
        rule_id="S608",
        message="m",
        file="a.py",
        line_start=3,
        line_end=3,
        col_start=5,
        category_hint="security_smell",
    )
    sarif = to_sarif(
        [
            AnalyzerResult(tool="ruff", ok=True, findings=[f]),
            AnalyzerResult(tool="tsc", ok=True, skipped_reason="no tsconfig"),
        ]
    )
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"]) == 2
    r0 = sarif["runs"][0]["results"][0]
    assert r0["ruleId"] == "S608"
    assert r0["locations"][0]["physicalLocation"]["region"] == {
        "startLine": 3,
        "endLine": 3,
        "startColumn": 5,
    }
    assert (
        sarif["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["sentinelCategory"]
        == "security_smell"
    )
    assert sarif["runs"][1]["tool"]["driver"]["properties"]["skippedReason"] == "no tsconfig"
    json.dumps(sarif)  # serialisable


# --------------------------------------------------------------------------- cli


def test_cli_index_then_search(tmp_path: Path, monkeypatch: object) -> None:
    import os

    os.environ["SENTINEL_WORK_DIR"] = str(tmp_path)
    os.environ["SENTINEL_DATABASE_URL"] = "sqlite://"
    try:
        r = runner.invoke(app, ["index", str(PYREPO)])
        assert r.exit_code == 0, r.output
        assert "symbols" in r.output
        r = runner.invoke(app, ["search", "where is auth token parsed", "-k", "3"])
        assert r.exit_code == 0, r.output
        assert "parse_auth_token" in r.output
        r = runner.invoke(app, ["symbol", "Session.lookup"])
        assert r.exit_code == 0, r.output
        assert "method Session.lookup" in r.output
        r = runner.invoke(
            app,
            [
                "analyze",
                str(PYREPO),
                "--no-semgrep",
                "--tools",
                "ruff,bandit",
                "--sarif",
                str(tmp_path / "out.sarif"),
            ],
        )
        assert r.exit_code == 0, r.output
        sarif = json.loads((tmp_path / "out.sarif").read_text())
        assert {run["tool"]["driver"]["name"] for run in sarif["runs"]} == {"ruff", "bandit"}
        assert sum(len(run["results"]) for run in sarif["runs"]) >= 4
    finally:
        os.environ.pop("SENTINEL_WORK_DIR", None)
        os.environ.pop("SENTINEL_DATABASE_URL", None)
