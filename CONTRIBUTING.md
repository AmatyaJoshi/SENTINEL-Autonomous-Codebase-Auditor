# Contributing

Read [SPEC.md](SPEC.md) and [CLAUDE.md](CLAUDE.md) first; they define the architecture and the
non-negotiables (sandbox isolation, schema-validated LLM output, prompts as files).

## Setup

```bash
uv sync --all-groups
cd dashboard && npm ci && cd ..
cp .env.example .env
uv run pre-commit install   # optional: ruff + mypy on commit
```

## Checks that must pass

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -o addopts=""
cd dashboard && npm run lint && npm run typecheck && npm run build
```

## Conventions

- Conventional commits (`feat(scope): ...`, `fix: ...`). One phase or feature per PR.
- Every new graph node or tool ships with a unit test (scripted/cassette LLM, mocked Docker) and a span.
- New LLM output shapes go in `sentinel/llm/schemas.py`; new prompts in `sentinel/llm/prompts/*.md`.
- Never weaken `SandboxSettings` defaults or add a host execution path.
- When a benchmark number changes, add a row to `bench/RESULTS.md` (date, commit, number).
- Adding a dependency or changing the tech stack table in SPEC §2.2 requires discussion in the PR.
