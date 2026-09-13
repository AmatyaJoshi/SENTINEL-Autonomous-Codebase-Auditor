"""mypy adapter (Python type errors). Non-strict, missing imports ignored: seeds, not gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, rel_posix, run_cmd
from sentinel.graph.state import Category

_CATEGORY: dict[str, Category] = {
    "union-attr": "null_deref",
    "optional": "null_deref",
    "arg-type": "type_error",
    "assignment": "type_error",
    "return-value": "type_error",
    "attr-defined": "api_misuse",
    "call-arg": "api_misuse",
    "call-overload": "api_misuse",
    "index": "type_error",
    "operator": "type_error",
    "unreachable": "dead_code",
    "return": "logic_error",
    "has-type": "type_error",
    "misc": "type_error",
}


class MypyAdapter:
    name = "mypy"
    languages = frozenset({"python"})

    def available(self, repo_path: Path) -> str | None:
        try:
            import mypy  # noqa: F401
        except ImportError:
            return "mypy package not installed in Sentinel environment"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        proc = run_cmd(
            [
                sys.executable,
                "-m",
                "mypy",
                "--output",
                "json",
                "--ignore-missing-imports",
                "--explicit-package-bases",
                "--no-error-summary",
                "--show-error-codes",
                "--no-color-output",
                "--exclude",
                r"(^|/)(\.venv|venv|node_modules|build|dist|tests?)(/|$)",
                "--cache-dir",
                str(repo_path / ".sentinel" / "mypy_cache"),
                ".",
            ],
            cwd=repo_path,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"mypy failed ({proc.returncode}): {proc.stderr[:500]}")
        out: list[AnalyzerFinding] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("severity") != "error":
                continue
            code = d.get("code") or "misc"
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=code,
                    message=d.get("message", ""),
                    file=rel_posix(repo_path, d.get("file", "")),
                    line_start=int(d.get("line", 1)),
                    line_end=int(d.get("line", 1)),
                    col_start=d.get("column"),
                    level="error",
                    category_hint=_CATEGORY.get(code, "type_error"),
                    raw={"hint": d.get("hint")},
                )
            )
        return out
