"""Ingest: clone at a pinned SHA (or use a local path), detect language / package manager /
test framework, read `sentinel.yaml`. Pure function `ingest()`; the graph node wraps it (Phase 3).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from sentinel.config import RepoConfig, Settings
from sentinel.indexing.treesitter import language_for
from sentinel.telemetry.otel import span

Language = Literal["python", "typescript", "mixed"]
PackageManager = Literal["uv", "poetry", "pip", "pnpm", "yarn", "npm", "bun", "unknown"]
TestFramework = Literal["pytest", "unittest", "vitest", "jest", "mocha", "node:test", "unknown"]

_URL_RE = re.compile(r"^(https?://|git@|ssh://)")


class IngestResult(BaseModel):
    repo_url: str
    repo_path: str
    commit_sha: str | None
    language: Language
    package_managers: list[PackageManager]
    test_frameworks: list[TestFramework]
    test_command: str | None
    lockfiles: list[str]
    lockfile_hash: str | None
    file_counts: dict[str, int]
    repo_config: RepoConfig


def is_url(repo: str) -> bool:
    return bool(_URL_RE.match(repo)) or repo.endswith(".git")


def _slug(url: str) -> str:
    tail = url.rstrip("/").removesuffix(".git").split("/")[-2:]
    base = "-".join(tail) or "repo"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", base) + "-" + hashlib.sha1(url.encode()).hexdigest()[:8]  # noqa: S324


def clone_or_update(url: str, work_dir: Path, sha: str | None = None) -> tuple[Path, str]:
    """Clone into work_dir/repos/<slug>; fetch if present; checkout `sha` if given."""
    import os

    from git import Repo

    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")  # never hang on a credential prompt
    dest = work_dir / "repos" / _slug(url)
    if (dest / ".git").exists():
        repo = Repo(dest)
        repo.remotes.origin.fetch()
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        repo = Repo.clone_from(url, dest)
    if sha:
        repo.git.checkout(sha)
    return dest, repo.head.commit.hexsha


def _head_sha(path: Path) -> str | None:
    try:
        from git import InvalidGitRepositoryError, Repo

        try:
            return Repo(path, search_parent_directories=False).head.commit.hexsha
        except (InvalidGitRepositoryError, ValueError):
            return None
    except ImportError:
        return None


def detect_language(
    repo_path: Path, files: list[str] | None = None
) -> tuple[Language, dict[str, int]]:
    from sentinel.indexing.pipeline import discover_files

    files = files if files is not None else discover_files(repo_path)
    counts: Counter[str] = Counter()
    for f in files:
        lang = language_for(f)
        if lang == "python":
            counts["python"] += 1
        elif lang in ("typescript", "tsx", "javascript"):
            counts["typescript"] += 1
    total = sum(counts.values()) or 1
    py, ts = counts["python"] / total, counts["typescript"] / total
    if py >= 0.2 and ts >= 0.2:
        return "mixed", dict(counts)
    return ("python" if py >= ts else "typescript"), dict(counts)


def detect_package_managers(repo_path: Path) -> tuple[list[PackageManager], list[str]]:
    table: list[tuple[str, PackageManager]] = [
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("requirements.txt", "pip"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
    ]
    pms: list[PackageManager] = []
    lockfiles: list[str] = []
    for name, pm in table:
        if (repo_path / name).exists():
            lockfiles.append(name)
            if pm not in pms:
                pms.append(pm)
    if (
        not any(p in pms for p in ("uv", "poetry", "pip"))
        and (repo_path / "pyproject.toml").exists()
    ):
        pms.append("pip")
    if (
        not any(p in pms for p in ("pnpm", "yarn", "npm", "bun"))
        and (repo_path / "package.json").exists()
    ):
        pms.append("npm")
    return (pms or ["unknown"]), lockfiles


def lockfile_hash(repo_path: Path, lockfiles: list[str]) -> str | None:
    if not lockfiles:
        return None
    h = hashlib.sha256()
    for name in sorted(lockfiles):
        h.update(name.encode())
        h.update((repo_path / name).read_bytes())
    return h.hexdigest()


def detect_test_frameworks(repo_path: Path) -> tuple[list[TestFramework], str | None]:
    fws: list[TestFramework] = []
    cmd: str | None = None

    pyproject = repo_path / "pyproject.toml"
    py_text = pyproject.read_text(encoding="utf-8", errors="replace") if pyproject.exists() else ""
    has_py_tests = any(
        (repo_path / d).exists() for d in ("tests", "test", "conftest.py", "pytest.ini")
    )
    if "[tool.pytest" in py_text or "pytest" in py_text or (repo_path / "pytest.ini").exists():
        fws.append("pytest")
    elif has_py_tests:
        fws.append("pytest" if (repo_path / "conftest.py").exists() else "unittest")

    pkg = repo_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        scripts = data.get("scripts", {})
        for name, fw in (("vitest", "vitest"), ("jest", "jest"), ("mocha", "mocha")):
            if name in deps:
                fws.append(fw)  # type: ignore[arg-type]
        test_script = scripts.get("test")
        if test_script and "node --test" in test_script:
            fws.append("node:test")
        if test_script and not cmd:
            cmd = "npm test"
    if "pytest" in fws and not cmd:
        cmd = "pytest -q"
    elif "unittest" in fws and not cmd:
        cmd = "python -m unittest discover -q"
    return (fws or ["unknown"]), cmd


def ingest(repo: str, settings: Settings, sha: str | None = None) -> IngestResult:
    with span("sentinel.ingest", repo=repo):
        commit: str | None
        if is_url(repo):
            path, commit = clone_or_update(repo, settings.work_dir, sha)
            url = repo
        else:
            path = Path(repo).resolve()
            if not path.is_dir():
                raise FileNotFoundError(f"not a directory: {repo}")
            commit = _head_sha(path)
            url = path.as_uri()
        cfg = RepoConfig.load(path)
        language, counts = detect_language(path)
        pms, locks = detect_package_managers(path)
        fws, cmd = detect_test_frameworks(path)
        return IngestResult(
            repo_url=url,
            repo_path=str(path),
            commit_sha=commit,
            language=cfg.language or language,
            package_managers=pms,
            test_frameworks=fws,
            test_command=cfg.test_command or cmd,
            lockfiles=locks,
            lockfile_hash=lockfile_hash(path, locks),
            file_counts=counts,
            repo_config=cfg,
        )
