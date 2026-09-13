"""Docker sandbox (SPEC §5). Security-critical: every run is `network_disabled`, memory/CPU/pid
limited, read-only root FS, drops all capabilities, runs as an unprivileged user, and is killed on
timeout. Network is used ONLY while building the per-repo dependency image.

There is deliberately no host-execution fallback. If Docker is unavailable, verification cannot
happen and findings stay `candidate`.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sentinel.config import SandboxSettings
from sentinel.telemetry.otel import span

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
IMAGES_DIR = Path(__file__).parent / "images"
BASE_TAGS = {"python": "sentinel-base-python:3.12", "typescript": "sentinel-base-node:20"}
COPY_EXCLUDES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".sentinel",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    ".mypy_cache",
    ".pytest_cache",
}


class SandboxUnavailableError(RuntimeError):
    pass


@dataclass
class ExecResult:
    command: list[str]
    image: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    oom_killed: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = text[: cap // 2]
    tail = text[-(cap // 2) :]
    return f"{head}\n... [{len(text) - cap} bytes truncated by Sentinel] ...\n{tail}"


def make_workspace(repo_path: Path, dest: Path) -> Path:
    """Copy of the repo the sandbox may write to. Excludes VCS metadata and dependency dirs."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(
        repo_path,
        dest,
        ignore=lambda _d, names: [n for n in names if n in COPY_EXCLUDES],
        symlinks=False,
    )
    return dest


class DockerRunner:
    def __init__(self, settings: SandboxSettings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client

    # ------------------------------------------------------------------ client
    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
                self._client.ping()
            except Exception as e:  # noqa: BLE001 - surfaced as SandboxUnavailableError
                raise SandboxUnavailableError(f"Docker engine unavailable: {e}") from e
        return self._client

    def available(self) -> bool:
        try:
            self.client.ping()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ images
    def ensure_base_image(self, language: str) -> str:
        tag = BASE_TAGS["python" if language == "python" else "typescript"]
        if self._image_exists(tag):
            return tag
        dockerfile = "Dockerfile.python" if language == "python" else "Dockerfile.node"
        with span("sentinel.sandbox.build", image=tag):
            self.client.images.build(
                path=str(IMAGES_DIR), dockerfile=dockerfile, tag=tag, rm=True, pull=True
            )
        return tag

    def build_repo_image(
        self,
        repo_path: Path,
        language: str,
        lockfile_hash: str | None,
        package_managers: list[str],
        build_dir: Path,
    ) -> str:
        """Per-repo image with dependencies pre-installed, cached by lockfile hash."""
        base = self.ensure_base_image(language)
        repo_key = hashlib.sha1(str(repo_path.resolve()).encode()).hexdigest()[:10]  # noqa: S324
        tag = f"sentinel-repo-{repo_key}:{(lockfile_hash or 'nolock')[:16]}"
        if self._image_exists(tag):
            return tag
        build_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"FROM {base}", "USER root", "WORKDIR /opt/app"]
        copied: list[str] = []
        for name in (
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "setup.py",
            "setup.cfg",
            "uv.lock",
            "poetry.lock",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig.json",
        ):
            src = repo_path / name
            if src.exists():
                shutil.copy2(src, build_dir / name)
                copied.append(name)
        if copied:
            lines.append("COPY " + " ".join(copied) + " /opt/app/")
        lines.extend(_install_steps(language, package_managers, copied))
        lines.extend(["USER sandbox", "WORKDIR /workspace"])
        (build_dir / "Dockerfile").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with span("sentinel.sandbox.build", image=tag):
            self.client.images.build(path=str(build_dir), tag=tag, rm=True)
        return tag

    def _image_exists(self, tag: str) -> bool:
        try:
            self.client.images.get(tag)
            return True
        except Exception:  # noqa: BLE001 - docker.errors.ImageNotFound and transport errors
            return False

    # ------------------------------------------------------------------ run
    def run(
        self,
        image: str,
        workspace: Path,
        command: list[str],
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = timeout_s or self.settings.test_timeout_s
        s = self.settings
        if s.network != "none":  # defence in depth: settings type forbids anything else
            raise SandboxUnavailableError("sandbox network must be 'none'")
        environment = {
            "PYTHONPATH": "/workspace:/workspace/src",
            "NODE_PATH": "/opt/app/node_modules",
            "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            **(env or {}),
        }
        # Link pre-installed node_modules into the writable workspace so tool binaries resolve.
        shell_cmd = (
            "[ -d /opt/app/node_modules ] && [ ! -e /workspace/node_modules ] && "
            'ln -s /opt/app/node_modules /workspace/node_modules; exec "$@"'
        )
        full_cmd = ["sh", "-c", shell_cmd, "sentinel", *command]
        t0 = time.perf_counter()
        with span("sentinel.sandbox.exec", image=image, command=" ".join(command)[:200]) as sp:
            container = self.client.containers.run(
                image,
                full_cmd,
                detach=True,
                network_disabled=True,
                mem_limit=s.memory,
                memswap_limit=s.memory,
                nano_cpus=int(s.cpus * 1e9),
                pids_limit=s.pids_limit,
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=512m"},
                volumes={str(workspace.resolve()): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                user="10001:10001",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                environment=environment,
                stdin_open=False,
                tty=False,
                labels={"sentinel": "sandbox"},
            )
            timed_out = False
            exit_code = -1
            oom = False
            try:
                try:
                    result = container.wait(timeout=timeout)
                    exit_code = int(result.get("StatusCode", -1))
                except Exception:  # noqa: BLE001 - requests ReadTimeout / ConnectionError
                    timed_out = True
                    with contextlib.suppress(Exception):
                        container.kill()
                    exit_code = 137
                with contextlib.suppress(Exception):
                    container.reload()
                    oom = bool(container.attrs.get("State", {}).get("OOMKilled", False))
                stdout = _decode(container.logs(stdout=True, stderr=False))
                stderr = _decode(container.logs(stdout=False, stderr=True))
            finally:
                with contextlib.suppress(Exception):
                    container.remove(force=True)
            duration = time.perf_counter() - t0
            sp.set_attribute("sentinel.exit_code", exit_code)
            sp.set_attribute("sentinel.duration_s", duration)
            sp.set_attribute("sentinel.timed_out", timed_out)
            sp.set_attribute(
                "sentinel.outcome", "timeout" if timed_out else ("oom" if oom else "ok")
            )
        cap = s.output_cap_bytes
        return ExecResult(
            command=command,
            image=image,
            exit_code=exit_code,
            stdout=truncate(strip_ansi(stdout), cap),
            stderr=truncate(strip_ansi(stderr), cap),
            duration_s=duration,
            timed_out=timed_out,
            oom_killed=oom,
        )


def _decode(b: bytes | str) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes | bytearray) else str(b)


def _install_steps(language: str, package_managers: list[str], copied: list[str]) -> list[str]:
    steps: list[str] = []
    if language in ("python", "mixed"):
        if "uv.lock" in copied:
            steps.append(
                "RUN uv export --no-hashes --no-emit-project -o /tmp/req.txt 2>/dev/null "
                "&& pip install --no-cache-dir -r /tmp/req.txt || true"
            )
        elif "poetry.lock" in copied:
            steps.append(
                "RUN pip install --no-cache-dir poetry && poetry export -f requirements.txt "
                "--without-hashes --with dev -o /tmp/req.txt && pip install --no-cache-dir "
                "-r /tmp/req.txt || true"
            )
        if "requirements.txt" in copied:
            steps.append("RUN pip install --no-cache-dir -r requirements.txt || true")
        if "requirements-dev.txt" in copied:
            steps.append("RUN pip install --no-cache-dir -r requirements-dev.txt || true")
        if "pyproject.toml" in copied and "uv.lock" not in copied and "poetry.lock" not in copied:
            # install declared deps without the project itself (source is bind-mounted later)
            steps.append(
                "RUN python - <<'PY' || true\n"
                "import tomllib,subprocess,sys\n"
                "d=tomllib.load(open('pyproject.toml','rb'))\n"
                "deps=list(d.get('project',{}).get('dependencies',[]))\n"
                "for g in d.get('project',{}).get('optional-dependencies',{}).values(): deps+=g\n"
                "for g in d.get('dependency-groups',{}).values(): deps+=[x for x in g if isinstance(x,str)]\n"
                "deps and subprocess.call([sys.executable,'-m','pip','install','--no-cache-dir',*deps])\n"
                "PY"
            )
    if language in ("typescript", "mixed") and "package.json" in copied:
        if "pnpm" in package_managers and "pnpm-lock.yaml" in copied:
            steps.append("RUN pnpm install --frozen-lockfile || pnpm install")
        elif "yarn" in package_managers and "yarn.lock" in copied:
            steps.append("RUN yarn install --frozen-lockfile || yarn install")
        elif "package-lock.json" in copied:
            steps.append("RUN npm ci || npm install")
        else:
            steps.append("RUN npm install")
    return steps
