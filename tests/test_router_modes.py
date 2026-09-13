"""Structured-output modes: schema-enforced for Anthropic/OpenAI, prompt-embedded for OpenRouter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sentinel.config import Settings
from sentinel.llm.router import LLMRouter, RawResponse, ScriptedBackend


class Out(BaseModel):
    n: int


def _router(
    tmp_path: Path, model: str, mode: str = "auto"
) -> tuple[LLMRouter, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []

    class Spy(ScriptedBackend):
        def complete(
            self,
            model: str,
            messages: Any,
            *,
            response_schema: Any,
            tools: Any,
            temperature: float,
            max_tokens: int,
        ) -> RawResponse:  # type: ignore[override]
            seen.append({"schema": response_schema, "last": messages[-1]["content"]})
            return RawResponse(content='{"n": 1}')

    s = Settings(
        _env_file=None,
        database_url="sqlite://",
        work_dir=tmp_path,
        primary_model=model,  # type: ignore[call-arg]
        structured_output_mode=mode,
    )  # type: ignore[arg-type]
    return LLMRouter(s, backend=Spy(lambda *_: RawResponse(content=""))), seen


def test_openrouter_uses_prompt_embedded_schema(tmp_path: Path) -> None:
    router, seen = _router(tmp_path, "openrouter/qwen/qwen3-coder:free")
    out, _ = router.structured(Out, "give n")
    assert out.n == 1
    assert (
        seen[0]["schema"] is None
        and '"properties"' in seen[0]["last"]
        and "ONLY a JSON object" in seen[0]["last"]
    )


def test_anthropic_uses_provider_schema(tmp_path: Path) -> None:
    router, seen = _router(tmp_path, "anthropic/claude-sonnet-5")
    router.structured(Out, "give n")
    assert seen[0]["schema"] is Out and "JSON Schema" not in seen[0]["last"]


def test_mode_override(tmp_path: Path) -> None:
    router, seen = _router(tmp_path, "anthropic/claude-sonnet-5", mode="prompt")
    router.structured(Out, "give n")
    assert seen[0]["schema"] is None


def test_effective_model_falls_back_to_openrouter_when_only_that_key_exists(tmp_path: Path) -> None:
    s = Settings(_env_file=None, work_dir=tmp_path, openrouter_api_key="k")  # type: ignore[call-arg,arg-type]
    assert s.effective_model("primary").startswith("openrouter/")
    assert s.effective_model("fallback") == "openrouter/free"
    assert (
        not s.has_key_for("openai/gpt-5")
        and s.has_key_for("openrouter/free")
        and s.has_key_for("ollama/x")
    )
    s2 = Settings(_env_file=None, work_dir=tmp_path, anthropic_api_key="k")  # type: ignore[call-arg,arg-type]
    assert s2.effective_model("primary") == "anthropic/claude-sonnet-5"
    assert (
        s2.effective_model("fallback") == "openai/gpt-5"
    )  # configured but keyless → router disables it
