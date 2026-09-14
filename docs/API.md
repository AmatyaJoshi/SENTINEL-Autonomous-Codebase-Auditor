# Sentinel HTTP API (v1)

Base path: `/api/v1`. All JSON. Served by `sentinel serve` (FastAPI). The dashboard is served from `/`.

## Authentication

Header `X-API-Key: <key>` (or `?api_key=<key>` for links a browser must follow, e.g. report downloads). Keys and roles come from settings (`SENTINEL_API_KEYS`, a JSON list of
`{"key": "...", "name": "...", "role": "viewer|operator|admin"}`). When no keys are configured the
server runs in **open dev mode** and every request is `admin`, with a startup warning.

| Role | Can |
|---|---|
| viewer | read runs, findings, bench results, stats, stream events |
| operator | viewer + start/cancel runs, approve/reject findings for PR |
| admin | operator + manage settings snapshot, delete runs |

Errors: `{"error": {"code": "unauthorized|forbidden|not_found|conflict|validation|rate_limited", "message": "..."}}`

Rate limit: 120 req/min per key (429 with `Retry-After`).

## Endpoints

### `GET /health` → `{"status":"ok","version":"0.1.0","checks":{"db":"ok","docker":"ok|unavailable","llm":"configured|missing"}}`
### `GET /ready` → 200 when DB reachable, else 503.

### `GET /api/v1/stats`
```json
{"runs_total": 12, "runs_active": 1, "findings_total": 84, "verified_total": 31, "fixed_total": 22,
 "pr_opened_total": 9, "cost_usd_total": 41.2, "precision_latest": 0.81,
 "by_category": {"off_by_one": 7, "null_deref": 5}, "by_status": {"verified": 31, "refuted": 40}}
```

### `GET /api/v1/runs?limit=50&offset=0&status=running|completed|failed|cancelled`
```json
{"items": [Run], "total": 12}
```
Run:
```json
{"id": "a1b2", "repo_url": "https://github.com/org/repo", "commit_sha": "abc123", "language": "python",
 "status": "created|running|completed|failed|cancelled|awaiting_review",
 "current_node": "hunt", "progress": 0.42,
 "started_at": "2026-09-14T10:00:00Z", "finished_at": null, "cost_usd": 1.23,
 "budget": {"max_usd": 5, "max_minutes": 60, "max_findings": 25},
 "counts": {"candidate": 10, "verified": 3, "refuted": 4, "fixed": 2, "regressed": 0, "pr_opened": 1},
 "nodes": [{"name": "ingest", "status": "done|running|pending|skipped|error", "started_at": "...", "finished_at": "...", "duration_s": 3.2}]}
```
Node order: ingest, index, analyze, plan, hunt, triage, verify, fix, regress, rank, report.

### `POST /api/v1/runs` (operator)
```json
{"repo": "https://github.com/org/repo or /local/path", "sha": null, "open_pr": false, "review": false,
 "max_usd": 5.0, "max_minutes": 60, "max_findings": 25, "arm": "full|no_triage|single_shot|analyzers"}
```
→ 202 `Run`.

### `GET /api/v1/runs/{id}` → `Run`
### `POST /api/v1/runs/{id}/cancel` (operator) → `Run`
### `POST /api/v1/runs/{id}/resume?from_node=verify` (operator) → `Run`
  Resumes a checkpointed run; `from_node` rewinds to the checkpoint before that node (`sentinel replay`).
### `DELETE /api/v1/runs/{id}` (admin) → 204

### `GET /api/v1/runs/{id}/findings?status=&category=&min_confidence=`
```json
{"items": [Finding]}
```
Finding:
```json
{"id": "f1", "run_id": "a1b2", "category": "off_by_one", "severity": "high", "file": "app/auth.py",
 "line_start": 41, "line_end": 44, "symbol": "first_n", "description": "...", "hypothesis": "...",
 "evidence": ["ruff SIM115 ..."], "confidence": 0.78, "status": "verified",
 "test_path": "tests/test_sentinel_f1.py", "test_code": "...", "patch_diff": "--- a/...",
 "verify_log": "...", "regress_log": "...", "pr_url": null, "explanation": "one paragraph",
 "blast_radius": 4, "cost_usd": 0.31, "created_at": "..."}
```

### `GET /api/v1/runs/{id}/findings/{fid}` → `Finding`
### `POST /api/v1/runs/{id}/findings/{fid}/decision` (operator) `{"decision": "approve|reject", "note": ""}` → `Finding`
  Used when the run is `awaiting_review`; approving all pending resumes the run to `report`.

### `GET /api/v1/runs/{id}/events` — **Server-Sent Events**
Each event: `event: <type>` and `data: <json>`. Types:
```
run.status     {"status": "running", "current_node": "hunt", "progress": 0.3}
node.start     {"node": "hunt", "at": "..."}
node.end       {"node": "hunt", "at": "...", "duration_s": 12.1, "ok": true}
finding.new    Finding
finding.update Finding
llm.call       {"node": "hunt", "model": "...", "tokens_in": 1200, "tokens_out": 300, "cost_usd": 0.02, "duration_s": 3.1}
sandbox.exec   {"node": "verify", "command": "pytest ...", "exit_code": 1, "duration_s": 4.2, "timed_out": false}
log            {"level": "info", "message": "..."}
run.end        Run
```
Reconnect with `Last-Event-ID` to replay from a sequence number. A heartbeat comment `: ping` is sent every 15 s.

### `GET /api/v1/runs/{id}/report.html` / `report.json` / `report.md`

### `GET /api/v1/bench/results?suite=small|full&limit=20`
```json
{"items": [{"id": "...", "created_at": "...", "commit": "abc", "suite": "small", "arm": "full",
  "precision": 0.8, "precision_strict": 0.7, "recall": 0.6, "f1": 0.69, "verified_rate": 0.5,
  "patch_pass_rate": 0.7, "cost_usd": 3.2, "wall_clock_s": 900,
  "per_category": {"off_by_one": {"tp": 4, "fp": 1, "fn": 2}}, "arms": {"analyzers": {...}, "single_shot": {...}}}]}
```

### `GET /api/v1/settings` (admin) → redacted settings snapshot.
### `GET|POST /api/v1/keys`, `POST /api/v1/keys/{id}/rotate`, `DELETE /api/v1/keys/{id}` (admin) — database-managed API keys.
### `POST /api/v1/admin/gc?older_than_days=&dry_run=` (admin) — retention sweep.
### `GET /api/v1/audit-log?limit=` (admin) — who did what.
### `GET /metrics` — Prometheus exposition (unauthenticated; restrict at the ingress).
Bearer tokens: `Authorization: Bearer <OIDC JWT>` is accepted when `SENTINEL_OIDC_ISSUER` is set.
Run status also includes `queued` when `SENTINEL_EXECUTION_MODE=queue`.
### `GET /api/v1/me` → `{"name": "ci-bot", "role": "operator"}`
