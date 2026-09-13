"""Index stores with hybrid (BM25 + vector, RRF-fused) search.

- `SqliteIndexStore`: dev backend. FTS5 for BM25, brute-force cosine for vectors.
- `PostgresIndexStore`: prod backend. `tsvector` for BM25-style ranking, pgvector for ANN.
Both expose the same `IndexStore` protocol; `open_store()` picks by DATABASE_URL.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from sentinel.indexing.chunker import Chunk
from sentinel.indexing.embeddings import Embedder
from sentinel.indexing.treesitter import Symbol

SearchMode = Literal["hybrid", "bm25", "vector"]
RRF_K = 60


class SearchHit(BaseModel):
    chunk_id: str
    file: str
    line_start: int
    line_end: int
    symbol: str | None
    text: str
    score: float
    sources: list[str]

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line_start}-{self.line_end}"


class SymbolRecord(BaseModel):
    name: str
    kind: str
    file: str
    line_start: int
    line_end: int
    signature: str
    docstring: str | None
    parent: str | None
    text: str


class IndexStore(Protocol):
    def reset(self, repo_id: str) -> None: ...
    def add_chunks(self, repo_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    def add_symbols(self, repo_id: str, symbols: Iterable[Symbol]) -> None: ...
    def search_bm25(self, repo_id: str, query: str, k: int) -> list[SearchHit]: ...
    def search_vector(self, repo_id: str, vector: list[float], k: int) -> list[SearchHit]: ...
    def get_symbol(self, repo_id: str, name: str) -> list[SymbolRecord]: ...
    def chunk_count(self, repo_id: str) -> int: ...
    def set_meta(self, repo_id: str, meta: dict[str, Any]) -> None: ...
    def get_meta(self, repo_id: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


def rrf_fuse(rankings: list[list[SearchHit]], k: int, rrf_k: int = RRF_K) -> list[SearchHit]:
    scores: dict[str, float] = {}
    hits: dict[str, SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank + 1)
            if hit.chunk_id in hits:
                hits[hit.chunk_id].sources.extend(hit.sources)
            else:
                hits[hit.chunk_id] = hit.model_copy(deep=True)
    fused = []
    for cid, score in sorted(scores.items(), key=lambda kv: -kv[1])[:k]:
        h = hits[cid]
        h.score = score
        h.sources = sorted(set(h.sources))
        fused.append(h)
    return fused


def hybrid_search(
    store: IndexStore,
    embedder: Embedder,
    repo_id: str,
    query: str,
    k: int = 8,
    mode: SearchMode = "hybrid",
) -> list[SearchHit]:
    rankings: list[list[SearchHit]] = []
    if mode in ("hybrid", "bm25"):
        rankings.append(store.search_bm25(repo_id, query, k * 3))
    if mode in ("hybrid", "vector"):
        vec = embedder.embed([query])[0]
        rankings.append(store.search_vector(repo_id, vec, k * 3))
    if mode != "hybrid":
        return rankings[0][:k]
    return rrf_fuse(rankings, k)


# --------------------------------------------------------------------------- sqlite

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|\d{2,}")
STOPWORDS = frozenset(
    (  # noqa: SIM905 - a word list reads better than 35 quoted literals
        "a an and are as at be by do does for from how in into is it of on or that the this "
        "to was we what when where which who why with you your"
    ).split()
)


def _fts_query(query: str) -> str:
    """Natural-language query → FTS5 OR-query of quoted terms plus sub-words."""
    terms: set[str] = set()
    for w in _WORD.findall(query):
        for part in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", w).replace("_", " ").lower().split():
            if len(part) > 1 and part not in STOPWORDS:
                terms.add(part)
    return " OR ".join(f'"{t}"' for t in sorted(terms)) or '""'


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class SqliteIndexStore:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT NOT NULL, repo_id TEXT NOT NULL, file TEXT NOT NULL,
                line_start INT NOT NULL, line_end INT NOT NULL, symbol TEXT, language TEXT,
                text TEXT NOT NULL, vector BLOB, PRIMARY KEY (repo_id, id));
            CREATE INDEX IF NOT EXISTS chunks_repo ON chunks(repo_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                id UNINDEXED, repo_id UNINDEXED, file, symbol, text,
                tokenize = "unicode61");
            CREATE TABLE IF NOT EXISTS symbols (
                repo_id TEXT NOT NULL, name TEXT NOT NULL, short TEXT NOT NULL, kind TEXT,
                file TEXT, line_start INT, line_end INT, signature TEXT, docstring TEXT,
                parent TEXT, text TEXT);
            CREATE INDEX IF NOT EXISTS symbols_short ON symbols(repo_id, short);
            CREATE TABLE IF NOT EXISTS meta (repo_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            """
        )

    def reset(self, repo_id: str) -> None:
        for t in ("chunks", "chunks_fts", "symbols", "meta"):
            self._conn.execute(f"DELETE FROM {t} WHERE repo_id=?", (repo_id,))  # noqa: S608
        self._conn.commit()

    def add_chunks(self, repo_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks/vectors length mismatch")
        rows = [
            (
                c.id,
                repo_id,
                c.file,
                c.line_start,
                c.line_end,
                c.symbol,
                c.language,
                c.text,
                _pack(v),
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self._conn.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self._conn.executemany(
            "INSERT INTO chunks_fts (id, repo_id, file, symbol, text) VALUES (?,?,?,?,?)",
            [(c.id, repo_id, c.file, c.symbol or "", c.text) for c in chunks],
        )
        self._conn.commit()

    def add_symbols(self, repo_id: str, symbols: Iterable[Symbol]) -> None:
        self._conn.executemany(
            "INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    repo_id,
                    s.name,
                    s.short_name,
                    s.kind,
                    s.file,
                    s.line_start,
                    s.line_end,
                    s.signature,
                    s.docstring,
                    s.parent,
                    s.text,
                )
                for s in symbols
            ],
        )
        self._conn.commit()

    def search_bm25(self, repo_id: str, query: str, k: int) -> list[SearchHit]:
        q = _fts_query(query)
        rows = self._conn.execute(
            """
            SELECT c.id, c.file, c.line_start, c.line_end, c.symbol, c.text,
                   bm25(chunks_fts, 0, 0, 0.5, 6.0, 1.0) AS rank
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.id AND c.repo_id = chunks_fts.repo_id
            WHERE chunks_fts MATCH ? AND chunks_fts.repo_id = ?
            ORDER BY rank LIMIT ?
            """,
            (q, repo_id, k),
        ).fetchall()
        return [
            SearchHit(
                chunk_id=r[0],
                file=r[1],
                line_start=r[2],
                line_end=r[3],
                symbol=r[4],
                text=r[5],
                score=-float(r[6]),
                sources=["bm25"],
            )
            for r in rows
        ]

    def search_vector(self, repo_id: str, vector: list[float], k: int) -> list[SearchHit]:
        rows = self._conn.execute(
            "SELECT id, file, line_start, line_end, symbol, text, vector FROM chunks "
            "WHERE repo_id=? AND vector IS NOT NULL",
            (repo_id,),
        ).fetchall()
        scored: list[tuple[float, Any]] = []
        for r in rows:
            v = _unpack(r[6])
            if len(v) != len(vector):
                continue
            dot = sum(a * b for a, b in zip(v, vector, strict=True))
            na = sum(a * a for a in v) ** 0.5 or 1.0
            nb = sum(b * b for b in vector) ** 0.5 or 1.0
            scored.append((dot / (na * nb), r))
        scored.sort(key=lambda t: -t[0])
        return [
            SearchHit(
                chunk_id=r[0],
                file=r[1],
                line_start=r[2],
                line_end=r[3],
                symbol=r[4],
                text=r[5],
                score=s,
                sources=["vector"],
            )
            for s, r in scored[:k]
        ]

    def get_symbol(self, repo_id: str, name: str) -> list[SymbolRecord]:
        short = name.rsplit(".", 1)[-1]
        rows = self._conn.execute(
            "SELECT name, kind, file, line_start, line_end, signature, docstring, parent, text "
            "FROM symbols WHERE repo_id=? AND short=?",
            (repo_id, short),
        ).fetchall()
        recs = [
            SymbolRecord(
                name=r[0],
                kind=r[1],
                file=r[2],
                line_start=r[3],
                line_end=r[4],
                signature=r[5],
                docstring=r[6],
                parent=r[7],
                text=r[8],
            )
            for r in rows
        ]
        exact = [r for r in recs if r.name == name]
        return exact or recs

    def chunk_count(self, repo_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE repo_id=?", (repo_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def set_meta(self, repo_id: str, meta: dict[str, Any]) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (repo_id, json.dumps(meta)))
        self._conn.commit()

    def get_meta(self, repo_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT data FROM meta WHERE repo_id=?", (repo_id,)).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        return data

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- postgres


class PostgresIndexStore:
    """Requires the `postgres` extra and the `vector` extension (docker-compose provides both)."""

    def __init__(self, url: str, dim: int) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        self.dim = dim
        self._conn = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"))
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT NOT NULL, repo_id TEXT NOT NULL, file TEXT NOT NULL,
                line_start INT NOT NULL, line_end INT NOT NULL, symbol TEXT, language TEXT,
                text TEXT NOT NULL, embedding vector({self.dim}),
                tsv tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('simple', coalesce(symbol, '')), 'A') ||
                    setweight(to_tsvector('simple', file), 'B') ||
                    setweight(to_tsvector('simple', text), 'C')) STORED,
                PRIMARY KEY (repo_id, id));
            CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin(tsv);
            CREATE TABLE IF NOT EXISTS symbols (
                repo_id TEXT NOT NULL, name TEXT NOT NULL, short TEXT NOT NULL, kind TEXT,
                file TEXT, line_start INT, line_end INT, signature TEXT, docstring TEXT,
                parent TEXT, text TEXT);
            CREATE INDEX IF NOT EXISTS symbols_short ON symbols(repo_id, short);
            CREATE TABLE IF NOT EXISTS index_meta (repo_id TEXT PRIMARY KEY, data JSONB NOT NULL);
            """
        )
        self._conn.commit()

    def reset(self, repo_id: str) -> None:
        for t in ("chunks", "symbols", "index_meta"):
            self._conn.execute(f"DELETE FROM {t} WHERE repo_id=%s", (repo_id,))  # noqa: S608
        self._conn.commit()

    def add_chunks(self, repo_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (id, repo_id, file, line_start, line_end, symbol, language, "
                "text, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (repo_id, id) DO NOTHING",
                [
                    (
                        c.id,
                        repo_id,
                        c.file,
                        c.line_start,
                        c.line_end,
                        c.symbol,
                        c.language,
                        c.text,
                        v,
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )
        self._conn.commit()

    def add_symbols(self, repo_id: str, symbols: Iterable[Symbol]) -> None:
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO symbols VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        repo_id,
                        s.name,
                        s.short_name,
                        s.kind,
                        s.file,
                        s.line_start,
                        s.line_end,
                        s.signature,
                        s.docstring,
                        s.parent,
                        s.text,
                    )
                    for s in symbols
                ],
            )
        self._conn.commit()

    def search_bm25(self, repo_id: str, query: str, k: int) -> list[SearchHit]:
        terms = [t.strip('"') for t in _fts_query(query).split(" OR ") if t.strip('"')]
        tsq = " | ".join(terms) or "''"
        rows = self._conn.execute(
            "SELECT id, file, line_start, line_end, symbol, text, "
            "ts_rank_cd(tsv, to_tsquery('simple', %s)) AS rank FROM chunks "
            "WHERE repo_id=%s AND tsv @@ to_tsquery('simple', %s) ORDER BY rank DESC LIMIT %s",
            (tsq, repo_id, tsq, k),
        ).fetchall()
        return [
            SearchHit(
                chunk_id=r[0],
                file=r[1],
                line_start=r[2],
                line_end=r[3],
                symbol=r[4],
                text=r[5],
                score=float(r[6]),
                sources=["bm25"],
            )
            for r in rows
        ]

    def search_vector(self, repo_id: str, vector: list[float], k: int) -> list[SearchHit]:
        rows = self._conn.execute(
            "SELECT id, file, line_start, line_end, symbol, text, 1 - (embedding <=> %s::vector) "
            "FROM chunks WHERE repo_id=%s AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (vector, repo_id, vector, k),
        ).fetchall()
        return [
            SearchHit(
                chunk_id=r[0],
                file=r[1],
                line_start=r[2],
                line_end=r[3],
                symbol=r[4],
                text=r[5],
                score=float(r[6]),
                sources=["vector"],
            )
            for r in rows
        ]

    def get_symbol(self, repo_id: str, name: str) -> list[SymbolRecord]:
        short = name.rsplit(".", 1)[-1]
        rows = self._conn.execute(
            "SELECT name, kind, file, line_start, line_end, signature, docstring, parent, text "
            "FROM symbols WHERE repo_id=%s AND short=%s",
            (repo_id, short),
        ).fetchall()
        recs = [
            SymbolRecord(
                name=r[0],
                kind=r[1],
                file=r[2],
                line_start=r[3],
                line_end=r[4],
                signature=r[5],
                docstring=r[6],
                parent=r[7],
                text=r[8],
            )
            for r in rows
        ]
        exact = [r for r in recs if r.name == name]
        return exact or recs

    def chunk_count(self, repo_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE repo_id=%s", (repo_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def set_meta(self, repo_id: str, meta: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO index_meta VALUES (%s, %s::jsonb) "
            "ON CONFLICT (repo_id) DO UPDATE SET data = EXCLUDED.data",
            (repo_id, json.dumps(meta)),
        )
        self._conn.commit()

    def get_meta(self, repo_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT data FROM index_meta WHERE repo_id=%s", (repo_id,)
        ).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = row[0]
        return data

    def close(self) -> None:
        self._conn.close()


def open_store(database_url: str, work_dir: Path, dim: int) -> IndexStore:
    if database_url.startswith("postgresql"):
        return PostgresIndexStore(database_url, dim)
    return SqliteIndexStore(work_dir / "index.sqlite")
