# Sentinel — Autonomous Codebase Auditor

Sentinel clones a repository, indexes it, hunts for bugs with static analyzers and LLM agents,
**proves each bug with a failing test executed in a network-isolated Docker sandbox**, generates a
minimal fix, verifies it against the existing suite, and opens a reviewable PR. Every step is traced
with OpenTelemetry. See [SPEC.md](SPEC.md) for the full architecture and phased plan.

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Skeleton: layout, CLI, config, DB models, CI, docker-compose | done |
| 1 | Ingest + index + analyzers | in progress |
| 2–8 | Sandbox, graph, fix/PR, benchmark, telemetry, triage model, polish | planned |

## Quickstart

```bash
uv sync --all-groups
cp .env.example .env            # fill in keys
docker compose up -d            # postgres+pgvector, jaeger (optional for dev; SQLite is default)
uv run sentinel --help
```

## Benchmark methodology note

Few-shot examples in prompts are drawn from the benchmark **training split only**. Test-split repos
are never seen by prompts or by the triage model. Results live in `bench/RESULTS.md`.
