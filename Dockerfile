# Sentinel control plane image: API + dashboard + CLI.
# The sandbox itself runs in sibling containers via the host Docker socket (mounted read-only in
# docker-compose); this image never executes audited code.

# ---- stage 1: dashboard ---------------------------------------------------------------------
FROM node:20-alpine AS dashboard
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---- stage 2: python deps -------------------------------------------------------------------
FROM python:3.14-slim AS deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY sentinel/__init__.py sentinel/__init__.py
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

# ---- stage 3: runtime -----------------------------------------------------------------------
FROM python:3.14-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SENTINEL_WORK_DIR=/data SENTINEL_API_HOST=0.0.0.0 SENTINEL_LOG_JSON=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10002 sentinel && useradd -m -u 10002 -g sentinel sentinel
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY --chown=sentinel:sentinel sentinel/ sentinel/
COPY --chown=sentinel:sentinel bench/ bench/
COPY --chown=sentinel:sentinel training/ training/
COPY --chown=sentinel:sentinel pyproject.toml README.md SPEC.md ./
COPY --from=dashboard --chown=sentinel:sentinel /app/dashboard/dist dashboard/dist
RUN /app/.venv/bin/python -c "import sentinel" && mkdir -p /data && chown sentinel:sentinel /data
USER sentinel
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1
ENTRYPOINT ["python", "-m", "sentinel.cli"]
CMD ["serve"]
