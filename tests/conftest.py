"""Test isolation: never read the developer's real .env."""

from __future__ import annotations

import pytest

from onix.config import Settings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in (
        "ONIX_API_KEYS",
        "ONIX_API_CORS_ORIGINS",
        "ONIX_DATABASE_URL",
        "ONIX_WORK_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
