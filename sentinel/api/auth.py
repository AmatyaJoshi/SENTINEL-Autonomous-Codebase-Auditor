"""Authentication (item 11): static API keys from settings, database-managed API keys with rotation
and revocation, and OIDC bearer tokens (JWT validated against the issuer's JWKS).

Role resolution order: OIDC claim → DB key → settings key → open dev mode (no keys configured).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import col, select

from sentinel.config import Role, Settings
from sentinel.db.models import ApiKeyRecord, as_utc
from sentinel.db.session import session_scope

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


class Principal(BaseModel):
    name: str
    role: Role
    via: str = "api-key"  # api-key | db-key | oidc | open


def _future(dt: datetime) -> bool:
    aware = as_utc(dt)
    return aware is not None and aware > datetime.now(UTC)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Authenticator:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings = settings
        self.engine = engine
        self.static = {
            hash_key(k.key.get_secret_value()): Principal(name=k.name, role=k.role)
            for k in settings.api_keys
        }
        self.open_mode = not settings.api_keys and not settings.oidc_issuer
        self._jwks_client: Any = None
        self._db_cache: dict[str, tuple[float, Principal | None]] = {}

    # ------------------------------------------------------------------ API keys
    def by_api_key(self, raw: str) -> Principal | None:
        h = hash_key(raw)
        if h in self.static:
            return self.static[h]
        cached = self._db_cache.get(h)
        if cached and cached[0] > time.time():
            return cached[1]
        principal: Principal | None = None
        with session_scope(self.engine) as s:
            rec = s.exec(select(ApiKeyRecord).where(ApiKeyRecord.key_hash == h)).first()
            if (
                rec is not None
                and rec.revoked_at is None
                and (rec.expires_at is None or _future(rec.expires_at))
            ):
                rec.last_used_at = datetime.now(UTC)
                s.add(rec)
                principal = Principal(name=rec.name, role=rec.role, via="db-key")
        self._db_cache[h] = (time.time() + 30, principal)
        return principal

    def create_key(
        self, name: str, role: Role, created_by: str, expires_days: int | None = None
    ) -> tuple[str, ApiKeyRecord]:
        raw = "sk-sentinel-" + secrets.token_urlsafe(32)
        rec = ApiKeyRecord(
            name=name,
            role=role,
            key_hash=hash_key(raw),
            prefix=raw[:16],
            created_by=created_by,
            expires_at=(datetime.now(UTC) + __import__("datetime").timedelta(days=expires_days))
            if expires_days
            else None,
        )
        with session_scope(self.engine) as s:
            s.add(rec)
            s.flush()
            s.refresh(rec)
            s.expunge(rec)
        return raw, rec

    def revoke_key(self, key_id: str) -> bool:
        with session_scope(self.engine) as s:
            rec = s.get(ApiKeyRecord, key_id)
            if rec is None or rec.revoked_at is not None:
                return False
            rec.revoked_at = datetime.now(UTC)
            s.add(rec)
            self._db_cache.pop(rec.key_hash, None)
            return True

    def rotate_key(self, key_id: str, created_by: str) -> tuple[str, ApiKeyRecord] | None:
        """Issue a replacement with the same name/role; the old key stays valid for a grace hour."""
        with session_scope(self.engine) as s:
            rec = s.get(ApiKeyRecord, key_id)
            if rec is None or rec.revoked_at is not None:
                return None
            rec.expires_at = datetime.now(UTC) + __import__("datetime").timedelta(hours=1)
            s.add(rec)
            name, role = rec.name, cast(Role, rec.role)
            self._db_cache.pop(rec.key_hash, None)
        return self.create_key(name, role, created_by)

    def list_keys(self) -> list[ApiKeyRecord]:
        with session_scope(self.engine) as s:
            rows = s.exec(select(ApiKeyRecord).order_by(col(ApiKeyRecord.created_at).desc())).all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    # ------------------------------------------------------------------ OIDC
    def by_bearer(self, token: str) -> Principal | None:
        if not self.settings.oidc_issuer:
            return None
        import jwt

        if self._jwks_client is None:
            issuer = self.settings.oidc_issuer.rstrip("/")
            jwks_url = self.settings.oidc_jwks_url or f"{issuer}/.well-known/jwks.json"
            self._jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256", "RS512", "ES512"],
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except Exception:  # noqa: BLE001 - any validation failure is "not authenticated"
            return None
        return Principal(
            name=str(claims.get("email") or claims.get("preferred_username") or claims["sub"]),
            role=self._role_from_claims(claims),
            via="oidc",
        )

    def _role_from_claims(self, claims: dict[str, Any]) -> Role:
        raw = claims.get(self.settings.oidc_role_claim)
        values: list[str] = []
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, list):
            values = [str(v) for v in raw]
        mapping = self.settings.oidc_role_map
        best: Role = self.settings.oidc_default_role
        for v in values:
            role = mapping.get(v)
            if role and ROLE_RANK[role] > ROLE_RANK[best]:
                best = role
        return best
