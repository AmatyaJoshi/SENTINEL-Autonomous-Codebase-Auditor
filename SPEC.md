# SENTINEL — Autonomous Codebase Auditor
## Technical Architecture & Requirements Specification (for Claude Code)

> **How to use this document:** Drop this file into the root of a new repo as `SPEC.md`, then tell Claude Code: *"Read SPEC.md fully. Build Phase 0 and Phase 1 exactly as specified. Do not skip the sandbox or the benchmark harness — they are non-negotiable. Ask before deviating from the tech stack."* Work phase by phase. Each phase has acceptance criteria that must pass before moving on.

---

## 0. One-paragraph pitch (for README / resume)

Sentinel is a LangGraph-based multi-agent system that clones a repository, builds a semantic + structural index of the code, hunts for bugs using a combination of static analyzers and LLM reasoning, **proves each bug by writing a failing test that executes in an isolated sandbox**, generates a minimal fix, verifies the fix makes the test pass without breaking the existing suite, and opens a reviewable pull request with a full audit trail. Every agent step is traced with OpenTelemetry. Detection quality is measured on a reproducible bug-injection benchmark and on SWE-bench-style real issues, with precision/recall reported per bug category.

**Why this is not a toy:** most "AI code review" projects print suggestions. Sentinel is held to the standard of *executable proof* — a finding only counts if a test reproduces it. That single design decision is what you will talk about in interviews.

---

## 1. Goals, non-goals, success criteria

### 1.1 Goals
1. Given a Git URL (public GitHub repo) or local path, autonomously produce a ranked list of **verified** bugs.
2. For each verified bug: a failing test, a candidate patch, and a passing re-run.
3. Open one PR per bug (or one grouped PR, configurable) with a structured description.
4. Support **TypeScript/JavaScript and Python** as first-class targets (chosen deliberately: your Next.js/Turbopack/React contributions are TS; your ML work is Python).
5. Report benchmark numbers: precision, recall, F1 per bug class, patch pass rate, cost per repo, wall-clock time.
6. Emit OpenTelemetry traces for every LLM call, tool call and graph transition (this is the bridge to Project 2).

### 1.2 Non-goals (v1)
- No IDE plugin.
- No support for compiled languages other than TS (no Rust/Go/Java in v1 — mention as roadmap).
- No automatic merging. Humans merge.
- No security pentesting or exploit generation. Security *smells* (hardcoded secrets, SQL string concatenation, unsafe `eval`) are in scope; exploit code is not.

### 1.3 Definition of done (project level)
- `sentinel audit https://github.com/<org>/<repo>` runs end-to-end on at least 3 real repos including a 50k+ LOC TS repo.
- Benchmark harness produces a `report.html` and `report.json` with the metrics in §8.
- ≥ 0.70 precision on injected-bug benchmark (target ≥ 0.80), recall reported honestly whatever it is.
- At least 5 real PRs opened on your own forks (screenshots for README).

---

## 2. System architecture

### 2.1 High-level component diagram (describe in README with Mermaid)

```
                ┌──────────────────────────────────────────────────────┐
                │                    CLI / FastAPI                     │
                │        sentinel audit | bench | serve | replay       │
                └───────────────────────┬──────────────────────────────┘
                                        │
                ┌───────────────────────▼──────────────────────────────┐
                │              LangGraph Orchestrator                   │
                │  (StateGraph, checkpointed to SQLite/Postgres)        │
                │                                                       │
                │  Ingest → Index → Plan → Hunt(N parallel) → Verify → │
                │  Fix → Regress → Rank → Report/PR                     │
                └──┬─────────┬──────────┬──────────┬──────────┬────────┘
                   │         │          │          │          │
        ┌──────────▼──┐ ┌────▼─────┐ ┌──▼───────┐ ┌▼───────┐ ┌▼──────────┐
        │ Repo Index  │ │ Analyzer │ │ Sandbox  │ │ GitHub │ │ Telemetry │
        │ tree-sitter │ │ adapters │ │ Docker   │ │ API    │ │ OTel →    │
        │ + pgvector  │ │ semgrep, │ │ runner   │ │ PyGit- │ │ Project 2 │
        │ + call graph│ │ eslint,  │ │ (no net) │ │ hub    │ │ (Lens)    │
        │             │ │ ruff,tsc │ │          │ │        │ │           │
        └─────────────┘ └──────────┘ └──────────┘ └────────┘ └───────────┘
```

### 2.2 Tech stack (do not substitute without asking)

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | LangGraph ecosystem, ML tooling |
| Orchestration | **LangGraph** (StateGraph + checkpointer) | Resumable, inspectable agent graphs; matches resume skill |
| LLM access | LiteLLM router → Anthropic Claude (primary), OpenAI (fallback), local vLLM (fine-tuned model, §7) | Provider-agnostic; lets you swap the fine-tuned model in |
| Structured output | Pydantic v2 models + provider JSON mode / tool calling | Every agent output must validate |
| Code parsing | **tree-sitter** (py-tree-sitter, tree-sitter-languages) | Language-agnostic AST → functions, classes, imports |
| Embeddings | `text-embedding-3-large` or `voyage-code-3` (config), stored in **Postgres + pgvector** | Code-aware embeddings; hybrid search with BM25 via `pg_search`/`tsvector` |
| Static analyzers | semgrep (both langs), ruff + bandit + mypy (Python), eslint + typescript-eslint + tsc (TS) | Cheap, high-precision seeds for the LLM hunter |
| Sandbox | Docker (python:3.12-slim, node:20-alpine), `--network none`, CPU/mem limits, 120 s timeout | Never execute untrusted code on host |
| VCS / PR | GitPython + PyGithub | Branching, commits, PR creation |
| Observability | OpenTelemetry SDK + **OpenLLMetry (Traceloop SDK)** exporting OTLP → Project 2 collector; fallback to Jaeger | Your OSS contribution, made visible |
| Storage | SQLite (dev) / Postgres 16 (prod) via SQLModel | Runs, findings, benchmark results |
| API/UI | FastAPI + minimal Next.js dashboard (optional, Phase 6) | Show live graph execution |
| Config | `pydantic-settings`, `.env`, `sentinel.yaml` per repo | |
| Testing | pytest, pytest-asyncio, hypothesis, VCR-style LLM response cassettes | Deterministic CI |
| CI | GitHub Actions: lint, type-check, unit tests, nightly benchmark on small suite | |
| Packaging | `uv` + `pyproject.toml`, entrypoint `sentinel` | |

### 2.3 Repository layout

```
sentinel/
├── SPEC.md                      # this file
├── CLAUDE.md                    # coding conventions for Claude Code (§11)
├── pyproject.toml
├── sentinel/
│   ├── cli.py                   # typer CLI
│   ├── config.py
│   ├── graph/
│   │   ├── state.py             # AuditState (Pydantic)
│   │   ├── build.py             # StateGraph assembly, edges, checkpointer
│   │   └── nodes/
│   │       ├── ingest.py
│   │       ├── index.py
│   │       ├── plan.py
│   │       ├── hunt.py          # fan-out per target
│   │       ├── verify.py
│   │       ├── fix.py
│   │       ├── regress.py
│   │       ├── rank.py
│   │       └── report.py
│   ├── indexing/
│   │   ├── treesitter.py        # symbol extraction
│   │   ├── chunker.py
│   │   ├── callgraph.py
│   │   └── store.py             # pgvector + BM25 hybrid
│   ├── analyzers/
│   │   ├── base.py              # AnalyzerAdapter protocol
│   │   ├── semgrep.py
│   │   ├── ruff.py
│   │   ├── bandit.py
│   │   ├── eslint.py
│   │   └── tsc.py
│   ├── sandbox/
│   │   ├── docker_runner.py
│   │   ├── images/              # Dockerfiles
│   │   └── test_runner.py       # pytest / vitest / jest adapters
│   ├── tools/                   # LangChain tools exposed to agents
│   │   ├── read_file.py
│   │   ├── search_code.py
│   │   ├── get_symbol.py
│   │   ├── get_callers.py
│   │   ├── run_tests.py
│   │   └── apply_patch.py
│   ├── llm/
│   │   ├── router.py            # LiteLLM wrapper, retries, cost tracking
│   │   ├── prompts/             # .md prompt files, versioned
│   │   └── schemas.py           # Pydantic output schemas
│   ├── github/
│   │   └── pr.py
│   ├── telemetry/
│   │   └── otel.py
│   └── db/
│       ├── models.py
│       └── session.py
├── bench/
│   ├── inject/                  # mutation operators (§8.2)
│   ├── datasets/                # SWE-bench Lite subset manifests, BugsInPy pointers
│   ├── run_bench.py
│   ├── score.py
│   └── report_template.html
├── training/                    # §7
│   ├── build_dataset.py
│   ├── train_triage_lora.py
│   ├── eval_triage.py
│   └── configs/
├── tests/
└── .github/workflows/
```

---

## 3. LangGraph state & graph definition

### 3.1 State schema (`graph/state.py`)

```python
class Finding(BaseModel):
    id: str
    category: Literal[
        "null_deref",
        "off_by_one",
        "unhandled_exception",
        "resource_leak",
        "race_condition",
        "type_error",
        "logic_error",
        "security_smell",
        "api_misuse",
        "dead_code",
        "perf",
    ]
    severity: Literal["critical", "high", "medium", "low"]
    file: str
    line_start: int
    line_end: int
    symbol: str | None
    description: str
    evidence: list[str]  # analyzer findings, code excerpts
    hypothesis: str  # what input triggers it
    confidence: float  # 0-1, from hunter + triage model
    status: Literal[
        "candidate", "test_written", "verified", "refuted", "fixed", "regressed", "pr_opened"
    ]
    test_path: str | None
    test_code: str | None
    patch_diff: str | None
    verify_log: str | None
    regress_log: str | None
    pr_url: str | None
    cost_usd: float = 0.0


class AuditState(TypedDict):
    run_id: str
    repo_url: str
    repo_path: str
    language: Literal["python", "typescript", "mixed"]
    commit_sha: str
    index_ready: bool
    plan: list[HuntTarget]  # files/symbols to examine, prioritized
    findings: Annotated[list[Finding], operator.add]  # reducer: append
    budget: Budget  # max_usd, max_minutes, max_findings
    spent: Budget
    messages: Annotated[list[BaseMessage], add_messages]
    errors: Annotated[list[str], operator.add]
```

### 3.2 Nodes

| Node | Type | Responsibility | Output |
|---|---|---|---|
| `ingest` | deterministic | Clone repo at pinned SHA, detect language(s), package manager, test framework, read `sentinel.yaml` if present | `repo_path`, `language`, test config |
| `index` | deterministic | tree-sitter symbol extraction → chunks (function-level, ≤ 400 tokens, with 20-line overlap for large fns) → embeddings → pgvector; build import/call graph (networkx) | `index_ready` |
| `analyze` | deterministic | Run all analyzer adapters in parallel; normalise to `AnalyzerFinding` (SARIF-like) | seeds for `plan` |
| `plan` | LLM | Given repo map (file tree + top-level symbols + analyzer seed counts + git churn from `git log --stat`), produce ≤ N `HuntTarget`s ranked by expected bug density. Uses **risk heuristics**: churn × complexity × analyzer hits × low test coverage | `plan` |
| `hunt` | LLM (fan-out via `Send`) | One sub-agent per target. ReAct loop with tools (§4). Must output `Finding[]` with `hypothesis` stating a concrete triggering input. Hard cap 12 tool calls per target | `findings[status=candidate]` |
| `triage` | model | Score each candidate with the fine-tuned triage classifier (§7) blended with hunter confidence: `conf = 0.5*llm + 0.5*triage`. Drop below threshold (default 0.35). Dedupe by (file, line range, category) | filtered candidates |
| `verify` | LLM + sandbox | For each candidate: write a **failing** test that encodes the hypothesis. Run in sandbox. Accept only if test fails on current code *for the hypothesised reason* (assert on error type/message, not just "fails"). Up to 3 attempts. | `status=verified` or `refuted` |
| `fix` | LLM + sandbox | Generate minimal patch (unified diff, ≤ 60 changed lines default). Apply, re-run the new test → must pass. Up to 3 attempts with error feedback | `patch_diff` |
| `regress` | sandbox | Run the repo's **existing** test suite (or the affected package's tests if suite > 10 min) on patched code. Any new failure → status `regressed`, patch discarded, finding kept as verified-but-unfixed | `regress_log` |
| `rank` | deterministic + LLM | Sort by severity, confidence, blast radius (callers count from call graph). LLM writes a one-paragraph human explanation per finding | ordered findings |
| `report` | deterministic | Write `report.md/json/html`; if `--pr` flag, `github/pr.py` opens PRs | `pr_url` |

### 3.3 Edges & control flow
- `ingest → index → analyze → plan`
- `plan → hunt` via `Send()` fan-out, one branch per target (max concurrency configurable, default 4)
- `hunt → triage → verify` (loop over candidates, batched)
- `verify → fix` only for `verified`; `refuted` go straight to `rank` (reported separately as "unverified suspicions" — do **not** hide them, they're useful telemetry)
- `fix → regress → rank → report`
- Conditional edge before every LLM node: `check_budget`. If `spent.usd > budget.max_usd` or time exceeded → jump to `rank` with partial results. Never fail silently.
- Checkpointer: `SqliteSaver` (dev) / `PostgresSaver`. `sentinel replay <run_id> --from verify` must work.

### 3.4 Human-in-the-loop (Phase 5)
`interrupt_before=["report"]` when `--review` flag: prints findings, waits for `y/n/edit` per finding before PR creation.

---

## 4. Agent tools (exposed via LangChain `@tool`, all typed)

| Tool | Signature | Notes |
|---|---|---|
| `read_file(path, start=None, end=None)` | → str with line numbers | Max 300 lines per call |
| `search_code(query, k=8, mode="hybrid")` | → chunks with file/line | Hybrid BM25 + vector, RRF fusion |
| `get_symbol(name)` | → definition + docstring + location | From tree-sitter index |
| `get_callers(symbol)` / `get_callees(symbol)` | → list | From call graph |
| `get_analyzer_hits(path)` | → normalised findings | |
| `git_blame(path, line)` | → author/date/commit msg | Recent churn = risk signal |
| `run_snippet(code, lang)` | → stdout/stderr/exit | Sandbox, 30 s |
| `run_tests(paths=None)` | → structured results | Sandbox |
| `apply_patch(diff)` | → ok/error | Validates with `git apply --check` first |

Rules baked into the system prompt of every agent:
1. Never claim a bug without citing file:line and a concrete triggering input.
2. Prefer *fewer, real* findings over many speculative ones. False positives are scored negatively in the benchmark.
3. Never modify files outside `apply_patch`.
4. Stop when confidence < 0.3 after 6 tool calls.

---

## 5. Sandbox specification (security-critical)

- Docker only; `--network none`, `--memory 2g`, `--cpus 2`, `--pids-limit 256`, read-only root FS with a writable `/workspace` bind mount of a *copy* of the repo.
- Dependencies pre-installed at `ingest` via a one-time image build per repo (cache by lockfile hash). Network is allowed **only** during this build step.
- Timeouts: snippet 30 s, single test 120 s, full suite 15 min (configurable).
- Output truncated to 20 KB, ANSI stripped, structured via JUnit XML (pytest `--junitxml`, vitest `--reporter=junit`).
- Every sandbox execution emits a span with image hash, command, exit code, duration.

---

## 6. Prompts (store as versioned `.md` files under `llm/prompts/`, load by name)

- `plan.md` — inputs: repo map, analyzer summary, churn table. Output schema: `PlanOutput{targets: HuntTarget[]}`.
- `hunt_system.md` — the rules in §4 + category taxonomy with one-line definitions and a positive and negative example each.
- `verify_write_test.md` — must produce a test that: imports the real module, asserts on the *specific* failure (e.g. `pytest.raises(TypeError)`), includes a comment `# SENTINEL: expected to FAIL on current code because <hypothesis>`.
- `fix.md` — minimal diff, no refactors, preserve style, no new dependencies.
- `explain.md` — human-readable finding summary for PR body.

All prompts include few-shot examples drawn from the benchmark **training split only** (never the test split — document this in README; interviewers ask).

---

## 7. Model training pipeline — Bug Triage Classifier

**Purpose:** The hunter agent (frontier LLM) has high recall and mediocre precision. A cheap fine-tuned model that predicts *"will this candidate survive verification?"* cuts sandbox cost and raises precision. This is your "below the API layer" story.

### 7.1 Task definition
Binary classification (extendable to category classification):
- **Input:** candidate finding serialized as text: category, description, hypothesis, code excerpt (±15 lines), analyzer hits, hunter confidence, callers count.
- **Label:** `1` if `verify` produced a failing test that later `fix` made pass (true bug), `0` if `refuted` or the "bug" was in fact an injected no-op.

### 7.2 Data sources
1. **Self-generated (primary):** run Sentinel with triage disabled on the benchmark *training split* (§8) and on ~20 popular OSS repos. Log every candidate → outcome. Target ≥ 3,000 labelled candidates.
2. **Injected bugs:** every injected mutation is a guaranteed positive with known location — pair with hunter descriptions.
3. **Public:** BugsInPy (Python), Defects4J-style TS analogues are thin; use SWE-bench Lite/Verified problem statements + gold patches as positives; sample random unchanged functions as hard negatives.
4. Dedupe by repo+file+line; hold out **entire repos** for test (no leakage).

### 7.3 Model
- Base: `Qwen2.5-Coder-1.5B` (or 0.5B for CPU inference) with **LoRA** (r=16, alpha=32, dropout 0.05, target q/k/v/o + MLP) via `peft` + `transformers` + `trl`. Alternative baseline: `microsoft/codebert-base` sequence classifier — train both, report both.
- Head: classification via last-token pooling + linear layer (or `AutoModelForSequenceClassification`).
- Loss: BCE with positive class weighting (positives will be minority).
- Hyperparams (starting point): lr 2e-4, 3 epochs, batch 16 (grad accum to 64), max_len 2048, bf16, cosine schedule, warmup 5%.
- Hardware: single consumer GPU (Colab T4/A10 or local RTX). Must train in < 2 hours.

### 7.4 Pipeline (`training/`)
```
build_dataset.py   → reads DB findings table, joins outcomes, applies repo-level split → jsonl {train,val,test}
train_triage_lora.py → HF Trainer, logs to W&B (or MLflow), saves adapter + merged model + calibration temp
eval_triage.py     → precision/recall/F1/AUROC/Brier on test; calibration curve; compares vs (a) hunter-confidence-only, (b) codebert baseline, (c) frontier LLM zero-shot judge on the same input
export.py          → merged model → vLLM-servable or ONNX for CPU; registered in LiteLLM router as `sentinel-triage`
```

### 7.5 Reporting (goes in README with plots)
- AUROC and F1 vs. the three baselines.
- **Cost impact:** sandbox runs avoided per repo, $ saved per audit, precision before/after triage.
- Ablation: hunter-only vs hunter+triage on the held-out repos.
- Honest failure analysis: which categories the triage model is bad at (probably race conditions).

### 7.6 Optional stretch (Phase 7)
Fine-tune `Qwen2.5-Coder-7B` with LoRA on (failing test, error log, code) → patch pairs harvested from your own successful `fix` runs. Compare patch pass rate against the frontier model. Even a partial result is a great talking point.

---

## 8. Benchmark harness (`bench/`) — the interview centerpiece

### 8.1 Two benchmark tracks
1. **Injected-bug track (controlled, measures precision/recall):** take clean repos with strong test suites; inject known bugs; measure whether Sentinel finds them *and* how many false positives it raises.
2. **Real-issue track (measures real-world usefulness):** SWE-bench Lite (Python) subset of 30–50 instances + a hand-curated set of 15–20 real, already-fixed bugs from Next.js / React / Turbopack history (pick the buggy parent commit; Sentinel must find the bug the fix addressed). This is where the "demo on the repos you contributed to" story lives.

### 8.2 Mutation operators (`bench/inject/`)
Implement with tree-sitter rewrites, one mutation per injected instance, recorded in a manifest with exact location and category:

| Operator | Category | Example |
|---|---|---|
| `OffByOne` | off_by_one | `range(n)` → `range(n-1)`; `<=` ↔ `<` |
| `NullCheckRemoval` | null_deref | delete `if x is None: return` / `if (!x) return` |
| `ExceptionSwallow` | unhandled_exception | remove `try/except` or replace `raise` with `pass` |
| `ResourceLeak` | resource_leak | `with open()` → bare `open()`; remove `finally: close()` |
| `WrongOperator` | logic_error | `and`↔`or`, `+`↔`-`, `==`↔`!=` |
| `TypeConfusion` | type_error | remove a cast / `parseInt` / `str()` |
| `AsyncMisuse` | race_condition | drop an `await`; remove a lock |
| `ReturnMutation` | logic_error | swap return values, return early |
| `SecuritySmell` | security_smell | f-string SQL, hardcoded token literal |

Constraints: only mutate lines covered by existing tests (measured via coverage.py / c8) **and** confirm the mutation makes ≥ 1 existing test fail (i.e. it's a real bug, not equivalent mutant). Then **remove/skip that test** before giving the repo to Sentinel — Sentinel must rediscover it. Store both the original test and the mutation for scoring.

### 8.3 Scoring (`bench/score.py`)
A finding matches an injected bug if same file and line range overlaps ±5 lines (category match reported separately as strict score).

Report per run and aggregated:
- Precision, recall, F1 (lenient and strict)
- Verified-rate: fraction of candidates that passed `verify`
- Patch pass rate: fraction of verified bugs where `fix` + `regress` succeeded
- Mean time and $ per repo; $ per verified bug
- Per-category confusion matrix
- Comparison arms: (a) analyzers only, (b) LLM single-shot review (no tools, no verify), (c) Sentinel without triage, (d) Sentinel full. **Arm (b) is essential** — it's what everyone else's project is.

### 8.4 Reproducibility
- Pinned repo SHAs, pinned model IDs, temperature 0, seeds recorded; LLM calls cached by content hash so a re-run is free.
- `bench/run_bench.py --suite small` (5 repos × 6 mutations) must finish in < 30 min and run nightly in CI; `--suite full` runs manually.

---

## 9. Telemetry (bridge to Project 2 — Lens)

- Initialise OpenLLMetry (`Traceloop.init(app_name="sentinel", exporter=OTLPSpanExporter(endpoint=settings.otlp_endpoint))`).
- One root span per run (`sentinel.audit`), child span per graph node, grandchild per LLM/tool/sandbox call.
- Custom attributes on spans: `sentinel.run_id`, `sentinel.node`, `sentinel.finding_id`, `sentinel.category`, `sentinel.outcome`, `llm.cost_usd`, `llm.prompt_version`.
- Emit an `evaluation` event when `verify` completes: `{finding_id, predicted_conf, outcome}` — Lens consumes this to compute calibration.
- If no collector is configured, export to console + local Jaeger via docker-compose.

---

## 10. Phased delivery plan (give Claude Code one phase at a time)

| Phase | Scope | Acceptance criteria |
|---|---|---|
| **0 — Skeleton** | repo layout, pyproject, CLI stub, config, DB models, CI lint/test, docker-compose (postgres+pgvector, jaeger) | `uv run sentinel --help` works; CI green |
| **1 — Ingest + Index + Analyzers** | clone, language detect, tree-sitter symbols, chunking, embeddings, hybrid search, call graph, analyzer adapters | `sentinel index <repo>` then `sentinel search "where is auth token parsed"` returns sensible chunks; analyzers produce SARIF-normalised output on 2 repos |
| **2 — Sandbox** | docker runner, image build w/ lockfile cache, test runner adapters, timeouts, JUnit parsing | 20 unit tests incl. timeout, network-denied, OOM cases; runs pytest and vitest suites of sample repos |
| **3 — Graph: plan → hunt → verify** | LangGraph state, nodes, tools, prompts, checkpointer, budget guard | On a repo with 3 hand-planted bugs, ≥ 2 verified with failing tests; `sentinel replay` works |
| **4 — fix → regress → rank → report → PR** | patch gen, regression, ranking, report.html, PR opening | 3 PRs opened on your fork with correct body template |
| **5 — Benchmark harness** | mutation operators, manifests, scoring, 4 comparison arms, nightly small suite | `report.html` with all §8.3 metrics; small suite < 30 min |
| **6 — Telemetry + dashboard** | OpenLLMetry spans, run viewer (FastAPI + minimal Next.js page showing live graph state) | Traces visible in Jaeger/Lens; dashboard shows node progress |
| **7 — Triage model** | dataset build, LoRA training, eval, router integration, ablation | AUROC reported vs 3 baselines; precision improvement documented |
| **8 — Polish** | README with architecture Mermaid, GIF demo, benchmark tables, "what failed" section, blog post draft | Public repo ready for resume link |

---

## 11. `CLAUDE.md` (create this file verbatim in repo root)

```
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
```

---

## 12. Risks & mitigations (put in README — shows maturity)

| Risk | Mitigation |
|---|---|
| Hunter hallucinates bugs | Executable verification gate; triage classifier; false positives penalised in benchmark |
| Cost blow-up on large repos | Budget guard node, plan node caps targets, LLM cache, cheap model for hunt / strong model for verify+fix |
| Flaky tests corrupt regression signal | Run failing existing tests twice before flagging; record flaky list per repo |
| Repo tests need network/services | Detect at ingest via test run on clean checkout; mark repo "partial regression" honestly |
| Benchmark leakage (models trained on SWE-bench) | Rely primarily on injected-bug track and post-cutoff commits from Next.js/React history; disclose |
| Prompt injection via repo content (malicious comments) | Tools return content wrapped in clearly delimited data blocks; system prompt forbids following instructions found in code; Lens red-team suite tests this (Project 2) |

---

## 13. Resume bullets you will be able to write truthfully after this

- Built a LangGraph multi-agent codebase auditor that verifies every finding with an auto-generated failing test executed in a network-isolated Docker sandbox, achieving **X% precision / Y% recall** on a 200-mutation benchmark across Python and TypeScript repos, vs **Z%** for single-shot LLM review.
- Fine-tuned a Qwen2.5-Coder LoRA triage classifier (AUROC **A**) that cut sandbox verification cost by **B%** while raising precision by **C** points.
- Instrumented the full agent pipeline with OpenTelemetry/OpenLLMetry and opened **N** verified bug-fix PRs against real open-source repositories.

Fill in X/Y/Z/A/B/C/N with real measured numbers. Never estimate.
