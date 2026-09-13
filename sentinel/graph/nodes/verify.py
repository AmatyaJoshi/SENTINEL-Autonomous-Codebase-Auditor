"""Verify node: write a failing test per candidate, run it in the sandbox, accept only when it fails
for the hypothesised reason (SPEC §3.2). Up to 3 attempts with failure feedback."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from sentinel.graph.state import AuditState, Finding
from sentinel.llm.prompts import load_prompt
from sentinel.llm.schemas import TestOutput
from sentinel.sandbox.test_runner import TestReport, run_tests
from sentinel.tools.context import RunContext

MAX_ATTEMPTS = 3


def suggested_test_path(f: Finding, language: str) -> str:
    stem = Path(f.file).stem
    if language == "python":
        return f"tests/test_sentinel_{stem}_{f.id[-6:]}.py"
    return f"{Path(f.file).parent.as_posix()}/{stem}.sentinel-{f.id[-6:]}.test.ts"


def test_conventions(ctx: RunContext) -> str:
    root = ctx.repo_path
    samples = []
    for pat in (
        "tests/test_*.py",
        "test/test_*.py",
        "**/*.test.ts",
        "**/*.spec.ts",
        "**/*.test.js",
    ):
        for p in list(root.glob(pat))[:1]:
            with contextlib.suppress(OSError):
                samples.append(
                    f"### {p.relative_to(root).as_posix()}\n"
                    + "\n".join(p.read_text(encoding="utf-8", errors="replace").split("\n")[:40])
                )
        if samples:
            break
    return "\n".join(samples) or "no existing tests found; use standard pytest/vitest style"


def _code_excerpt(ctx: RunContext, f: Finding, pad: int = 30) -> str:
    try:
        lines = (ctx.repo_path / f.file).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    s, e = max(1, f.line_start - pad), min(len(lines), f.line_end + pad)
    return "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(s, e + 1))


def failure_matches(expected: str, report: TestReport) -> tuple[bool, str]:
    """Did the new test fail *for the hypothesised reason*? Compare exception type / assertion."""
    failing = [c for c in report.cases if c.status in ("failed", "error")]
    if not failing:
        return False, "test did not fail"
    exp = expected.lower()
    exp_types = set(re.findall(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning))\b", expected))
    for c in failing:
        blob = f"{c.failure_type} {c.message}".lower()
        if (
            c.status == "error"
            and "import" in blob
            and "error" in blob
            and "importerror" not in exp
        ):
            return False, f"collection/import error, not the hypothesis: {c.message[:200]}"
        if exp_types and any(t.lower() in blob for t in exp_types):
            return True, f"failed with expected {sorted(exp_types)}"
        if ("assert" in exp or "expected" in exp) and ("assert" in blob or "expected" in blob):
            return True, "failed with expected assertion"
        if not exp_types and c.status == "failed":
            return True, "failed (no specific type expected)"
    return (
        False,
        f"failed for a different reason: {failing[0].failure_type} {failing[0].message[:200]}",
    )


def verify_node(state: AuditState, ctx: RunContext) -> AuditState:
    candidates = [f for f in state.get("findings", []) if f.status == "candidate"]
    if not candidates:
        return {}
    if (
        not ctx.sandbox_ready()
        or ctx.sandbox is None
        or ctx.workspace is None
        or ctx.sandbox_image is None
    ):
        ctx.log("verify skipped: sandbox unavailable; candidates stay unverified", level="warning")
        return {
            "findings": [
                f.model_copy(update={"verify_log": "not verified: sandbox unavailable"})
                for f in candidates
            ],
            "errors": ["verify: sandbox unavailable"],
        }
    if ctx.arm in ("analyzers", "single_shot"):
        return {}

    prompt = load_prompt("verify_write_test")
    conventions = test_conventions(ctx)
    updates: list[Finding] = []
    for f in candidates:
        if ctx.cancel_event.is_set():
            break
        updates.append(_verify_one(ctx, f, prompt, conventions))
    verified = sum(1 for u in updates if u.status == "verified")
    ctx.log(f"verify: {verified}/{len(updates)} candidates verified with failing tests")
    return {"findings": updates}


def _verify_one(ctx: RunContext, f: Finding, prompt, conventions: str) -> Finding:  # type: ignore[no-untyped-def]
    sandbox, workspace, image = ctx.sandbox, ctx.workspace, ctx.sandbox_image
    assert sandbox is not None and workspace is not None and image is not None
    feedback = ""
    log_parts: list[str] = []
    cost_before = ctx.router.total_cost_usd
    test_rel = suggested_test_path(f, ctx.language)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            out, _ = ctx.router.structured(
                TestOutput,
                prompt.render(
                    language=ctx.language,
                    framework=ctx.framework,
                    category=f.category,
                    file=f.file,
                    line_start=f.line_start,
                    line_end=f.line_end,
                    symbol=f.symbol or "-",
                    description=f.description,
                    hypothesis=f.hypothesis,
                    code=_code_excerpt(ctx, f),
                    test_conventions=conventions,
                    suggested_test_path=test_rel,
                    feedback=f"## Previous attempt feedback\n{feedback}" if feedback else "",
                ),
                tier="strong",
                prompt_name="verify_write_test",
                prompt_version=prompt.version,
            )
        except Exception as e:  # noqa: BLE001
            log_parts.append(f"attempt {attempt}: LLM output invalid: {e}")
            feedback = f"Your previous output did not validate: {e}"
            continue
        rel = out.test_path.replace("\\", "/").removeprefix("./") or test_rel
        if ".." in rel.split("/") or not rel.endswith((".py", ".ts", ".tsx", ".js", ".mjs")):
            rel = test_rel
        marker = "SENTINEL: expected to FAIL"
        if marker not in out.test_code:
            feedback = f"The test is missing the required comment line containing `{marker}`."
            log_parts.append(f"attempt {attempt}: missing SENTINEL marker")
            continue
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out.test_code, encoding="utf-8")
        tr = run_tests(sandbox, image, workspace, ctx.framework, [rel])
        ctx.emit(
            "sandbox.exec",
            {
                "node": "verify",
                "command": " ".join(tr.exec.command)[:200],
                "exit_code": tr.exec.exit_code,
                "duration_s": round(tr.exec.duration_s, 2),
                "timed_out": tr.exec.timed_out,
                "finding_id": f.id,
            },
        )
        rep = tr.report
        if tr.exec.timed_out:
            feedback = "The test timed out (120 s). Make it fast and deterministic."
            log_parts.append(f"attempt {attempt}: timeout")
            target.unlink(missing_ok=True)
            continue
        if rep.parse_error or rep.total == 0:
            feedback = f"No tests were collected/ran. Output:\n{tr.exec.combined[-1500:]}"
            log_parts.append(f"attempt {attempt}: no tests ran ({rep.parse_error})")
            target.unlink(missing_ok=True)
            continue
        ok, why = failure_matches(out.expected_failure, rep)
        log_parts.append(f"attempt {attempt}: {why}")
        if ok:
            cost = ctx.router.total_cost_usd - cost_before
            nf = f.model_copy(
                update={
                    "status": "verified",
                    "test_path": rel,
                    "test_code": out.test_code,
                    "expected_failure": out.expected_failure,
                    "verify_log": "\n".join(log_parts) + "\n\n" + tr.exec.combined[-3000:],
                    "cost_usd": f.cost_usd + cost,
                }
            )
            ctx.emit("finding.update", nf.model_dump())
            ctx.emit(
                "evaluation",
                {"finding_id": f.id, "predicted_conf": f.confidence, "outcome": "verified"},
            )
            return nf
        target.unlink(missing_ok=True)
        if rep.failed == 0 and rep.errors == 0:
            feedback = (
                "The test PASSED on the current code, so it does not reproduce the bug. Either the "
                "hypothesis is wrong or the input does not trigger it. Re-read the code; if you cannot "
                "make it fail honestly, return a test that asserts the correct behaviour anyway."
            )
        else:
            feedback = f"{why}\nTest output:\n{tr.exec.combined[-1500:]}"
    cost = ctx.router.total_cost_usd - cost_before
    nf = f.model_copy(
        update={
            "status": "refuted",
            "verify_log": "\n".join(log_parts) or "no attempts",
            "cost_usd": f.cost_usd + cost,
        }
    )
    ctx.emit("finding.update", nf.model_dump())
    ctx.emit(
        "evaluation", {"finding_id": f.id, "predicted_conf": f.confidence, "outcome": "refuted"}
    )
    return nf
