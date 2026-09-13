"""Phase 1: tree-sitter symbols, chunker, call graph, hybrid store, pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.config import Settings
from sentinel.indexing.callgraph import CallGraph
from sentinel.indexing.chunker import MAX_TOKENS, chunk_parsed_file, estimate_tokens
from sentinel.indexing.embeddings import HashEmbedder
from sentinel.indexing.pipeline import discover_files, index_repo, read_last_index
from sentinel.indexing.store import SqliteIndexStore, hybrid_search, rrf_fuse
from sentinel.indexing.treesitter import parse_file, parse_source

FIX = Path(__file__).parent / "fixtures"
PYREPO = FIX / "pyrepo"
TSREPO = FIX / "tsrepo"


# --------------------------------------------------------------------------- tree-sitter


def test_python_symbols() -> None:
    pf = parse_file(PYREPO, "app/auth.py")
    assert pf is not None
    names = {s.name: s for s in pf.symbols}
    assert {
        "parse_auth_token",
        "Session",
        "Session.__init__",
        "Session.lookup",
        "Session.is_admin",
        "first_n",
    } <= set(names)
    fn = names["parse_auth_token"]
    assert fn.kind == "function"
    assert fn.docstring == "Parse a bearer token from an Authorization header."
    assert "split_header" in fn.calls and "b64decode" in fn.calls
    assert fn.line_start == 11 and fn.line_end == 16
    assert names["Session.lookup"].kind == "method"
    assert names["Session.lookup"].parent == "Session"
    assert names["Session"].docstring == "A user session."
    mods = {i.module: i.names for i in pf.imports}
    assert mods["app.util"] == ["split_header"]
    assert "base64" in mods


def test_typescript_symbols() -> None:
    pf = parse_file(TSREPO, "src/auth.ts")
    assert pf is not None
    by = {s.name: s for s in pf.symbols}
    assert by["Token"].kind == "interface"
    assert by["Role"].kind == "type"
    assert by["parseAuthToken"].kind == "function"
    assert "splitHeader" in by["parseAuthToken"].calls
    assert by["firstN"].kind == "function"  # arrow function const
    assert by["SessionStore"].kind == "class"
    assert by["SessionStore.refresh"].kind == "method"
    assert "load" in by["SessionStore.refresh"].calls
    imp = pf.imports[0]
    assert imp.module == "./util" and imp.names == ["splitHeader"]


def test_tsx_and_js_parse() -> None:
    tsx = parse_source("export const App = () => <div onClick={() => go()} />;", "a.tsx")
    assert [s.name for s in tsx.symbols] == ["App"]
    js = parse_source("function f(a){ return g(a) }\nclass K { m(){} }", "b.js")
    assert {s.name for s in js.symbols} == {"f", "K", "K.m"}


def test_nested_and_decorated() -> None:
    src = (
        "@dec\ndef outer():\n    def inner():\n        pass\n    return inner\n\n"
        "class A:\n    @staticmethod\n    def s():\n        return 1\n"
    )
    pf = parse_source(src, "x.py")
    names = {s.name: s for s in pf.symbols}
    assert set(names) == {"outer", "outer.inner", "A", "A.s"}
    assert names["outer"].line_start == 1  # decorator included in range


# --------------------------------------------------------------------------- chunker


def test_chunks_cover_symbols_and_respect_budget() -> None:
    src = (PYREPO / "app/auth.py").read_text()
    pf = parse_source(src, "app/auth.py")
    chunks = chunk_parsed_file(pf, src)
    syms = {c.symbol for c in chunks if c.symbol}
    assert {"parse_auth_token", "Session.lookup", "first_n", "Session"} <= syms
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)
    # module-level leftovers (imports/constants) are chunked too
    assert any(c.symbol is None and "API_TOKEN" in c.text for c in chunks)
    # no chunk overlaps another symbol's chunk at the leaf level
    leaf = sorted((c.line_start, c.line_end) for c in chunks if c.symbol != "Session")
    for (s1, e1), (s2, _e2) in zip(leaf, leaf[1:], strict=False):
        assert s2 > e1, f"overlap {s1}-{e1} vs {s2}"


def test_large_function_split_with_overlap() -> None:
    body = "\n".join(f"    x{i} = compute_value_{i}(x{i - 1}) + {i}" for i in range(1, 200))
    src = f"def big(x0):\n{body}\n    return x199\n"
    pf = parse_source(src, "big.py")
    chunks = chunk_parsed_file(pf, src)
    assert len(chunks) > 1
    assert all(c.symbol == "big" for c in chunks)
    assert all(estimate_tokens(c.text) <= MAX_TOKENS for c in chunks)
    assert chunks[0].line_start == 1 and chunks[-1].line_end == src.count("\n")
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.line_start <= a.line_end  # overlap present
        assert a.line_end - b.line_start + 1 == 20


# --------------------------------------------------------------------------- call graph


def test_callgraph_edges_and_blast_radius() -> None:
    files = [p for p in (parse_file(PYREPO, f) for f in discover_files(PYREPO)) if p]
    cg = CallGraph.build(files)
    assert "app/util.py::split_header" in cg.callees("app/auth.py::parse_auth_token")
    assert cg.callers("split_header") == ["app/auth.py::parse_auth_token"]
    assert cg.callers("app/auth.py::first_n") == ["tests/test_auth.py::test_first_n"]
    assert cg.blast_radius("split_header") == 1
    assert cg.blast_radius("nonexistent") == 0


def test_callgraph_roundtrip(tmp_path: Path) -> None:
    files = [p for p in (parse_file(TSREPO, f) for f in discover_files(TSREPO)) if p]
    cg = CallGraph.build(files)
    assert "src/util.ts::splitHeader" in cg.callees("parseAuthToken")
    assert "src/auth.ts::SessionStore.load" in cg.callees("SessionStore.refresh")
    cg.save(tmp_path / "cg.json")
    cg2 = CallGraph.load(tmp_path / "cg.json")
    assert set(cg2.g.edges) == set(cg.g.edges)
    assert cg2.callers("splitHeader") == cg.callers("splitHeader")


# --------------------------------------------------------------------------- store


def test_rrf_fuse_prefers_items_in_both_lists() -> None:
    from sentinel.indexing.store import SearchHit

    def h(i: str) -> SearchHit:
        return SearchHit(
            chunk_id=i,
            file="f",
            line_start=1,
            line_end=1,
            symbol=None,
            text="",
            score=0,
            sources=["x"],
        )

    fused = rrf_fuse([[h("a"), h("b"), h("c")], [h("c"), h("d")]], k=3)
    assert fused[0].chunk_id == "c"
    assert set(fused[0].sources) == {"x"}


def test_sqlite_hybrid_search_finds_auth_token(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url="sqlite://", work_dir=tmp_path)  # type: ignore[call-arg]
    store = SqliteIndexStore(tmp_path / "idx.sqlite")
    emb = HashEmbedder()
    res = index_repo(PYREPO, settings, store=store, embedder=emb)
    assert res.files == 4 and res.symbols >= 8 and res.chunks >= 8 and res.edges == 2
    assert store.chunk_count(res.repo_id) == res.chunks
    assert read_last_index(tmp_path) == (res.repo_id, str(PYREPO.resolve()))

    for mode in ("hybrid", "bm25", "vector"):
        hits = hybrid_search(
            store, emb, res.repo_id, "where is the auth token parsed", k=3, mode=mode
        )  # type: ignore[arg-type]
        assert hits, mode
        symbols = [h.symbol for h in hits]
        if mode == "vector":  # lexical hash embedder: only require presence in top-3
            assert "parse_auth_token" in symbols, symbols
        else:
            assert symbols[0] == "parse_auth_token", (mode, symbols)
    hyb = hybrid_search(store, emb, res.repo_id, "auth token", k=3)
    assert set(hyb[0].sources) == {"bm25", "vector"}

    recs = store.get_symbol(res.repo_id, "Session.lookup")
    assert len(recs) == 1 and recs[0].kind == "method" and recs[0].line_start == 25
    assert store.get_symbol(res.repo_id, "lookup")[0].name == "Session.lookup"
    meta = store.get_meta(res.repo_id)
    assert meta and meta["embedder"] == "hash-256" and Path(meta["callgraph_path"]).exists()

    # re-index is idempotent
    res2 = index_repo(PYREPO, settings, store=store, embedder=emb)
    assert store.chunk_count(res2.repo_id) == res.chunks
    store.close()


def test_discover_files_filters(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "i.js").write_text("x")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.min.js").write_text("x")
    (tmp_path / "c.d.ts").write_text("x")
    (tmp_path / "d.ts").write_text("x")
    (tmp_path / "e.txt").write_text("x")
    assert discover_files(tmp_path) == ["a.py", "d.ts"]


@pytest.mark.skipif(
    not __import__("os").environ.get("SENTINEL_TEST_PG_URL"),
    reason="set SENTINEL_TEST_PG_URL to run against pgvector (docker compose up -d postgres)",
)
def test_postgres_store_roundtrip(tmp_path: Path) -> None:
    import os

    from sentinel.indexing.store import PostgresIndexStore

    settings = Settings(_env_file=None, work_dir=tmp_path)  # type: ignore[call-arg]
    emb = HashEmbedder()
    store = PostgresIndexStore(os.environ["SENTINEL_TEST_PG_URL"], emb.dim)
    res = index_repo(PYREPO, settings, store=store, embedder=emb)
    hits = hybrid_search(store, emb, res.repo_id, "where is the auth token parsed", k=3)
    assert hits and hits[0].symbol == "parse_auth_token"
    store.reset(res.repo_id)
    store.close()
