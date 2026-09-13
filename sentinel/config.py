"""Settings: environment (.env, SENTINEL_*) plus optional per-repo sentinel.yaml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["viewer", "operator", "admin"]


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


class ApiKey(BaseModel):
    key: SecretStr
    name: str
    role: Role = "viewer"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # deployment
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # storage
    database_url: str = "sqlite:///sentinel.db"
    work_dir: Path = Path(".sentinel")

    # llm
    primary_model: str = "anthropic/claude-sonnet-5"
    cheap_model: str = "anthropic/claude-haiku-4-5-20251001"
    fallback_model: str = "openai/gpt-5"
    triage_model: str = "sentinel-triage"
    triage_endpoint: str | None = None
    embedding_model: str = "text-embedding-3-large"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None
    llm_cache_enabled: bool = True

    # github
    github_token: SecretStr | None = None
    pr_grouped: bool = False

    # telemetry
    otlp_endpoint: str | None = None
    telemetry_console: bool = False

    # graph
    hunt_concurrency: int = 4
    hunt_max_tool_calls: int = 12
    plan_max_targets: int = 8
    triage_threshold: float = 0.35
    max_patch_lines: int = 60
    analyzers_semgrep: bool = True

    # api
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_keys: list[ApiKey] = Field(default_factory=list)
    api_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    api_rate_limit_per_minute: int = 120
    max_concurrent_runs: int = 2

    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_keys(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v else []
        return v

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def llm_configured(self) -> bool:
        return any(
            k is not None
            for k in (self.anthropic_api_key, self.openai_api_key, self.deepseek_api_key)
        )

    def redacted(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        for k in (
            "anthropic_api_key",
            "openai_api_key",
            "deepseek_api_key",
            "voyage_api_key",
            "github_token",
        ):
            d[k] = "***" if getattr(self, k) is not None else None
        d["api_keys"] = [{"name": k.name, "role": k.role, "key": "***"} for k in self.api_keys]
        d["database_url"] = (
            self.database_url.split("@")[-1] if "@" in self.database_url else self.database_url
        )
        return d


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
