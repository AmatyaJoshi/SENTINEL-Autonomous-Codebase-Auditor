"""Agent tools (SPEC §4) as LangChain StructuredTools bound to a RunContext.

All results are wrapped in `<sentinel-data>` blocks: repository content is untrusted data.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from sentinel.indexing.store import SearchMode, hybrid_search
from sentinel.sandbox.test_runner import run_tests, snippet_command
from sentinel.tools.context import RunContext, wrap_data

MAX_LINES = 300


# --------------------------------------------------------------------------- arg schemas
class ReadFileArgs(BaseModel):
    path: str = Field(description="Repo-relative path")
    start: int | None = Field(default=None, description="1-based first line (inclusive)")
    end: int | None = Field(default=None, description="1-based last line (inclusive)")


class SearchArgs(BaseModel):
    query: str
    k: int = Field(default=8, ge=1, le=20)
    mode: str = Field(default="hybrid", description="hybrid | bm25 | vector")


class SymbolArgs(BaseModel):
    name: str = Field(description="Symbol name, e.g. Session.lookup or parse_token")


class PathArgs(BaseModel):
    path: str


class BlameArgs(BaseModel):
    path: str
    line: int = Field(ge=1)


class SnippetArgs(BaseModel):
    code: str = Field(description="Self-contained script; may import the repo's modules")
    lang: str = Field(default="python", description="python | javascript")


class RunTestsArgs(BaseModel):
    paths: list[str] | None = Field(default=None, description="Test files/dirs; None = whole suite")


class PatchArgs(BaseModel):
    diff: str = Field(description="Unified diff (a/ b/ prefixes)")


# --------------------------------------------------------------------------- toolbox
class ToolBox:
    def __init__(self, ctx: RunContext, include_write_tools: bool = False) -> None:
        self.ctx = ctx
        self.calls = 0
        self.tools: list[BaseTool] = self._build(include_write_tools)
        self._by_name = {t.name: t for t in self.tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [convert_to_openai_tool(t) for t in self.tools]

    def call(self, name: str, arguments: str | dict[str, Any]) -> str:
        self.calls += 1
        tool = self._by_name.get(name)
        if tool is None:
            return wrap_data("error", f"unknown tool {name!r}")
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as e:
            return wrap_data("error", f"invalid JSON arguments: {e}")
        try:
            out = tool.invoke(args)
            return str(out)
        except Exception as e:  # noqa: BLE001 - tool errors are data for the agent
            return wrap_data("error", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ implementations
    def _build(self, include_write_tools: bool) -> list[BaseTool]:
        tools: list[BaseTool] = [
            StructuredTool.from_function(
                func=self.read_file,
                name="read_file",
                args_schema=ReadFileArgs,
                description="Read a file with line numbers. Max 300 lines per call.",
            ),
            StructuredTool.from_function(
                func=self.search_code,
                name="search_code",
                args_schema=SearchArgs,
                description="Hybrid BM25+vector search over the indexed repo; returns chunks with file:lines.",
            ),
            StructuredTool.from_function(
                func=self.get_symbol,
                name="get_symbol",
                args_schema=SymbolArgs,
                description="Definition, docstring and location of a symbol from the tree-sitter index.",
            ),
            StructuredTool.from_function(
                func=self.get_callers,
                name="get_callers",
                args_schema=SymbolArgs,
                description="Symbols that call the given symbol (from the call graph).",
            ),
            StructuredTool.from_function(
                func=self.get_callees,
                name="get_callees",
                args_schema=SymbolArgs,
                description="Symbols the given symbol calls.",
            ),
            StructuredTool.from_function(
                func=self.get_analyzer_hits,
                name="get_analyzer_hits",
                args_schema=PathArgs,
                description="Static-analyzer findings for a file, normalised.",
            ),
            StructuredTool.from_function(
                func=self.git_blame,
                name="git_blame",
                args_schema=BlameArgs,
                description="Author, date and commit message for a line. Recent churn is a risk signal.",
            ),
            StructuredTool.from_function(
                func=self.run_snippet,
                name="run_snippet",
                args_schema=SnippetArgs,
                description="Run a short script in the network-isolated sandbox (30 s). Returns stdout/stderr/exit.",
            ),
        ]
        if include_write_tools:
            tools += [
                StructuredTool.from_function(
                    func=self.run_tests,
                    name="run_tests",
                    args_schema=RunTestsArgs,
                    description="Run tests in the sandbox and return structured results.",
                ),
                StructuredTool.from_function(
                    func=self.apply_patch,
                    name="apply_patch",
                    args_schema=PatchArgs,
                    description="Validate with `git apply --check` then apply a unified diff to the workspace.",
                ),
            ]
        return tools

    def _safe_path(self, path: str) -> Path:
        root = self.ctx.repo_path.resolve()
        p = (root / path).resolve()
        if root not in p.parents and p != root:
            raise PermissionError(f"path escapes repository: {path}")
        return p

    def read_file(self, path: str, start: int | None = None, end: int | None = None) -> str:
        p = self._safe_path(path)
        if not p.is_file():
            return wrap_data(path, "ERROR: not a file")
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        s = max(1, start or 1)
        e = min(len(lines), end or (s + MAX_LINES - 1))
        if e - s + 1 > MAX_LINES:
            e = s + MAX_LINES - 1
        body = "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(s, e + 1))
        note = f" (showing {s}-{e} of {len(lines)} lines)"
        return wrap_data(f"{path}{note}", body)

    def search_code(self, query: str, k: int = 8, mode: str = "hybrid") -> str:
        if self.ctx.store is None or self.ctx.embedder is None or self.ctx.repo_id is None:
            return wrap_data("search_code", "ERROR: index not available")
        search_mode: SearchMode = mode if mode in ("hybrid", "bm25", "vector") else "hybrid"  # type: ignore[assignment]
        hits = hybrid_search(
            self.ctx.store, self.ctx.embedder, self.ctx.repo_id, query, k=k, mode=search_mode
        )
        parts = []
        for h in hits:
            preview = "\n".join(h.text.split("\n")[:25])
            parts.append(f"## {h.location} {h.symbol or ''}\n{preview}")
        return wrap_data(f"search_code({query!r})", "\n\n".join(parts) or "no results")

    def get_symbol(self, name: str) -> str:
        if self.ctx.store is None or self.ctx.repo_id is None:
            return wrap_data("get_symbol", "ERROR: index not available")
        recs = self.ctx.store.get_symbol(self.ctx.repo_id, name)
        if not recs:
            return wrap_data(f"get_symbol({name!r})", "no such symbol")
        parts = []
        for r in recs[:3]:
            doc = f"\n/// {r.docstring}" if r.docstring else ""
            text = "\n".join(r.text.split("\n")[:MAX_LINES])
            parts.append(
                f"## {r.kind} {r.name} @ {r.file}:{r.line_start}-{r.line_end}{doc}\n{text}"
            )
        return wrap_data(f"get_symbol({name!r})", "\n\n".join(parts))

    def get_callers(self, name: str) -> str:
        cg = self.ctx.callgraph
        if cg is None:
            return wrap_data("get_callers", "ERROR: call graph not available")
        callers = cg.callers(name)
        return wrap_data(
            f"get_callers({name!r})",
            "\n".join(callers)
            if callers
            else "no callers found (may be an entry point or called dynamically)",
        )

    def get_callees(self, name: str) -> str:
        cg = self.ctx.callgraph
        if cg is None:
            return wrap_data("get_callees", "ERROR: call graph not available")
        callees = cg.callees(name)
        return wrap_data(f"get_callees({name!r})", "\n".join(callees) or "no resolved callees")

    def get_analyzer_hits(self, path: str) -> str:
        norm = path.replace("\\", "/").removeprefix("./")
        hits = [f for f in self.ctx.analyzer_findings if f.file == norm]
        lines = [
            f"{f.tool} {f.rule_id} L{f.line_start}: {f.message} [{f.category_hint or '-'}]"
            for f in hits
        ]
        return wrap_data(f"get_analyzer_hits({path!r})", "\n".join(lines) or "no analyzer hits")

    def git_blame(self, path: str, line: int) -> str:
        try:
            out = subprocess.run(  # noqa: S603
                [
                    "git",
                    "-C",
                    str(self.ctx.repo_path),
                    "blame",
                    "-L",
                    f"{line},{line}",
                    "--porcelain",
                    "--",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return wrap_data("git_blame", f"ERROR: {e}")
        if out.returncode != 0:
            return wrap_data("git_blame", f"ERROR: {out.stderr.strip()[:300]}")
        info: dict[str, str] = {}
        for ln in out.stdout.splitlines():
            for key in ("author ", "author-time ", "summary "):
                if ln.startswith(key):
                    info[key.strip()] = ln[len(key) :]
        sha = out.stdout.split(" ", 1)[0][:10]
        import datetime as dt

        when = info.get("author-time")
        date = dt.datetime.fromtimestamp(int(when), tz=dt.UTC).date().isoformat() if when else "?"
        return wrap_data(
            f"git_blame({path}:{line})",
            f"commit {sha} by {info.get('author', '?')} on {date}: {info.get('summary', '')}",
        )

    def run_snippet(self, code: str, lang: str = "python") -> str:
        ctx = self.ctx
        if (
            not ctx.sandbox_ready()
            or ctx.sandbox is None
            or ctx.workspace is None
            or ctx.sandbox_image is None
        ):
            return wrap_data("run_snippet", "ERROR: sandbox unavailable (Docker not running)")
        ext = "py" if lang == "python" else "mjs"
        rel = f".sentinel/snippets/{uuid.uuid4().hex[:8]}.{ext}"
        target = ctx.workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        res = ctx.sandbox.run(
            ctx.sandbox_image,
            ctx.workspace,
            snippet_command(lang, rel),
            timeout_s=ctx.settings.sandbox.snippet_timeout_s,
        )
        ctx.emit(
            "sandbox.exec",
            {
                "node": "hunt",
                "command": " ".join(res.command),
                "exit_code": res.exit_code,
                "duration_s": round(res.duration_s, 2),
                "timed_out": res.timed_out,
            },
        )
        status = "TIMEOUT" if res.timed_out else f"exit {res.exit_code}"
        return wrap_data(f"run_snippet [{status}]", f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}")

    def run_tests(self, paths: list[str] | None = None) -> str:
        ctx = self.ctx
        if (
            not ctx.sandbox_ready()
            or ctx.sandbox is None
            or ctx.workspace is None
            or ctx.sandbox_image is None
        ):
            return wrap_data("run_tests", "ERROR: sandbox unavailable")
        tr = run_tests(ctx.sandbox, ctx.sandbox_image, ctx.workspace, ctx.framework, paths)
        rep = tr.report
        summary = (
            f"total={rep.total} passed={rep.passed} failed={rep.failed} errors={rep.errors} "
            f"skipped={rep.skipped} timed_out={tr.exec.timed_out}"
        )
        fails = "\n".join(
            f"- {c.id}: {c.failure_type} {c.message[:300]}"
            for c in rep.cases
            if c.status in ("failed", "error")
        )
        return wrap_data("run_tests", f"{summary}\n{fails}\n\n{tr.exec.combined[-3000:]}")

    def apply_patch(self, diff: str) -> str:
        ctx = self.ctx
        if ctx.workspace is None:
            return wrap_data("apply_patch", "ERROR: no workspace")
        ok, msg = apply_unified_diff(ctx.workspace, diff)
        return wrap_data("apply_patch", "ok" if ok else f"ERROR: {msg}")


def apply_unified_diff(workspace: Path, diff: str, check_only: bool = False) -> tuple[bool, str]:
    """`git apply --check` then apply, with the workspace as cwd. Works without a .git directory."""
    if not diff.endswith("\n"):
        diff += "\n"
    base = ["git", "apply", "--whitespace=nowarn"]
    check = subprocess.run(  # noqa: S603
        [*base, "--check", "-"],
        input=diff,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    if check.returncode != 0:
        return False, check.stderr.strip()[:1000] or "git apply --check failed"
    if check_only:
        return True, "check ok"
    res = subprocess.run(  # noqa: S603
        [*base, "-"],
        input=diff,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    return res.returncode == 0, (res.stderr.strip()[:1000] or "applied")


def count_changed_lines(diff: str) -> int:
    return sum(
        1
        for ln in diff.splitlines()
        if (ln.startswith("+") and not ln.startswith("+++"))
        or (ln.startswith("-") and not ln.startswith("---"))
    )


def files_in_diff(diff: str) -> list[str]:
    out: list[str] = []
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            p = ln[4:].strip()
            p = p.removeprefix("b/")
            if p != "/dev/null":
                out.append(p)
    return out
