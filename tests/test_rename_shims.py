"""Sentinel → Onix (0.2.0) compatibility shims: env prefix, work dir, API-key prefix. Remove in 0.3.0."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlmodel import create_engine

from onix import config
from onix.api.auth import KEY_PREFIX, LEGACY_KEY_PREFIX, Authenticator, hash_key
from onix.config import ApiKey, Settings, apply_legacy_env, migrate_legacy_work_dir
from onix.db.session import init_db


@pytest.fixture(autouse=True)
def _reset_warned() -> None:
    config._legacy_env_warned.clear()


def test_legacy_env_used_when_new_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ONIX_LOG_JSON", raising=False)
    monkeypatch.setenv("SENTINEL_LOG_JSON", "1")
    with caplog.at_level(logging.WARNING, logger="onix.config"):
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.log_json is True
    warnings = [r for r in caplog.records if "SENTINEL_LOG_JSON" in r.getMessage()]
    assert len(warnings) == 1, "exactly one warning per legacy variable"
    assert "ONIX_LOG_JSON" in warnings[0].getMessage()


def test_new_env_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONIX_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("SENTINEL_LOG_LEVEL", "ERROR")
    assert Settings(_env_file=None).log_level == "DEBUG"  # type: ignore[call-arg]


def test_apply_legacy_env_returns_filled_names() -> None:
    env = {"SENTINEL_FOO": "a", "ONIX_BAR": "b", "SENTINEL_BAR": "c", "OTHER": "d"}
    assert apply_legacy_env(env) == ["ONIX_FOO"]
    assert env["ONIX_FOO"] == "a" and env["ONIX_BAR"] == "b"


def test_work_dir_migration(tmp_path: Path) -> None:
    legacy = tmp_path / ".sentinel"
    (legacy / "runs").mkdir(parents=True)
    target = tmp_path / ".onix"
    assert migrate_legacy_work_dir(target) is True
    assert (target / "runs").is_dir() and not legacy.exists()
    # idempotent, and never touches a non-default work dir name
    assert migrate_legacy_work_dir(target) is False
    (tmp_path / ".sentinel").mkdir()
    assert migrate_legacy_work_dir(tmp_path / "custom") is False
    assert migrate_legacy_work_dir(target) is False  # target exists -> untouched


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_legacy_api_key_prefix_accepted_with_one_warning(tmp_path: Path) -> None:
    # init_db() runs alembic's fileConfig, which replaces root handlers (and caplog's), so listen
    # on the module logger directly.
    cap = _Capture()
    logging.getLogger("onix.auth").addHandler(cap)
    old_raw = LEGACY_KEY_PREFIX + "abc123"
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        work_dir=tmp_path,
        api_keys=[ApiKey(key=old_raw, name="legacy", role="operator")],
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}")
    init_db(engine)
    auth = Authenticator(s, engine)
    try:
        assert auth.by_api_key(old_raw) is not None
        assert auth.by_api_key(old_raw) is not None
        msgs = [m for m in cap.messages if LEGACY_KEY_PREFIX in m]
        assert len(msgs) == 1 and "deprecated" in msgs[0]
        assert old_raw not in msgs[0]  # never log the full key

        new_raw, rec = auth.create_key("fresh", "viewer", "test")
        assert new_raw.startswith(KEY_PREFIX) and rec.key_hash == hash_key(new_raw)
        cap.messages.clear()
        assert auth.by_api_key(new_raw) is not None
        assert not [m for m in cap.messages if "deprecated" in m]
    finally:
        logging.getLogger("onix.auth").removeHandler(cap)
