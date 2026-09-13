"""Mutation operators for Python and TypeScript/JavaScript.

Operators are conservative regex rewrites applied to single source lines, restricted to lines that
tree-sitter places inside a function/method body (so we never mutate imports, signatures or
docstrings). One mutation per injected instance; the manifest records the exact location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from sentinel.indexing.treesitter import ParsedFile, parse_source


@dataclass(frozen=True)
class Candidate:
    operator: str
    category: str
    line: int  # 1-based
    original: str
    mutated: str
    description: str


class MutationOperator(Protocol):
    name: str
    category: str

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        """Return (mutated_line, description) alternatives for one source line, or []."""
        ...


def _swap(line: str, a: str, b: str, desc: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if a in line:
        out.append((line.replace(a, b, 1), desc))
    return out


class OffByOne:
    name = "OffByOne"
    category = "off_by_one"
    _range = re.compile(r"range\(\s*([^()]+?)\s*\)")
    _len_idx = re.compile(r"\[\s*len\(([^)]+)\)\s*-\s*1\s*\]")

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if lang == "python" and (m := self._range.search(line)) and "," not in m.group(1):
            out.append(
                (
                    line[: m.start()] + f"range({m.group(1)} - 1)" + line[m.end() :],
                    "range(n) -> range(n - 1)",
                )
            )
        if " <= " in line:
            out.append((line.replace(" <= ", " < ", 1), "<= -> <"))
        elif " < " in line and "<<" not in line:
            out.append((line.replace(" < ", " <= ", 1), "< -> <="))
        if " >= " in line:
            out.append((line.replace(" >= ", " > ", 1), ">= -> >"))
        if m := self._len_idx.search(line):
            out.append(
                (
                    line[: m.start()] + f"[len({m.group(1)})]" + line[m.end() :],
                    "[len(x) - 1] -> [len(x)]",
                )
            )
        return out


class NullCheckRemoval:
    name = "NullCheckRemoval"
    category = "null_deref"
    _py_inline = re.compile(
        r"^(\s*)if\s+(.+?)\s+is\s+None\s*:\s*(return[^#]*|raise[^#]*|continue|break)\s*$"
    )
    _py_block = re.compile(r"^(\s*)if\s+(.+?)\s+is\s+None\s*:\s*(#.*)?$")
    _ts_inline = re.compile(
        r"^(\s*)if\s*\(\s*!\s*([A-Za-z_$][\w$.]*)\s*\)\s*(return[^;]*;?|throw[^;]*;?|continue;?|break;?)\s*$"
    )
    _ts_block = re.compile(
        r"^(\s*)if\s*\(\s*(!\s*[A-Za-z_$][\w$.]*|[A-Za-z_$][\w$.]*\s*[!=]==?\s*(?:null|undefined))\s*\)\s*\{\s*$"
    )

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        if lang == "python":
            if m := self._py_inline.match(line):
                return [
                    (
                        m.group(1) + "pass  # null check removed",
                        f"removed `if {m.group(2)} is None` guard",
                    )
                ]
            if m := self._py_block.match(line):
                return [
                    (
                        m.group(1) + "if False:  # null check removed",
                        f"disabled `if {m.group(2)} is None` guard",
                    )
                ]
        else:
            if m := self._ts_inline.match(line):
                return [
                    (m.group(1) + "// null check removed", f"removed `if (!{m.group(2)})` guard")
                ]
            if m := self._ts_block.match(line):
                return [
                    (
                        m.group(1) + "if (false) {  // null check removed",
                        f"disabled `if ({m.group(2)})` guard",
                    )
                ]
        return []


class ExceptionSwallow:
    name = "ExceptionSwallow"
    category = "unhandled_exception"

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        s = line.strip()
        if lang == "python" and (s == "raise" or s.startswith("raise ")):
            return [
                (
                    line[: len(line) - len(line.lstrip())] + "pass  # exception swallowed",
                    "raise → pass",
                )
            ]
        if lang != "python" and s.startswith("throw "):
            return [
                (line[: len(line) - len(line.lstrip())] + "// exception swallowed", "throw removed")
            ]
        return []


class ResourceLeak:
    name = "ResourceLeak"
    category = "resource_leak"
    _close = re.compile(r"^\s*([A-Za-z_][\w.]*)\.close\(\)\s*;?\s*$")

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        if m := self._close.match(line):
            indent = line[: len(line) - len(line.lstrip())]
            return [
                (
                    indent
                    + ("pass  # close() removed" if lang == "python" else "// close() removed"),
                    f"removed {m.group(1)}.close()",
                )
            ]
        return []


class WrongOperator:
    name = "WrongOperator"
    category = "logic_error"

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if lang == "python":
            out += _swap(line, " and ", " or ", "and → or")
            out += _swap(line, " or ", " and ", "or → and")
            out += _swap(line, " not in ", " in ", "not in → in")
        else:
            out += _swap(line, " && ", " || ", "&& → ||")
            out += _swap(line, " || ", " && ", "|| → &&")
            out += _swap(line, " !== ", " === ", "!== → ===")
        out += _swap(line, " == ", " != ", "== → !=")
        out += _swap(line, " != ", " == ", "!= → ==")
        if (
            re.search(r"\w \+ \w", line)
            and "+=" not in line
            and '"' not in line
            and "'" not in line
        ):
            out.append((re.sub(r"(\w) \+ (\w)", r"\1 - \2", line, count=1), "+ → -"))
        return out


class TypeConfusion:
    name = "TypeConfusion"
    category = "type_error"
    _py = re.compile(r"\b(int|str|float|list|tuple|set)\(([^()]+)\)")
    _ts = re.compile(r"\b(parseInt|parseFloat|Number|String|Boolean)\(([^()]+)\)")

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        pat = self._py if lang == "python" else self._ts
        m = pat.search(line)
        if m and not line.strip().startswith(("def ", "class ", "import", "from ")):
            return [
                (line[: m.start()] + m.group(2) + line[m.end() :], f"removed {m.group(1)}() cast")
            ]
        return []


class AsyncMisuse:
    name = "AsyncMisuse"
    category = "race_condition"

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        if "await " in line and not line.strip().startswith(("async ", "def ", "function")):
            return [(line.replace("await ", "", 1), "dropped await")]
        return []


class ReturnMutation:
    name = "ReturnMutation"
    category = "logic_error"

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        s = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if lang == "python":
            if s == "return True":
                return [(indent + "return False", "return True → False")]
            if s == "return False":
                return [(indent + "return True", "return False → True")]
            if re.match(r"^return [A-Za-z_][\w.]*$", s):
                return [(indent + "return None", f"{s} → return None")]
        else:
            if s in ("return true;", "return true"):
                return [(indent + "return false;", "return true → false")]
            if s in ("return false;", "return false"):
                return [(indent + "return true;", "return false → true")]
            if re.match(r"^return [A-Za-z_$][\w$.]*;?$", s):
                return [(indent + "return undefined;", f"{s} → return undefined")]
        return []


class SecuritySmell:
    name = "SecuritySmell"
    category = "security_smell"
    _env = re.compile(
        r"os\.environ(?:\.get\(|\[)\s*['\"]([A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*)['\"]"
    )
    _ts_env = re.compile(r"process\.env\.([A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*)")
    _execute = re.compile(
        r"\.execute\(\s*(\"[^\"]*\?[^\"]*\"|'[^']*\?[^']*')\s*,\s*\(?([A-Za-z_][\w.]*)\s*,?\)?\s*\)"
    )

    def mutate_line(self, line: str, lang: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if lang == "python":
            if m := self._env.search(line):
                end = (
                    line.find(")", m.end()) + 1
                    if ".get(" in m.group(0)
                    else line.find("]", m.end()) + 1
                )
                out.append(
                    (
                        line[: m.start()] + '"sk-live-4f9a1c2e7b3d6a8f0e1c2b3a"' + line[end:],
                        f"hardcoded {m.group(1)}",
                    )
                )
            if m := self._execute.search(line):
                q = m.group(1)[1:-1].replace("?", "{" + m.group(2) + "}", 1)
                out.append(
                    (
                        line[: m.start()] + f'.execute(f"{q}")' + line[m.end() :],
                        "parameterised query → f-string SQL",
                    )
                )
        elif m := self._ts_env.search(line):
            out.append(
                (
                    line[: m.start()] + '"sk-live-4f9a1c2e7b3d6a8f0e1c2b3a"' + line[m.end() :],
                    f"hardcoded {m.group(1)}",
                )
            )
        return out


ALL_OPERATORS: list[MutationOperator] = [
    OffByOne(),
    NullCheckRemoval(),
    ExceptionSwallow(),
    ResourceLeak(),
    WrongOperator(),
    TypeConfusion(),
    AsyncMisuse(),
    ReturnMutation(),
    SecuritySmell(),
]


def _body_lines(pf: ParsedFile) -> set[int]:
    lines: set[int] = set()
    for s in pf.symbols:
        if s.kind in ("function", "method"):
            lines.update(range(s.line_start + 1, s.line_end + 1))
    return lines


def propose(
    source: str, rel_path: str, operators: list[MutationOperator] | None = None
) -> list[Candidate]:
    """All single-line mutation candidates inside function bodies of one file."""
    try:
        pf = parse_source(source, rel_path)
    except ValueError:
        return []
    lang = "python" if pf.language == "python" else "typescript"
    body = _body_lines(pf)
    lines = source.split("\n")
    out: list[Candidate] = []
    for op in operators or ALL_OPERATORS:
        for ln in sorted(body):
            if ln > len(lines):
                continue
            raw = lines[ln - 1]
            stripped = raw.strip()
            if not stripped or stripped.startswith(("#", "//", "*", "/*", '"""', "'''")):
                continue
            for mutated, desc in op.mutate_line(raw, lang):
                if mutated != raw:
                    out.append(Candidate(op.name, op.category, ln, raw, mutated, desc))
    return out


def apply_candidate(source: str, c: Candidate) -> str:
    lines = source.split("\n")
    if lines[c.line - 1] != c.original:
        raise ValueError(f"line {c.line} changed since candidate was proposed")
    lines[c.line - 1] = c.mutated
    return "\n".join(lines)
