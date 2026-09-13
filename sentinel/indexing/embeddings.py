"""Embedding providers behind one protocol, with a content-hash cache (SPEC.md §8.4)."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Protocol

from sentinel.config import Settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic feature-hashing embedder. Offline fallback and test double.

    It is *lexical* (hashed identifier n-grams), not semantic. Used only when no embedding
    API key is configured; the CLI warns loudly when this is active.
    """

    name = "hash-256"
    dim = 256
    _token_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")

    def _tokens(self, text: str) -> list[str]:
        from sentinel.indexing.store import STOPWORDS

        out: list[str] = []
        for tok in self._token_re.findall(text):
            # split camelCase / snake_case into sub-words; drop stopwords and 1-char noise
            parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tok).replace("_", " ").lower().split()
            out.extend(p for p in parts if len(p) > 1 and p not in STOPWORDS)
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in self._tokens(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)  # noqa: S324 - not security
                v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / norm for x in v])
        return vecs


class LiteLLMEmbedder:
    """Embeds via LiteLLM (`text-embedding-3-large`, `voyage/voyage-code-3`, ...)."""

    _DIMS = {
        "text-embedding-3-large": 3072,
        "text-embedding-3-small": 1536,
        "voyage/voyage-code-3": 1024,
        "voyage-code-3": 1024,
    }

    def __init__(self, model: str, cache_dir: Path | None = None, batch_size: int = 64) -> None:
        self.name = model
        self.dim = self._DIMS.get(model, 0)
        self.batch_size = batch_size
        self._cache = _EmbeddingCache(cache_dir / "embeddings.sqlite") if cache_dir else None

    def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        out: list[list[float] | None] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            cached = self._cache.get(self.name, t) if self._cache else None
            if cached is not None:
                out[i] = cached
            else:
                todo.append(i)
        for start in range(0, len(todo), self.batch_size):
            idx = todo[start : start + self.batch_size]
            resp = litellm.embedding(model=self.name, input=[texts[i] for i in idx])
            for i, item in zip(idx, resp.data, strict=True):
                vec = [float(x) for x in item["embedding"]]
                out[i] = vec
                if self._cache:
                    self._cache.put(self.name, texts[i], vec)
        vecs = [v for v in out if v is not None]
        if vecs and not self.dim:
            self.dim = len(vecs[0])
        return vecs


class _EmbeddingCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec TEXT NOT NULL)"
        )

    @staticmethod
    def _key(model: str, text: str) -> str:
        return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()

    def get(self, model: str, text: str) -> list[float] | None:
        row = self._conn.execute("SELECT vec FROM emb WHERE key=?", (self._key(model, text),))
        r = row.fetchone()
        if r is None:
            return None
        return [float(x) for x in json.loads(r[0])]

    def put(self, model: str, text: str, vec: list[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO emb VALUES (?, ?)", (self._key(model, text), json.dumps(vec))
        )
        self._conn.commit()


def make_embedder(settings: Settings) -> Embedder:
    model = settings.embedding_model
    has_openai = settings.openai_api_key is not None
    has_voyage = settings.voyage_api_key is not None
    if (model.startswith("text-embedding") and has_openai) or ("voyage" in model and has_voyage):
        import os

        if has_openai and settings.openai_api_key is not None:
            os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
        if has_voyage and settings.voyage_api_key is not None:
            os.environ.setdefault("VOYAGE_API_KEY", settings.voyage_api_key.get_secret_value())
        cache_dir = settings.work_dir / "cache" if settings.llm_cache_enabled else None
        return LiteLLMEmbedder(model, cache_dir=cache_dir)
    return HashEmbedder()
