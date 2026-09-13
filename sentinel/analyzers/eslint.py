"""eslint adapter (TS/JS). Uses the repo's own eslint + config; never installs into the repo."""

from __future__ import annotations

import json
from pathlib import Path

from sentinel.analyzers.base import AnalyzerFinding, rel_posix, run_cmd, which
from sentinel.graph.state import Category

_CATEGORY: dict[str, Category] = {
    "no-unused-vars": "dead_code",
    "@typescript-eslint/no-unused-vars": "dead_code",
    "no-unreachable": "dead_code",
    "no-undef": "logic_error",
    "no-constant-condition": "logic_error",
    "no-dupe-keys": "logic_error",
    "no-self-compare": "logic_error",
    "eqeqeq": "logic_error",
    "no-fallthrough": "logic_error",
    "no-cond-assign": "logic_error",
    "use-isnan": "logic_error",
    "valid-typeof": "type_error",
    "no-implicit-coercion": "type_error",
    "no-empty": "unhandled_exception",
    "no-ex-assign": "unhandled_exception",
    "no-unsafe-finally": "unhandled_exception",
    "no-async-promise-executor": "race_condition",
    "no-await-in-loop": "perf",
    "require-atomic-updates": "race_condition",
    "@typescript-eslint/no-floating-promises": "race_condition",
    "@typescript-eslint/no-misused-promises": "race_condition",
    "@typescript-eslint/await-thenable": "race_condition",
    "@typescript-eslint/no-non-null-assertion": "null_deref",
    "@typescript-eslint/no-unnecessary-condition": "logic_error",
    "@typescript-eslint/no-explicit-any": "type_error",
    "@typescript-eslint/no-unsafe-member-access": "type_error",
    "no-eval": "security_smell",
    "no-implied-eval": "security_smell",
    "no-new-func": "security_smell",
    "no-prototype-builtins": "api_misuse",
    "array-callback-return": "logic_error",
}
_CONFIGS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
)


def _eslint_entry(repo_path: Path) -> Path | None:
    for cand in ("node_modules/eslint/bin/eslint.js",):
        p = repo_path / cand
        if p.exists():
            return p
    return None


class EslintAdapter:
    name = "eslint"
    languages = frozenset({"typescript"})

    def available(self, repo_path: Path) -> str | None:
        if which("node") is None:
            return "node not found on PATH"
        if _eslint_entry(repo_path) is None:
            return "repo has no node_modules/eslint (run the package install first)"
        if not any((repo_path / c).exists() for c in _CONFIGS):
            pkg = repo_path / "package.json"
            if not pkg.exists() or '"eslintConfig"' not in pkg.read_text("utf-8", "replace"):
                return "repo has no eslint config"
        return None

    def run(self, repo_path: Path) -> list[AnalyzerFinding]:
        entry = _eslint_entry(repo_path)
        assert entry is not None
        proc = run_cmd(
            [
                "node",
                str(entry),
                "-f",
                "json",
                "--no-error-on-unmatched-pattern",
                "--ext",
                ".ts,.tsx,.js,.jsx,.mjs,.cjs",
                ".",
            ],
            cwd=repo_path,
        )
        stdout = proc.stdout.strip()
        if not stdout.startswith("["):
            raise RuntimeError(f"eslint failed ({proc.returncode}): {proc.stderr[:500]}")
        out: list[AnalyzerFinding] = []
        for file_entry in json.loads(stdout):
            rel = rel_posix(repo_path, file_entry.get("filePath", ""))
            for m in file_entry.get("messages", []):
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
                        category_hint=_CATEGORY.get(rule),
                        raw={"fatal": bool(m.get("fatal"))},
                    )
                )
        return out
