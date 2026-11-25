"""AST delta + PR scope -> Mermaid `graph TD` adapter.

Consumes the structured output of `lib.pr_ast_differ.diff_ast` plus the
`lib.pr_scope_extractor.PrScope` record and produces a Mermaid graph
suitable for inclusion in the HAL Council's PR review comment.

Behaviour:
- Each symbol in the AST delta becomes a colour-coded node
  (added / removed / sigchange / bodychange).
- Class inheritance and added imports produce labelled edges.
- An imported module or a base class is a name from the code, not a symbol
  of this PR. It becomes a ``ref_``-prefixed id with the real name in a
  quoted label, because a bare name such as ``click`` or ``end`` is a
  Mermaid keyword and breaks the whole diagram (GRIP#8956).
- PRs exceeding `PrScope.size_limit` are suppressed (the graph is replaced
  with a single placeholder). Required by plan senior-infra gotcha #4.
- When the node count exceeds `max_nodes` (default 20, plan value), the
  graph is truncated to the top-N by degree centrality and a marker node
  is appended so readers know some symbols are hidden. Truncation drops
  only the links that touch a dropped symbol, so a file's imports and a
  kept class's bases stay, and the marker counts the links it hid.
- A double quote inside a label is written as ``#quot;`` and a ``#`` as
  ``#35;``, so a base such as ``Base["it's"]`` is drawn exactly as written.

Pure stdlib. Safe to import on CI runners before any pip install.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from lib.pr_ast_differ import AstDelta, SymbolChange, SymbolSig
from lib.pr_scope_extractor import ChangedFile, PrScope


_CLASSDEF_STYLES = (
    "classDef added fill:#d4ffd4,stroke:#2d8a2d,color:#0a3d0a\n"
    "classDef removed fill:#ffd4d4,stroke:#8a2d2d,color:#3d0a0a\n"
    "classDef sigchange fill:#ffe4b3,stroke:#b37a00,color:#3d2a0a\n"
    "classDef bodychange fill:#fff4c2,stroke:#8a7a00,color:#2a2500"
)

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class MermaidNode:
    node_id: str
    label: str
    style_class: str


@dataclass(frozen=True)
class MermaidEdge:
    source: str
    target: str
    label: Optional[str] = None
    target_label: Optional[str] = None


@dataclass
class MermaidDiagram:
    nodes: list[MermaidNode] = field(default_factory=list)
    edges: list[MermaidEdge] = field(default_factory=list)
    suppressed: bool = False
    suppress_reason: Optional[str] = None
    truncated: bool = False
    total_symbols_before_truncation: int = 0
    links_hidden: int = 0

    def render(self) -> str:
        """Return the Mermaid graph body (no fence — caller wraps in ```mermaid)."""
        if self.suppressed:
            reason = self.suppress_reason or "diagram suppressed"
            return f'graph TD\n    _suppressed["{_safe_label(reason)}"]'
        lines = ["graph TD"]
        for style_line in _CLASSDEF_STYLES.split("\n"):
            lines.append(f"    {style_line}")
        for node in self.nodes:
            lines.append(f'    {node.node_id}["{_safe_label(node.label)}"]:::{node.style_class}')
        lines.extend(f"    {_render_edge(edge)}" for edge in self.edges)
        if self.truncated:
            lines.append(f'    _truncated["({self._truncation_note()})"]')
        return "\n".join(lines)

    def _truncation_note(self) -> str:
        """Say what truncation hid, links included, so a reader never misses them."""
        dropped = self.total_symbols_before_truncation - len(self.nodes)
        note = f"+{dropped} more symbols truncated"
        if self.links_hidden:
            note += f", {self.links_hidden} link{'' if self.links_hidden == 1 else 's'} hidden"
        return note


def _safe_label(text: str) -> str:
    """Text for a quoted label, drawn exactly as written.

    A double quote would end the label early, so it becomes ``#quot;``. Mermaid
    reads every ``#name;`` or ``#123;`` in a label as an entity, so each ``#``
    becomes ``#35;`` first. Otherwise a literal ``#quot;`` in a base class would
    be drawn as a quote (measured on mmdc 11.17.0).
    """
    return text.replace("#", "#35;").replace('"', "#quot;")


def _render_edge(edge: MermaidEdge) -> str:
    target = edge.target
    if edge.target_label is not None:
        target = f'{edge.target}["{_safe_label(edge.target_label)}"]'
    arrow = f"-->|{edge.label}|" if edge.label else "-->"
    return f"{edge.source} {arrow} {target}"


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text).strip("_")
    return slug or "node"


def _ref_id(name: str) -> str:
    """The id for an import or a base class: prefixed, so it is never a keyword.

    Every character outside ASCII letters and digits becomes ``_<hex>_``, so two
    different names never share an id: ``click.testing`` gives
    ``ref_click_2e_testing`` and ``click_testing`` gives ``ref_click_5f_testing``.
    """
    return "ref_" + "".join(c if c.isascii() and c.isalnum() else f"_{ord(c):x}_" for c in name)


def _path_prefix(path: str) -> str:
    stem = path.rsplit(".", 1)[0] if "." in path else path
    return _slugify(stem)


def _label_for_symbol(sym: SymbolSig) -> str:
    qname = sym.qualified_name
    if sym.kind == "class":
        return f"class {qname}"
    if sym.kind in ("function", "method"):
        return f"{qname}({', '.join(sym.args)})"
    return qname


def _node_for_symbol(path: str, sym: SymbolSig, style_class: str) -> MermaidNode:
    return MermaidNode(
        node_id=f"{_path_prefix(path)}__{_slugify(sym.qualified_name)}",
        label=_label_for_symbol(sym),
        style_class=style_class,
    )


def _class_for_modification(change: SymbolChange) -> Optional[str]:
    if change.signature_changed:
        return "sigchange"
    if change.body_changed:
        return "bodychange"
    return None


def _collect_file_nodes(path: str, delta: AstDelta) -> list[MermaidNode]:
    nodes: list[MermaidNode] = []
    for sym in delta.added:
        nodes.append(_node_for_symbol(path, sym, "added"))
    for sym in delta.removed:
        nodes.append(_node_for_symbol(path, sym, "removed"))
    for change in delta.modified:
        style = _class_for_modification(change)
        if style is None:
            continue
        sym = change.new or change.old
        if sym is not None:
            nodes.append(_node_for_symbol(path, sym, style))
    return nodes


def _edges_to_bases(source_id: str, bases: tuple[str, ...]) -> list[MermaidEdge]:
    return [
        MermaidEdge(source=source_id, target=_ref_id(base), label="inherits", target_label=base)
        for base in bases
    ]


def _added_classes(delta: AstDelta) -> list[SymbolSig]:
    return [s for s in delta.added if s.kind == "class"]


def _inheritance_edges(path: str, delta: AstDelta) -> list[MermaidEdge]:
    edges: list[MermaidEdge] = []
    prefix = _path_prefix(path)
    for sym in _added_classes(delta):
        source_id = f"{prefix}__{_slugify(sym.qualified_name)}"
        edges.extend(_edges_to_bases(source_id, sym.bases))
    return edges


def _import_edges(path: str, delta: AstDelta) -> list[MermaidEdge]:
    prefix = _path_prefix(path)
    file_node_id = f"{prefix}__file"
    return [
        MermaidEdge(source=file_node_id, target=_ref_id(imp), label="imports", target_label=imp)
        for imp in delta.added_imports
    ]


def _collect_file_edges(path: str, delta: AstDelta) -> list[MermaidEdge]:
    return _inheritance_edges(path, delta) + _import_edges(path, delta)


def _degree(nodes: list[MermaidNode], edges: list[MermaidEdge]) -> dict[str, int]:
    counts: dict[str, int] = {n.node_id: 0 for n in nodes}
    for edge in edges:
        if edge.source in counts:
            counts[edge.source] += 1
        if edge.target in counts:
            counts[edge.target] += 1
    return counts


def _truncate(
    nodes: list[MermaidNode],
    edges: list[MermaidEdge],
    max_nodes: int,
) -> tuple[list[MermaidNode], list[MermaidEdge], bool]:
    if len(nodes) <= max_nodes:
        return nodes, edges, False
    counts = _degree(nodes, edges)
    ranked = sorted(nodes, key=lambda n: (-counts[n.node_id], n.node_id))
    kept = ranked[:max_nodes]
    # Drop only the links that touch a dropped symbol. A link from a file to an
    # import, or from a kept class to its base, ends at a name that was never a
    # symbol node, so it stays, as it does when nothing is truncated (GRIP#9000).
    dropped_ids = {n.node_id for n in ranked[max_nodes:]}
    kept_edges = [e for e in edges if e.source not in dropped_ids and e.target not in dropped_ids]
    return kept, kept_edges, True


def _eligible_files(scope: PrScope) -> list[ChangedFile]:
    return [f for f in scope.python_files() if not f.is_binary]


def build_diagram(
    scope: PrScope,
    ast_deltas: Mapping[str, AstDelta],
    *,
    max_nodes: int = 20,
) -> MermaidDiagram:
    """Build a Mermaid diagram from a PR scope and per-file AST deltas."""
    if scope.too_large:
        return MermaidDiagram(
            suppressed=True,
            suppress_reason=(
                f"PR too large ({scope.total_changed} lines changed > "
                f"{scope.size_limit} limit)"
            ),
        )
    nodes: list[MermaidNode] = []
    edges: list[MermaidEdge] = []
    for changed in _eligible_files(scope):
        delta = ast_deltas.get(changed.path)
        if delta is None:
            continue
        nodes.extend(_collect_file_nodes(changed.path, delta))
        edges.extend(_collect_file_edges(changed.path, delta))
    total_before = len(nodes)
    kept_nodes, kept_edges, was_truncated = _truncate(nodes, edges, max_nodes)
    return MermaidDiagram(
        nodes=kept_nodes,
        edges=kept_edges,
        truncated=was_truncated,
        total_symbols_before_truncation=total_before,
        links_hidden=len(edges) - len(kept_edges),
    )


__all__ = [
    "MermaidNode",
    "MermaidEdge",
    "MermaidDiagram",
    "build_diagram",
]
