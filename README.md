# Sentinel — Autonomous Codebase Auditor

Sentinel clones a repository, indexes it, hunts for bugs with static analyzers and LLM agents,
**proves each bug with a failing test executed in a network-isolated Docker sandbox**, generates a
minimal fix, verifies it against the existing suite, and opens a reviewable PR. Every step is traced
with OpenTelemetry. See [SPEC.md](SPEC.md) for the full architecture and phased plan.

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Skeleton: layout, CLI, config, DB models, CI, docker-compose | done |
| 1 | Ingest + tree-sitter index + hybrid search + call graph + analyzer adapters | done |
| 2 | Docker sandbox, image cache, test-runner adapters | next |
| 3–8 | Graph, fix/PR, benchmark, telemetry, triage model, polish | planned |

## Quickstart

```bash
uv sync --all-groups
cp .env.example .env            # fill in keys (optional for Phase 1)
uv run sentinel --help

# Phase 1
uv run sentinel ingest  https://github.com/org/repo        # clone + detect language/tests
uv run sentinel index   https://github.com/org/repo        # symbols, chunks, embeddings, call graph
uv run sentinel search  "where is the auth token parsed"   # hybrid BM25 + vector (RRF)
uv run sentinel symbol  Session.lookup                     # definition + callers/callees
uv run sentinel analyze ./repo --sarif out.sarif           # ruff, bandit, mypy, eslint, tsc, semgrep
```

Dev storage is SQLite (FTS5 for BM25, brute-force cosine for vectors). Set
`SENTINEL_DATABASE_URL=postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel` and
`uv sync --extra postgres` to use Postgres + pgvector from `docker compose up -d postgres`.

### Embeddings

`text-embedding-3-large` (OpenAI) or `voyage-code-3` via LiteLLM, cached by content hash. With no
embedding key configured the indexer falls back to an **offline lexical hash embedder** and warns
loudly. That fallback is fine for BM25-dominated queries and CI, not for semantic search.

### Analyzers

Ruff, bandit and mypy run from Sentinel's own environment. ESLint and tsc use the target repo's own
`node_modules` and config and are skipped (with a recorded reason) when absent. Semgrep runs from a
local binary or the `semgrep/semgrep` Docker image; it has no native Windows build. All output is
normalised to one `AnalyzerFinding` shape and exported as SARIF 2.1.0 with a `sentinelCategory`
hint per rule.

## Deviations from SPEC.md §2.2 (flagged, not silent)

- `tree-sitter-languages` is unmaintained and incompatible with current py-tree-sitter; the official
  per-language packages (`tree-sitter-python`, `tree-sitter-typescript`, `tree-sitter-javascript`)
  are used instead.
- `tree-sitter` is pinned `<0.26`: 0.26.0 raises an access violation on Windows during node access.
- SQLite is a first-class *dev* index store alongside pgvector so the pipeline runs without Docker.

## Benchmark methodology note

Few-shot examples in prompts are drawn from the benchmark **training split only**. Test-split repos
are never seen by prompts or by the triage model. Results live in `bench/RESULTS.md`.
