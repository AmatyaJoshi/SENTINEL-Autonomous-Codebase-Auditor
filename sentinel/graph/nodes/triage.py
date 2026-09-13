"""Triage node: dedupe candidates and blend hunter confidence with the triage classifier (§7)."""

from __future__ import annotations

from typing import Any

from sentinel.graph.state import AuditState, Finding
from sentinel.tools.context import RunContext
from sentinel.triage.model import load_triage_model


def _overlaps(a: Finding, b: Finding, slack: int = 3) -> bool:
    return (
        a.file == b.file
        and a.category == b.category
        and a.line_start <= b.line_end + slack
        and b.line_start <= a.line_end + slack
    )


def dedupe(findings: list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: -x.confidence):
        if any(_overlaps(f, k) for k in kept):
            continue
        kept.append(f)
    return kept


def triage_node(state: AuditState, ctx: RunContext) -> AuditState:
    candidates = [f for f in state.get("findings", []) if f.status == "candidate"]
    if not candidates:
        return {}
    unique = dedupe(candidates)
    dropped_dupes = {f.id for f in candidates} - {f.id for f in unique}
    updates: list[Finding] = []
    for fid in dropped_dupes:
        f = next(x for x in candidates if x.id == fid)
        updates.append(
            f.model_copy(
                update={
                    "status": "refuted",
                    "verify_log": "triage: duplicate of a higher-confidence finding",
                }
            )
        )

    if ctx.arm == "no_triage" or ctx.arm == "single_shot":
        for f in unique:
            updates.append(f.model_copy(update={"triage_score": None}))
        ctx.log(f"triage disabled (arm={ctx.arm}): {len(unique)} candidates pass")
        return {"findings": updates}

    model = load_triage_model(ctx.settings)
    threshold = ctx.settings.triage_threshold
    passed = 0
    for f in unique:
        callers = len(ctx.callgraph.callers(f.symbol)) if (ctx.callgraph and f.symbol) else 0
        hits = [
            a
            for a in ctx.analyzer_findings
            if a.file == f.file and abs(a.line_start - f.line_start) <= 5
        ]
        score = model.predict(f, analyzer_hits=len(hits), callers=callers)
        blended = 0.5 * (f.hunter_confidence or f.confidence) + 0.5 * score
        upd: dict[str, Any] = {
            "triage_score": round(score, 4),
            "confidence": round(blended, 4),
            "blast_radius": callers,
        }
        if blended < threshold:
            upd |= {
                "status": "refuted",
                "verify_log": f"triage: blended confidence {blended:.2f} < {threshold} (model={model.name})",
            }
        else:
            passed += 1
        nf = f.model_copy(update=upd)
        updates.append(nf)
        ctx.emit("finding.update", nf.model_dump())
    ctx.log(
        f"triage ({model.name}): {passed}/{len(unique)} candidates pass, {len(dropped_dupes)} duplicates"
    )
    return {"findings": updates}
