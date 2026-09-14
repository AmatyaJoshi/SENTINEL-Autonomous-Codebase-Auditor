"""Model rotation pools: comma-separated model lists with failover on errors/rate limits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sentinel.config import Settings
from sentinel.llm.router import LLMRouter, RawResponse, ScriptedBackend


class Out(BaseModel):
    n: int


def test_model_pool_rotates_on_failure(tmp_path: Path) -> None:
    calls: list[str] = []

    class Flaky(ScriptedBackend):
        def complete(  # type: ignore[override]
            self,
            model: str,
            messages: Any,
            *,
            response_schema: Any,
            tools: Any,
            temperature: float,
            max_tokens: int,
        ) -> RawResponse:
            calls.append(model)
            if model == "openrouter/a:free":
                raise RuntimeError("429 rate limited")
            return RawResponse(content='{"n": 2}')

    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="sqlite://",
        work_dir=tmp_path,
        openrouter_api_key="k",  # type: ignore[arg-type]
        llm_backoff_s=0.0,
        primary_model="openrouter/a:free, openrouter/b:free",
        cheap_model="openrouter/c:free",
    )
    assert s.model_pool("primary") == ["openrouter/a:free", "openrouter/b:free"]
    router = LLMRouter(s, backend=Flaky(lambda *_: RawResponse(content="")))
    out, _ = router.structured(Out, "x")
    assert out.n == 2 and calls == ["openrouter/a:free", "openrouter/b:free"]
    router.structured(Out, "y")  # the model that worked is now tried first
    assert calls[-1] == "openrouter/b:free" and len(calls) == 3


def test_pool_drops_keyless_providers(tmp_path: Path) -> None:
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        work_dir=tmp_path,
        openrouter_api_key="k",  # type: ignore[arg-type]
        primary_model="anthropic/claude-sonnet-5,openrouter/x:free",
    )
    assert s.model_pool("primary") == ["openrouter/x:free"]
    s2 = Settings(_env_file=None, work_dir=tmp_path)  # type: ignore[call-arg]
    assert s2.model_pool("primary") == [
        "anthropic/claude-sonnet-5"
    ]  # nothing usable: keep configured
