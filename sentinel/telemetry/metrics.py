"""Prometheus metrics (item 13). Exposed at /metrics by the API; updated by the runner and router."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

RUNS_STARTED = Counter(
    "sentinel_runs_started_total", "Audit runs started", ["arm"], registry=registry
)
RUNS_FINISHED = Counter(
    "sentinel_runs_finished_total", "Audit runs finished", ["status"], registry=registry
)
RUNS_ACTIVE = Gauge("sentinel_runs_active", "Audit runs currently executing", registry=registry)
NODE_DURATION = Histogram(
    "sentinel_node_duration_seconds",
    "Graph node duration",
    ["node"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
    registry=registry,
)
FINDINGS = Counter(
    "sentinel_findings_total", "Findings by final status", ["status", "category"], registry=registry
)
LLM_CALLS = Counter(
    "sentinel_llm_calls_total", "LLM calls", ["model", "prompt", "cached"], registry=registry
)
LLM_COST = Counter("sentinel_llm_cost_usd_total", "LLM spend in USD", ["model"], registry=registry)
LLM_TOKENS = Counter(
    "sentinel_llm_tokens_total", "LLM tokens", ["model", "direction"], registry=registry
)
LLM_FAILOVERS = Counter(
    "sentinel_llm_failovers_total",
    "Model rotations after a provider error",
    ["from_model"],
    registry=registry,
)
SANDBOX_EXECS = Counter(
    "sentinel_sandbox_execs_total", "Sandbox executions", ["node", "outcome"], registry=registry
)
SANDBOX_DURATION = Histogram(
    "sentinel_sandbox_duration_seconds",
    "Sandbox execution duration",
    ["node"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 900),
    registry=registry,
)
HTTP_REQUESTS = Counter(
    "sentinel_http_requests_total", "API requests", ["method", "route", "status"], registry=registry
)
HTTP_LATENCY = Histogram(
    "sentinel_http_request_seconds",
    "API request latency",
    ["method", "route"],
    buckets=(0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=registry,
)
QUEUE_DEPTH = Gauge("sentinel_queue_depth", "Runs waiting for a worker", registry=registry)


def render() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
