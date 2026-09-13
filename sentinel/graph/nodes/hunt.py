"""Hunt node: one ReAct sub-agent per target (fan-out via Send), hard-capped tool calls."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sentinel.graph.state import AuditState, Finding, HuntTarget
from sentinel.indexing.treesitter import parse_file
from sentinel.llm.prompts import load_prompt
from sentinel.llm.schemas import HuntOutput
from sentinel.tools.context import RunContext
from sentinel.tools.toolbox import ToolBox


def _excerpt(ctx: RunContext, target: HuntTarget, max_lines: int = 120) -> str:
    p = ctx.repo_path / target.file
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    start, end = 1, min(len(lines), max_lines)
    if target.symbol:
        pf = parse_file(ctx.repo_path, target.file)
        for s in pf.symbols if pf else []:
            if s.name == target.symbol or s.short_name == target.symbol:
                start = max(1, s.line_start - 5)
                end = min(len(lines), max(s.line_end + 5, start + 20))
                break
    return "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(start, end + 1))


def _to_finding(raw: dict[str, Any], target: HuntTarget, cost: float) -> Finding | None:
    try:
        return Finding(
            id=f"f_{uuid.uuid4().hex[:10]}",
            category=raw["category"],
            severity=raw["severity"],
            file=(raw.get("file") or target.file).replace("\\", "/").removeprefix("./"),
            line_start=int(raw["line_start"]),
            line_end=max(int(raw["line_end"]), int(raw["line_start"])),
            symbol=raw.get("symbol") or target.symbol,
            description=raw["description"],
            evidence=list(raw.get("evidence") or []),
            hypothesis=raw["hypothesis"],
            confidence=float(raw["confidence"]),
            hunter_confidence=float(raw["confidence"]),
            status="candidate",
            cost_usd=cost,
        )
    except Exception:  # noqa: BLE001 - malformed finding from the model is dropped, not fatal
        return None


def hunt_one(payload: dict[str, Any], ctx: RunContext) -> AuditState:
    """Node body for a single target. `payload` = {"target": HuntTarget-dict, ...}."""
    target = HuntTarget.model_validate(payload["target"])
    if ctx.cancel_event.is_set():
        return {"hunted_targets": 1}
    system = load_prompt("hunt_system")
    user = load_prompt("hunt_target")
    hits = [f for f in ctx.analyzer_findings if f.file == target.file]
    hit_text = (
        "\n".join(
            f"{f.tool} {f.rule_id} L{f.line_start}: {f.message} [{f.category_hint or '-'}]"
            for f in hits[:25]
        )
        or "none"
    )
    toolbox = ToolBox(ctx, include_write_tools=False)
    max_calls = ctx.settings.hunt_max_tool_calls
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system.text},
        {
            "role": "user",
            "content": user.render(
                language=ctx.language,
                file=target.file,
                symbol=target.symbol or "(whole file)",
                reason=target.reason,
                analyzer_hits=hit_text,
                excerpt=_excerpt(ctx, target),
                max_tool_calls=max_calls,
            ),
        },
    ]
    cost_before = ctx.router.total_cost_usd
    schemas = toolbox.schemas()
    for _ in range(max_calls + 1):
        resp = ctx.router.chat(
            messages,
            tools=schemas if toolbox.calls < max_calls else None,
            tier="cheap",
            prompt_name="hunt_target",
            prompt_version=user.version,
        )
        if not resp.tool_calls:
            messages.append({"role": "assistant", "content": resp.content or ""})
            break
        messages.append(
            {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in resp.tool_calls
                ],
            }
        )
        for tc in resp.tool_calls:
            if toolbox.calls >= max_calls:
                result = "tool budget exhausted; produce your final HuntOutput now"
            else:
                result = toolbox.call(tc["name"], tc["arguments"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": result[:12000],
                }
            )
        if ctx.cancel_event.is_set():
            break

    final_user = (
        "Return your final findings for this target as HuntOutput JSON now. "
        "Only include findings with a concrete triggering input."
    )
    try:
        out, _ = ctx.router.structured(
            HuntOutput,
            final_user,
            system=system.text,
            tier="cheap",
            history=messages[1:],
            prompt_name="hunt_final",
            prompt_version=system.version,
        )
    except Exception as e:  # noqa: BLE001
        ctx.log(f"hunt {target.file}: final output invalid ({type(e).__name__})", level="warning")
        return {"hunted_targets": 1, "errors": [f"hunt {target.file}: {e}"]}
    cost = ctx.router.total_cost_usd - cost_before
    per = cost / max(1, len(out.findings))
    findings = [
        f
        for f in (_to_finding(json.loads(h.model_dump_json()), target, per) for h in out.findings)
        if f
    ]
    for f in findings:
        ctx.emit("finding.new", f.model_dump())
    ctx.log(f"hunt {target.file}: {len(findings)} candidate(s) after {toolbox.calls} tool calls")
    return {"findings": findings, "hunted_targets": 1}
