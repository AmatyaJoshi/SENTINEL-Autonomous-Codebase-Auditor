"""Test-framework adapters (pytest / vitest / jest) producing JUnit XML, and the parser."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - input is our own sandbox's junit output
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sentinel.sandbox.docker_runner import DockerRunner, ExecResult

Framework = Literal["pytest", "unittest", "vitest", "jest", "mocha", "node:test", "unknown"]
JUNIT_PATH = ".sentinel/junit.xml"
CaseStatus = Literal["passed", "failed", "error", "skipped"]


@dataclass
class TestCase:
    name: str
    classname: str
    status: CaseStatus
    time_s: float = 0.0
    message: str = ""
    failure_type: str = ""

    @property
    def id(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    cases: list[TestCase] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0 and self.parse_error is None

    def failing_ids(self) -> set[str]:
        return {c.id for c in self.cases if c.status in ("failed", "error")}


def parse_junit(xml_text: str) -> TestReport:
    rep = TestReport()
    try:
        root = ET.fromstring(xml_text)  # noqa: S314
    except ET.ParseError as e:
        rep.parse_error = f"junit parse error: {e}"
        return rep
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        for tc in suite.iter("testcase"):
            name = tc.get("name", "")
            classname = tc.get("classname", "") or tc.get("file", "")
            t = float(tc.get("time", "0") or 0)
            status: CaseStatus = "passed"
            msg = ""
            ftype = ""
            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")
            if failure is not None:
                status, msg, ftype = "failed", _msg(failure), failure.get("type", "")
            elif error is not None:
                status, msg, ftype = "error", _msg(error), error.get("type", "")
            elif skipped is not None:
                status, msg = "skipped", _msg(skipped)
            rep.cases.append(TestCase(name, classname, status, t, msg, ftype))
    rep.total = len(rep.cases)
    rep.passed = sum(c.status == "passed" for c in rep.cases)
    rep.failed = sum(c.status == "failed" for c in rep.cases)
    rep.errors = sum(c.status == "error" for c in rep.cases)
    rep.skipped = sum(c.status == "skipped" for c in rep.cases)
    rep.duration_s = sum(c.time_s for c in rep.cases)
    return rep


def _msg(el: ET.Element) -> str:
    text = (el.get("message") or "").strip()
    body = (el.text or "").strip()
    return (text + ("\n" + body if body and body != text else "")).strip()[:4000]


def test_command(
    framework: Framework, paths: list[str] | None, junit: str = JUNIT_PATH
) -> list[str]:
    paths = paths or []
    if framework in ("pytest", "unittest"):
        return [
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--timeout=100",
            f"--junitxml={junit}",
            "-o",
            "junit_family=xunit2",
            *paths,
        ]
    if framework == "vitest":
        return [
            "node",
            "/opt/app/node_modules/vitest/vitest.mjs",
            "run",
            "--reporter=junit",
            f"--outputFile={junit}",
            "--passWithNoTests",
            *paths,
        ]
    if framework == "jest":
        return [
            "node",
            "/opt/app/node_modules/jest/bin/jest.js",
            "--ci",
            "--reporters=default",
            "--reporters=jest-junit",
            "--passWithNoTests",
            *paths,
        ]
    if framework == "mocha":
        return [
            "node",
            "/opt/app/node_modules/mocha/bin/mocha.js",
            "--reporter",
            "xunit",
            "--reporter-option",
            f"output={junit}",
            *paths,
        ]
    if framework == "node:test":
        return [
            "node",
            "--test",
            "--test-reporter=junit",
            f"--test-reporter-destination={junit}",
            *paths,
        ]
    raise ValueError(f"unsupported test framework: {framework}")


def jest_env(junit: str = JUNIT_PATH) -> dict[str, str]:
    return {
        "JEST_JUNIT_OUTPUT_DIR": str(Path(junit).parent),
        "JEST_JUNIT_OUTPUT_NAME": Path(junit).name,
    }


@dataclass
class TestRun:
    exec: ExecResult
    report: TestReport


def run_tests(
    runner: DockerRunner,
    image: str,
    workspace: Path,
    framework: Framework,
    paths: list[str] | None = None,
    timeout_s: int | None = None,
) -> TestRun:
    junit_file = workspace / JUNIT_PATH
    junit_file.parent.mkdir(parents=True, exist_ok=True)
    if junit_file.exists():
        junit_file.unlink()
    cmd = test_command(framework, paths)
    env = jest_env() if framework == "jest" else None
    res = runner.run(image, workspace, cmd, timeout_s=timeout_s, env=env)
    if junit_file.exists():
        report = parse_junit(junit_file.read_text(encoding="utf-8", errors="replace"))
    else:
        report = TestReport(
            parse_error="no junit output produced" + (" (timeout)" if res.timed_out else "")
        )
        if res.timed_out:
            report.errors = 1
    return TestRun(res, report)


def snippet_command(lang: str, code_path: str) -> list[str]:
    if lang == "python":
        return ["python", code_path]
    return ["node", code_path]
