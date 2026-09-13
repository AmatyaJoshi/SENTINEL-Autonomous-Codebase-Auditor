"""Settings: environment (.env, SENTINEL_*) plus optional per-repo sentinel.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseModel):
    """Sandbox limits. These are security-critical; see SPEC.md §5. Never relax by default."""

    network: Literal["none"] = "none"
    memory: str = "2g"
    cpus: float = 2.0
    pids_limit: int = 256
    snippet_timeout_s: int = 30
    test_timeout_s: int = 120
    suite_timeout_s: int = 900
    output_cap_bytes: int = 20 * 1024


class BudgetSettings(BaseModel):
    max_usd: float = 5.0
    max_minutes: int = 60
    max_findings: int = 25


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # storage
    database_url: str = "sqlite:///sentinel.db"
    work_dir: Path = Path(".sentinel")

    # llm
    primary_model: str = "anthropic/claude-sonnet-5"
    fallback_model: str = "openai/gpt-5"
    triage_model: str = "sentinel-triage"
    embedding_model: str = "text-embedding-3-large"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_cache_enabled: bool = True

    # github
    github_token: SecretStr | None = None

    # telemetry
    otlp_endpoint: str | None = None
    telemetry_console: bool = False

    # graph
    hunt_concurrency: int = 4
    hunt_max_tool_calls: int = 12
    triage_threshold: float = 0.35
    max_patch_lines: int = 60

    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class RepoConfig(BaseModel):
    """Per-repo overrides read from `sentinel.yaml` at the repo root."""

    language: Literal["python", "typescript", "mixed"] | None = None
    test_command: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    budget: BudgetSettings | None = None

    @classmethod
    def load(cls, repo_path: Path) -> RepoConfig:
        path = repo_path / "sentinel.yaml"
        if not path.exists():
            return cls()
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)


def get_settings() -> Settings:
    return Settings()
