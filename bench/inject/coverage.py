"""Coverage-guided mutation (item 6, SPEC §8.2): only lines executed by the existing test suite are
candidates. Python via coverage.py (`coverage json`), TypeScript/JavaScript via c8's JSON summary or
an lcov.info file. Executors run the command inside the sandbox; the parser runs on the host."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from pathlib import Path

CoverageRunner = Callable[
    [Path], Path | None
]  # repo -> path of coverage report (json/lcov), or None

COVERAGE_JSON = ".sentinel/coverage.json"
LCOV = "coverage/lcov.info"


def python_coverage_command(junit: str = ".sentinel/junit.xml") -> list[str]:
    return [
        "sh",
        "-c",
        f"python -m coverage run --data-file=.sentinel/.coverage -m pytest -q -p no:cacheprovider "
        f"--junitxml={junit}; python -m coverage json --data-file=.sentinel/.coverage -o {COVERAGE_JSON} -q",
    ]


def node_coverage_command(framework: str) -> list[str]:
    if framework == "vitest":
        return [
            "node",
            "/opt/app/node_modules/vitest/vitest.mjs",
            "run",
            "--coverage",
            "--coverage.reporter=lcov",
            "--coverage.reportsDirectory=coverage",
            "--passWithNoTests",
        ]
    return [
        "node",
        "/opt/app/node_modules/c8/bin/c8.js",
        "--reporter=lcov",
        "--reports-dir=coverage",
        "node",
        "/opt/app/node_modules/jest/bin/jest.js",
        "--ci",
        "--passWithNoTests",
    ]


def parse_coverage_json(text: str, repo: Path | None = None) -> dict[str, set[int]]:
    """coverage.py JSON → {repo-relative posix path: executed line numbers}."""
    data = json.loads(text)
    out: dict[str, set[int]] = {}
    for file, info in data.get("files", {}).items():
        rel = _rel(file, repo)
        out[rel] = set(int(x) for x in info.get("executed_lines", []))
    return out


def parse_lcov(text: str, repo: Path | None = None) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            current = _rel(line[3:].strip(), repo)
            out.setdefault(current, set())
        elif line.startswith("DA:") and current is not None:
            ln, hits = line[3:].split(",")[:2]
            if int(hits) > 0:
                out[current].add(int(ln))
        elif line.startswith("end_of_record"):
            current = None
    return out


def _rel(file: str, repo: Path | None) -> str:
    p = file.replace("\\", "/")
    if p.startswith("/workspace/"):
        p = p[len("/workspace/") :]
    if repo is not None:
        with contextlib.suppress(ValueError, OSError):
            p = Path(p).resolve().relative_to(repo.resolve()).as_posix()
    return re.sub(r"^\./", "", p)


def load_coverage(repo: Path) -> dict[str, set[int]] | None:
    """Read whatever coverage artifact exists in the repo copy after a coverage run."""
    cj = repo / COVERAGE_JSON
    if cj.exists():
        return parse_coverage_json(cj.read_text(encoding="utf-8", errors="replace"), repo)
    lc = repo / LCOV
    if lc.exists():
        return parse_lcov(lc.read_text(encoding="utf-8", errors="replace"), repo)
    return None


def is_covered(cov: dict[str, set[int]] | None, file: str, line: int) -> bool:
    if cov is None:
        return True  # no coverage data: fall back to "a test must fail" as the only filter
    lines = cov.get(file.replace("\\", "/"))
    return lines is not None and line in lines
