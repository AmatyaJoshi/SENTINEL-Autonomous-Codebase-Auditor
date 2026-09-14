# Deploying Sentinel

## Single host (docker-compose)

```bash
docker compose up -d           # API+dashboard :8000, Postgres+pgvector, Jaeger
```

## Kubernetes (Helm)

```bash
kubectl create secret generic sentinel-secrets \
  --from-literal=SENTINEL_OPENROUTER_API_KEY=... \
  --from-literal=SENTINEL_API_KEYS='[{"key":"...","name":"admin","role":"admin"}]' \
  --from-literal=SENTINEL_GITHUB_TOKEN=...
helm upgrade --install sentinel deploy/helm/sentinel \
  --set postgres.url=postgresql+psycopg://sentinel:pw@pg:5432/sentinel \
  --set ingress.host=sentinel.example.com
```

The chart runs the API (`sentinel serve`, N replicas, `SENTINEL_EXECUTION_MODE=queue`) and workers
(`sentinel worker`) that claim queued runs from the database. Each worker pod has a rootless
docker:dind sidecar; sandboxes run as sibling containers of that daemon and never inside the worker.
Put worker pods on a dedicated, tainted node pool. Migrations run in an init container
(`sentinel db upgrade`). Live SSE works from any API replica because events are persisted and tailed.

Secrets can be literal env vars or references resolved at startup: `file:///run/secrets/x`,
`vault://secret/data/sentinel#key`, `aws-sm://name#key`, `gcp-sm://projects/p/secrets/s`.

## Observability

- `/metrics` (Prometheus), `deploy/prometheus/alerts.yaml`, `deploy/grafana/sentinel-dashboard.json`.
- OpenTelemetry spans via `SENTINEL_OTLP_ENDPOINT`.
- Every API response carries `X-Request-ID`; JSON logs include it.

## Retention

`sentinel gc` (or `POST /api/v1/admin/gc`) deletes finished runs older than `SENTINEL_RETENTION_DAYS`
(default 30), their workspaces and events, keeping `report.*`. Stale clones are pruned at 2× the
window. The API runs one sweep at startup.
