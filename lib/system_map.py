"""GRIP System Map -- module dependency and data flow visualisation.

Discovers all Python modules in lib/, extracts import relationships via AST,
assigns each module to a dependency layer (0=Data through 4=Meta), and renders
layered ASCII diagrams showing the architecture at a glance.

Usage (CLI):
    python3 lib/system_map.py map              # full layered ASCII map
    python3 lib/system_map.py map --layer 2    # single layer
    python3 lib/system_map.py flow <module>    # data flow to/from module
    python3 lib/system_map.py deps <module>    # dependency tree
    python3 lib/system_map.py stats            # per-layer counts + totals
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModuleNode:
    """A single Python module inside lib/."""
    name: str
    layer: int = -1
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SystemLayer:
    """A horizontal layer in the dependency graph."""
    number: int
    name: str
    modules: List[ModuleNode] = field(default_factory=list)


@dataclass
class DataFlow:
    """A directed data-flow edge between two modules."""
    source: str
    target: str
    data_type: str = ""


@dataclass
class SystemMap:
    """Complete system map: layers, nodes, and flows."""
    layers: Dict[int, SystemLayer] = field(default_factory=dict)
    nodes: Dict[str, ModuleNode] = field(default_factory=dict)
    flows: List[DataFlow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer names
# ---------------------------------------------------------------------------

LAYER_NAMES: Dict[int, str] = {
    0: "Data",
    1: "Analytical",
    2: "Composite",
    3: "Synthesis",
    4: "Meta",
}


# ---------------------------------------------------------------------------
# Discovery and extraction
# ---------------------------------------------------------------------------

def discover_modules(lib_dir: str) -> List[str]:
    """Find all .py files in *lib_dir* and return their stem names sorted."""
    lib_path = Path(lib_dir)
    if not lib_path.is_dir():
        return []
    modules: List[str] = []
    for entry in sorted(lib_path.iterdir()):
        if entry.suffix == ".py" and entry.is_file():
            modules.append(entry.stem)
    return modules


def extract_imports(filepath: str, known_modules: Optional[Set[str]] = None) -> List[str]:
    """Use AST to extract imports that reference other lib/ modules.

    Only ``import <name>`` and ``from <name> import ...`` where *name* is in
    *known_modules* are returned.  If *known_modules* is None every import is
    returned (useful for testing).
    """
    path = Path(filepath)
    if not path.is_file():
        return []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError):
        return []

    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if known_modules is None or top in known_modules:
                    if top not in imports:
                        imports.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if known_modules is None or top in known_modules:
                    if top not in imports:
                        imports.append(top)
    return sorted(imports)


# ---------------------------------------------------------------------------
# Layer assignment via topological depth
# ---------------------------------------------------------------------------

def _build_adjacency(nodes: Dict[str, ModuleNode]) -> Dict[str, Set[str]]:
    """Adjacency list: module -> set of modules it imports (within lib/)."""
    adj: Dict[str, Set[str]] = {}
    for name, node in nodes.items():
        adj[name] = {imp for imp in node.imports if imp in nodes}
    return adj


def _topological_depth(adj: Dict[str, Set[str]]) -> Dict[str, int]:
    """Compute the longest-path depth for each node (0 = leaf/no deps)."""
    cache: Dict[str, int] = {}
    visiting: Set[str] = set()

    def depth(name: str) -> int:
        if name in cache:
            return cache[name]
        if name in visiting:
            # cycle -- break it
            return 0
        visiting.add(name)
        deps = adj.get(name, set())
        d = 0
        if deps:
            d = max(depth(dep) for dep in deps) + 1
        visiting.discard(name)
        cache[name] = d
        return d

    for n in adj:
        depth(n)
    return cache


def assign_layers(nodes: Dict[str, ModuleNode], max_layers: int = 5) -> Dict[str, ModuleNode]:
    """Assign a layer (0..4) to each module based on topological depth.

    Modules at depth 0 are Layer 0 (Data).  The maximum depth is clamped to
    *max_layers - 1* (default 4 = Meta).
    """
    adj = _build_adjacency(nodes)
    depths = _topological_depth(adj)
    if not depths:
        return nodes

    max_depth = max(depths.values()) if depths else 0
    for name, d in depths.items():
        if max_depth == 0:
            layer = 0
        else:
            # Scale depth into 0..max_layers-1
            layer = min(int(d / (max_depth + 1) * max_layers), max_layers - 1)
        nodes[name].layer = layer
    return nodes


# ---------------------------------------------------------------------------
# Map construction
# ---------------------------------------------------------------------------

def build_system_map(lib_dir: str) -> SystemMap:
    """Discover modules, extract imports, assign layers, return SystemMap."""
    module_names = discover_modules(lib_dir)
    known = set(module_names)
    lib_path = Path(lib_dir)

    # Build nodes
    nodes: Dict[str, ModuleNode] = {}
    for name in module_names:
        filepath = lib_path / f"{name}.py"
        imports = extract_imports(str(filepath), known)
        # Exclude self-imports
        imports = [i for i in imports if i != name]
        nodes[name] = ModuleNode(name=name, imports=imports)

    # Populate imported_by (reverse edges)
    for name, node in nodes.items():
        for imp in node.imports:
            if imp in nodes:
                if name not in nodes[imp].imported_by:
                    nodes[imp].imported_by.append(name)

    # Sort imported_by for determinism
    for node in nodes.values():
        node.imported_by.sort()

    # Assign layers
    assign_layers(nodes)

    # Build layer objects
    layers: Dict[int, SystemLayer] = {}
    for i in range(5):
        layer_name = LAYER_NAMES.get(i, f"Layer {i}")
        layer_modules = sorted(
            [n for n in nodes.values() if n.layer == i],
            key=lambda m: m.name,
        )
        layers[i] = SystemLayer(number=i, name=layer_name, modules=layer_modules)

    # Build flows
    flows: List[DataFlow] = []
    for name, node in sorted(nodes.items()):
        for imp in node.imports:
            if imp in nodes:
                flows.append(DataFlow(source=imp, target=name, data_type="import"))

    return SystemMap(layers=layers, nodes=nodes, flows=flows)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_map(system_map: SystemMap, width: int = 80) -> str:
    """Render a layered ASCII diagram of the full system map."""
    lines: List[str] = []
    separator = "-" * width

    # Render from highest layer to lowest
    for layer_num in sorted(system_map.layers.keys(), reverse=True):
        layer = system_map.layers[layer_num]
        if not layer.modules:
            continue
        header = f"Layer {layer_num} [{layer.name}]"
        lines.append(header)
        lines.append(separator)

        # Render module names in rows of up to width
        module_names = [m.name for m in layer.modules]
        row: List[str] = []
        row_len = 0
        for mname in module_names:
            token = mname
            addition = len(token) + (4 if row else 0)  # " -- " separator
            if row and row_len + addition > width:
                lines.append("  " + " -- ".join(row))
                row = [token]
                row_len = len(token) + 2
            else:
                row.append(token)
                row_len += addition
        if row:
            lines.append("  " + " -- ".join(row))

        # Connection indicators to lower layers
        connected_below: Set[str] = set()
        for mod in layer.modules:
            for imp in mod.imports:
                if imp in system_map.nodes and system_map.nodes[imp].layer < layer_num:
                    connected_below.add(imp)
        if connected_below:
            targets = sorted(connected_below)
            connector_line = "  " + " | ".join(f"v {t}" for t in targets[:8])
            if len(targets) > 8:
                connector_line += f" (+{len(targets) - 8} more)"
            lines.append(connector_line)

        lines.append("")

    return "\n".join(lines)


def render_map_layer(system_map: SystemMap, layer_num: int, width: int = 80) -> str:
    """Render a single layer from the system map."""
    if layer_num not in system_map.layers:
        return f"Layer {layer_num} not found."
    layer = system_map.layers[layer_num]
    if not layer.modules:
        return f"Layer {layer_num} [{layer.name}]: (empty)"

    lines: List[str] = []
    header = f"Layer {layer_num} [{layer.name}]"
    lines.append(header)
    lines.append("-" * width)

    for mod in layer.modules:
        deps = ", ".join(mod.imports) if mod.imports else "(none)"
        users = ", ".join(mod.imported_by) if mod.imported_by else "(none)"
        lines.append(f"  {mod.name}")
        lines.append(f"    imports: {deps}")
        lines.append(f"    used by: {users}")

    return "\n".join(lines)


def render_flow(module_name: str, system_map: SystemMap) -> str:
    """Show data flow to/from a specific module."""
    if module_name not in system_map.nodes:
        return f"Module '{module_name}' not found."
    node = system_map.nodes[module_name]

    lines: List[str] = []
    lines.append(f"Data flow for: {module_name} (Layer {node.layer})")
    lines.append("")

    # Inbound flows (modules this one imports = data sources)
    inbound = [f for f in system_map.flows if f.target == module_name]
    lines.append("Inbound (imports from):")
    if inbound:
        for flow in inbound:
            src = system_map.nodes.get(flow.source)
            src_layer = f"L{src.layer}" if src else "?"
            lines.append(f"  {flow.source} [{src_layer}] -> {module_name}")
    else:
        lines.append("  (none)")

    lines.append("")

    # Outbound flows (modules that import this one = data consumers)
    outbound = [f for f in system_map.flows if f.source == module_name]
    lines.append("Outbound (used by):")
    if outbound:
        for flow in outbound:
            tgt = system_map.nodes.get(flow.target)
            tgt_layer = f"L{tgt.layer}" if tgt else "?"
            lines.append(f"  {module_name} -> {flow.target} [{tgt_layer}]")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def render_deps(module_name: str, system_map: SystemMap, max_depth: int = 10) -> str:
    """Show the direct and transitive dependency tree for a module."""
    if module_name not in system_map.nodes:
        return f"Module '{module_name}' not found."

    lines: List[str] = []
    lines.append(f"Dependency tree for: {module_name}")
    lines.append("")

    visited: Set[str] = set()

    def _walk(name: str, indent: int) -> None:
        if name in visited:
            lines.append("  " * indent + f"{name} (circular)")
            return
        if indent > max_depth:
            lines.append("  " * indent + f"{name} (max depth)")
            return
        visited.add(name)
        node = system_map.nodes.get(name)
        if not node:
            lines.append("  " * indent + f"{name} (external)")
            return
        marker = f"[L{node.layer}]"
        lines.append("  " * indent + f"{name} {marker}")
        for imp in sorted(node.imports):
            if imp in system_map.nodes:
                _walk(imp, indent + 1)
        visited.discard(name)

    _walk(module_name, 0)
    return "\n".join(lines)


def format_stats(system_map: SystemMap) -> str:
    """Module count per layer, total edges, summary statistics."""
    lines: List[str] = []
    lines.append("System Map Statistics")
    lines.append("=" * 40)

    total_modules = len(system_map.nodes)
    total_edges = len(system_map.flows)

    for layer_num in sorted(system_map.layers.keys()):
        layer = system_map.layers[layer_num]
        count = len(layer.modules)
        pct = (count / total_modules * 100) if total_modules > 0 else 0
        lines.append(f"  Layer {layer_num} [{layer.name:12s}]: {count:3d} modules ({pct:5.1f}%)")

    lines.append("-" * 40)
    lines.append(f"  Total modules: {total_modules}")
    lines.append(f"  Total edges:   {total_edges}")

    if total_modules > 0:
        avg_deps = total_edges / total_modules
        lines.append(f"  Avg deps/module: {avg_deps:.1f}")

    # Most connected modules
    if system_map.nodes:
        by_imports = sorted(system_map.nodes.values(), key=lambda n: len(n.imports), reverse=True)
        by_users = sorted(system_map.nodes.values(), key=lambda n: len(n.imported_by), reverse=True)

        lines.append("")
        lines.append("Most dependencies (imports):")
        for mod in by_imports[:5]:
            if mod.imports:
                lines.append(f"  {mod.name}: {len(mod.imports)}")

        lines.append("")
        lines.append("Most depended on (imported by):")
        for mod in by_users[:5]:
            if mod.imported_by:
                lines.append(f"  {mod.name}: {len(mod.imported_by)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_lib_dir() -> str:
    """Find the lib/ directory relative to this script or CWD."""
    # If this file is inside lib/, use its parent
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == "lib":
        return str(script_dir)
    # Otherwise check CWD/lib
    cwd_lib = Path.cwd() / "lib"
    if cwd_lib.is_dir():
        return str(cwd_lib)
    return str(script_dir)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GRIP System Map -- module dependency visualisation",
    )
    sub = parser.add_subparsers(dest="command")

    # map
    map_parser = sub.add_parser("map", help="Full layered ASCII map")
    map_parser.add_argument("--layer", type=int, default=None, help="Show single layer")
    map_parser.add_argument("--width", type=int, default=80, help="Output width")

    # flow
    flow_parser = sub.add_parser("flow", help="Data flow to/from a module")
    flow_parser.add_argument("module", help="Module name (stem, no .py)")

    # deps
    deps_parser = sub.add_parser("deps", help="Dependency tree for a module")
    deps_parser.add_argument("module", help="Module name (stem, no .py)")

    # stats
    sub.add_parser("stats", help="Per-layer counts and totals")

    # --lib-dir global option
    parser.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    args = parser.parse_args(argv)
    lib_dir = args.lib_dir or _resolve_lib_dir()

    if not args.command:
        parser.print_help()
        return 1

    smap = build_system_map(lib_dir)

    if args.command == "map":
        if args.layer is not None:
            print(render_map_layer(smap, args.layer, width=args.width))
        else:
            print(render_map(smap, width=args.width))
    elif args.command == "flow":
        print(render_flow(args.module, smap))
    elif args.command == "deps":
        print(render_deps(args.module, smap))
    elif args.command == "stats":
        print(format_stats(smap))

    return 0


if __name__ == "__main__":
    sys.exit(main())
