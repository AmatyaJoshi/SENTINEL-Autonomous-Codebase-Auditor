"""tree-sitter symbol extraction for Python, TypeScript/TSX and JavaScript (SPEC.md §3.2 index)."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from tree_sitter import Language, Node, Parser

Lang = Literal["python", "typescript", "tsx", "javascript"]
SymbolKind = Literal["function", "method", "class", "interface", "type", "variable"]

EXT_TO_LANG: dict[str, Lang] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
}


@dataclass
class Symbol:
    name: str  # qualified within file, e.g. "Auth.parse_token"
    kind: SymbolKind
    file: str  # repo-relative POSIX path
    line_start: int  # 1-based inclusive
    line_end: int
    signature: str
    docstring: str | None = None
    parent: str | None = None
    calls: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def short_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def qualified_id(self) -> str:
        return f"{self.file}::{self.name}"


@dataclass
class Import:
    file: str
    module: str
    names: list[str]
    line: int


@dataclass
class ParsedFile:
    path: str
    language: Lang
    n_lines: int
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)


def language_for(path: str | Path) -> Lang | None:
    return EXT_TO_LANG.get(Path(path).suffix.lower())


_LANGUAGES: dict[str, Language] = {}


@lru_cache(maxsize=8)
def _parser(lang: Lang) -> Parser:
    if lang == "python":
        import tree_sitter_python as tsp

        _LANGUAGES[lang] = Language(tsp.language())
        return Parser(_LANGUAGES[lang])
    if lang == "typescript":
        import tree_sitter_typescript as tst

        _LANGUAGES[lang] = Language(tst.language_typescript())
        return Parser(_LANGUAGES[lang])
    if lang == "tsx":
        import tree_sitter_typescript as tst

        _LANGUAGES[lang] = Language(tst.language_tsx())
        return Parser(_LANGUAGES[lang])
    import tree_sitter_javascript as tsj

    _LANGUAGES[lang] = Language(tsj.language())
    return Parser(_LANGUAGES[lang])


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def parse_source(source: str, rel_path: str, lang: Lang | None = None) -> ParsedFile:
    lang = lang or language_for(rel_path)
    if lang is None:
        raise ValueError(f"unsupported file type: {rel_path}")
    data = source.encode("utf-8")
    tree = _parser(lang).parse(data)
    parsed = ParsedFile(path=rel_path, language=lang, n_lines=source.count("\n") + 1)
    extractor = _PyExtractor(parsed) if lang == "python" else _TsExtractor(parsed)
    extractor.walk(tree.root_node, parent=None)
    return parsed


def parse_file(root: Path, rel_path: str) -> ParsedFile | None:
    lang = language_for(rel_path)
    if lang is None:
        return None
    try:
        source = (root / rel_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_source(source, rel_path, lang)


# --------------------------------------------------------------------------- extractors


class _BaseExtractor:
    def __init__(self, parsed: ParsedFile) -> None:
        self.parsed = parsed

    def walk(self, node: Node, parent: str | None) -> None:
        raise NotImplementedError

    def _add(
        self,
        node: Node,
        name: str,
        kind: SymbolKind,
        parent: str | None,
        body: Node | None,
        docstring: str | None = None,
    ) -> Symbol:
        text = _text(node)
        qual = f"{parent}.{name}" if parent else name
        sym = Symbol(
            name=qual,
            kind=kind,
            file=self.parsed.path,
            line_start=node.start_point.row + 1,
            line_end=node.end_point.row + 1,
            signature=text.split("\n", 1)[0].strip(),
            docstring=docstring,
            parent=parent,
            calls=sorted(set(self._collect_calls(body))) if body is not None else [],
            text=text,
        )
        self.parsed.symbols.append(sym)
        return sym

    def _collect_calls(self, node: Node) -> list[str]:
        raise NotImplementedError


class _PyExtractor(_BaseExtractor):
    def walk(self, node: Node, parent: str | None) -> None:
        for child in node.children:
            t = child.type
            if t == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner is not None:
                    self._definition(inner, parent, outer=child)
            elif t in ("function_definition", "class_definition"):
                self._definition(child, parent, outer=child)
            elif t in ("import_statement", "import_from_statement"):
                self._import(child)
            elif t in ("if_statement", "try_statement", "with_statement", "block", "module"):
                self.walk(child, parent)
            elif t in ("expression_statement",) and parent is None:
                # module-level assignments are not symbols; nothing to do
                pass

    def _definition(self, node: Node, parent: str | None, outer: Node) -> None:
        name = _text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        if node.type == "class_definition":
            sym = self._add(outer, name, "class", parent, None, self._docstring(body))
            if body is not None:
                self.walk(body, sym.name)
        else:
            kind: SymbolKind = "method" if parent else "function"
            self._add(outer, name, kind, parent, body, self._docstring(body))
            if body is not None:
                # nested functions/classes
                self.walk(body, f"{parent}.{name}" if parent else name)

    @staticmethod
    def _docstring(body: Node | None) -> str | None:
        if body is None or body.child_count == 0:
            return None
        first = body.children[0]
        if first.type == "expression_statement" and first.child_count > 0:
            s = first.children[0]
            if s.type == "string":
                raw = _text(s).strip()
                for q in ('"""', "'''", '"', "'"):
                    if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                        return raw[len(q) : -len(q)].strip()
                return raw
        return None

    def _import(self, node: Node) -> None:
        line = node.start_point.row + 1
        if node.type == "import_statement":
            for c in node.children:
                if c.type == "dotted_name":
                    self.parsed.imports.append(Import(self.parsed.path, _text(c), [], line))
                elif c.type == "aliased_import":
                    mod = _text(c.child_by_field_name("name"))
                    self.parsed.imports.append(Import(self.parsed.path, mod, [], line))
        else:
            module = _text(node.child_by_field_name("module_name"))
            names: list[str] = []
            for c in node.children:
                if c.type == "dotted_name" and _text(c) != module:
                    names.append(_text(c))
                elif c.type == "aliased_import":
                    names.append(_text(c.child_by_field_name("name")))
                elif c.type == "wildcard_import":
                    names.append("*")
            self.parsed.imports.append(Import(self.parsed.path, module, names, line))

    def _collect_calls(self, node: Node) -> list[str]:
        out: list[str] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "identifier":
                        out.append(_text(fn))
                    elif fn.type == "attribute":
                        out.append(_text(fn.child_by_field_name("attribute")))
            stack.extend(n.children)
        return out


class _TsExtractor(_BaseExtractor):
    _DECLS = {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "type",
    }

    def walk(self, node: Node, parent: str | None) -> None:
        for child in node.children:
            t = child.type
            if t == "export_statement":
                decl = child.child_by_field_name("declaration")
                if decl is not None:
                    self._declaration(decl, parent, outer=child)
                else:
                    self.walk(child, parent)
            elif t in self._DECLS or t in ("lexical_declaration", "variable_declaration"):
                self._declaration(child, parent, outer=child)
            elif t in ("method_definition", "method_signature", "public_field_definition"):
                self._member(child, parent)
            elif t in ("import_statement",):
                self._import(child)
            elif t in ("class_body", "statement_block", "program", "if_statement", "block"):
                self.walk(child, parent)

    def _declaration(self, node: Node, parent: str | None, outer: Node) -> None:
        t = node.type
        if t in ("lexical_declaration", "variable_declaration"):
            for d in node.children:
                if d.type != "variable_declarator":
                    continue
                name = _text(d.child_by_field_name("name"))
                value = d.child_by_field_name("value")
                if value is not None and value.type in (
                    "arrow_function",
                    "function_expression",
                    "function",
                ):
                    kind: SymbolKind = "method" if parent else "function"
                    body = value.child_by_field_name("body")
                    self._add(outer, name, kind, parent, body)
                    if body is not None:
                        self.walk(body, f"{parent}.{name}" if parent else name)
                elif value is not None and value.type == "class":
                    sym = self._add(outer, name, "class", parent, None)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        self.walk(body, sym.name)
            return
        kind = self._DECLS[t]  # type: ignore[assignment]
        name = _text(node.child_by_field_name("name")) or "<anonymous>"
        body = node.child_by_field_name("body")
        if kind == "class":
            sym = self._add(outer, name, "class", parent, None)
            if body is not None:
                self.walk(body, sym.name)
        elif kind == "function":
            self._add(outer, name, "method" if parent else "function", parent, body)
            if body is not None:
                self.walk(body, f"{parent}.{name}" if parent else name)
        else:
            self._add(outer, name, kind, parent, None)

    def _member(self, node: Node, parent: str | None) -> None:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return
        if node.type == "public_field_definition":
            value = node.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression"):
                return
            body = value.child_by_field_name("body")
            self._add(node, name, "method", parent, body)
            return
        body = node.child_by_field_name("body")
        self._add(node, name, "method", parent, body)
        if body is not None:
            self.walk(body, f"{parent}.{name}" if parent else name)

    def _import(self, node: Node) -> None:
        source = _text(node.child_by_field_name("source")).strip("'\"`")
        names: list[str] = []
        stack = list(node.children)
        while stack:
            n = stack.pop()
            if n.type in ("import_specifier",):
                names.append(_text(n.child_by_field_name("name")))
            elif (
                n.type == "identifier"
                and n.parent is not None
                and n.parent.type
                in (
                    "import_clause",
                    "namespace_import",
                )
            ):
                names.append(_text(n))
            stack.extend(n.children)
        self.parsed.imports.append(
            Import(self.parsed.path, source, sorted(set(names)), node.start_point.row + 1)
        )

    def _collect_calls(self, node: Node) -> list[str]:
        out: list[str] = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                if fn is not None:
                    if fn.type == "identifier":
                        out.append(_text(fn))
                    elif fn.type == "member_expression":
                        out.append(_text(fn.child_by_field_name("property")))
            elif n.type == "new_expression":
                ctor = n.child_by_field_name("constructor")
                if ctor is not None and ctor.type == "identifier":
                    out.append(_text(ctor))
            stack.extend(n.children)
        return out
