"""Test doubles for Phases 2–4.

`HostPytestSandbox` is a TEST-ONLY stand-in for DockerRunner. It runs pytest on the host against
our own fixture repository (trusted code we wrote) so the verify → fix → regress loop can be
exercised deterministically in CI without a Docker engine. It is never importable from the
`sentinel` package and the production runner has no such fallback (SPEC §5).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sentinel.config import SandboxSettings
from sentinel.llm.router import Message, RawResponse, ScriptedBackend
from sentinel.sandbox.docker_runner import DockerRunner, ExecResult

FIX = Path(__file__).parent / "fixtures"
PYREPO = FIX / "pyrepo"


class HostPytestSandbox(DockerRunner):
    def __init__(self) -> None:
        super().__init__(SandboxSettings(), client=object())
        self.calls: list[list[str]] = []

    def available(self, *_: Any) -> bool:  # type: ignore[override]
        return True

    def ensure_base_image(self, language: str) -> str:
        return "fake-base"

    def build_repo_image(
        self,
        repo_path: Path,
        language: str,
        lockfile_hash: str | None,
        package_managers: list[str],
        build_dir: Path,
    ) -> str:
        return "fake-repo-image"

    def run(
        self,
        image: str,
        workspace: Path,
        command: list[str],
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        self.calls.append(command)
        t0 = time.perf_counter()
        # translate the sandbox's `python -m pytest ...` into a host run of the same thing
        if command[:3] == ["python", "-m", "pytest"]:
            argv = [sys.executable, "-m", "pytest", *command[3:]]
        elif command[0] == "python":
            argv = [sys.executable, *command[1:]]
        else:
            return ExecResult(
                command, image, 127, "", f"unsupported in fake sandbox: {command[0]}", 0.0
            )
        argv = [a for a in argv if not a.startswith("--timeout")]
        proc = subprocess.run(  # noqa: S603
            argv,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_s or 120,
            env={**os.environ, "PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"},
            encoding="utf-8",
            errors="replace",
        )
        return ExecResult(
            command,
            image,
            proc.returncode,
            proc.stdout[-20000:],
            proc.stderr[-20000:],
            time.perf_counter() - t0,
        )


# --------------------------------------------------------------------------- scripted LLM

FIRST_N_TEST = """from app.auth import first_n


# SENTINEL: expected to FAIL on current code because range(n - 1) drops the last requested item
def test_first_n_returns_exactly_n_items():
    assert first_n([1, 2, 3], 2) == [1, 2]
"""

FIRST_N_PATCH = """--- a/app/auth.py
+++ b/app/auth.py
@@ -33,5 +33,5 @@
 def first_n(items: list[int], n: int) -> list[int]:
     out = []
-    for i in range(n - 1):  # off-by-one
+    for i in range(n):
         out.append(items[i])
     return out
"""

BAD_PATCH = """--- a/app/auth.py
+++ b/app/auth.py
@@ -33,5 +33,5 @@
 def first_n(items: list[int], n: int) -> list[int]:
     out = []
-    for i in range(n - 1):  # off-by-one
+    for i in range(n + 1):
         out.append(items[i])
     return out
"""


def _json(obj: Any) -> RawResponse:
    return RawResponse(
        content=json.dumps(obj), model="scripted", tokens_in=100, tokens_out=50, cost_usd=0.001
    )


def make_scripted_backend(
    *, patch_first_attempt_bad: bool = False, extra_finding: bool = False
) -> ScriptedBackend:
    """A deterministic 'LLM' that finds the off-by-one in fixtures/pyrepo/app/auth.py."""
    state = {"fix_calls": 0}

    def fn(
        prompt: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None
    ) -> RawResponse:
        if prompt == "plan":
            return _json(
                {
                    "targets": [
                        {
                            "file": "app/auth.py",
                            "symbol": "first_n",
                            "reason": "boundary loop, analyzer hits",
                            "risk_score": 8.5,
                        },
                        {
                            "file": "app/util.py",
                            "symbol": "read_config",
                            "reason": "resource leak smell",
                            "risk_score": 5.0,
                        },
                    ]
                }
            )
        if prompt == "hunt_target":
            # first turn: call a tool; second turn: finish
            used_tool = any(m.get("role") == "tool" for m in messages)
            if tools and not used_tool:
                return RawResponse(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "name": "read_file",
                            "arguments": json.dumps(
                                {"path": "app/auth.py", "start": 30, "end": 40}
                            ),
                        }
                    ],
                    model="scripted",
                    tokens_in=200,
                    tokens_out=20,
                    cost_usd=0.001,
                )
            return RawResponse(
                content="I have what I need.",
                model="scripted",
                tokens_in=300,
                tokens_out=10,
                cost_usd=0.001,
            )
        if prompt == "hunt_final":
            target_is_auth = any(
                "app/auth.py" in str(m.get("content", ""))
                for m in messages
                if m.get("role") == "user"
            )
            if not target_is_auth:
                return _json({"findings": [], "notes": "nothing concrete"})
            findings = [
                {
                    "category": "off_by_one",
                    "severity": "high",
                    "file": "app/auth.py",
                    "line_start": 35,
                    "line_end": 35,
                    "symbol": "first_n",
                    "description": "first_n iterates range(n - 1) so it returns n-1 items instead of n.",
                    "hypothesis": "first_n([1, 2, 3], 2) returns [1] instead of [1, 2]",
                    "evidence": ["app/auth.py:35 `for i in range(n - 1)`"],
                    "confidence": 0.85,
                }
            ]
            if extra_finding:
                findings.append(
                    {
                        "category": "race_condition",
                        "severity": "low",
                        "file": "app/auth.py",
                        "line_start": 29,
                        "line_end": 30,
                        "symbol": "Session.is_admin",
                        "description": "speculative: is_admin might race with user id updates somewhere.",
                        "hypothesis": "unclear; maybe concurrent set of user_id",
                        "evidence": [],
                        "confidence": 0.2,
                    }
                )
            return _json({"findings": findings, "notes": ""})
        if prompt == "verify_write_test":
            return _json(
                {
                    "test_path": "tests/test_sentinel_first_n.py",
                    "test_code": FIRST_N_TEST,
                    "expected_failure": "assertion: expected [1, 2] got [1]",
                }
            )
        if prompt == "fix":
            state["fix_calls"] += 1
            diff = (
                BAD_PATCH
                if (patch_first_attempt_bad and state["fix_calls"] == 1)
                else FIRST_N_PATCH
            )
            return _json(
                {
                    "diff": diff,
                    "rationale": "The loop bound excluded the last item; range(n) yields n indices.",
                }
            )
        if prompt == "explain":
            return _json(
                {
                    "summary": "first_n drops the last requested element because its loop runs n-1 times; "
                    "calling first_n([1, 2, 3], 2) returns [1]. The patch changes the bound to range(n)."
                }
            )
        raise AssertionError(f"unexpected prompt {prompt}")

    return ScriptedBackend(fn)
