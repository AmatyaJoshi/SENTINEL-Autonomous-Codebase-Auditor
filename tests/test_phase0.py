"""Phase 0 acceptance: CLI, config, DB models, state schema, telemetry no-op."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import create_engine, select
from typer.testing import CliRunner

from sentinel.cli import app
from sentinel.config import RepoConfig, Settings
from sentinel.db.models import FindingRecord, Run
from sentinel.db.session import init_db, session_scope
from sentinel.graph.state import Budget, Finding
from sentinel.telemetry.otel import span

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("audit", "index", "search", "bench", "serve", "replay"):
        assert cmd in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "sentinel 0.1.0" in result.stdout


@pytest.mark.parametrize(
    "args",
    [["audit", "https://github.com/x/y"], ["index", "."], ["search", "auth token"], ["bench"]],
)
def test_unimplemented_commands_exit_nonzero_loudly(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "Phase" in result.stdout


def test_settings_defaults_keep_sandbox_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_DATABASE_URL", "sqlite://")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.sandbox.network == "none"
    assert s.sandbox.test_timeout_s == 120
    assert s.sandbox.snippet_timeout_s == 30
    assert s.database_url == "sqlite://"


def test_repo_config_absent_and_present(tmp_path: Path) -> None:
    assert RepoConfig.load(tmp_path).language is None
    (tmp_path / "sentinel.yaml").write_text("language: python\ntest_command: pytest -q\n")
    cfg = RepoConfig.load(tmp_path)
    assert cfg.language == "python"
    assert cfg.test_command == "pytest -q"


def test_db_roundtrip() -> None:
    engine = create_engine("sqlite://")
    init_db(engine)
    with session_scope(engine) as s:
        run = Run(repo_url="https://github.com/x/y")
        s.add(run)
        s.flush()
        s.add(
            FindingRecord(
                run_id=run.id,
                category="off_by_one",
                severity="high",
                file="a.py",
                line_start=1,
                line_end=2,
                description="d",
                hypothesis="h",
                confidence=0.7,
                evidence=["ruff:E1"],
            )
        )
    with session_scope(engine) as s:
        rec = s.exec(select(FindingRecord)).one()
        assert rec.evidence == ["ruff:E1"]
        assert rec.status == "candidate"


def test_finding_schema_validates_confidence() -> None:
    with pytest.raises(ValueError):
        Finding(
            id="f1",
            category="null_deref",
            severity="low",
            file="a.py",
            line_start=1,
            line_end=1,
            description="d",
            hypothesis="h",
            confidence=1.5,
        )


def test_budget_exceeded() -> None:
    b = Budget(max_usd=1.0, max_minutes=10, max_findings=5)
    assert not b.exceeded_by(Budget(max_usd=0.5, max_minutes=1, max_findings=1))
    assert b.exceeded_by(Budget(max_usd=1.5, max_minutes=1, max_findings=1))


def test_span_is_noop_before_init() -> None:
    with span("sentinel.test", run_id="r1", finding_id=None) as s:
        assert s is not None
