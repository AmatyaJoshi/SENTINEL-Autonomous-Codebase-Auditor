# Operating Onix

## Execution modes

| `ONIX_EXECUTION_MODE` | Behaviour |
|---|---|
| `inline` (default) | The API process executes runs in threads. Fine for one replica and local use. |
| `queue` | The API only enqueues. `onix worker` processes claim runs from the database (lease + heartbeat; a dead worker's run is reclaimed after 10 min). Scale by running more workers. Live SSE works from any API replica because events are persisted and tailed. |

## Authentication

Order of resolution: `Authorization: Bearer <OIDC JWT>` → `X-API-Key` (database key) → `X-API-Key`
(static key from `ONIX_API_KEYS`) → open dev mode when neither keys nor OIDC are configured.

- **Database keys**: `onix keys create <name> --role operator --expires-days 90`, `onix keys list`,
  `onix keys revoke <id>`, or `POST/GET/DELETE /api/v1/keys`, `POST /api/v1/keys/{id}/rotate`
  (old key valid for one hour). Keys are stored hashed; the raw key is shown once.
- **OIDC / SSO**: set `ONIX_OIDC_ISSUER`, `ONIX_OIDC_AUDIENCE` (and `ONIX_OIDC_JWKS_URL` if
  discovery is non-standard). Roles come from the claim named by `ONIX_OIDC_ROLE_CLAIM` (default
  `groups`) mapped through `ONIX_OIDC_ROLE_MAP` (JSON, e.g. `{"eng-leads":"admin"}`); unmapped users
  get `ONIX_OIDC_DEFAULT_ROLE` (viewer).
- Every mutating call is written to `audit_log` (`GET /api/v1/audit-log`).

## Rate limiting

`ONIX_API_RATE_LIMIT_PER_MINUTE` (120) applies per credential (or per client IP in open mode).
`ONIX_API_RATE_LIMIT_BACKEND`: `memory` is a per-process sliding window; `db` keeps fixed
one-minute counters in `rate_limit_buckets` so every API replica sharing the database enforces one
combined budget (credentials are stored as SHA-256). `auto` (default) picks `db` in queue mode and
`memory` otherwise. `/health` reports the active backend under `checks.rate_limit`. An ingress
limiter in front is still recommended as the first line of defence against unauthenticated floods.

## Secrets

Any `ONIX_*` value may be a reference resolved at startup: `file:///run/secrets/x`,
`env://OTHER_VAR`, `vault://secret/data/onix#key` (hvac, `VAULT_ADDR`/`VAULT_TOKEN`),
`aws-sm://name#key` (boto3), `gcp-sm://projects/p/secrets/s` (google-cloud-secret-manager).
Install providers with `uv sync --extra secrets`.

## Database migrations

Schema is managed by Alembic. `onix db upgrade` applies migrations (a database created by an older
`create_all` build is stamped at head first). `onix db current`, `onix db downgrade <rev>`,
`onix db revision -m "..."` (autogenerate, developers only). The API also runs `init_db` on
startup, which applies pending migrations.

## Retention

`ONIX_RETENTION_DAYS` (30). `onix gc [--dry-run] [--older-than-days N] [--drop-reports]` or
`POST /api/v1/admin/gc`. Deletes finished runs, findings, events, workspaces and sandbox build
contexts older than the window (reports kept), plus clones untouched for twice the window. One sweep
runs at API startup (`ONIX_GC_ON_STARTUP`).

## Observability

- `GET /metrics` (Prometheus): runs, node durations, findings by status/category, LLM calls/cost/tokens/failovers, sandbox executions, HTTP latency, queue depth.
- `deploy/prometheus/alerts.yaml`: API down, 5xx rate, failing runs, queue backlog, LLM failover storm, sandbox timeouts, spend.
- `deploy/grafana/onix-dashboard.json`: import into Grafana.
- OpenTelemetry spans via `ONIX_OTLP_ENDPOINT` (Jaeger in docker-compose). Every API response carries `X-Request-ID`, echoed in JSON logs (`ONIX_LOG_JSON=1`).

## LLM cost and models

Model settings accept comma-separated rotation lists; the router rotates on any provider error and makes
up to three passes with backoff (`ONIX_LLM_BACKOFF_S`). Providers with no key are dropped from the
pool. For models LiteLLM cannot price, set `ONIX_MODEL_PRICES='{"openrouter/": {"input_per_m": 0,
"output_per_m": 0}, "vllm/my-model": {"input_per_m": 0.2, "output_per_m": 0.6}}'`; token counts fall
back to a chars/4 estimate when the provider omits usage.

## Kubernetes

See `deploy/README.md` and `deploy/helm/onix`. Workers run a rootless docker:dind sidecar for the
sandbox; pin them to a dedicated node pool.
