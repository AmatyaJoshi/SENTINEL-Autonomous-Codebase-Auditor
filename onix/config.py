"""Settings: environment (.env, ONIX_*) plus optional per-repo onix.yaml."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Role = Literal["viewer", "operator", "admin"]
log = logging.getLogger("onix.config")

# ---------------------------------------------------------------------------- legacy names
# Onix was called Sentinel until 0.2.0. `SENTINEL_*` variables and the `.sentinel/` work dir are
# still honoured, with a warning, through the 0.2.x line; both shims go away in 0.3.0.
ENV_PREFIX = "ONIX_"
LEGACY_ENV_PREFIX = "SENTINEL_"
LEGACY_WORK_DIR_NAME = ".sentinel"
_legacy_env_warned: set[str] = set()


def apply_legacy_env(environ: dict[str, str] | os._Environ[str] | None = None) -> list[str]:
    """Copy `SENTINEL_X` into `ONIX_X` when only the old name is set, warning once per variable.

    Returns the names of the new variables that were populated. Idempotent."""
    env = os.environ if environ is None else environ
    filled: list[str] = []
    for old, value in list(env.items()):
        if not old.startswith(LEGACY_ENV_PREFIX):
            continue
        new = ENV_PREFIX + old[len(LEGACY_ENV_PREFIX) :]
        if new in env:
            continue
        env[new] = value
        filled.append(new)
        if new not in _legacy_env_warned:
            _legacy_env_warned.add(new)
            log.warning(
                "%s is deprecated and will be ignored from Onix 0.3.0; rename it to %s", old, new
            )
    return filled


def migrate_legacy_work_dir(work_dir: Path) -> bool:
    """One-time `.sentinel/` → `.onix/` move. Only when the target is absent and the old dir exists."""
    legacy = work_dir.with_name(LEGACY_WORK_DIR_NAME)
    if work_dir.name != ".onix" or work_dir.exists() or not legacy.is_dir():
        return False
    log.warning("moving legacy work dir %s -> %s (renamed from Sentinel)", legacy, work_dir)
    legacy.rename(work_dir)
    return True


_DEFAULT_ROLE_MAP: dict[str, Role] = {
    "onix-admin": "admin",
    "onix-operator": "operator",
    "onix-viewer": "viewer",
}


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
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, **values: Any) -> None:
        apply_legacy_env()
        super().__init__(**values)

    # deployment
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # storage
    database_url: str = "sqlite:///onix.db"
    work_dir: Path = Path(".onix")

    # llm
    primary_model: str = "anthropic/claude-sonnet-5"
    cheap_model: str = "anthropic/claude-haiku-4-5-20251001"
    fallback_model: str = "openai/gpt-5"
    triage_model: str = "onix-triage"
    triage_endpoint: str | None = None
    embedding_model: str = "text-embedding-3-large"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # "schema": provider-enforced JSON schema; "prompt": schema embedded in the prompt (free/open models);
    # "auto": prompt mode for openrouter/ and ollama/ models, schema otherwise
    structured_output_mode: Literal["auto", "schema", "prompt"] = "auto"
    voyage_api_key: SecretStr | None = None
    llm_cache_enabled: bool = True
    llm_backoff_s: float = 5.0  # sleep between passes over the model pool when every model failed

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

    # execution / retention
    execution_mode: Literal["inline", "queue"] = "inline"
    retention_days: int = 30
    gc_on_startup: bool = True
    model_prices: str = ""  # JSON, see onix/llm/pricing.py

    # oidc (optional SSO). Role comes from `oidc_role_claim` mapped through `oidc_role_map`.
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_role_claim: str = "groups"
    oidc_role_map: dict[str, Role] = Field(default_factory=lambda: dict(_DEFAULT_ROLE_MAP))
    oidc_default_role: Role = "viewer"

    # api
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_keys: Annotated[list[ApiKey], NoDecode] = Field(default_factory=list)
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    api_rate_limit_per_minute: int = 120
    # memory: per process (sliding window). db: shared across replicas through the database
    # (fixed one-minute windows). auto: db when execution_mode is queue, memory otherwise.
    api_rate_limit_backend: Literal["auto", "memory", "db"] = "auto"
    max_concurrent_runs: int = 2

    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)

    @field_validator(
        "anthropic_api_key",
        "openai_api_key",
        "deepseek_api_key",
        "openrouter_api_key",
        "voyage_api_key",
        "github_token",
        "otlp_endpoint",
        "triage_endpoint",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        """`KEY=` lines in .env mean unset, not the empty string."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("oidc_role_map", mode="before")
    @classmethod
    def _parse_role_map(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v else {}
        return v

    @field_validator("*", mode="before")
    @classmethod
    def _resolve_secret_refs(cls, v: Any) -> Any:
        """file:// env:// vault:// aws-sm:// gcp-sm:// references become their values (onix.secrets)."""
        from onix.secrets import is_reference, resolve

        if isinstance(v, str) and is_reference(v):
            return resolve(v)
        return v

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

    PROVIDER_KEYS: ClassVar[dict[str, str]] = {
        "anthropic/": "anthropic_api_key",
        "openai/": "openai_api_key",
        "deepseek/": "deepseek_api_key",
        "openrouter/": "openrouter_api_key",
    }
    # User-curated free OpenRouter rotation (largest/strongest first). Used when only an
    # OpenRouter key is configured, or when the configured models' providers have no key.
    OPENROUTER_DEFAULTS: ClassVar[dict[str, str]] = {
        "primary": ",".join(
            [
                "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
                "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                "openrouter/poolside/laguna-s-2.1:free",
                "openrouter/thinkingmachines/inkling-small:free",
                "openrouter/inclusionai/ling-3.0-flash-fin:free",
            ]
        ),
        "cheap": ",".join(
            [
                "openrouter/nvidia/nemotron-3.5-lightning:free",
                "openrouter/google/gemma-4-26b-a4b-it:free",
                "openrouter/poolside/laguna-xs-2.1:free",
                "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            ]
        ),
        "fallback": "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    }

    def has_key_for(self, model: str) -> bool:
        """True if the provider prefix of `model` has a configured key (or needs none, e.g. ollama)."""
        for prefix, attr in self.PROVIDER_KEYS.items():
            if model.startswith(prefix):
                return getattr(self, attr) is not None
        if "/" not in model:  # bare OpenAI-style id
            return self.openai_api_key is not None
        return True  # ollama/, huggingface/, custom endpoints: no key needed here

    def model_pool(self, tier: str) -> list[str]:
        """Models for a tier, in rotation order. Settings accept comma-separated lists, e.g.
        ONIX_PRIMARY_MODEL=openrouter/a:free,openrouter/b:free. Keyless providers are
        dropped; if nothing usable remains, fall back to OpenRouter/DeepSeek defaults."""
        configured = {
            "primary": self.primary_model,
            "cheap": self.cheap_model,
            "fallback": self.fallback_model,
        }[tier]
        candidates = [m.strip() for m in configured.split(",") if m.strip()]
        usable = [m for m in candidates if self.has_key_for(m)]
        if usable:
            return usable
        if self.openrouter_api_key is not None:
            return [m for m in self.OPENROUTER_DEFAULTS[tier].split(",") if m]
        if self.deepseek_api_key is not None:
            return ["deepseek/deepseek-chat"]
        return candidates[:1] or [configured]

    def effective_model(self, tier: str) -> str:
        return self.model_pool(tier)[0]

    def llm_configured(self) -> bool:
        return any(
            k is not None
            for k in (
                self.anthropic_api_key,
                self.openai_api_key,
                self.deepseek_api_key,
                self.openrouter_api_key,
            )
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
    """Per-repo overrides read from `onix.yaml` at the repo root."""

    language: Literal["python", "typescript", "mixed"] | None = None
    test_command: str | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    budget: BudgetSettings | None = None

    @classmethod
    def load(cls, repo_path: Path) -> RepoConfig:
        path = repo_path / "onix.yaml"
        if not path.exists():
            return cls()
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)


def get_settings() -> Settings:
    settings = Settings()
    migrate_legacy_work_dir(settings.work_dir)
    return settings
