"""Prompt loader: `.md` files in this directory, referenced by name, versioned by content hash."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class Prompt:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text
        self.version = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]  # noqa: S324

    def render(self, **variables: object) -> str:
        missing = [v for v in set(_VAR.findall(self.text)) if v not in variables]
        if missing:
            raise KeyError(f"prompt {self.name!r} missing variables: {sorted(missing)}")
        return _VAR.sub(lambda m: str(variables[m.group(1)]), self.text)


@lru_cache(maxsize=64)
def load_prompt(name: str) -> Prompt:
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt named {name!r} in {_DIR}")
    return Prompt(name, path.read_text(encoding="utf-8"))


def list_prompts() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.md"))
