# Deploying Onix

## Single host (docker-compose)

```bash
docker compose up -d           # API+dashboard :8000, Postgres+pgvector, Jaeger
```

## Kubernetes (Helm)

```bash
kubectl create secret generic onix-secrets \
  --from-literal=ONIX_OPENROUTER_API_KEY=... \
  --from-literal=ONIX_API_KEYS='[{"key":"...","name":"admin","role":"admin"}]' \
  --from-literal=ONIX_GITHUB_TOKEN=...
helm upgrade --install onix deploy/helm/onix \
  --set postgres.url=postgresql+psycopg://onix:pw@pg:5432/onix \
  --set ingress.host=onix.example.com
```

The chart runs the API (`onix serve`, N replicas, `ONIX_EXECUTION_MODE=queue`) and workers
(`onix worker`) that claim queued runs from the database. Each worker pod has a rootless
docker:dind sidecar; sandboxes run as sibling containers of that daemon and never inside the worker.
Put worker pods on a dedicated, tainted node pool. Migrations run in an init container
(`onix db upgrade`). Live SSE works from any API replica because events are persisted and tailed.

Secrets can be literal env vars or references resolved at startup: `file:///run/secrets/x`,
`vault://secret/data/onix#key`, `aws-sm://name#key`, `gcp-sm://projects/p/secrets/s`.

## Observability

- `/metrics` (Prometheus), `deploy/prometheus/alerts.yaml`, `deploy/grafana/onix-dashboard.json`.
- OpenTelemetry spans via `ONIX_OTLP_ENDPOINT`.
- Every API response carries `X-Request-ID`; JSON logs include it.

## Retention

`onix gc` (or `POST /api/v1/admin/gc`) deletes finished runs older than `ONIX_RETENTION_DAYS`
(default 30), their workspaces and events, keeping `report.*`. Stale clones are pruned at 2× the
window. The API runs one sweep at startup.
