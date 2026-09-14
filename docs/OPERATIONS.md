# Operating Sentinel

## Execution modes

| `SENTINEL_EXECUTION_MODE` | Behaviour |
|---|---|
| `inline` (default) | The API process executes runs in threads. Fine for one replica and local use. |
| `queue` | The API only enqueues. `sentinel worker` processes claim runs from the database (lease + heartbeat; a dead worker's run is reclaimed after 10 min). Scale by running more workers. Live SSE works from any API replica because events are persisted and tailed. |

## Authentication

Order of resolution: `Authorization: Bearer <OIDC JWT>` → `X-API-Key` (database key) → `X-API-Key`
(static key from `SENTINEL_API_KEYS`) → open dev mode when neither keys nor OIDC are configured.

- **Database keys**: `sentinel keys create <name> --role operator --expires-days 90`, `sentinel keys list`,
  `sentinel keys revoke <id>`, or `POST/GET/DELETE /api/v1/keys`, `POST /api/v1/keys/{id}/rotate`
  (old key valid for one hour). Keys are stored hashed; the raw key is shown once.
- **OIDC / SSO**: set `SENTINEL_OIDC_ISSUER`, `SENTINEL_OIDC_AUDIENCE` (and `SENTINEL_OIDC_JWKS_URL` if
  discovery is non-standard). Roles come from the claim named by `SENTINEL_OIDC_ROLE_CLAIM` (default
  `groups`) mapped through `SENTINEL_OIDC_ROLE_MAP` (JSON, e.g. `{"eng-leads":"admin"}`); unmapped users
  get `SENTINEL_OIDC_DEFAULT_ROLE` (viewer).
- Every mutating call is written to `audit_log` (`GET /api/v1/audit-log`).

## Secrets

Any `SENTINEL_*` value may be a reference resolved at startup: `file:///run/secrets/x`,
`env://OTHER_VAR`, `vault://secret/data/sentinel#key` (hvac, `VAULT_ADDR`/`VAULT_TOKEN`),
`aws-sm://name#key` (boto3), `gcp-sm://projects/p/secrets/s` (google-cloud-secret-manager).
Install providers with `uv sync --extra secrets`.

## Database migrations

Schema is managed by Alembic. `sentinel db upgrade` applies migrations (a database created by an older
`create_all` build is stamped at head first). `sentinel db current`, `sentinel db downgrade <rev>`,
`sentinel db revision -m "..."` (autogenerate, developers only). The API also runs `init_db` on
startup, which applies pending migrations.

## Retention

`SENTINEL_RETENTION_DAYS` (30). `sentinel gc [--dry-run] [--older-than-days N] [--drop-reports]` or
`POST /api/v1/admin/gc`. Deletes finished runs, findings, events, workspaces and sandbox build
contexts older than the window (reports kept), plus clones untouched for twice the window. One sweep
runs at API startup (`SENTINEL_GC_ON_STARTUP`).

## Observability

- `GET /metrics` (Prometheus): runs, node durations, findings by status/category, LLM calls/cost/tokens/failovers, sandbox executions, HTTP latency, queue depth.
- `deploy/prometheus/alerts.yaml`: API down, 5xx rate, failing runs, queue backlog, LLM failover storm, sandbox timeouts, spend.
- `deploy/grafana/sentinel-dashboard.json`: import into Grafana.
- OpenTelemetry spans via `SENTINEL_OTLP_ENDPOINT` (Jaeger in docker-compose). Every API response carries `X-Request-ID`, echoed in JSON logs (`SENTINEL_LOG_JSON=1`).

## LLM cost and models

Model settings accept comma-separated rotation lists; the router rotates on any provider error and makes
up to three passes with backoff (`SENTINEL_LLM_BACKOFF_S`). Providers with no key are dropped from the
pool. For models LiteLLM cannot price, set `SENTINEL_MODEL_PRICES='{"openrouter/": {"input_per_m": 0,
"output_per_m": 0}, "vllm/my-model": {"input_per_m": 0.2, "output_per_m": 0.6}}'`; token counts fall
back to a chars/4 estimate when the provider omits usage.

## Kubernetes

See `deploy/README.md` and `deploy/helm/sentinel`. Workers run a rootless docker:dind sidecar for the
sandbox; pin them to a dedicated node pool.
