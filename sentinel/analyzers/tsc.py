"""tsc adapter (TypeScript compiler diagnostics via the repo's own typescript package)."""

from __future__ import annotations

import re
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, rel_posix, run_cmd, which
from sentinel.graph.state import Category

# file(line,col): error TS2345: message
_LINE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$"
)

_CATEGORY: dict[str, Category] = {
    "TS2531": "null_deref",  # Object is possibly 'null'
    "TS2532": "null_deref",  # possibly 'undefined'
    "TS2533": "null_deref",
    "TS18047": "null_deref",
    "TS18048": "null_deref",
    "TS2345": "type_error",
    "TS2322": "type_error",
    "TS2339": "api_misuse",  # property does not exist
    "TS2551": "api_misuse",
    "TS2554": "api_misuse",  # wrong arg count
    "TS2304": "logic_error",  # cannot find name
    "TS7027": "dead_code",  # unreachable
    "TS6133": "dead_code",  # declared but never read
    "TS1345": "logic_error",  # expression of type void cannot be tested
    "TS2367": "logic_error",  # comparison appears unintentional
    "TS2801": "race_condition",  # condition always true since Promise is always defined
    "TS80007": "race_condition",  # 'await' has no effect
}


def _tsc_entry(repo_path: Path) -> Path | None:
    p = repo_path / "node_modules" / "typescript" / "bin" / "tsc"
    return p if p.exists() else None


class TscAdapter:
    name = "tsc"
    languages = frozenset({"typescript"})

    def available(self, repo_path: Path) -> str | None:
        if which("node") is None:
            return "node not found on PATH"
        if _tsc_entry(repo_path) is None:
            return "repo has no node_modules/typescript (run the package install first)"
        if not (repo_path / "tsconfig.json").exists():
            return "repo has no tsconfig.json"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        entry = _tsc_entry(repo_path)
        assert entry is not None
        proc = run_cmd(
            ["node", str(entry), "--noEmit", "--pretty", "false", "-p", "tsconfig.json"],
            cwd=repo_path,
            timeout=900,
        )
        out: list[AnalyzerFinding] = []
        for raw in proc.stdout.splitlines():
            m = _LINE.match(raw.strip())
            if not m:
                continue
            code = m.group("code")
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=code,
                    message=m.group("msg"),
                    file=rel_posix(repo_path, m.group("file")),
                    line_start=int(m.group("line")),
                    line_end=int(m.group("line")),
                    col_start=int(m.group("col")),
                    level="error",
                    category_hint=_CATEGORY.get(code, "type_error"),
                )
            )
        if not out and proc.returncode not in (0, 1, 2):
            raise RuntimeError(f"tsc failed ({proc.returncode}): {proc.stderr[:500]}")
        return out
