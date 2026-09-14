"""Secret references (item 12). Any SENTINEL_* value may be a reference instead of a literal:

    file:///run/secrets/anthropic_key            Docker / Kubernetes mounted secret
    env://OTHER_VAR                              indirection to another environment variable
    vault://secret/data/sentinel#anthropic_key   HashiCorp Vault KV v2 (needs `hvac`, VAULT_ADDR/VAULT_TOKEN)
    aws-sm://sentinel/prod#anthropic_key         AWS Secrets Manager (needs `boto3`; JSON secret, optional #key)
    gcp-sm://projects/p/secrets/s/versions/latest Google Secret Manager (needs `google-cloud-secret-manager`)

Resolution happens once at settings load; providers are imported lazily so the core install stays lean.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

SCHEMES = ("file://", "env://", "vault://", "aws-sm://", "gcp-sm://")


def is_reference(value: str) -> bool:
    return isinstance(value, str) and value.startswith(SCHEMES)


@lru_cache(maxsize=128)
def resolve(value: str) -> str:
    if value.startswith("file://"):
        return Path(value.removeprefix("file://")).read_text(encoding="utf-8").strip()
    if value.startswith("env://"):
        name = value.removeprefix("env://")
        out = os.environ.get(name)
        if out is None:
            raise KeyError(f"secret reference {value}: environment variable {name} is not set")
        return out.strip()
    if value.startswith("vault://"):
        return _vault(value.removeprefix("vault://"))
    if value.startswith("aws-sm://"):
        return _aws(value.removeprefix("aws-sm://"))
    if value.startswith("gcp-sm://"):
        return _gcp(value.removeprefix("gcp-sm://"))
    return value


def _split(ref: str) -> tuple[str, str | None]:
    path, _, key = ref.partition("#")
    return path, (key or None)


def _vault(ref: str) -> str:
    import hvac

    path, key = _split(ref)
    client = hvac.Client(url=os.environ.get("VAULT_ADDR"), token=os.environ.get("VAULT_TOKEN"))
    mount, _, rel = path.partition("/")
    rel = rel.removeprefix("data/")
    data = client.secrets.kv.v2.read_secret_version(path=rel, mount_point=mount)["data"]["data"]
    if key is None:
        if len(data) == 1:
            return str(next(iter(data.values())))
        raise KeyError(f"vault secret {path} has several keys; add #<key>")
    return str(data[key])


def _aws(ref: str) -> str:
    import boto3

    name, key = _split(ref)
    resp = boto3.client("secretsmanager").get_secret_value(SecretId=name)
    raw = resp.get("SecretString") or resp["SecretBinary"].decode("utf-8")
    if key is None:
        return str(raw).strip()
    return str(json.loads(raw)[key])


def _gcp(ref: str) -> str:
    from google.cloud import secretmanager

    name, key = _split(ref)
    if "/versions/" not in name:
        name = f"{name}/versions/latest"
    payload = (
        secretmanager.SecretManagerServiceClient()
        .access_secret_version(name=name)
        .payload.data.decode("utf-8")
    )
    return str(json.loads(payload)[key]) if key else payload.strip()
