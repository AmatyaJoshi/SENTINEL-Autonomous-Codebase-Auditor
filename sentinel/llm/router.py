"""LLM access: LiteLLM behind a small backend protocol, with retries, cost tracking, a content-hash
cache (SPEC §8.4: re-runs are free) and a cassette backend for deterministic tests.

Every call goes through `LLMRouter.structured()` (schema-validated JSON) or `LLMRouter.chat()`
(tool-calling turn). Nothing else in the codebase talks to a provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from sentinel.config import Settings
from sentinel.llm.schemas import Tier
from sentinel.telemetry.otel import span

T = TypeVar("T", bound=BaseModel)

Message = dict[str, Any]


@dataclass
class RawResponse:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "finish_reason": self.finish_reason,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> RawResponse:
        return cls(
            content=d.get("content"),
            tool_calls=list(d.get("tool_calls") or []),
            model=str(d.get("model") or ""),
            tokens_in=int(d.get("tokens_in") or 0),
            tokens_out=int(d.get("tokens_out") or 0),
            cost_usd=float(d.get("cost_usd") or 0.0),
            finish_reason=d.get("finish_reason"),
        )


@dataclass
class CallStats:
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_s: float
    cached: bool
    prompt_name: str | None
    prompt_version: str | None


class Backend(Protocol):
    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        response_schema: type[BaseModel] | None,
        tools: Sequence[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> RawResponse: ...


# --------------------------------------------------------------------------- backends


class LiteLLMBackend:
    def __init__(self, settings: Settings) -> None:
        import os

        if settings.anthropic_api_key:
            os.environ.setdefault(
                "ANTHROPIC_API_KEY", settings.anthropic_api_key.get_secret_value()
            )
        if settings.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
        if settings.deepseek_api_key:
            os.environ.setdefault("DEEPSEEK_API_KEY", settings.deepseek_api_key.get_secret_value())
        if settings.openrouter_api_key:
            os.environ.setdefault(
                "OPENROUTER_API_KEY", settings.openrouter_api_key.get_secret_value()
            )
        fb = settings.effective_model("fallback")
        self._fallback = fb if settings.has_key_for(fb) else None

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        response_schema: type[BaseModel] | None,
        tools: Sequence[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> RawResponse:
        import litellm

        litellm.suppress_debug_info = True
        litellm.drop_params = True  # e.g. reasoning models reject temperature=0
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "num_retries": 2,
            "fallbacks": [self._fallback] if self._fallback and self._fallback != model else None,
        }
        if response_schema is not None:
            kwargs["response_format"] = response_schema
        if tools:
            kwargs["tools"] = list(tools)
            kwargs["tool_choice"] = "auto"
        resp = litellm.completion(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (msg.tool_calls or [])
        ]
        usage = getattr(resp, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=resp))
        except Exception:  # noqa: BLE001 - unknown models have no price table entry
            cost = 0.0
        return RawResponse(
            content=msg.content,
            tool_calls=tool_calls,
            model=getattr(resp, "model", model) or model,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=cost,
            finish_reason=choice.finish_reason,
        )


class CassetteBackend:
    """Replays recorded responses keyed by `<prompt_name>#<n>` (n-th call for that prompt).

    Record mode (`record=True` with an inner backend) writes new entries. Test cassettes are
    hand-authorable JSON: {"hunt_target#0": {"content": "...json..."}, ...}.
    """

    def __init__(self, path: Path, inner: Backend | None = None, record: bool = False) -> None:
        self.path = path
        self.inner = inner
        self.record = record
        self._data: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self.current_prompt: str = "unknown"

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        response_schema: type[BaseModel] | None,
        tools: Sequence[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> RawResponse:
        with self._lock:
            n = self._counts.get(self.current_prompt, 0)
            self._counts[self.current_prompt] = n + 1
            key = f"{self.current_prompt}#{n}"
        if key in self._data:
            return RawResponse.from_json(self._data[key])
        if self.inner is None or not self.record:
            raise LookupError(
                f"cassette {self.path.name} has no entry {key!r} and recording is disabled"
            )
        resp = self.inner.complete(
            model,
            messages,
            response_schema=response_schema,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        with self._lock:
            self._data[key] = resp.to_json()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        return resp


class ScriptedBackend:
    """Test double: a function decides the response from (prompt_name, messages, tools)."""

    def __init__(
        self, fn: Callable[[str, Sequence[Message], Sequence[dict[str, Any]] | None], RawResponse]
    ) -> None:
        self.fn = fn
        self.current_prompt = "unknown"
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        response_schema: type[BaseModel] | None,
        tools: Sequence[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> RawResponse:
        self.calls.append((self.current_prompt, list(messages)))
        return self.fn(self.current_prompt, messages, tools)


# --------------------------------------------------------------------------- cache


class _ResponseCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT NOT NULL, "
            "created REAL NOT NULL)"
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> RawResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT response FROM llm_cache WHERE key=?", (key,)
            ).fetchone()
        return RawResponse.from_json(json.loads(row[0])) if row else None

    def put(self, key: str, resp: RawResponse) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)",
                (key, json.dumps(resp.to_json()), time.time()),
            )
            self._conn.commit()


# --------------------------------------------------------------------------- router


class LLMRouter:
    def __init__(
        self,
        settings: Settings,
        backend: Backend | None = None,
        on_call: Callable[[CallStats], None] | None = None,
    ) -> None:
        self.settings = settings
        self.backend: Backend = backend or LiteLLMBackend(settings)
        self.on_call = on_call
        self.total_cost_usd = 0.0
        self.total_calls = 0
        self._warned_substitution = False
        self._pool_offset: dict[str, int] = {}
        self._cache = (
            _ResponseCache(settings.work_dir / "cache" / "llm.sqlite")
            if settings.llm_cache_enabled and backend is None
            else None
        )

    def model_for(self, tier: Tier) -> str:
        return self.model_pool(tier)[0]

    def model_pool(self, tier: Tier) -> list[str]:
        """Rotation order for a tier: the last model that worked comes first."""
        key = "cheap" if tier == "cheap" else "primary"
        pool = self.settings.model_pool(key)
        configured = self.settings.cheap_model if tier == "cheap" else self.settings.primary_model
        if pool[0] not in configured and not self._warned_substitution:
            self._warned_substitution = True
            logging.getLogger("sentinel.llm").warning(
                "no API key for %s; using %s instead (set SENTINEL_PRIMARY_MODEL/SENTINEL_CHEAP_MODEL)",
                configured,
                pool[0],
            )
        off = self._pool_offset.get(key, 0) % len(pool)
        return pool[off:] + pool[:off]

    def _rotate(self, tier: Tier, failed_model: str, reason: str) -> None:
        key = "cheap" if tier == "cheap" else "primary"
        pool = self.settings.model_pool(key)
        if len(pool) < 2:
            return
        nxt = (pool.index(failed_model) + 1) % len(pool) if failed_model in pool else 0
        self._pool_offset[key] = nxt
        logging.getLogger("sentinel.llm").warning(
            "model %s failed (%s); rotating to %s", failed_model, reason[:120], pool[nxt]
        )

    # ------------------------------------------------------------------ core call
    def _call(
        self,
        messages: Sequence[Message],
        *,
        tier: Tier,
        response_schema: type[BaseModel] | None,
        tools: Sequence[dict[str, Any]] | None,
        prompt_name: str | None,
        prompt_version: str | None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> RawResponse:
        pool = self.model_pool(tier)
        model = pool[0]
        key = hashlib.sha256(
            json.dumps(
                {
                    "model": model,
                    "messages": list(messages),
                    "schema": response_schema.__name__ if response_schema else None,
                    "tools": [t.get("function", {}).get("name") for t in (tools or [])],
                    "temperature": temperature,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        t0 = time.perf_counter()
        cached = False
        resp = self._cache.get(key) if (self._cache and temperature == 0.0) else None
        if resp is not None:
            cached = True
        else:
            if hasattr(self.backend, "current_prompt"):
                self.backend.current_prompt = prompt_name or "unknown"
            with span("sentinel.llm", node=prompt_name) as s:
                resp = None
                last_exc: Exception | None = None
                for candidate in pool:
                    try:
                        resp = self.backend.complete(
                            candidate,
                            messages,
                            response_schema=response_schema,
                            tools=tools,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        model = candidate
                        break
                    except Exception as e:  # noqa: BLE001 - rate limits, outages, bad params
                        last_exc = e
                        self._rotate(tier, candidate, f"{type(e).__name__}: {e}")
                if resp is None:
                    assert last_exc is not None
                    raise last_exc  # keep the original type (LookupError, RateLimitError, ...)
                s.set_attribute("llm.model", resp.model or model)
                s.set_attribute("llm.cost_usd", resp.cost_usd)
                s.set_attribute("llm.tokens_in", resp.tokens_in)
                s.set_attribute("llm.tokens_out", resp.tokens_out)
                if prompt_version:
                    s.set_attribute("llm.prompt_version", prompt_version)
            if self._cache and temperature == 0.0:
                self._cache.put(key, resp)
        duration = time.perf_counter() - t0
        if not cached:
            self.total_cost_usd += resp.cost_usd
        self.total_calls += 1
        if self.on_call:
            self.on_call(
                CallStats(
                    model=resp.model or model,
                    tokens_in=resp.tokens_in,
                    tokens_out=resp.tokens_out,
                    cost_usd=0.0 if cached else resp.cost_usd,
                    duration_s=duration,
                    cached=cached,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                )
            )
        return resp

    # ------------------------------------------------------------------ public API
    def structured(
        self,
        schema: type[T],
        user: str,
        *,
        system: str | None = None,
        tier: Tier = "strong",
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        history: Sequence[Message] | None = None,
        max_attempts: int = 2,
    ) -> tuple[T, RawResponse]:
        """Ask for JSON matching `schema`; on validation failure, feed the error back once."""
        prompt_mode = self._prompt_mode(self.model_for(tier))
        messages: list[Message] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history or [])
        if prompt_mode:
            user = (
                f"{user}\n\nRespond with ONLY a JSON object (no prose, no code fences) that validates "
                f"against this JSON Schema:\n{json.dumps(schema.model_json_schema())}"
            )
        messages.append({"role": "user", "content": user})
        last_err: Exception | None = None
        resp: RawResponse | None = None
        for _ in range(max_attempts):
            resp = self._call(
                messages,
                tier=tier,
                response_schema=None if prompt_mode else schema,
                tools=None,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
            )
            try:
                return schema.model_validate_json(_extract_json(resp.content or "")), resp
            except (ValidationError, ValueError) as e:
                last_err = e
                messages.append({"role": "assistant", "content": resp.content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your JSON did not validate against {schema.__name__}: "
                        f"{str(e)[:800]}\nReturn only corrected JSON.",
                    }
                )
        raise ValueError(f"LLM output failed {schema.__name__} validation: {last_err}")

    def _prompt_mode(self, model: str) -> bool:
        mode = self.settings.structured_output_mode
        if mode == "auto":
            return model.startswith(("openrouter/", "ollama/", "ollama_chat/", "huggingface/"))
        return mode == "prompt"

    def chat(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] | None,
        tier: Tier = "strong",
        prompt_name: str | None = None,
        prompt_version: str | None = None,
    ) -> RawResponse:
        return self._call(
            messages,
            tier=tier,
            response_schema=None,
            tools=tools,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )


def _extract_json(text: str) -> str:
    """Tolerate ```json fences around otherwise valid JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
