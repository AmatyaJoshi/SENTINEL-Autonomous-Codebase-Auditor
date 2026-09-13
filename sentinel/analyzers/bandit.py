"""bandit adapter (Python security smells)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, Level, rel_posix, run_cmd
from sentinel.graph.state import Category

_CATEGORY: dict[str, Category] = {
    "B110": "unhandled_exception",  # try/except/pass
    "B112": "unhandled_exception",  # try/except/continue
}
_LEVEL: dict[str, Level] = {"HIGH": "error", "MEDIUM": "warning", "LOW": "note"}


class BanditAdapter:
    name = "bandit"
    languages = frozenset({"python"})

    def available(self, repo_path: Path) -> str | None:
        try:
            import bandit  # noqa: F401
        except ImportError:
            return "bandit package not installed in Sentinel environment"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        proc = run_cmd(
            [
                sys.executable,
                "-m",
                "bandit",
                "-r",
                "-q",
                "-f",
                "json",
                "-x",
                "./.venv,./venv,./node_modules,./.git,./tests,./test",
                ".",
            ],
            cwd=repo_path,
        )
        # bandit exits 1 when issues are found; anything else is an error
        if proc.returncode not in (0, 1) or not proc.stdout.strip():
            raise RuntimeError(f"bandit failed ({proc.returncode}): {proc.stderr[:500]}")
        data = json.loads(proc.stdout)
        out: list[AnalyzerFinding] = []
        for d in data.get("results", []):
            code = d.get("test_id", "B000")
            lines = d.get("line_range") or [d.get("line_number", 1)]
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=code,
                    message=d.get("issue_text", ""),
                    file=rel_posix(repo_path, d.get("filename", "")),
                    line_start=int(min(lines)),
                    line_end=int(max(lines)),
                    col_start=d.get("col_offset"),
                    level=_LEVEL.get(d.get("issue_severity", "MEDIUM"), "warning"),
                    category_hint=_CATEGORY.get(code, "security_smell"),
                    raw={
                        "severity": d.get("issue_severity"),
                        "confidence": d.get("issue_confidence"),
                        "cwe": d.get("issue_cwe", {}).get("id"),
                        "test_name": d.get("test_name"),
                    },
                )
            )
        return out
