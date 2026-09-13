"""Function-level chunking: ≤ 400 tokens per chunk, 20-line overlap when splitting large symbols."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sentinel.indexing.treesitter import ParsedFile, Symbol

MAX_TOKENS = 400
OVERLAP_LINES = 20
CHARS_PER_TOKEN = 4  # conservative heuristic for code; avoids a tokenizer dependency


@dataclass(frozen=True)
class Chunk:
    id: str
    file: str
    line_start: int
    line_end: int
    symbol: str | None
    language: str
    text: str

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line_start}-{self.line_end}"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _chunk_id(file: str, start: int, end: int, text: str) -> str:
    h = hashlib.sha1(f"{file}:{start}:{end}:{text}".encode()).hexdigest()
    return h[:16]


def _make(
    file: str, lines: list[str], start: int, end: int, symbol: str | None, lang: str
) -> Chunk:
    """start/end are 1-based inclusive line numbers."""
    text = "\n".join(lines[start - 1 : end])
    return Chunk(_chunk_id(file, start, end, text), file, start, end, symbol, lang, text)


def _split_range(
    file: str,
    lines: list[str],
    start: int,
    end: int,
    symbol: str | None,
    lang: str,
    max_tokens: int,
    overlap: int,
) -> list[Chunk]:
    whole = _make(file, lines, start, end, symbol, lang)
    if estimate_tokens(whole.text) <= max_tokens:
        return [whole]
    out: list[Chunk] = []
    cur = start
    while cur <= end:
        # grow window until the token budget is hit
        hi = cur
        budget = max_tokens * CHARS_PER_TOKEN
        used = 0
        while hi <= end:
            used += len(lines[hi - 1]) + 1
            if used > budget and hi > cur:
                break
            hi += 1
        hi = min(hi - 1, end)
        if hi < cur:
            hi = cur
        out.append(_make(file, lines, cur, hi, symbol, lang))
        if hi >= end:
            break
        cur = max(hi - overlap + 1, cur + 1)
    return out


def chunk_parsed_file(
    parsed: ParsedFile,
    source: str,
    max_tokens: int = MAX_TOKENS,
    overlap: int = OVERLAP_LINES,
) -> list[Chunk]:
    lines = source.split("\n")
    n = len(lines)
    chunks: list[Chunk] = []
    covered = [False] * (n + 2)

    # Leaf units: functions/methods, plus class headers (class lines not owned by a member).
    leaves: list[Symbol] = [s for s in parsed.symbols if s.kind in ("function", "method")]
    # Nested functions are contained in their parent's range; keep only outermost leaves.
    leaves.sort(key=lambda s: (s.line_start, -s.line_end))
    outer: list[Symbol] = []
    for s in leaves:
        if outer and s.line_start <= outer[-1].line_end:
            continue
        outer.append(s)

    for s in outer:
        chunks.extend(
            _split_range(
                parsed.path,
                lines,
                s.line_start,
                s.line_end,
                s.name,
                parsed.language,
                max_tokens,
                overlap,
            )
        )
        for i in range(s.line_start, s.line_end + 1):
            covered[i] = True

    for s in parsed.symbols:
        if s.kind not in ("class", "interface", "type"):
            continue
        # header = contiguous uncovered lines from the symbol start
        start = s.line_start
        end = start
        while end + 1 <= s.line_end and not covered[end + 1]:
            end += 1
        if covered[start]:
            continue
        chunks.extend(
            _split_range(
                parsed.path, lines, start, end, s.name, parsed.language, max_tokens, overlap
            )
        )
        for i in range(start, end + 1):
            covered[i] = True

    # Module-level leftovers (imports, constants, top-level statements) in windows.
    i = 1
    while i <= n:
        if covered[i] or not lines[i - 1].strip():
            i += 1
            continue
        j = i
        while j + 1 <= n and not covered[j + 1]:
            j += 1
        # trim trailing blank lines
        while j > i and not lines[j - 1].strip():
            j -= 1
        chunks.extend(
            _split_range(parsed.path, lines, i, j, None, parsed.language, max_tokens, overlap)
        )
        i = j + 1

    chunks.sort(key=lambda c: (c.line_start, c.line_end))
    return chunks
