"""semgrep adapter (Python + TS/JS). Local binary when present, else the official Docker image."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, Level, rel_posix, run_cmd, which
from sentinel.graph.state import Category

CONFIGS = ["p/python", "p/javascript", "p/typescript", "p/security-audit", "p/secrets"]
_LEVEL: dict[str, Level] = {"ERROR": "error", "WARNING": "warning", "INFO": "note"}
_HINTS: list[tuple[re.Pattern[str], Category]] = [
    (
        re.compile(r"sql|inject|secret|hardcoded|eval|exec|xss|crypto|jwt|password", re.I),
        "security_smell",
    ),
    (re.compile(r"null|none|undefined|optional", re.I), "null_deref"),
    (re.compile(r"await|async|promise|race|lock|thread", re.I), "race_condition"),
    (re.compile(r"leak|close|unclosed|resource|file-open", re.I), "resource_leak"),
    (re.compile(r"except|catch|raise|throw|error-handl", re.I), "unhandled_exception"),
    (re.compile(r"unused|unreachable|dead", re.I), "dead_code"),
    (re.compile(r"type|cast|coerc", re.I), "type_error"),
    (re.compile(r"perf|inefficien|slow|n\+1", re.I), "perf"),
]


def _category(check_id: str, message: str) -> Category | None:
    hay = f"{check_id} {message}"
    for pat, cat in _HINTS:
        if pat.search(hay):
            return cat
    return None


class SemgrepAdapter:
    name = "semgrep"
    languages = frozenset({"python", "typescript"})

    def __init__(
        self, configs: list[str] | None = None, docker_image: str = "semgrep/semgrep"
    ) -> None:
        self.configs = configs or CONFIGS
        self.docker_image = docker_image

    def _mode(self) -> str | None:
        if which("semgrep"):
            return "local"
        if which("docker"):
            return "docker"
        return None

    def available(self, repo_path: Path) -> str | None:
        if self._mode() is None:
            return "semgrep binary not found and docker unavailable"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        cfg_args = [a for c in self.configs for a in ("--config", c)]
        common = [
            "scan",
            "--json",
            "--quiet",
            "--metrics",
            "off",
            "--timeout",
            "60",
            "--exclude",
            "node_modules",
            "--exclude",
            ".venv",
            "--exclude",
            "tests",
            *cfg_args,
        ]
        if self._mode() == "local":
            proc = run_cmd(["semgrep", *common, "."], cwd=repo_path, timeout=900)
            root = repo_path
        else:
            proc = run_cmd(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{repo_path.resolve()}:/src:ro",
                    self.docker_image,
                    "semgrep",
                    *common,
                    "/src",
                ],
                cwd=repo_path,
                timeout=1200,
            )
            root = Path("/src")
        if not proc.stdout.strip():
            raise RuntimeError(
                f"semgrep produced no output ({proc.returncode}): {proc.stderr[:500]}"
            )
        data = json.loads(proc.stdout)
        out: list[AnalyzerFinding] = []
        for r in data.get("results", []):
            extra = r.get("extra", {})
            path = r.get("path", "")
            if root != repo_path and path.startswith("/src/"):
                path = path.removeprefix("/src/")
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=r.get("check_id", "semgrep"),
                    message=extra.get("message", ""),
                    file=rel_posix(repo_path, path),
                    line_start=int(r.get("start", {}).get("line", 1)),
                    line_end=int(r.get("end", {}).get("line", 1)),
                    col_start=r.get("start", {}).get("col"),
                    level=_LEVEL.get(extra.get("severity", "WARNING"), "warning"),
                    category_hint=_category(r.get("check_id", ""), extra.get("message", "")),
                    raw={
                        "metadata": {
                            k: extra.get("metadata", {}).get(k)
                            for k in ("cwe", "owasp", "category", "confidence")
                        }
                    },
                )
            )
        return out
