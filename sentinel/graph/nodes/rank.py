"""Rank node: severity × confidence × blast radius ordering; LLM explanation per verified finding."""

from __future__ import annotations

from sentinel.graph.state import SEVERITY_WEIGHT, AuditState, Finding
from sentinel.llm.prompts import load_prompt
from sentinel.llm.schemas import ExplainOutput
from sentinel.tools.context import RunContext

STATUS_ORDER = {
    "pr_opened": 0,
    "fixed": 0,
    "verified": 1,
    "candidate": 2,
    "regressed": 1,
    "test_written": 2,
    "refuted": 3,
}


def score(f: Finding) -> float:
    return (
        SEVERITY_WEIGHT[f.severity] * (0.5 + f.confidence) * (1.0 + min(f.blast_radius, 20) / 10.0)
    )


def rank_node(state: AuditState, ctx: RunContext) -> AuditState:
    findings = list(state.get("findings", []))
    if not findings:
        return {}
    for f in findings:
        if ctx.callgraph and f.symbol and not f.blast_radius:
            f.blast_radius = ctx.callgraph.blast_radius(f.symbol)
    ordered = sorted(findings, key=lambda f: (STATUS_ORDER.get(f.status, 9), -score(f)))
    prompt = load_prompt("explain")
    updates: list[Finding] = []
    for i, f in enumerate(ordered, 1):
        upd: dict[str, object] = {"rank": i}
        if (
            f.status in ("verified", "fixed", "regressed")
            and not f.explanation
            and ctx.arm not in ("analyzers",)
        ):
            try:
                out, _ = ctx.router.structured(
                    ExplainOutput,
                    prompt.render(
                        category=f.category,
                        severity=f.severity,
                        file=f.file,
                        line_start=f.line_start,
                        symbol=f.symbol or "-",
                        description=f.description,
                        hypothesis=f.hypothesis,
                        test_code=(f.test_code or "")[:3000],
                        patch_diff=(f.patch_diff or "(no patch)")[:3000],
                    ),
                    tier="cheap",
                    prompt_name="explain",
                    prompt_version=prompt.version,
                )
                upd["explanation"] = out.summary
            except Exception as e:  # noqa: BLE001
                upd["explanation"] = (
                    f"{f.description} (explanation unavailable: {type(e).__name__})"
                )
        updates.append(f.model_copy(update=upd))
    ctx.log(
        f"rank: {len(updates)} findings ordered; "
        f"{sum(1 for f in updates if f.status in ('fixed', 'verified', 'regressed'))} actionable"
    )
    return {"findings": updates}
