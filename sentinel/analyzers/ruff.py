"""ruff adapter (Python). Runs the ruff bundled in Sentinel's own environment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, Level, rel_posix, run_cmd
from sentinel.graph.state import Category

# Bug-oriented rule sets only; style rules are noise for a bug hunter.
SELECT = "F,E7,E9,B,BLE,S,PLE,PLW,RUF,ASYNC,SIM1,RET5,ARG,PIE,T10"

_CATEGORY: dict[str, Category] = {
    "F821": "logic_error",  # undefined name
    "F841": "dead_code",
    "F401": "dead_code",
    "F811": "dead_code",
    "F632": "logic_error",  # `is` with literal
    "F702": "logic_error",
    "F706": "logic_error",
    "E711": "logic_error",
    "E712": "logic_error",
    "E721": "type_error",
    "E722": "unhandled_exception",
    "B006": "logic_error",  # mutable default
    "B008": "logic_error",
    "B015": "dead_code",
    "B017": "unhandled_exception",
    "B018": "dead_code",
    "B023": "race_condition",  # loop variable binding in closure
    "B904": "unhandled_exception",
    "BLE001": "unhandled_exception",
    "PLW0602": "logic_error",
    "PLW0603": "logic_error",
    "PLE1205": "api_misuse",
    "PLE1206": "api_misuse",
    "RET505": "dead_code",
    "SIM115": "resource_leak",
    "ASYNC100": "race_condition",
    "ASYNC101": "race_condition",
    "ASYNC102": "race_condition",
    "ASYNC110": "race_condition",
    "T100": "dead_code",
}


def _category(code: str) -> Category | None:
    if code in _CATEGORY:
        return _CATEGORY[code]
    if code.startswith("S"):
        return "security_smell"
    if code.startswith("ASYNC"):
        return "race_condition"
    if code.startswith("BLE"):
        return "unhandled_exception"
    if code.startswith("F"):
        return "logic_error"
    return None


class RuffAdapter:
    name = "ruff"
    languages = frozenset({"python"})

    def available(self, repo_path: Path) -> str | None:
        try:
            import ruff  # noqa: F401
        except ImportError:
            return "ruff package not installed in Sentinel environment"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        proc = run_cmd(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--output-format",
                "json",
                "--exit-zero",
                "--isolated",
                "--select",
                SELECT,
                "--no-cache",
                ".",
            ],
            cwd=repo_path,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"ruff failed ({proc.returncode}): {proc.stderr[:500]}")
        data = json.loads(proc.stdout or "[]")
        out: list[AnalyzerFinding] = []
        for d in data:
            code = d.get("code") or "RUF000"
            loc, end = d.get("location", {}), d.get("end_location", {})
            level: Level = "error" if code.startswith(("F821", "E9", "PLE")) else "warning"
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=code,
                    message=d.get("message", ""),
                    file=rel_posix(repo_path, d.get("filename", "")),
                    line_start=int(loc.get("row", 1)),
                    line_end=int(end.get("row", loc.get("row", 1))),
                    col_start=loc.get("column"),
                    level=level,
                    category_hint=_category(code),
                    raw={"url": d.get("url"), "fix": bool(d.get("fix"))},
                )
            )
        return out
