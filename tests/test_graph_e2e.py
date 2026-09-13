"""Phase 3/4 acceptance: the full graph on the fixture repo with a scripted LLM and a test-only
sandbox double finds, verifies (failing test), fixes (test passes), regresses (suite green), ranks
and reports the planted off-by-one; replay works; budget guard short-circuits; API serves it."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from sentinel.api.app import create_app
from sentinel.config import ApiKey, Settings
from sentinel.graph.state import Budget
from sentinel.runner import RunManager
from tests.fakes import PYREPO, HostPytestSandbox, make_scripted_backend


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        work_dir=tmp_path / "w",
        analyzers_semgrep=False,
        hunt_concurrency=2,
        **kw,
    )


def _manager(tmp_path: Path, backend, sandbox: bool = True, **kw: object) -> RunManager:  # type: ignore[no-untyped-def]
    s = _settings(tmp_path, **kw)
    engine = create_engine(s.database_url)
    return RunManager(
        s,
        engine,
        backend=backend,
        sandbox_factory=(lambda: HostPytestSandbox()) if sandbox else (lambda: None),
    )


def _wait(mgr: RunManager, run_id: str, timeout: float = 180) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = mgr.get(run_id)
        if r and r.status in ("completed", "failed", "cancelled", "awaiting_review"):
            return
        time.sleep(0.2)
    raise TimeoutError(mgr.get(run_id))


@pytest.mark.timeout(300)
def test_full_audit_finds_verifies_fixes_reports(tmp_path: Path) -> None:
    mgr = _manager(
        tmp_path, make_scripted_backend(patch_first_attempt_bad=True, extra_finding=True)
    )
    run = mgr.create(
        str(PYREPO), budget=Budget(max_usd=5, max_minutes=10, max_findings=10), created_by="test"
    )
    mgr.start(run, block=True)
    final = mgr.get(run.id)
    assert final is not None and final.status == "completed", final.error
    assert final.language == "python"  # fixture dir has no own git history → commit_sha None
    assert [n["status"] for n in final.nodes] == ["done"] * 11

    findings = mgr.findings(run.id)
    by_cat = {f.category: f for f in findings}
    bug = by_cat["off_by_one"]
    assert bug.status == "fixed", (bug.status, bug.verify_log, bug.regress_log)
    assert bug.test_path == "tests/test_sentinel_first_n.py" and "SENTINEL: expected to FAIL" in (
        bug.test_code or ""
    )
    assert "expected assertion" in (bug.verify_log or "")
    assert bug.patch_diff and "range(n):" in bug.patch_diff
    assert "attempt 1: test still failing" in (
        bug.regress_log or ""
    )  # bad first patch was rejected
    assert "attempt 2: new test passes" in (bug.regress_log or "")
    assert "suite green" in (bug.regress_log or "")
    assert bug.explanation and "range(n)" in bug.explanation
    assert bug.rank == 1 and bug.triage_score is not None and bug.confidence > 0.35

    weak = by_cat[
        "race_condition"
    ]  # low-confidence speculative finding must be triaged out, not hidden
    assert weak.status == "refuted" and "triage" in (weak.verify_log or "")

    run_dir = tmp_path / "w" / "runs" / run.id
    report = json.loads((run_dir / "report.json").read_text())
    assert report["counts"] == {"fixed": 1, "refuted": 1} and report["sandbox_ready"] is True
    md = (run_dir / "report.md").read_text()
    assert "## Verified bugs (1)" in md and "## Unverified suspicions (1)" in md
    html = (run_dir / "report.html").read_text()
    assert "Sentinel audit report" in html and "first_n" in html

    events = mgr.events(run.id)
    types = {e.type for e in events}
    assert {
        "node.start",
        "node.end",
        "finding.new",
        "finding.update",
        "llm.call",
        "sandbox.exec",
        "evaluation",
        "run.end",
        "log",
    } <= types
    assert final.cost_usd > 0
    # the sandbox double ran the new test alone (verify, fix) and the full suite (baseline, regress)
    sb_cmds = [e.data["command"] for e in events if e.type == "sandbox.exec"]
    assert any("test_sentinel_first_n" in c for c in sb_cmds) and any("suite" in c for c in sb_cmds)


@pytest.mark.timeout(120)
def test_without_sandbox_findings_stay_unverified_and_report_says_so(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, make_scripted_backend(), sandbox=False)
    run = mgr.create(str(PYREPO))
    mgr.start(run, block=True)
    final = mgr.get(run.id)
    assert final and final.status == "completed"
    fs = mgr.findings(run.id)
    assert fs and all(f.status == "candidate" for f in fs)
    assert "sandbox unavailable" in fs[0].verify_log.lower()  # type: ignore[union-attr]
    md = (tmp_path / "w" / "runs" / run.id / "report.md").read_text()
    assert "Sandbox unavailable" in md


@pytest.mark.timeout(120)
def test_budget_guard_skips_llm_nodes(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, make_scripted_backend(), sandbox=False)
    run = mgr.create(str(PYREPO), budget=Budget(max_usd=0.0000001, max_minutes=10, max_findings=5))
    mgr.start(run, block=True)
    final = mgr.get(run.id)
    assert final and final.status == "completed"
    statuses = {n["name"]: n["status"] for n in final.nodes}
    # budget is exceeded right after the first LLM call (plan) → hunt/verify/fix skipped, rank+report ran
    assert (
        statuses["plan"] == "done"
        and statuses["hunt"] == "skipped"
        and statuses["report"] == "done"
    )
    assert any(
        "budget exceeded" in e.data.get("message", "")
        for e in mgr.events(run.id)
        if e.type == "log"
    )


@pytest.mark.timeout(300)
def test_review_interrupt_then_decision_resumes(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, make_scripted_backend())
    run = mgr.create(str(PYREPO), review=True)
    mgr.start(run, block=True)
    paused = mgr.get(run.id)
    assert paused and paused.status == "awaiting_review"
    assert not (tmp_path / "w" / "runs" / run.id / "report.json").exists()
    fixed = [f for f in mgr.findings(run.id) if f.status == "fixed"]
    assert len(fixed) == 1
    mgr.decide(run.id, fixed[0].id, "approve", "looks right")
    _wait(mgr, run.id)
    done = mgr.get(run.id)
    assert done and done.status == "completed"
    assert (tmp_path / "w" / "runs" / run.id / "report.json").exists()
    assert mgr.findings(run.id)[0].review_decision == "approve"


@pytest.mark.timeout(300)
def test_replay_from_verify(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, make_scripted_backend())
    run = mgr.create(str(PYREPO))
    mgr.start(run, block=True)
    assert mgr.get(run.id).status == "completed"  # type: ignore[union-attr]
    n_events = len(mgr.events(run.id))
    mgr.resume(run.id, from_node="verify", block=True)
    final = mgr.get(run.id)
    assert final and final.status == "completed"
    assert len(mgr.events(run.id)) > n_events
    assert [f.status for f in mgr.findings(run.id)] == ["fixed"]


# --------------------------------------------------------------------------- API
@pytest.mark.timeout(300)
def test_api_end_to_end_with_roles(tmp_path: Path) -> None:
    keys = [
        ApiKey(key="v", name="viewer", role="viewer"),
        ApiKey(key="o", name="op", role="operator"),  # type: ignore[arg-type]
        ApiKey(key="a", name="root", role="admin"),
    ]  # type: ignore[arg-type]
    mgr = _manager(tmp_path, make_scripted_backend(), api_keys=keys, api_rate_limit_per_minute=1000)
    app = create_app(mgr.settings, manager=mgr)
    c = TestClient(app)

    assert c.get("/health").json()["checks"]["auth"] == "api-key"
    assert c.get("/api/v1/stats").status_code == 401
    assert (
        c.get("/api/v1/stats", headers={"X-API-Key": "bad"}).json()["error"]["code"]
        == "unauthorized"
    )
    assert c.get("/api/v1/me", headers={"X-API-Key": "v"}).json() == {
        "name": "viewer",
        "role": "viewer",
    }

    body = {"repo": str(PYREPO), "max_usd": 5, "max_minutes": 10, "max_findings": 10, "arm": "full"}
    assert c.post("/api/v1/runs", json=body, headers={"X-API-Key": "v"}).status_code == 403
    r = c.post("/api/v1/runs", json=body, headers={"X-API-Key": "o"})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    _wait(mgr, run_id)

    run = c.get(f"/api/v1/runs/{run_id}", headers={"X-API-Key": "v"}).json()
    assert run["status"] == "completed" and run["counts"] == {"fixed": 1} and run["progress"] == 1.0
    fs = c.get(f"/api/v1/runs/{run_id}/findings", headers={"X-API-Key": "v"}).json()["items"]
    assert len(fs) == 1 and fs[0]["status"] == "fixed"
    assert (
        c.get(f"/api/v1/runs/{run_id}/findings?status=refuted", headers={"X-API-Key": "v"}).json()[
            "items"
        ]
        == []
    )
    assert (
        c.get(f"/api/v1/runs/{run_id}/report.html", headers={"X-API-Key": "v"}).status_code == 200
    )
    stats = c.get("/api/v1/stats", headers={"X-API-Key": "v"}).json()
    assert stats["runs_total"] == 1 and stats["fixed_total"] == 1 and stats["cost_usd_total"] > 0

    with c.stream("GET", f"/api/v1/runs/{run_id}/events", headers={"X-API-Key": "v"}) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text()).replace("\r\n", "\n")
    assert "event: node.start" in text and "event: run.end" in text and "id: 1\n" in text

    assert c.get("/api/v1/settings", headers={"X-API-Key": "o"}).status_code == 403
    settings = c.get("/api/v1/settings", headers={"X-API-Key": "a"}).json()
    assert settings["api_keys"][0]["key"] == "***"
    assert c.delete(f"/api/v1/runs/{run_id}", headers={"X-API-Key": "o"}).status_code == 403
    assert c.delete(f"/api/v1/runs/{run_id}", headers={"X-API-Key": "a"}).status_code == 204
    assert c.get(f"/api/v1/runs/{run_id}", headers={"X-API-Key": "v"}).status_code == 404
    assert (
        c.get("/api/v1/runs/nope", headers={"X-API-Key": "v"}).json()["error"]["code"]
        == "not_found"
    )


def test_api_rate_limit(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, make_scripted_backend(), api_rate_limit_per_minute=3)
    c = TestClient(create_app(mgr.settings, manager=mgr))
    codes = [c.get("/api/v1/me").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
