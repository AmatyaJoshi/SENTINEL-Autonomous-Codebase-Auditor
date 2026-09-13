"""Report node: report.md / report.json / report.html into the run dir; optional PR opening."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from sentinel import __version__
from sentinel.graph.state import AuditState, Finding
from sentinel.tools.context import RunContext

TEMPLATE = Path(__file__).parent.parent.parent / "templates" / "report.html"


def summarize(state: AuditState, ctx: RunContext) -> dict[str, Any]:
    findings = state.get("findings", [])
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    return {
        "run_id": state.get("run_id"),
        "repo_url": state.get("repo_url"),
        "repo_path": state.get("repo_path"),
        "commit_sha": state.get("commit_sha"),
        "language": state.get("language"),
        "arm": ctx.arm,
        "sentinel_version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(time.time() - ctx.started_at, 1) if ctx.started_at else None,
        "cost_usd": round(ctx.router.total_cost_usd, 4),
        "llm_calls": ctx.router.total_calls,
        "budget": (state.get("budget") or {}) and state["budget"].model_dump(),
        "stopped_early": state.get("stopped_early"),
        "sandbox_ready": state.get("sandbox_ready", False),
        "counts": counts,
        "errors": state.get("errors", []),
        "findings": [f.model_dump() for f in findings],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    fs = [Finding.model_validate(f) for f in summary["findings"]]
    lines = [
        f"# Sentinel audit report — `{summary['repo_url']}` @ `{(summary.get('commit_sha') or '')[:10]}`",
        "",
        f"Generated {summary['generated_at']} by Sentinel {summary['sentinel_version']} · arm `{summary['arm']}` · "
        f"{summary['llm_calls']} LLM calls · ${summary['cost_usd']:.2f} · {summary['duration_s']}s",
        "",
    ]
    if not summary["sandbox_ready"]:
        lines += [
            "> **Sandbox unavailable:** findings below are UNVERIFIED candidates. No executable proof.",
            "",
        ]
    if summary.get("stopped_early"):
        lines += [f"> Stopped early: {summary['stopped_early']}", ""]
    c = summary["counts"]
    lines += (
        ["| status | count |", "|---|---|"]
        + [f"| {k} | {v} |" for k, v in sorted(c.items())]
        + [""]
    )
    actionable = [f for f in fs if f.status in ("fixed", "pr_opened", "verified", "regressed")]
    lines += [f"## Verified bugs ({len(actionable)})", ""]
    for f in actionable:
        lines += [
            f"### {f.rank}. [{f.severity}] {f.category} — `{f.file}:{f.line_start}` {f.symbol or ''}",
            "",
            f.explanation or f.description,
            "",
            f"**Triggering input:** {f.hypothesis}",
            "",
            f"**Status:** {f.status} · confidence {f.confidence:.2f} · blast radius {f.blast_radius}"
            + (f" · PR {f.pr_url}" if f.pr_url else ""),
            "",
        ]
        if f.test_code:
            lines += [
                f"<details><summary>Failing test `{f.test_path}`</summary>",
                "",
                "```",
                f.test_code,
                "```",
                "</details>",
                "",
            ]
        if f.patch_diff:
            lines += [
                "<details><summary>Patch</summary>",
                "",
                "```diff",
                f.patch_diff,
                "```",
                "</details>",
                "",
            ]
        if f.regress_log and "REGRESSED" in f.regress_log:
            lines += [
                f"> Patch discarded: {f.regress_log.split('REGRESSED:')[-1].strip()[:300]}",
                "",
            ]
    rest = [f for f in fs if f.status not in ("fixed", "pr_opened", "verified", "regressed")]
    lines += [
        f"## Unverified suspicions ({len(rest)})",
        "",
        "Reported for transparency; these did NOT survive verification.",
        "",
        "| # | category | location | confidence | status | reason |",
        "|---|---|---|---|---|---|",
    ]
    for f in rest:
        reason = (f.verify_log or "").split("\n")[0][:80]
        lines.append(
            f"| {f.rank} | {f.category} | `{f.file}:{f.line_start}` | {f.confidence:.2f} | {f.status} | {reason} |"
        )
    if summary["errors"]:
        lines += ["", "## Errors", ""] + [f"- {e}" for e in summary["errors"]]
    return "\n".join(lines) + "\n"


def render_html(summary: dict[str, Any], markdown: str) -> str:
    if TEMPLATE.exists():
        tpl = TEMPLATE.read_text(encoding="utf-8")
        return (
            tpl.replace("{{TITLE}}", html.escape(f"Sentinel report — {summary['repo_url']}"))
            .replace("{{SUMMARY_JSON}}", json.dumps(summary).replace("</", "<\\/"))
            .replace("{{MARKDOWN}}", html.escape(markdown))
        )
    return f"<pre>{html.escape(markdown)}</pre>"


def report_node(state: AuditState, ctx: RunContext) -> AuditState:
    findings = list(state.get("findings", []))
    updates: list[Finding] = []
    if ctx.open_pr and ctx.arm == "full":
        from sentinel.github.pr import open_pull_requests

        try:
            updates = open_pull_requests(
                ctx,
                state,
                [f for f in findings if f.status == "fixed" and f.review_decision != "reject"],
            )
        except Exception as e:  # noqa: BLE001 - PR failure must not lose the report
            ctx.log(f"PR creation failed: {e}", level="error")
            state = {**state, "errors": [*state.get("errors", []), f"pr: {e}"]}
    if updates:
        by = {u.id: u for u in updates}
        findings = [by.get(f.id, f) for f in findings]
        state = {**state, "findings": findings}
    summary = summarize(state, ctx)
    md = render_markdown(summary)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (ctx.run_dir / "report.md").write_text(md, encoding="utf-8")
    (ctx.run_dir / "report.html").write_text(render_html(summary, md), encoding="utf-8")
    ctx.log(f"report written to {ctx.run_dir}")
    return {"findings": updates} if updates else {}
