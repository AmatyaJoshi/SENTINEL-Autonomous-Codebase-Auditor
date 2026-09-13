"""Fix and regress nodes (SPEC §3.2): minimal patch that makes the new test pass; then the existing
suite must not regress. Regressed patches are discarded, the finding stays verified-but-unfixed."""

from __future__ import annotations

import shutil
from pathlib import Path

from sentinel.graph.nodes.pipeline_nodes import baseline_failures
from sentinel.graph.state import AuditState, Finding
from sentinel.llm.prompts import load_prompt
from sentinel.llm.schemas import PatchOutput
from sentinel.sandbox.test_runner import run_tests
from sentinel.tools.context import RunContext
from sentinel.tools.toolbox import apply_unified_diff, count_changed_lines, files_in_diff

MAX_ATTEMPTS = 3


def _numbered(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    return "\n".join(f"{i:5d} | {ln}" for i, ln in enumerate(lines, 1))


class _Snapshot:
    """Restore touched files after a failed attempt (workspace has no .git)."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.saved: dict[str, bytes | None] = {}

    def save(self, rels: list[str]) -> None:
        for r in rels:
            p = self.workspace / r
            self.saved.setdefault(r, p.read_bytes() if p.exists() else None)

    def restore(self) -> None:
        for r, data in self.saved.items():
            p = self.workspace / r
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
        self.saved.clear()


def fix_node(state: AuditState, ctx: RunContext) -> AuditState:
    verified = [f for f in state.get("findings", []) if f.status == "verified"]
    if not verified or ctx.arm in ("analyzers", "single_shot"):
        return {}
    if (
        not ctx.sandbox_ready()
        or ctx.sandbox is None
        or ctx.workspace is None
        or ctx.sandbox_image is None
    ):
        return {"errors": ["fix: sandbox unavailable"]}
    prompt = load_prompt("fix")
    updates = [_fix_one(ctx, f, prompt) for f in verified if not ctx.cancel_event.is_set()]
    fixed = sum(1 for u in updates if u.patch_diff)
    ctx.log(f"fix: {fixed}/{len(updates)} verified findings patched (new test passes)")
    return {"findings": updates}


def _fix_one(ctx: RunContext, f: Finding, prompt) -> Finding:  # type: ignore[no-untyped-def]
    sandbox, workspace, image = ctx.sandbox, ctx.workspace, ctx.sandbox_image
    assert sandbox is not None and workspace is not None and image is not None and f.test_path
    src = workspace / f.file
    if not src.exists():
        return f.model_copy(update={"regress_log": f"fix skipped: {f.file} not in workspace"})
    failure_output = (f.verify_log or "")[-2500:]
    feedback = ""
    log: list[str] = []
    snap = _Snapshot(workspace)
    cost_before = ctx.router.total_cost_usd
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            out, _ = ctx.router.structured(
                PatchOutput,
                prompt.render(
                    language=ctx.language,
                    category=f.category,
                    file=f.file,
                    line_start=f.line_start,
                    line_end=f.line_end,
                    description=f.description,
                    hypothesis=f.hypothesis,
                    test_code=f.test_code or "",
                    failure_output=failure_output,
                    source_numbered=_numbered(src),
                    max_lines=ctx.settings.max_patch_lines,
                    feedback=f"## Previous attempt feedback\n{feedback}" if feedback else "",
                ),
                tier="strong",
                prompt_name="fix",
                prompt_version=prompt.version,
            )
        except Exception as e:  # noqa: BLE001
            log.append(f"attempt {attempt}: LLM output invalid: {e}")
            feedback = f"Your previous output did not validate: {e}"
            continue
        diff = out.diff
        changed = count_changed_lines(diff)
        touched = files_in_diff(diff)
        if changed > ctx.settings.max_patch_lines:
            feedback = f"Patch changes {changed} lines; limit is {ctx.settings.max_patch_lines}. Make it smaller."
            log.append(f"attempt {attempt}: too large ({changed} lines)")
            continue
        if any(t == f.test_path or "/test" in "/" + t.lower() for t in touched):
            feedback = "The patch modifies test files. Fix the source only."
            log.append(f"attempt {attempt}: touched tests")
            continue
        snap.save(touched)
        ok, msg = apply_unified_diff(workspace, diff)
        if not ok:
            snap.restore()
            feedback = f"`git apply --check` rejected the diff: {msg}\nRegenerate against the numbered source."
            log.append(f"attempt {attempt}: apply failed: {msg[:120]}")
            continue
        tr = run_tests(sandbox, image, workspace, ctx.framework, [f.test_path])
        ctx.emit(
            "sandbox.exec",
            {
                "node": "fix",
                "command": " ".join(tr.exec.command)[:200],
                "exit_code": tr.exec.exit_code,
                "duration_s": round(tr.exec.duration_s, 2),
                "timed_out": tr.exec.timed_out,
                "finding_id": f.id,
            },
        )
        rep = tr.report
        if rep.total > 0 and rep.ok:
            log.append(f"attempt {attempt}: new test passes with patch")
            cost = ctx.router.total_cost_usd - cost_before
            nf = f.model_copy(
                update={
                    "patch_diff": diff,
                    "regress_log": "\n".join(log),
                    "cost_usd": f.cost_usd + cost,
                    "evidence": [*f.evidence, f"fix rationale: {out.rationale}"],
                }
            )
            ctx.emit("finding.update", nf.model_dump())
            snap.restore()  # keep the shared workspace clean; regress re-applies each patch alone
            return nf
        snap.restore()
        feedback = f"With your patch applied the test still fails:\n{tr.exec.combined[-1500:]}"
        log.append(f"attempt {attempt}: test still failing")
    cost = ctx.router.total_cost_usd - cost_before
    return f.model_copy(
        update={"regress_log": "fix failed:\n" + "\n".join(log), "cost_usd": f.cost_usd + cost}
    )


def regress_node(state: AuditState, ctx: RunContext) -> AuditState:
    patched = [f for f in state.get("findings", []) if f.status == "verified" and f.patch_diff]
    if not patched:
        return {}
    if (
        not ctx.sandbox_ready()
        or ctx.sandbox is None
        or ctx.workspace is None
        or ctx.sandbox_image is None
    ):
        return {"errors": ["regress: sandbox unavailable"]}
    sandbox, workspace, image = ctx.sandbox, ctx.workspace, ctx.sandbox_image
    updates: list[Finding] = []
    # Each patch is evaluated in isolation: reset to a clean copy, apply just this patch + its test.
    clean = ctx.run_dir / "workspace-clean"
    if not clean.exists():
        shutil.copytree(
            workspace,
            clean,
            ignore=shutil.ignore_patterns(
                ".sentinel", "node_modules", "*sentinel_*", "*.sentinel-*"
            ),
        )
    _reset_workspace(workspace, clean)
    _remove_generated_tests(workspace)
    baseline = baseline_failures(ctx)
    for f in patched:
        if ctx.cancel_event.is_set():
            break
        _reset_workspace(workspace, clean)
        _remove_generated_tests(workspace)
        if f.test_path and f.test_code:
            tp = workspace / f.test_path
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text(f.test_code, encoding="utf-8")
        ok, msg = apply_unified_diff(workspace, f.patch_diff or "")
        if not ok:
            updates.append(
                f.model_copy(
                    update={"status": "regressed", "regress_log": f"patch no longer applies: {msg}"}
                )
            )
            continue
        tr = run_tests(
            sandbox,
            image,
            workspace,
            ctx.framework,
            None,
            timeout_s=ctx.settings.sandbox.suite_timeout_s,
        )
        ctx.emit(
            "sandbox.exec",
            {
                "node": "regress",
                "command": "full suite",
                "exit_code": tr.exec.exit_code,
                "duration_s": round(tr.exec.duration_s, 1),
                "timed_out": tr.exec.timed_out,
                "finding_id": f.id,
            },
        )
        new_failures = tr.report.failing_ids() - baseline
        new_failures -= {
            c.id
            for c in tr.report.cases
            if f.test_path and f.test_path.split("/")[-1].split(".")[0] in c.classname
        }
        if tr.exec.timed_out:
            status, note = "regressed", "suite timed out with patch applied"
        elif new_failures:
            # flaky guard: re-run once, only persistent failures count
            tr2 = run_tests(
                sandbox,
                image,
                workspace,
                ctx.framework,
                None,
                timeout_s=ctx.settings.sandbox.suite_timeout_s,
            )
            persistent = new_failures & tr2.report.failing_ids()
            if persistent:
                status, note = "regressed", "new failures: " + ", ".join(sorted(persistent)[:10])
            else:
                status, note = (
                    "fixed",
                    f"suite green ({tr.report.total} tests; {len(new_failures)} flaky ignored)",
                )
        else:
            status, note = (
                "fixed",
                f"suite green ({tr.report.total} tests, {tr.report.passed} passed)",
            )
        nf = f.model_copy(
            update={
                "status": status,
                "regress_log": (f.regress_log or "")
                + "\n"
                + ("REGRESSED: " if status == "regressed" else "")
                + note,
                "patch_diff": f.patch_diff if status == "fixed" else None,
                "evidence": [*f.evidence, "patch discarded: " + note]
                if status == "regressed"
                else f.evidence,
            }
        )
        ctx.emit("finding.update", nf.model_dump())
        updates.append(nf)
    _reset_workspace(workspace, clean)
    fixed = sum(1 for u in updates if u.status == "fixed")
    ctx.log(f"regress: {fixed}/{len(updates)} patches keep the existing suite green")
    return {"findings": updates}


def _remove_generated_tests(workspace: Path) -> None:
    """Drop every Sentinel-written test so each patch is judged against the repo's own suite."""
    for p in list(workspace.rglob("test_sentinel_*.py")) + list(
        workspace.rglob("*.sentinel-*.test.*")
    ):
        p.unlink(missing_ok=True)


def _reset_workspace(workspace: Path, clean: Path) -> None:
    for child in workspace.iterdir():
        if child.name in (".sentinel", "node_modules"):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    for child in clean.iterdir():
        dst = workspace / child.name
        if child.is_dir():
            shutil.copytree(child, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(child, dst)
