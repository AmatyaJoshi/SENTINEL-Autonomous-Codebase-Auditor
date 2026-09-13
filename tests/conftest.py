"""Test isolation: never read the developer's real .env."""

from __future__ import annotations

import pytest

from sentinel.config import Settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in (
        "SENTINEL_API_KEYS",
        "SENTINEL_API_CORS_ORIGINS",
        "SENTINEL_DATABASE_URL",
        "SENTINEL_WORK_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
