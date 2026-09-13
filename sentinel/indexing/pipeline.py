"""`index_repo`: discover files → tree-sitter → chunks → embeddings → store; build call graph."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.config import RepoConfig, Settings
from sentinel.indexing.callgraph import CallGraph
from sentinel.indexing.chunker import Chunk, chunk_parsed_file
from sentinel.indexing.embeddings import Embedder, make_embedder
from sentinel.indexing.store import IndexStore, open_store
from sentinel.indexing.treesitter import ParsedFile, language_for, parse_source
from sentinel.telemetry.otel import span

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    "site-packages",
    ".sentinel",
    "target",
    "out",
    "vendor",
    "third_party",
}
EXCLUDED_GLOBS = ("*.min.js", "*.d.ts", "*.bundle.js", "*.map", "*.lock", "*_pb2.py")
MAX_FILE_BYTES = 1_000_000


@dataclass
class IndexResult:
    repo_id: str
    repo_path: str
    files: int
    symbols: int
    chunks: int
    edges: int
    duration_s: float
    embedder: str
    skipped: list[str] = field(default_factory=list)


def repo_id_for(repo_path: Path) -> str:
    return hashlib.sha1(str(repo_path.resolve()).encode()).hexdigest()[:12]  # noqa: S324


def discover_files(repo_path: Path, cfg: RepoConfig | None = None) -> list[str]:
    """Repo-relative POSIX paths of supported source files. Honors .gitignore via git."""
    cfg = cfg or RepoConfig()
    rels: list[str] = []
    try:
        out = subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(repo_path),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
        rels = [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        for p in repo_path.rglob("*"):
            if p.is_file():
                rels.append(p.relative_to(repo_path).as_posix())

    keep: list[str] = []
    for rel in rels:
        rel = rel.replace("\\", "/")
        parts = rel.split("/")
        if any(part in EXCLUDED_DIRS for part in parts[:-1]):
            continue
        if language_for(rel) is None:
            continue
        if any(fnmatch.fnmatch(parts[-1], g) for g in EXCLUDED_GLOBS):
            continue
        if cfg.include and not any(fnmatch.fnmatch(rel, g) for g in cfg.include):
            continue
        if any(fnmatch.fnmatch(rel, g) for g in cfg.exclude):
            continue
        full = repo_path / rel
        try:
            if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        keep.append(rel)
    return sorted(set(keep))


def parse_repo(
    repo_path: Path, files: list[str]
) -> tuple[list[ParsedFile], list[Chunk], list[str]]:
    parsed_files: list[ParsedFile] = []
    chunks: list[Chunk] = []
    skipped: list[str] = []
    for rel in files:
        try:
            source = (repo_path / rel).read_text(encoding="utf-8", errors="replace")
            pf = parse_source(source, rel)
        except Exception as e:  # noqa: BLE001 - one bad file must not abort indexing
            skipped.append(f"{rel}: {type(e).__name__}: {e}")
            continue
        parsed_files.append(pf)
        chunks.extend(chunk_parsed_file(pf, source))
    return parsed_files, chunks, skipped


def _chunk_embedding_text(c: Chunk) -> str:
    """Symbol words are repeated so identifier-level intent dominates for short chunks."""
    head = c.file
    if c.symbol:
        words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", c.symbol).replace("_", " ").replace(".", " ")
        head += f" :: {c.symbol} {words} {words}"
    return f"{head}\n{c.text}"


def index_repo(
    repo_path: Path,
    settings: Settings,
    store: IndexStore | None = None,
    embedder: Embedder | None = None,
    commit_sha: str | None = None,
) -> IndexResult:
    t0 = time.perf_counter()
    repo_path = repo_path.resolve()
    embedder = embedder or make_embedder(settings)
    own_store = store is None
    store = store or open_store(settings.database_url, settings.work_dir, embedder.dim or 256)
    repo_id = repo_id_for(repo_path)
    cfg = RepoConfig.load(repo_path)

    with span("sentinel.index", repo_id=repo_id, repo_path=str(repo_path)) as s:
        files = discover_files(repo_path, cfg)
        parsed_files, chunks, skipped = parse_repo(repo_path, files)

        store.reset(repo_id)
        vectors = embedder.embed([_chunk_embedding_text(c) for c in chunks]) if chunks else []
        store.add_chunks(repo_id, chunks, vectors)
        symbols = [sym for pf in parsed_files for sym in pf.symbols]
        store.add_symbols(repo_id, symbols)

        cg = CallGraph.build(parsed_files)
        cg_path = settings.work_dir / "index" / repo_id / "callgraph.json"
        cg.save(cg_path)

        meta = {
            "repo_path": str(repo_path),
            "commit_sha": commit_sha,
            "embedder": embedder.name,
            "dim": embedder.dim,
            "files": len(files),
            "symbols": len(symbols),
            "chunks": len(chunks),
            "callgraph_path": str(cg_path),
            "indexed_at": time.time(),
        }
        store.set_meta(repo_id, meta)
        _write_last_index(settings.work_dir, repo_id, str(repo_path))
        s.set_attribute("sentinel.chunks", len(chunks))
        s.set_attribute("sentinel.symbols", len(symbols))

    if own_store:
        store.close()
    return IndexResult(
        repo_id=repo_id,
        repo_path=str(repo_path),
        files=len(files),
        symbols=len(symbols),
        chunks=len(chunks),
        edges=cg.g.number_of_edges(),
        duration_s=time.perf_counter() - t0,
        embedder=embedder.name,
        skipped=skipped,
    )


def _write_last_index(work_dir: Path, repo_id: str, repo_path: str) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "last_index.json").write_text(
        json.dumps({"repo_id": repo_id, "repo_path": repo_path}), encoding="utf-8"
    )


def read_last_index(work_dir: Path) -> tuple[str, str] | None:
    p = work_dir / "last_index.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["repo_id"], d["repo_path"]
