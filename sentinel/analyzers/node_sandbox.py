"""Run eslint / tsc inside the repo's sandbox image when the host has no node_modules (item 8).

The per-repo image installs dependencies at /opt/app/node_modules during ingest; these adapters
execute the same JSON-producing commands there, so TypeScript repos get analyzer seeds without
installing anything on the host. Network stays disabled: the tools only read the workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, rel_posix
from sentinel.analyzers.eslint import _CATEGORY as ESLINT_CATEGORY
from sentinel.analyzers.eslint import _CONFIGS
from sentinel.analyzers.tsc import _CATEGORY as TSC_CATEGORY
from sentinel.analyzers.tsc import _LINE
from sentinel.sandbox.docker_runner import DockerRunner


class SandboxEslintAdapter:
    name = "eslint"
    languages = frozenset({"typescript"})

    def __init__(self, runner: DockerRunner, image: str, workspace: Path) -> None:
        self.runner, self.image, self.workspace = runner, image, workspace

    def available(self, repo_path: Path) -> str | None:
        if not any((repo_path / c).exists() for c in _CONFIGS):
            return "repo has no eslint config"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        res = self.runner.run(
            self.image,
            self.workspace,
            [
                "sh",
                "-c",
                "[ -f /opt/app/node_modules/eslint/bin/eslint.js ] && node /opt/app/node_modules/eslint/bin/eslint.js "
                "-f json --no-error-on-unmatched-pattern . || echo '[]'",
            ],
            timeout_s=600,
        )
        stdout = res.stdout.strip()
        start = stdout.find("[")
        if start < 0:
            raise RuntimeError(f"eslint (sandbox) produced no JSON: {res.stderr[:300]}")
        out: list[AnalyzerFinding] = []
        for entry in json.loads(stdout[start:]):
            rel = rel_posix(repo_path, entry.get("filePath", "").replace("/workspace/", ""))
            for m in entry.get("messages", []):
                rule = m.get("ruleId") or ("parse-error" if m.get("fatal") else "unknown")
                out.append(
                    AnalyzerFinding(
                        tool=self.name,
                        rule_id=rule,
                        message=m.get("message", ""),
                        file=rel,
                        line_start=int(m.get("line") or 1),
                        line_end=int(m.get("endLine") or m.get("line") or 1),
                        col_start=m.get("column"),
                        level="error" if m.get("severity") == 2 else "warning",
                        category_hint=ESLINT_CATEGORY.get(rule),
                        raw={"fatal": bool(m.get("fatal")), "sandbox": True},
                    )
                )
        return out


class SandboxTscAdapter:
    name = "tsc"
    languages = frozenset({"typescript"})

    def __init__(self, runner: DockerRunner, image: str, workspace: Path) -> None:
        self.runner, self.image, self.workspace = runner, image, workspace

    def available(self, repo_path: Path) -> str | None:
        if not (repo_path / "tsconfig.json").exists():
            return "repo has no tsconfig.json"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        res = self.runner.run(
            self.image,
            self.workspace,
            [
                "sh",
                "-c",
                "[ -f /opt/app/node_modules/typescript/bin/tsc ] && node /opt/app/node_modules/typescript/bin/tsc "
                "--noEmit --pretty false -p tsconfig.json || true",
            ],
            timeout_s=900,
        )
        out: list[AnalyzerFinding] = []
        for raw in res.stdout.splitlines():
            m = _LINE.match(raw.strip())
            if not m:
                continue
            code = m.group("code")
            out.append(
                AnalyzerFinding(
                    tool=self.name,
                    rule_id=code,
                    message=m.group("msg"),
                    file=rel_posix(repo_path, re.sub(r"^/workspace/", "", m.group("file"))),
                    line_start=int(m.group("line")),
                    line_end=int(m.group("line")),
                    col_start=int(m.group("col")),
                    level="error",
                    category_hint=TSC_CATEGORY.get(code, "type_error"),
                    raw={"sandbox": True},
                )
            )
        return out
