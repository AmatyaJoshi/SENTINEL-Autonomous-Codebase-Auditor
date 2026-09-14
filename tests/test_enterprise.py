"""Items 10-16: queue/worker, DB API keys + roles, secrets refs, metrics, pricing, retention,
migrations, coverage parsing, sandbox node analyzers."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from bench.inject.coverage import is_covered, parse_coverage_json, parse_lcov
from sentinel.api.app import create_app
from sentinel.api.auth import Authenticator, hash_key
from sentinel.config import ApiKey, Settings
from sentinel.db.models import Run
from sentinel.db.session import init_db, session_scope
from sentinel.llm.pricing import PriceTable, estimate_tokens
from sentinel.queue import Worker, claim_next, enqueue
from sentinel.retention import collect_garbage
from sentinel.runner import RunManager
from sentinel.secrets import is_reference, resolve
from tests.fakes import PYREPO, make_scripted_backend


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        work_dir=tmp_path / "w",
        analyzers_semgrep=False,
        gc_on_startup=False,
        **kw,
    )


def _manager(tmp_path: Path, **kw: object) -> RunManager:
    s = _settings(tmp_path, **kw)
    return RunManager(
        s,
        create_engine(s.database_url),
        backend=make_scripted_backend(),
        sandbox_factory=lambda: None,
    )


# --------------------------------------------------------------------------- secrets
def test_secret_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "k.txt"
    f.write_text("  from-file  \n")
    assert resolve(f"file://{f.as_posix()}") == "from-file"
    monkeypatch.setenv("OTHER", "from-env")
    assert resolve("env://OTHER") == "from-env"
    assert is_reference("vault://x") and not is_reference("sk-plain")
    with pytest.raises(KeyError):
        resolve("env://DOES_NOT_EXIST_XYZ")
    monkeypatch.setenv("SENTINEL_OPENROUTER_API_KEY", f"file://{f.as_posix()}")
    s = Settings(_env_file=None, work_dir=tmp_path)  # type: ignore[call-arg]
    assert (
        s.openrouter_api_key is not None and s.openrouter_api_key.get_secret_value() == "from-file"
    )


# --------------------------------------------------------------------------- pricing
def test_price_table_and_token_estimate() -> None:
    pt = PriceTable(
        '{"openrouter/": {"input_per_m": 0, "output_per_m": 0}, "vllm/q": {"input_per_m": 0.2, "output_per_m": 0.6}}'
    )
    assert pt.cost("openrouter/any:free", 1000, 1000) == 0.0
    assert pt.cost("vllm/q", 1_000_000, 500_000) == pytest.approx(0.5)
    assert pt.cost("unknown", 1, 1) is None
    assert estimate_tokens([{"role": "user", "content": "x" * 400}]) == 100
    assert PriceTable(None).lookup("x") is None


# --------------------------------------------------------------------------- auth / keys
def test_db_api_keys_lifecycle(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    engine = create_engine(s.database_url)
    init_db(engine)
    auth = Authenticator(s, engine)
    assert auth.open_mode  # no static keys, no OIDC
    raw, rec = auth.create_key("ci", "operator", "test", expires_days=1)
    assert raw.startswith("sk-sentinel-") and rec.key_hash == hash_key(raw)
    who = auth.by_api_key(raw)
    assert who and who.role == "operator" and who.via == "db-key"
    assert auth.by_api_key("nope") is None
    rotated = auth.rotate_key(rec.id, "test")
    assert rotated is not None
    new_raw, new_rec = rotated
    assert new_rec.name == "ci" and auth.by_api_key(new_raw) is not None
    assert auth.revoke_key(new_rec.id) and not auth.revoke_key(new_rec.id)
    auth._db_cache.clear()
    assert auth.by_api_key(new_raw) is None
    assert {k.name for k in auth.list_keys()} == {"ci"}


def test_key_management_endpoints_and_audit_log(tmp_path: Path) -> None:
    mgr = _manager(
        tmp_path,
        api_keys=[
            ApiKey(key="root", name="root", role="admin"),
            ApiKey(key="v", name="v", role="viewer"),
        ],
    )  # type: ignore[list-item]
    c = TestClient(create_app(mgr.settings, manager=mgr))
    assert c.get("/api/v1/keys", headers={"X-API-Key": "v"}).status_code == 403
    r = c.post(
        "/api/v1/keys", json={"name": "bot", "role": "operator"}, headers={"X-API-Key": "root"}
    )
    assert r.status_code == 201
    new_key = r.json()["key"]
    assert c.get("/api/v1/me", headers={"X-API-Key": new_key}).json()["role"] == "operator"
    assert (
        c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {new_key}", "X-API-Key": new_key}
        ).status_code
        == 200
    )
    kid = r.json()["id"]
    assert c.post(f"/api/v1/keys/{kid}/rotate", headers={"X-API-Key": "root"}).status_code == 200
    assert c.delete(f"/api/v1/keys/{kid}", headers={"X-API-Key": "root"}).status_code == 204
    log = c.get("/api/v1/audit-log", headers={"X-API-Key": "root"}).json()["items"]
    assert {e["action"] for e in log} >= {"key.create", "key.rotate", "key.revoke"}
    resp = c.get("/health")
    assert resp.headers["X-Request-ID"] and resp.json()["checks"]["auth"] == "api-key"
    m = c.get("/metrics").text
    assert "sentinel_http_requests_total" in m


def test_oidc_bearer_rejected_without_issuer_and_role_mapping(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        oidc_issuer="https://issuer.example",
        oidc_audience="sentinel",
        oidc_role_map={"grp-admin": "admin"},
    )
    engine = create_engine(s.database_url)
    init_db(engine)
    auth = Authenticator(s, engine)
    assert not auth.open_mode
    assert auth.by_bearer("not.a.jwt") is None  # invalid token → unauthenticated, never a crash
    assert auth._role_from_claims({"groups": ["grp-admin", "x"]}) == "admin"
    assert auth._role_from_claims({"groups": ["nobody"]}) == "viewer"


# --------------------------------------------------------------------------- queue / worker
@pytest.mark.timeout(180)
def test_queue_mode_enqueues_and_worker_executes(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, execution_mode="queue")
    c = TestClient(create_app(mgr.settings, manager=mgr))
    r = c.post("/api/v1/runs", json={"repo": str(PYREPO), "arm": "analyzers"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    run_id = r.json()["id"]
    assert mgr.active_count() == 0  # API did not execute it
    w = Worker(mgr, poll_seconds=0.1)
    assert w.run_once() is True
    final = mgr.get(run_id)
    assert final and final.status == "completed" and final.worker_id == w.worker_id
    assert w.run_once() is False  # queue empty
    # SSE from a process that does not own the run tails the events table and terminates
    with c.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        text = "".join(resp.iter_text())
    assert "event: run.end" in text


def test_claim_recovers_stale_lease(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    run = mgr.create(str(PYREPO), arm="analyzers")
    enqueue(mgr.engine, run.id)
    first = claim_next(mgr.engine, "w1")
    assert first and first.status == "running" and first.worker_id == "w1"
    assert claim_next(mgr.engine, "w2") is None  # lease is fresh
    with session_scope(mgr.engine) as s:
        r = s.get(Run, run.id)
        assert r
        r.worker_heartbeat = datetime.now(UTC) - timedelta(hours=1)
        s.add(r)
    second = claim_next(mgr.engine, "w2")
    assert second and second.worker_id == "w2"  # stale lease taken over


# --------------------------------------------------------------------------- retention
def test_gc_removes_old_runs_but_keeps_reports(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, retention_days=7)
    old = mgr.create(str(PYREPO))
    recent = mgr.create(str(PYREPO))
    for run, age in ((old, 30), (recent, 1)):
        d = mgr.settings.work_dir / "runs" / run.id
        (d / "workspace").mkdir(parents=True)
        (d / "workspace" / "big.bin").write_bytes(b"x" * 10_000)
        (d / "report.json").write_text("{}")
        with session_scope(mgr.engine) as s:
            r = s.get(Run, run.id)
            assert r
            r.status = "completed"
            r.finished_at = datetime.now(UTC) - timedelta(days=age)
            s.add(r)
    stale_clone = mgr.settings.work_dir / "repos" / "old-clone"
    stale_clone.mkdir(parents=True)
    os.utime(stale_clone, (time.time() - 90 * 86400, time.time() - 90 * 86400))
    dry = collect_garbage(mgr.settings, mgr.engine, dry_run=True)
    assert dry.runs_deleted == 1 and mgr.get(old.id) is not None
    rep = collect_garbage(mgr.settings, mgr.engine)
    assert rep.runs_deleted == 1 and rep.clones_removed == 1 and rep.bytes_freed >= 10_000
    assert mgr.get(old.id) is None and mgr.get(recent.id) is not None
    assert (mgr.settings.work_dir / "runs" / old.id / "report.json").exists()
    assert not (mgr.settings.work_dir / "runs" / old.id / "workspace").exists()
    assert not stale_clone.exists()


# --------------------------------------------------------------------------- migrations
def test_alembic_upgrade_and_stamp(tmp_path: Path) -> None:
    from sentinel.db.migrate import current_revision, head_revision, upgrade

    url = f"sqlite:///{(tmp_path / 'm.sqlite').as_posix()}"
    engine = create_engine(url)
    upgrade(engine, url)  # fresh DB → full migration
    assert current_revision(engine) == head_revision(url)
    # pre-existing create_all schema without history gets stamped, not re-created
    url2 = f"sqlite:///{(tmp_path / 'legacy.sqlite').as_posix()}"
    engine2 = create_engine(url2)
    from sqlmodel import SQLModel

    from sentinel.db import models  # noqa: F401

    SQLModel.metadata.create_all(engine2)
    upgrade(engine2, url2)
    assert current_revision(engine2) == head_revision(url2)


# --------------------------------------------------------------------------- coverage
def test_coverage_parsers() -> None:
    cj = json.dumps({"files": {"/workspace/app/auth.py": {"executed_lines": [1, 2, 35]}}})
    cov = parse_coverage_json(cj)
    assert cov == {"app/auth.py": {1, 2, 35}}
    lcov = "SF:/workspace/src/auth.ts\nDA:1,1\nDA:2,0\nDA:12,3\nend_of_record\n"
    assert parse_lcov(lcov) == {"src/auth.ts": {1, 12}}
    assert is_covered(cov, "app/auth.py", 35) and not is_covered(cov, "app/auth.py", 36)
    assert is_covered(None, "anything", 1)  # no data → no filtering


# --------------------------------------------------------------------------- sandbox node analyzers
def test_sandbox_eslint_tsc_adapters_parse(tmp_path: Path) -> None:
    from sentinel.analyzers.node_sandbox import SandboxEslintAdapter, SandboxTscAdapter
    from sentinel.sandbox.docker_runner import ExecResult

    runner = MagicMock()
    ts = Path(__file__).parent / "fixtures" / "tsrepo"
    (tmp_path / "eslint.config.js").write_text("export default [];")
    (tmp_path / "tsconfig.json").write_text("{}")
    runner.run.return_value = ExecResult(
        ["x"],
        "img",
        1,
        json.dumps(
            [
                {
                    "filePath": "/workspace/src/auth.ts",
                    "messages": [
                        {"ruleId": "no-unused-vars", "message": "m", "line": 3, "severity": 2}
                    ],
                }
            ]
        ),
        "",
        0.1,
    )
    es = SandboxEslintAdapter(runner, "img", ts)
    assert es.available(tmp_path) is None and es.available(ts) is not None
    fs = es.run(tmp_path)
    assert (
        fs[0].file == "src/auth.ts" and fs[0].category_hint == "dead_code" and fs[0].raw["sandbox"]
    )
    runner.run.return_value = ExecResult(
        ["x"],
        "img",
        2,
        "/workspace/src/auth.ts(31,12): error TS2532: Object is possibly 'undefined'.",
        "",
        0.1,
    )
    tf = SandboxTscAdapter(runner, "img", ts).run(tmp_path)
    assert (
        tf[0].rule_id == "TS2532"
        and tf[0].category_hint == "null_deref"
        and tf[0].file == "src/auth.ts"
    )
