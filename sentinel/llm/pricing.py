"""Cost accounting for models LiteLLM cannot price (item 14).

`SENTINEL_MODEL_PRICES` is a JSON object: {"openrouter/x:free": {"input_per_m": 0, "output_per_m": 0},
"my-vllm/qwen": {"input_per_m": 0.2, "output_per_m": 0.6}}. Prices are USD per million tokens.
Token counts fall back to a chars/4 estimate when the provider returns no usage.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def estimate_tokens(messages: Sequence[dict[str, Any]] | str) -> int:
    if isinstance(messages, str):
        return max(1, len(messages) // 4)
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(json.dumps(part)) for part in c)
        for tc in m.get("tool_calls") or []:
            total += len(json.dumps(tc))
    return max(1, total // 4)


class PriceTable:
    def __init__(self, raw: dict[str, Any] | str | None) -> None:
        data: dict[str, Any] = (
            (json.loads(raw) if raw.strip() else {}) if isinstance(raw, str) else (raw or {})
        )
        self.prices: dict[str, tuple[float, float]] = {
            k: (float(v.get("input_per_m", 0.0)), float(v.get("output_per_m", 0.0)))
            for k, v in data.items()
        }

    def lookup(self, model: str) -> tuple[float, float] | None:
        if model in self.prices:
            return self.prices[model]
        # allow prefix matches, e.g. "openrouter/" for everything routed through OpenRouter
        for k, v in sorted(self.prices.items(), key=lambda kv: -len(kv[0])):
            if k.endswith("/") and model.startswith(k):
                return v
        return None

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float | None:
        p = self.lookup(model)
        if p is None:
            return None
        return tokens_in / 1e6 * p[0] + tokens_out / 1e6 * p[1]
