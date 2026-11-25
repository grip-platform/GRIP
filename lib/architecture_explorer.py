"""Architecture explorer - generates interactive architecture diagrams from Python code.

Scans a Python codebase, traces import relationships, identifies modules/packages,
and generates architecture maps showing how everything connects.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_EXCLUDE = ["node_modules", "venv", "__pycache__", ".git", "dist", "build"]


@dataclass
class ModuleInfo:
    """Information about a single Python module."""

    name: str
    path: str
    imports: list = field(default_factory=list)
    imported_by: list = field(default_factory=list)
    functions: int = 0
    classes: int = 0
    lines: int = 0
    category: str = "core"


@dataclass
class ArchitectureMap:
    """Full architecture analysis result."""

    modules: list = field(default_factory=list)
    packages: list = field(default_factory=list)
    connections: list = field(default_factory=list)
    hub_modules: list = field(default_factory=list)
    orphan_modules: list = field(default_factory=list)
    layers: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    generated_at: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_imports(source_code: str) -> list[str]:
    """Extract import statements from Python source code.

    Returns a list of imported module names. Handles:
    - import foo
    - from foo import bar
    - from foo.bar import baz
    - from . import x (relative imports)
    - import foo as f (aliases)
    - Multi-line imports
    - Ignores comments that look like imports
    """
    imports: list[str] = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
            elif node.level and node.level > 0:
                # Relative import like `from . import x` or `from .. import y`
                dots = "." * node.level
                for alias in node.names:
                    imports.append(f"{dots}{alias.name}")
    return imports


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def categorize_module(name: str, path: str, imports: list) -> str:
    """Classify a module into a category based on name, path, and imports.

    Categories:
    - "test" - Test files (name starts with test_ or ends with _test)
    - "config" - Configuration (has settings/config in name)
    - "api" - API/interface layer (has route/endpoint/handler in name)
    - "model" - Data models (has dataclass/model/schema in name)
    - "util" - Utility/helper (has util/helper/common/shared in name)
    - "core" - Default for everything else
    """
    basename = os.path.basename(path).replace(".py", "").lower()
    name_lower = name.lower()
    path_lower = path.lower()

    # Test files
    if basename.startswith("test_") or basename.endswith("_test"):
        return "test"
    if "/tests/" in path_lower or "/test/" in path_lower:
        return "test"

    # Config files
    config_markers = ["config", "settings", "constants", "env"]
    if any(m in basename for m in config_markers):
        return "config"

    # API files
    api_markers = ["route", "endpoint", "handler", "view", "api", "server", "controller"]
    if any(m in basename for m in api_markers):
        return "api"

    # Model files
    model_markers = ["model", "schema", "entity", "dataclass", "dto"]
    if any(m in basename for m in model_markers):
        return "model"

    # Util files
    util_markers = ["util", "helper", "common", "shared", "misc", "tool", "lib"]
    if any(m in basename for m in util_markers):
        return "util"

    return "core"


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_module(path: str) -> ModuleInfo:
    """Analyze a single Python file and return ModuleInfo."""
    path = os.path.abspath(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except (OSError, IOError):
        return ModuleInfo(
            name=_path_to_module_name(path),
            path=path,
            imports=[],
            imported_by=[],
            functions=0,
            classes=0,
            lines=0,
            category="core",
        )

    lines = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    imports = parse_imports(source)
    name = _path_to_module_name(path)

    functions = 0
    classes = 0
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += 1
            elif isinstance(node, ast.ClassDef):
                classes += 1
    except SyntaxError:
        pass

    category = categorize_module(name, path, imports)

    return ModuleInfo(
        name=name,
        path=path,
        imports=imports,
        imported_by=[],
        functions=functions,
        classes=classes,
        lines=lines,
        category=category,
    )


def _path_to_module_name(path: str) -> str:
    """Convert a file path to a dotted module name."""
    p = Path(path)
    parts = []
    # Walk up to find a non-package directory or root
    current = p
    if current.name == "__init__.py":
        current = current.parent
        parts.append(current.name)
    else:
        parts.append(current.stem)
    current = current.parent

    # Walk up through packages (directories with __init__.py)
    while current != current.parent:
        init = current / "__init__.py"
        if init.exists():
            parts.append(current.name)
            current = current.parent
        else:
            break

    parts.reverse()
    return ".".join(parts)


def scan_directory(path: str, exclude: list = None) -> list[ModuleInfo]:
    """Scan all .py files in a directory tree and return ModuleInfo list."""
    if exclude is None:
        exclude = list(DEFAULT_EXCLUDE)

    path = os.path.abspath(path)
    modules: list[ModuleInfo] = []

    for root, dirs, files in os.walk(path):
        # Filter excluded directories in-place
        dirs[:] = [d for d in dirs if d not in exclude]

        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            mod = scan_module(fpath)
            modules.append(mod)

    return modules


# ---------------------------------------------------------------------------
# Connection building
# ---------------------------------------------------------------------------


def build_connections(modules: list) -> list[tuple]:
    """Build import relationship graph from module list.

    Returns list of (from_module, to_module, import_type) tuples.
    import_type is "direct" for `import X` / `from X import Y` and
    "relative" for relative imports.
    """
    # Build lookup: module name -> ModuleInfo, also basename variants
    name_set = {m.name for m in modules}
    basename_map: dict[str, str] = {}
    for m in modules:
        parts = m.name.split(".")
        basename_map[parts[-1]] = m.name

    connections: list[tuple] = []
    imported_by_map: dict[str, list[str]] = {m.name: [] for m in modules}

    for mod in modules:
        for imp in mod.imports:
            import_type = "relative" if imp.startswith(".") else "direct"
            target_name = imp.lstrip(".")

            # Try exact match first, then basename match
            resolved = None
            if target_name in name_set:
                resolved = target_name
            elif target_name in basename_map:
                resolved = basename_map[target_name]
            else:
                # Try prefix match (e.g., "foo.bar" matches "foo.bar.baz" module group)
                for known in name_set:
                    if known.startswith(target_name + ".") or target_name.startswith(known + "."):
                        resolved = known
                        break

            if resolved and resolved != mod.name:
                conn = (mod.name, resolved, import_type)
                if conn not in connections:
                    connections.append(conn)
                    if resolved in imported_by_map:
                        if mod.name not in imported_by_map[resolved]:
                            imported_by_map[resolved].append(mod.name)

    # Update imported_by on module objects
    for mod in modules:
        mod.imported_by = imported_by_map.get(mod.name, [])

    return connections


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_hubs(modules: list, threshold: int = 5) -> list[str]:
    """Find hub modules with more than `threshold` total connections."""
    hubs = []
    for m in modules:
        total = len(m.imports) + len(m.imported_by)
        if total > threshold:
            hubs.append(m.name)
    return sorted(hubs, key=lambda n: next(
        (len(m.imports) + len(m.imported_by) for m in modules if m.name == n), 0
    ), reverse=True)


def detect_orphans(modules: list) -> list[str]:
    """Find modules with zero connections (no imports and not imported by anyone)."""
    orphans = []
    for m in modules:
        if not m.imports and not m.imported_by:
            orphans.append(m.name)
    return sorted(orphans)


def detect_layers(modules: list) -> dict:
    """Auto-detect architectural layers based on import depth.

    Layer 0 (Foundation): Only stdlib imports or no internal imports
    Layer 1 (Core): Imports from Layer 0 modules
    Layer 2 (Service): Imports from Layer 1 modules
    Layer 3 (Interface): Imports from Layer 2 modules, typically API/CLI
    """
    name_set = {m.name for m in modules}
    mod_map = {m.name: m for m in modules}

    # Determine which imports are internal (within this codebase)
    internal_imports: dict[str, set] = {}
    for m in modules:
        internal = set()
        for imp in m.imports:
            clean = imp.lstrip(".")
            if clean in name_set:
                internal.add(clean)
            else:
                # Check partial matches
                for known in name_set:
                    if known.startswith(clean + ".") or clean.startswith(known + "."):
                        internal.add(known)
                        break
        internal_imports[m.name] = internal

    # Assign layers iteratively
    layers: dict[str, int] = {}
    assigned = set()

    # Layer 0: no internal imports
    for m in modules:
        if not internal_imports[m.name]:
            layers[m.name] = 0
            assigned.add(m.name)

    # Iteratively assign higher layers
    for layer_num in range(1, 4):
        for m in modules:
            if m.name in assigned:
                continue
            deps = internal_imports[m.name]
            if deps and all(d in assigned for d in deps):
                max_dep_layer = max(layers.get(d, 0) for d in deps)
                layers[m.name] = max_dep_layer + 1
                assigned.add(m.name)

    # Anything unassigned goes to highest layer (likely circular deps)
    for m in modules:
        if m.name not in layers:
            layers[m.name] = 3

    # Group by layer
    layer_names = {
        0: "foundation",
        1: "core",
        2: "service",
        3: "interface",
    }

    result: dict[str, list] = {}
    for layer_num, layer_label in layer_names.items():
        members = sorted(n for n, l in layers.items() if l == layer_num)
        if members:
            result[layer_label] = members

    return result


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------


def analyze_architecture(path: str = ".", exclude: list = None) -> ArchitectureMap:
    """Full architecture analysis of a Python codebase."""
    if exclude is None:
        exclude = list(DEFAULT_EXCLUDE)

    path = os.path.abspath(path)
    modules = scan_directory(path, exclude=exclude)
    connections = build_connections(modules)
    hubs = detect_hubs(modules)
    orphans = detect_orphans(modules)
    layers = detect_layers(modules)

    # Detect packages (top-level directories containing __init__.py)
    packages = set()
    for m in modules:
        parts = m.name.split(".")
        if len(parts) > 1:
            packages.add(parts[0])
    packages = sorted(packages)

    # Category counts
    category_counts: dict[str, int] = {}
    total_lines = 0
    total_functions = 0
    total_classes = 0
    for m in modules:
        category_counts[m.category] = category_counts.get(m.category, 0) + 1
        total_lines += m.lines
        total_functions += m.functions
        total_classes += m.classes

    stats = {
        "total_modules": len(modules),
        "total_packages": len(packages),
        "total_connections": len(connections),
        "total_lines": total_lines,
        "total_functions": total_functions,
        "total_classes": total_classes,
        "hub_count": len(hubs),
        "orphan_count": len(orphans),
        "categories": category_counts,
    }

    return ArchitectureMap(
        modules=modules,
        packages=packages,
        connections=connections,
        hub_modules=hubs,
        orphan_modules=orphans,
        layers=layers,
        stats=stats,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _repo_name(arch: ArchitectureMap) -> str:
    """Extract repo name from module paths."""
    if not arch.modules:
        return "unknown"
    first_path = arch.modules[0].path
    parts = Path(first_path).parts
    # Try to find a meaningful directory name
    for i, part in enumerate(parts):
        if part in (".git", "lib", "src", "app"):
            if i > 0:
                return parts[i - 1]
    # Fallback: parent of first module
    return Path(first_path).parent.name or "project"


def format_text(arch: ArchitectureMap) -> str:
    """Plain text architecture report."""
    lines = []
    repo = _repo_name(arch)
    s = arch.stats

    lines.append(f"Architecture Report: {repo}")
    lines.append("=" * (len(lines[0])))
    lines.append("")

    lines.append("Summary")
    lines.append("-" * 7)
    lines.append(f"  Modules:     {s.get('total_modules', 0)}")
    lines.append(f"  Packages:    {s.get('total_packages', 0)}")
    lines.append(f"  Connections: {s.get('total_connections', 0)}")
    lines.append(f"  Lines:       {s.get('total_lines', 0)}")
    lines.append(f"  Functions:   {s.get('total_functions', 0)}")
    lines.append(f"  Classes:     {s.get('total_classes', 0)}")
    lines.append(f"  Hubs:        {s.get('hub_count', 0)}")
    lines.append(f"  Orphans:     {s.get('orphan_count', 0)}")
    lines.append("")

    categories = s.get("categories", {})
    if categories:
        lines.append("Categories")
        lines.append("-" * 10)
        for cat, count in sorted(categories.items()):
            lines.append(f"  {cat}: {count}")
        lines.append("")

    if arch.hub_modules:
        lines.append("Hub Modules (>5 connections)")
        lines.append("-" * 28)
        for h in arch.hub_modules:
            mod = next((m for m in arch.modules if m.name == h), None)
            if mod:
                total = len(mod.imports) + len(mod.imported_by)
                lines.append(f"  {h} ({total} connections)")
        lines.append("")

    if arch.orphan_modules:
        lines.append("Orphan Modules (0 connections)")
        lines.append("-" * 30)
        for o in arch.orphan_modules:
            lines.append(f"  {o}")
        lines.append("")

    if arch.layers:
        lines.append("Layers")
        lines.append("-" * 6)
        layer_order = ["foundation", "core", "service", "interface"]
        for layer in layer_order:
            members = arch.layers.get(layer, [])
            if members:
                lines.append(f"  {layer.title()} ({len(members)} modules)")
                for m in members[:10]:
                    lines.append(f"    - {m}")
                if len(members) > 10:
                    lines.append(f"    ... and {len(members) - 10} more")
        lines.append("")

    lines.append(f"Generated: {arch.generated_at}")
    return "\n".join(lines)


def format_slack(arch: ArchitectureMap) -> str:
    """Slack mrkdwn architecture report. Uses *bold* not **bold**."""
    lines = []
    repo = _repo_name(arch)
    s = arch.stats

    lines.append(f"*Architecture: {repo}*")
    lines.append("")

    lines.append(f"*Summary*")
    lines.append(f"Modules: {s.get('total_modules', 0)} | "
                 f"Packages: {s.get('total_packages', 0)} | "
                 f"Lines: {s.get('total_lines', 0)}")
    lines.append(f"Functions: {s.get('total_functions', 0)} | "
                 f"Classes: {s.get('total_classes', 0)} | "
                 f"Connections: {s.get('total_connections', 0)}")
    lines.append("")

    categories = s.get("categories", {})
    if categories:
        cat_parts = [f"{cat}: {count}" for cat, count in sorted(categories.items())]
        lines.append(f"*Categories*: {' | '.join(cat_parts)}")
        lines.append("")

    if arch.hub_modules:
        lines.append(f"*Hubs* ({len(arch.hub_modules)})")
        for h in arch.hub_modules[:5]:
            mod = next((m for m in arch.modules if m.name == h), None)
            if mod:
                total = len(mod.imports) + len(mod.imported_by)
                lines.append(f"  {h} ({total} conn)")
        if len(arch.hub_modules) > 5:
            lines.append(f"  +{len(arch.hub_modules) - 5} more")
        lines.append("")

    if arch.orphan_modules:
        lines.append(f"*Orphans* ({len(arch.orphan_modules)}): "
                     + ", ".join(arch.orphan_modules[:5]))
        if len(arch.orphan_modules) > 5:
            lines[-1] += f" +{len(arch.orphan_modules) - 5} more"
        lines.append("")

    if arch.layers:
        lines.append("*Layers*")
        layer_order = ["foundation", "core", "service", "interface"]
        for layer in layer_order:
            members = arch.layers.get(layer, [])
            if members:
                lines.append(f"  _{layer.title()}_ ({len(members)}): "
                             + ", ".join(members[:5]))
                if len(members) > 5:
                    lines[-1] += f" +{len(members) - 5} more"

    return "\n".join(lines)


def format_json(arch: ArchitectureMap) -> str:
    """JSON architecture report."""
    data = {
        "generated_at": arch.generated_at,
        "stats": arch.stats,
        "packages": arch.packages,
        "hub_modules": arch.hub_modules,
        "orphan_modules": arch.orphan_modules,
        "layers": arch.layers,
        "modules": [
            {
                "name": m.name,
                "path": m.path,
                "category": m.category,
                "lines": m.lines,
                "functions": m.functions,
                "classes": m.classes,
                "imports": m.imports,
                "imported_by": m.imported_by,
            }
            for m in arch.modules
        ],
        "connections": [
            {"from": src, "to": dst, "type": imp_type}
            for src, dst, imp_type in arch.connections
        ],
    }
    return json.dumps(data, indent=2)


def format_concept_map(arch: ArchitectureMap) -> dict:
    """grip_concept_map MCP app dict."""
    repo = _repo_name(arch)
    return {
        "title": f"Architecture: {repo}",
        "nodes": [
            {"id": mod.name, "label": mod.name, "type": mod.category}
            for mod in arch.modules[:50]
        ],
        "edges": [
            {
                "from": src,
                "to": dst,
                "label": imp_type,
                "style": "solid" if imp_type == "direct" else "dashed",
            }
            for src, dst, imp_type in arch.connections[:100]
        ],
    }


def format_data_table(arch: ArchitectureMap) -> dict:
    """grip_data_table MCP app dict."""
    repo = _repo_name(arch)
    return {
        "title": f"Module Analysis: {repo}",
        "columns": [
            {"key": "name", "label": "Module"},
            {"key": "category", "label": "Category"},
            {"key": "lines", "label": "Lines", "sortable": True},
            {"key": "functions", "label": "Functions", "sortable": True},
            {"key": "classes", "label": "Classes", "sortable": True},
            {"key": "imports_count", "label": "Imports", "sortable": True},
            {"key": "imported_by_count", "label": "Used By", "sortable": True},
        ],
        "rows": [
            {
                "name": m.name,
                "category": m.category,
                "lines": m.lines,
                "functions": m.functions,
                "classes": m.classes,
                "imports_count": len(m.imports),
                "imported_by_count": len(m.imported_by),
            }
            for m in sorted(arch.modules, key=lambda m: len(m.imported_by), reverse=True)
        ],
        "searchable": True,
        "pageSize": 20,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Python Architecture Explorer")
    parser.add_argument("--path", default=".", help="Directory to scan")
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "concept_map", "data_table"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="Comma-separated directories to exclude",
    )
    args = parser.parse_args()

    exclude = list(DEFAULT_EXCLUDE)
    if args.exclude:
        exclude.extend(args.exclude.split(","))

    arch = analyze_architecture(args.path, exclude=exclude)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "concept_map": lambda a: json.dumps(format_concept_map(a), indent=2),
        "data_table": lambda a: json.dumps(format_data_table(a), indent=2),
    }

    output = formatters[args.format](arch)
    print(output)


if __name__ == "__main__":
    main()
