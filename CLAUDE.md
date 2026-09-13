# Sentinel — conventions for Claude Code
- Read SPEC.md before any change. Never remove the sandbox network isolation or timeouts.
- Python 3.12, uv, ruff (line length 100), mypy --strict on sentinel/ (tests exempt).
- Every LLM output goes through a Pydantic schema in sentinel/llm/schemas.py. No free-text parsing.
- Prompts live in sentinel/llm/prompts/*.md and are loaded by name; never inline prompts in code.
- Every new node/tool gets: a unit test with a recorded LLM cassette, and a span.
- No secrets in code. Read from settings only.
- Commit messages: conventional commits. One phase = one PR.
- When a benchmark number changes, update bench/RESULTS.md with date, commit, and number.
- Ask before adding a dependency or changing the tech stack table in SPEC.md §2.2.
