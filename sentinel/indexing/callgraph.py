"""Import/call graph over tree-sitter symbols (networkx). Name-based resolution, import-aware."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx

from sentinel.indexing.treesitter import ParsedFile, Symbol


class CallGraph:
    """Nodes are `file::Qualified.name`; edge u→v means u calls v.

    Resolution order for a callee short name: same file → symbols in files the caller
    imports (by module path suffix) → any file. Ambiguous matches produce multiple edges
    tagged `ambiguous=True` so blast-radius stays conservative (over-approximates).
    """

    def __init__(self) -> None:
        self.g: nx.DiGraph[str] = nx.DiGraph()
        self._by_short: dict[str, list[str]] = defaultdict(list)
        self._by_file: dict[str, list[str]] = defaultdict(list)

    # ----------------------------------------------------------------- build
    @classmethod
    def build(cls, files: list[ParsedFile]) -> CallGraph:
        cg = cls()
        for pf in files:
            for s in pf.symbols:
                cg._add_symbol(s)
        for pf in files:
            imported_files = cg._resolve_imports(pf, files)
            for s in pf.symbols:
                for callee in s.calls:
                    for target, ambiguous in cg._resolve(callee, pf.path, imported_files):
                        if target == s.qualified_id:
                            continue
                        cg.g.add_edge(s.qualified_id, target, ambiguous=ambiguous)
        return cg

    def _add_symbol(self, s: Symbol) -> None:
        self.g.add_node(
            s.qualified_id,
            name=s.name,
            kind=s.kind,
            file=s.file,
            line_start=s.line_start,
            line_end=s.line_end,
        )
        self._by_short[s.short_name].append(s.qualified_id)
        self._by_file[s.file].append(s.qualified_id)

    @staticmethod
    def _resolve_imports(pf: ParsedFile, files: list[ParsedFile]) -> set[str]:
        paths = {f.path for f in files}
        out: set[str] = set()
        for imp in pf.imports:
            mod = imp.module.strip("./").replace(".", "/").replace("\\", "/")
            if not mod:
                continue
            for p in paths:
                stem = p.rsplit(".", 1)[0]
                if stem == mod or stem.endswith("/" + mod) or stem.endswith(mod + "/index"):
                    out.add(p)
        return out

    def _resolve(self, short: str, caller_file: str, imported: set[str]) -> list[tuple[str, bool]]:
        cands = self._by_short.get(short, [])
        if not cands:
            return []
        same = [c for c in cands if self.g.nodes[c]["file"] == caller_file]
        if same:
            return [(c, len(same) > 1) for c in same]
        via_import = [c for c in cands if self.g.nodes[c]["file"] in imported]
        if via_import:
            return [(c, len(via_import) > 1) for c in via_import]
        return [(c, len(cands) > 1) for c in cands]

    # ----------------------------------------------------------------- query
    def find(self, name: str) -> list[str]:
        """Accepts `file::Qual.name`, `Qual.name`, or a short name."""
        if name in self.g:
            return [name]
        short = name.rsplit(".", 1)[-1]
        hits = [n for n in self._by_short.get(short, []) if self.g.nodes[n]["name"] == name]
        return hits or list(self._by_short.get(short, []))

    def callers(self, name: str) -> list[str]:
        out: set[str] = set()
        for n in self.find(name):
            out.update(self.g.predecessors(n))
        return sorted(out)

    def callees(self, name: str) -> list[str]:
        out: set[str] = set()
        for n in self.find(name):
            out.update(self.g.successors(n))
        return sorted(out)

    def blast_radius(self, name: str, depth: int = 3) -> int:
        """Number of distinct transitive callers within `depth` hops."""
        seen: set[str] = set()
        frontier = set(self.find(name))
        for _ in range(depth):
            nxt: set[str] = set()
            for n in frontier:
                nxt.update(self.g.predecessors(n))
            nxt -= seen
            nxt -= frontier
            if not nxt:
                break
            seen |= nxt
            frontier = nxt
        return len(seen)

    # ----------------------------------------------------------------- io
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [{"id": n, **d} for n, d in self.g.nodes(data=True)],
            "edges": [{"u": u, "v": v, **d} for u, v, d in self.g.edges(data=True)],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CallGraph:
        data = json.loads(path.read_text(encoding="utf-8"))
        cg = cls()
        for n in data["nodes"]:
            nid = n.pop("id")
            cg.g.add_node(nid, **n)
            cg._by_short[n["name"].rsplit(".", 1)[-1]].append(nid)
            cg._by_file[n["file"]].append(nid)
        for e in data["edges"]:
            cg.g.add_edge(e["u"], e["v"], ambiguous=e.get("ambiguous", False))
        return cg
