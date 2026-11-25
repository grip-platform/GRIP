#!/usr/bin/env python3
"""Dependency mapper for GRIP lib/ modules.

Maps Python import dependencies between lib/ modules using AST parsing.
Pure Python, no external dependencies.

Usage:
    python3 lib/dependency_mapper.py metrics
    python3 lib/dependency_mapper.py cycles
    python3 lib/dependency_mapper.py layers
    python3 lib/dependency_mapper.py tree converge
    python3 lib/dependency_mapper.py module converge --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModuleInfo:
    """Information about a single Python module."""
    name: str
    path: str
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    fan_in: int = 0
    fan_out: int = 0


@dataclass
class DependencyEdge:
    """A directed edge from source to target."""
    source: str
    target: str


@dataclass
class DependencyGraph:
    """Complete dependency graph with analysis results."""
    modules: Dict[str, ModuleInfo] = field(default_factory=dict)
    edges: List[DependencyEdge] = field(default_factory=list)
    layers: List[List[str]] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)


@dataclass
class DependencyMetrics:
    """Summary metrics for the dependency graph."""
    total_modules: int = 0
    total_edges: int = 0
    avg_fan_in: float = 0.0
    avg_fan_out: float = 0.0
    max_depth: int = 0
    cycles_count: int = 0
    most_imported: List[Tuple[str, int]] = field(default_factory=list)
    most_importing: List[Tuple[str, int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_name_from_path(file_path: str, lib_dir: str) -> str:
    """Derive module name from file path relative to lib_dir's parent.

    e.g. /path/to/lib/converge.py -> converge
         /path/to/lib/converge_dna.py -> converge_dna
    """
    rel = os.path.relpath(file_path, lib_dir)
    name = rel.replace(os.sep, "/")
    if name.endswith(".py"):
        name = name[:-3]
    return name


def _normalize_module_name(raw: str) -> str:
    """Normalize a module name for comparison.

    Handles hyphens in filenames by converting to underscores.
    """
    return raw.replace("-", "_")


# ---------------------------------------------------------------------------
# Core: AST-based import parsing
# ---------------------------------------------------------------------------

def _collect_known_modules(lib_dir: str) -> Set[str]:
    """Collect known module names from a lib directory."""
    known: Set[str] = set()
    if os.path.isdir(lib_dir):
        for f in os.listdir(lib_dir):
            if f.endswith(".py") and not f.startswith("__"):
                mod = f[:-3]
                known.add(mod)
                known.add(_normalize_module_name(mod))
    return known


def _resolve_import_candidate(parts: List[str], known_modules: Set[str]) -> Optional[str]:
    """Resolve an import's dotted parts to a known internal module name.

    Returns the normalised module name if found, otherwise None.
    """
    candidate = None
    if parts[0] == "lib" and len(parts) > 1:
        candidate = parts[1]
    elif _normalize_module_name(parts[0]) in known_modules:
        candidate = parts[0]

    if candidate is not None:
        normalized = _normalize_module_name(candidate)
        if normalized in known_modules:
            return normalized
    return None


def parse_imports(file_path: str, lib_dir: Optional[str] = None) -> List[str]:
    """Parse a Python file and return list of internal lib/ module names it imports.

    Only returns imports that reference other modules within lib_dir.
    Uses ast.parse for reliable extraction.
    """
    if lib_dir is None:
        lib_dir = str(Path(file_path).parent)

    known_modules = _collect_known_modules(lib_dir)

    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return []

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []

    imports: List[str] = []
    seen: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            resolved = _resolve_import_candidate(parts, known_modules)
            if resolved is not None and resolved not in seen:
                seen.add(resolved)
                imports.append(resolved)

        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                resolved = _resolve_import_candidate(parts, known_modules)
                if resolved is not None and resolved not in seen:
                    seen.add(resolved)
                    imports.append(resolved)

    return sorted(imports)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(lib_dir: Optional[str] = None) -> DependencyGraph:
    """Build a complete dependency graph from all Python modules in lib_dir."""
    if lib_dir is None:
        lib_dir = str(Path(__file__).parent)

    graph = DependencyGraph()

    # Discover all Python modules
    if not os.path.isdir(lib_dir):
        return graph

    py_files: List[Tuple[str, str]] = []
    for f in sorted(os.listdir(lib_dir)):
        if f.endswith(".py") and not f.startswith("__"):
            full = os.path.join(lib_dir, f)
            if os.path.isfile(full):
                mod_name = _normalize_module_name(f[:-3])
                py_files.append((mod_name, full))

    # Create ModuleInfo for each
    for mod_name, full_path in py_files:
        graph.modules[mod_name] = ModuleInfo(
            name=mod_name,
            path=full_path,
        )

    # Parse imports and build edges
    for mod_name, full_path in py_files:
        imported = parse_imports(full_path, lib_dir)
        internal = [m for m in imported if m in graph.modules and m != mod_name]
        graph.modules[mod_name].imports = sorted(internal)
        graph.modules[mod_name].fan_out = len(internal)

        for target in internal:
            graph.edges.append(DependencyEdge(source=mod_name, target=target))
            graph.modules[target].imported_by.append(mod_name)

    # Compute fan_in
    for mod in graph.modules.values():
        mod.imported_by = sorted(set(mod.imported_by))
        mod.fan_in = len(mod.imported_by)

    # Detect cycles
    graph.cycles = detect_cycles(graph)

    # Compute layers
    graph.layers = compute_layers(graph)

    return graph


# ---------------------------------------------------------------------------
# Cycle detection (DFS-based with Tarjan's SCC)
# ---------------------------------------------------------------------------

def detect_cycles(graph: DependencyGraph) -> List[List[str]]:
    """Detect circular dependencies using Tarjan's SCC algorithm.

    Returns list of cycles (each cycle = list of module names).
    Only SCCs with size > 1 are reported as cycles.
    """
    index_counter = [0]
    stack: List[str] = []
    lowlink: Dict[str, int] = {}
    index: Dict[str, int] = {}
    on_stack: Set[str] = set()
    sccs: List[List[str]] = []

    adj: Dict[str, List[str]] = {}
    for mod_name, info in graph.modules.items():
        adj[mod_name] = list(info.imports)

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in adj.get(v, []):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc: List[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(sorted(scc))

    for v in sorted(graph.modules.keys()):
        if v not in index:
            strongconnect(v)

    return sorted(sccs)


# ---------------------------------------------------------------------------
# Topological layers (Kahn's algorithm)
# ---------------------------------------------------------------------------

def compute_layers(graph: DependencyGraph) -> List[List[str]]:
    """Compute topological layers using Kahn's algorithm.

    Modules with no internal dependencies are in layer 0.
    Cyclic modules are placed in the last layer.
    """
    if not graph.modules:
        return []

    # Find cycle members
    cycle_members: Set[str] = set()
    cycles = graph.cycles if graph.cycles else detect_cycles(graph)
    for cycle in cycles:
        cycle_members.update(cycle)

    # Build adjacency excluding cycle members
    non_cycle = {m for m in graph.modules if m not in cycle_members}
    in_degree: Dict[str, int] = {m: 0 for m in non_cycle}
    adj: Dict[str, List[str]] = {m: [] for m in non_cycle}

    for edge in graph.edges:
        if edge.source in non_cycle and edge.target in non_cycle:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    # Kahn's algorithm
    layers: List[List[str]] = []
    queue = sorted([m for m, d in in_degree.items() if d == 0])

    while queue:
        layers.append(sorted(queue))
        next_queue: List[str] = []
        for node in queue:
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)
        queue = sorted(next_queue)

    # Remaining non-cycle nodes (shouldn't happen if cycles are excluded, but safety)
    placed = set()
    for layer in layers:
        placed.update(layer)
    remaining = sorted(non_cycle - placed)
    if remaining:
        layers.append(remaining)

    # Cycle members go in the last layer
    if cycle_members:
        layers.append(sorted(cycle_members))

    return layers


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(graph: DependencyGraph) -> DependencyMetrics:
    """Compute summary metrics from a dependency graph."""
    n = len(graph.modules)
    if n == 0:
        return DependencyMetrics()

    total_fan_in = sum(m.fan_in for m in graph.modules.values())
    total_fan_out = sum(m.fan_out for m in graph.modules.values())

    # Most imported (highest fan-in)
    by_fan_in = sorted(
        [(m.name, m.fan_in) for m in graph.modules.values()],
        key=lambda x: (-x[1], x[0]),
    )
    most_imported = by_fan_in[:5]

    # Most importing (highest fan-out)
    by_fan_out = sorted(
        [(m.name, m.fan_out) for m in graph.modules.values()],
        key=lambda x: (-x[1], x[0]),
    )
    most_importing = by_fan_out[:5]

    return DependencyMetrics(
        total_modules=n,
        total_edges=len(graph.edges),
        avg_fan_in=round(total_fan_in / n, 2),
        avg_fan_out=round(total_fan_out / n, 2),
        max_depth=len(graph.layers),
        cycles_count=len(graph.cycles),
        most_imported=most_imported,
        most_importing=most_importing,
    )


def instability_score(graph: DependencyGraph) -> float:
    """Compute average instability across all modules (Robert C. Martin).

    I = Ce / (Ca + Ce) per module, where Ca = fan_in, Ce = fan_out.
    Returns average I in [0.0, 1.0]. Lower = more stable = better SOLID.
    Modules with zero coupling are excluded (isolated, no signal).
    Returns 0.0 for empty graphs.
    """
    if not graph.modules:
        return 0.0
    instabilities = []
    for mod in graph.modules.values():
        total = mod.fan_in + mod.fan_out
        if total > 0:
            instabilities.append(mod.fan_out / total)
    if not instabilities:
        return 0.0
    return sum(instabilities) / len(instabilities)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_tree(graph: DependencyGraph, root_module: str, max_depth: int = 4) -> str:
    """Render an ASCII tree from a root module showing its dependencies."""
    if root_module not in graph.modules:
        return f"Module '{root_module}' not found."

    lines: List[str] = []
    visited: Set[str] = set()

    def _walk(mod: str, prefix: str, is_last: bool, depth: int) -> None:
        connector = "`-- " if is_last else "|-- "
        if depth == 0:
            lines.append(mod)
        else:
            lines.append(f"{prefix}{connector}{mod}")

        if mod in visited:
            if graph.modules.get(mod, ModuleInfo(name=mod, path="")).imports:
                new_prefix = prefix + ("    " if is_last else "|   ")
                lines.append(f"{new_prefix}(circular ref)")
            return

        if depth >= max_depth:
            deps = graph.modules.get(mod, ModuleInfo(name=mod, path="")).imports
            if deps:
                new_prefix = prefix + ("    " if is_last else "|   ")
                lines.append(f"{new_prefix}... ({len(deps)} deps)")
            return

        visited.add(mod)
        info = graph.modules.get(mod)
        if not info:
            return

        deps = info.imports
        for i, dep in enumerate(deps):
            is_dep_last = i == len(deps) - 1
            new_prefix = prefix + ("    " if is_last else "|   ")
            _walk(dep, new_prefix, is_dep_last, depth + 1)

        visited.discard(mod)

    _walk(root_module, "", True, 0)
    return "\n".join(lines)


def format_layers(graph: DependencyGraph) -> str:
    """Format layer visualization."""
    if not graph.layers:
        return "No layers computed."

    lines: List[str] = []
    for i, layer in enumerate(graph.layers):
        label = f"Layer {i}"
        # Check if this is the cycle layer
        cycle_members: Set[str] = set()
        for cycle in graph.cycles:
            cycle_members.update(cycle)
        if all(m in cycle_members for m in layer) and cycle_members:
            label += " (cyclic)"

        lines.append(f"{label}:")
        for mod in layer:
            fan = graph.modules[mod].fan_in if mod in graph.modules else 0
            lines.append(f"  {mod} (fan-in: {fan})")
    return "\n".join(lines)


def format_metrics(metrics: DependencyMetrics) -> str:
    """Format metrics summary."""
    lines = [
        "Dependency Metrics",
        "=" * 40,
        f"Total modules:    {metrics.total_modules}",
        f"Total edges:      {metrics.total_edges}",
        f"Avg fan-in:       {metrics.avg_fan_in}",
        f"Avg fan-out:      {metrics.avg_fan_out}",
        f"Max depth:        {metrics.max_depth}",
        f"Cycles:           {metrics.cycles_count}",
        "",
        "Most imported (fan-in):",
    ]
    for name, count in metrics.most_imported:
        lines.append(f"  {name}: {count}")

    lines.append("")
    lines.append("Most importing (fan-out):")
    for name, count in metrics.most_importing:
        lines.append(f"  {name}: {count}")

    return "\n".join(lines)


def format_cycles(cycles: List[List[str]]) -> str:
    """Format cycle listing."""
    if not cycles:
        return "No circular dependencies detected."

    lines = [f"Found {len(cycles)} circular dependency group(s):", ""]
    for i, cycle in enumerate(cycles, 1):
        lines.append(f"  Cycle {i}: {' -> '.join(cycle)} -> {cycle[0]}")
    return "\n".join(lines)


def format_module(graph: DependencyGraph, module_name: str) -> str:
    """Format detailed info for a single module."""
    if module_name not in graph.modules:
        return f"Module '{module_name}' not found."

    info = graph.modules[module_name]
    lines = [
        f"Module: {info.name}",
        f"Path: {info.path}",
        f"Fan-out: {info.fan_out} (imports {info.fan_out} internal modules)",
        f"Fan-in: {info.fan_in} (imported by {info.fan_in} modules)",
        "",
        "Imports:",
    ]
    for imp in info.imports:
        lines.append(f"  -> {imp}")
    if not info.imports:
        lines.append("  (none)")

    lines.append("")
    lines.append("Imported by:")
    for imp in info.imported_by:
        lines.append(f"  <- {imp}")
    if not info.imported_by:
        lines.append("  (none)")

    # Coupling assessment
    coupling = info.fan_in + info.fan_out
    lines.append("")
    if coupling == 0:
        lines.append("Status: ISOLATED (no internal dependencies)")
    elif coupling > 10:
        lines.append(f"Status: HIGHLY COUPLED (coupling score: {coupling})")
    else:
        lines.append(f"Status: Normal (coupling score: {coupling})")

    return "\n".join(lines)


def format_json(graph: DependencyGraph) -> str:
    """Format the full graph as JSON."""
    data = {
        "modules": {
            name: {
                "path": info.path,
                "imports": info.imports,
                "imported_by": info.imported_by,
                "fan_in": info.fan_in,
                "fan_out": info.fan_out,
            }
            for name, info in sorted(graph.modules.items())
        },
        "edges": [
            {"source": e.source, "target": e.target}
            for e in graph.edges
        ],
        "layers": graph.layers,
        "cycles": graph.cycles,
        "metrics": {
            "total_modules": len(graph.modules),
            "total_edges": len(graph.edges),
            "cycles_count": len(graph.cycles),
            "layer_count": len(graph.layers),
        },
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_metrics(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'metrics' subcommand."""
    metrics = compute_metrics(graph)
    if args.as_json:
        print(json.dumps({
            "total_modules": metrics.total_modules,
            "total_edges": metrics.total_edges,
            "avg_fan_in": metrics.avg_fan_in,
            "avg_fan_out": metrics.avg_fan_out,
            "max_depth": metrics.max_depth,
            "cycles_count": metrics.cycles_count,
            "most_imported": metrics.most_imported,
            "most_importing": metrics.most_importing,
        }, indent=2))
    else:
        print(format_metrics(metrics))


def _cmd_cycles(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'cycles' subcommand."""
    if args.as_json:
        print(json.dumps(graph.cycles, indent=2))
    else:
        print(format_cycles(graph.cycles))


def _cmd_layers(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'layers' subcommand."""
    if args.as_json:
        print(json.dumps(graph.layers, indent=2))
    else:
        print(format_layers(graph))


def _cmd_tree(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'tree' subcommand."""
    if args.as_json:
        print(json.dumps({
            "root": args.root,
            "tree": format_tree(graph, args.root, args.depth),
        }, indent=2))
    else:
        print(format_tree(graph, args.root, args.depth))


def _cmd_module(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'module' subcommand."""
    if args.as_json:
        if args.name in graph.modules:
            info = graph.modules[args.name]
            print(json.dumps({
                "name": info.name,
                "path": info.path,
                "imports": info.imports,
                "imported_by": info.imported_by,
                "fan_in": info.fan_in,
                "fan_out": info.fan_out,
            }, indent=2))
        else:
            print(json.dumps({"error": f"Module '{args.name}' not found"}))
    else:
        print(format_module(graph, args.name))


def _cmd_highly_coupled(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'highly-coupled' subcommand."""
    coupled = sorted(
        [(m.name, m.fan_in + m.fan_out) for m in graph.modules.values()
         if m.fan_in + m.fan_out > 10],
        key=lambda x: (-x[1], x[0]),
    )
    if args.as_json:
        print(json.dumps(coupled, indent=2))
    else:
        if coupled:
            print("Highly coupled modules (coupling > 10):\n")
            for name, score in coupled:
                print(f"  {name}: {score}")
        else:
            print("No highly coupled modules found.")


def _cmd_isolated(graph: DependencyGraph, args: argparse.Namespace) -> None:
    """Handle 'isolated' subcommand."""
    isolated = sorted(
        m.name for m in graph.modules.values()
        if m.fan_in == 0 and m.fan_out == 0
    )
    if args.as_json:
        print(json.dumps(isolated, indent=2))
    else:
        if isolated:
            print(f"Isolated modules ({len(isolated)}):\n")
            for name in isolated:
                print(f"  {name}")
        else:
            print("No isolated modules found.")


# Command dispatch table
_COMMANDS: Dict[str, object] = {
    "metrics": _cmd_metrics,
    "cycles": _cmd_cycles,
    "layers": _cmd_layers,
    "tree": _cmd_tree,
    "module": _cmd_module,
    "json": lambda g, a: print(format_json(g)),
    "highly-coupled": _cmd_highly_coupled,
    "isolated": _cmd_isolated,
}


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GRIP Dependency Mapper -- map Python import dependencies in lib/",
    )
    parser.add_argument(
        "--lib-dir",
        default=None,
        help="Path to lib/ directory (default: auto-detect)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output in JSON format",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("metrics", help="Show dependency metrics summary")
    sub.add_parser("cycles", help="List circular dependencies")
    sub.add_parser("layers", help="Show topological layers")

    tree_p = sub.add_parser("tree", help="Show ASCII dependency tree")
    tree_p.add_argument("root", help="Root module name")
    tree_p.add_argument("--depth", type=int, default=4, help="Max tree depth")

    mod_p = sub.add_parser("module", help="Show details for a module")
    mod_p.add_argument("name", help="Module name")

    sub.add_parser("json", help="Full graph as JSON")
    sub.add_parser("highly-coupled", help="List highly coupled modules (coupling > 10)")
    sub.add_parser("isolated", help="List isolated modules (no dependencies)")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    lib_dir = args.lib_dir
    if lib_dir is None:
        lib_dir = str(Path(__file__).parent)

    graph = build_graph(lib_dir)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    handler(graph, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
