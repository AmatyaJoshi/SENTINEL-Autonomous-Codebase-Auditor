"""Structured logging: JSON lines in prod (`SENTINEL_LOG_JSON=1`), rich-ish text in dev."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from sentinel.config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for k in ("run_id", "node", "finding_id", "actor"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "docker", "git"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # LiteLLM's async logging worker is chatty when called from threads; Sentinel records its own
    # per-call metrics/spans, so only real errors from the provider layer are useful.
    for noisy in ("LiteLLM", "litellm", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
