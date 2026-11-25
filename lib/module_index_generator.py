#!/usr/bin/env python3
"""Module Index Generator — auto-catalogue lib/ with AST parsing.

Scans all Python files in lib/, extracts module-level docstrings,
class names, and function names via AST. Groups by category using
keyword matching. Detects stale/missing docstrings.

Usage:
    python3 lib/module_index_generator.py              # full markdown index
    python3 lib/module_index_generator.py --json        # JSON output
    python3 lib/module_index_generator.py --text        # plain text summary
    python3 lib/module_index_generator.py --category tools  # filter by category
    python3 lib/module_index_generator.py --stats       # statistics only
    python3 lib/module_index_generator.py --stale       # stale/undocumented only

Pure Python, stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

# Order matters: first match wins. More specific patterns before general ones.
CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    ("genome", [
        "genome", "gene_", "gene.", "dna", "evolutionary", "phylogeny",
        "mutation", "fitness", "lineage",
    ]),
    ("converge", [
        "converge", "convergence",
    ]),
    ("session", [
        "session", "resurrection", "resurrect", "compaction",
    ]),
    ("metrics", [
        "metric", "score", "dashboard", "analytics", "velocity", "forecast",
        "heatmap", "weather", "energy", "mood",
    ]),
    ("analysis", [
        "analyzer", "analyser", "analysis", "detect", "scanner", "audit",
        "profiler", "explorer", "mapper", "inspector", "dead_code",
        "complexity", "dedup", "stale_branch", "tech_debt", "silent_exception",
    ]),
    ("testing", [
        "test_", "test.", "tester", "integration_test", "red_team",
        "redteam", "trust_review",
    ]),
    ("tools", [
        "cli", "command", "shell", "complete", "completions", "formatter",
        "generator", "export", "changelog", "release_notes", "wiki",
        "viewer", "tui", "storyteller", "narrative",
    ]),
    ("core", [
        "grip_", "grip.", "config", "registry", "atomic", "lock",
        "compat", "utils", "helpers", "json_util", "path_encoding",
        "subprocess_", "bridge",
    ]),
    ("skill", [
        "skill", "apprentice", "learning", "knowledge", "insight",
        "pattern_library", "onboarding",
    ]),
    ("rsi", [
        "rsi", "improvement", "backlog", "sprint", "todo",
    ]),
    ("mcp", [
        "mcp", "slack", "github", "org_", "pair_", "tailscale",
    ]),
    ("cost", [
        "cost", "budget",
    ]),
    ("memory", [
        "memory", "kono", "embedding", "knowledge_base", "knowledge_graph",
    ]),
]


def detect_category(filename: str, docstring: Optional[str] = None) -> str:
    """Return the category for a module based on filename and docstring."""
    name_lower = filename.lower()
    doc_lower = (docstring or "").lower()
    combined = f"{name_lower} {doc_lower}"

    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in combined:
                return category

    return "other"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FunctionInfo:
    """Information about a function or method."""
    name: str
    is_async: bool = False
    lineno: int = 0
    decorators: List[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """Information about a class."""
    name: str
    lineno: int = 0
    methods: List[FunctionInfo] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)
    is_dataclass: bool = False


@dataclass
class ModuleInfo:
    """Extracted information about a Python module."""
    filepath: str
    filename: str
    docstring: Optional[str] = None
    one_liner: Optional[str] = None
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    category: str = "other"
    file_size: int = 0
    line_count: int = 0
    parse_error: Optional[str] = None
    staleness_issues: List[str] = field(default_factory=list)

    @property
    def has_docstring(self) -> bool:
        return self.docstring is not None and len(self.docstring.strip()) > 0

    @property
    def public_classes(self) -> List[ClassInfo]:
        return [c for c in self.classes if not c.name.startswith("_")]

    @property
    def public_functions(self) -> List[FunctionInfo]:
        return [f for f in self.functions if not f.name.startswith("_")]

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""
        return {
            "filepath": self.filepath,
            "filename": self.filename,
            "docstring": self.docstring,
            "one_liner": self.one_liner,
            "category": self.category,
            "file_size": self.file_size,
            "line_count": self.line_count,
            "parse_error": self.parse_error,
            "staleness_issues": self.staleness_issues,
            "classes": [
                {
                    "name": c.name,
                    "lineno": c.lineno,
                    "bases": c.bases,
                    "is_dataclass": c.is_dataclass,
                    "methods": [
                        {"name": m.name, "is_async": m.is_async, "lineno": m.lineno}
                        for m in c.methods
                    ],
                }
                for c in self.classes
            ],
            "functions": [
                {
                    "name": f.name,
                    "is_async": f.is_async,
                    "lineno": f.lineno,
                    "decorators": f.decorators,
                }
                for f in self.functions
            ],
        }


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _extract_decorator_names(decorator_list: list) -> List[str]:
    """Extract decorator names from AST decorator nodes."""
    names = []
    for dec in decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                names.append(dec.func.attr)
    return names


def _extract_base_names(bases: list) -> List[str]:
    """Extract base class names from AST."""
    names = []
    for base in bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
        elif isinstance(base, ast.Subscript):
            if isinstance(base.value, ast.Name):
                names.append(base.value.id)
    return names


def extract_module_info(filepath: str) -> ModuleInfo:
    """Parse a Python file and extract module-level information via AST.

    Args:
        filepath: Absolute or relative path to a .py file.

    Returns:
        ModuleInfo with all extracted data. On parse error, parse_error
        is set and classes/functions are empty.
    """
    path = Path(filepath)
    info = ModuleInfo(
        filepath=str(path),
        filename=path.name,
    )

    # File stats
    try:
        stat = path.stat()
        info.file_size = stat.st_size
    except OSError:
        info.file_size = 0

    # Read source
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        info.parse_error = f"Cannot read file: {exc}"
        return info

    info.line_count = source.count("\n") + (1 if source and not source.endswith("\n") else 0)

    # Parse AST
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        info.parse_error = f"SyntaxError: {exc.msg} (line {exc.lineno})"
        return info

    # Module docstring
    info.docstring = ast.get_docstring(tree)
    if info.docstring:
        first_line = info.docstring.strip().split("\n")[0].strip()
        # Strip common prefixes like "module_name.py --" or "module_name --"
        for prefix in [f"{path.stem}.py", f"{path.stem}.py —", f"{path.stem} —",
                        f"{path.stem}.py --", f"{path.stem} --",
                        f"{path.stem}.py:", f"{path.stem}:"]:
            if first_line.lower().startswith(prefix.lower()):
                first_line = first_line[len(prefix):].strip()
                # Strip leading dash/emdash if leftover
                first_line = first_line.lstrip("—- ").strip()
                break
        info.one_liner = first_line if first_line else info.docstring.strip().split("\n")[0].strip()

    # Extract top-level classes and functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            decorators = _extract_decorator_names(node.decorator_list)
            cls = ClassInfo(
                name=node.name,
                lineno=node.lineno,
                bases=_extract_base_names(node.bases),
                is_dataclass="dataclass" in decorators,
            )
            # Extract methods
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method = FunctionInfo(
                        name=item.name,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        lineno=item.lineno,
                        decorators=_extract_decorator_names(item.decorator_list),
                    )
                    cls.methods.append(method)
            info.classes.append(cls)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = FunctionInfo(
                name=node.name,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                lineno=node.lineno,
                decorators=_extract_decorator_names(node.decorator_list),
            )
            info.functions.append(func)

    # Detect category
    info.category = detect_category(info.filename, info.docstring)

    return info


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def detect_staleness(module: ModuleInfo) -> List[str]:
    """Check for staleness issues in a module.

    Flags:
    - No docstring at all
    - Docstring mentions function/class names that don't exist in AST
    - Empty module (no classes or functions)

    Returns:
        List of issue descriptions. Empty list means no issues.
    """
    issues: List[str] = []

    if module.parse_error:
        issues.append(f"Parse error: {module.parse_error}")
        return issues

    if not module.has_docstring:
        issues.append("Missing module docstring")

    if not module.classes and not module.functions:
        issues.append("Empty module: no classes or functions defined")

    # Check if docstring references names that don't exist
    if module.docstring:
        all_names: Set[str] = set()
        for cls in module.classes:
            all_names.add(cls.name)
            for method in cls.methods:
                all_names.add(method.name)
        for func in module.functions:
            all_names.add(func.name)

        doc = module.docstring
        # Look for patterns like "FunctionName(", "ClassName.", function_name()
        import re
        # Match PascalCase or snake_case identifiers followed by ( or .
        mentioned = set(re.findall(r'\b([A-Z][a-zA-Z0-9]+|[a-z_][a-z0-9_]+)\s*\(', doc))
        # Filter to likely references (ignore common words)
        common_words = {
            "the", "and", "for", "not", "are", "was", "has", "had", "but",
            "all", "can", "use", "see", "set", "get", "run", "new", "old",
            "any", "one", "two", "may", "str", "int", "dir", "log", "max",
            "min", "len", "map", "zip", "sum", "abs", "hex", "oct", "bin",
            "ord", "chr", "type", "list", "dict", "tuple", "bool", "None",
            "True", "False", "print", "range", "open", "input", "super",
            "self", "cls", "import", "from", "return", "yield", "raise",
            "with", "pass", "break", "continue", "class", "def", "async",
            "await", "lambda", "global", "nonlocal", "assert", "del",
            "elif", "else", "except", "finally", "try", "while", "if",
            "isinstance", "issubclass", "callable",
        }
        for name in mentioned:
            if name.lower() in common_words:
                continue
            if len(name) < 3:
                continue
            if name not in all_names and name not in {m.filename.replace(".py", "")
                                                       for m in [module]}:
                # Only flag if it looks like a real reference (starts with upper or has underscore)
                if name[0].isupper() or "_" in name:
                    issues.append(f"Docstring mentions '{name}' but it is not defined in the module")

    return issues


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_lib_directory(lib_dir: str) -> List[ModuleInfo]:
    """Scan a directory for Python files and extract module info.

    Args:
        lib_dir: Path to the lib/ directory.

    Returns:
        Sorted list of ModuleInfo objects.
    """
    lib_path = Path(lib_dir)
    if not lib_path.is_dir():
        return []

    modules: List[ModuleInfo] = []
    for py_file in sorted(lib_path.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        if py_file.name == "__pycache__":
            continue
        info = extract_module_info(str(py_file))
        info.staleness_issues = detect_staleness(info)
        modules.append(info)

    return modules


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@dataclass
class IndexStatistics:
    """Aggregate statistics across all modules."""
    total_modules: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    total_classes: int = 0
    total_functions: int = 0
    avg_functions_per_module: float = 0.0
    docstring_coverage: float = 0.0
    modules_with_docstring: int = 0
    modules_without_docstring: int = 0
    total_lines: int = 0
    total_size_bytes: int = 0
    stale_modules: int = 0
    parse_errors: int = 0

    def to_dict(self) -> dict:
        return {
            "total_modules": self.total_modules,
            "by_category": self.by_category,
            "total_classes": self.total_classes,
            "total_functions": self.total_functions,
            "avg_functions_per_module": round(self.avg_functions_per_module, 1),
            "docstring_coverage": round(self.docstring_coverage, 1),
            "modules_with_docstring": self.modules_with_docstring,
            "modules_without_docstring": self.modules_without_docstring,
            "total_lines": self.total_lines,
            "total_size_bytes": self.total_size_bytes,
            "stale_modules": self.stale_modules,
            "parse_errors": self.parse_errors,
        }


def compute_statistics(modules: List[ModuleInfo]) -> IndexStatistics:
    """Compute aggregate statistics from module list."""
    stats = IndexStatistics()
    stats.total_modules = len(modules)

    for mod in modules:
        # Category counts
        stats.by_category[mod.category] = stats.by_category.get(mod.category, 0) + 1

        # Totals
        stats.total_classes += len(mod.classes)
        stats.total_functions += len(mod.functions)
        stats.total_lines += mod.line_count
        stats.total_size_bytes += mod.file_size

        if mod.has_docstring:
            stats.modules_with_docstring += 1
        else:
            stats.modules_without_docstring += 1

        if mod.staleness_issues:
            stats.stale_modules += 1

        if mod.parse_error:
            stats.parse_errors += 1

    if stats.total_modules > 0:
        stats.avg_functions_per_module = stats.total_functions / stats.total_modules
        stats.docstring_coverage = (stats.modules_with_docstring / stats.total_modules) * 100

    return stats


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(modules: List[ModuleInfo], stats: IndexStatistics) -> str:
    """Format the module index as a Markdown document."""
    lines: List[str] = []
    lines.append("# Module Index")
    lines.append("")
    lines.append(f"**{stats.total_modules}** modules | "
                 f"**{stats.total_classes}** classes | "
                 f"**{stats.total_functions}** functions | "
                 f"**{stats.docstring_coverage:.0f}%** docstring coverage")
    lines.append("")

    # Group by category
    by_cat: Dict[str, List[ModuleInfo]] = {}
    for mod in modules:
        by_cat.setdefault(mod.category, []).append(mod)

    for category in sorted(by_cat.keys()):
        cat_modules = by_cat[category]
        lines.append(f"## {category.title()} ({len(cat_modules)})")
        lines.append("")
        lines.append("| Module | Description | Classes | Functions | Lines |")
        lines.append("|--------|-------------|---------|-----------|-------|")

        for mod in sorted(cat_modules, key=lambda m: m.filename):
            desc = mod.one_liner or ("*parse error*" if mod.parse_error else "*no docstring*")
            # Truncate long descriptions
            if len(desc) > 60:
                desc = desc[:57] + "..."
            stale_marker = " :warning:" if mod.staleness_issues else ""
            pub_cls = len(mod.public_classes)
            pub_fn = len(mod.public_functions)
            lines.append(
                f"| `{mod.filename}` | {desc}{stale_marker} | {pub_cls} | {pub_fn} | {mod.line_count} |"
            )

        lines.append("")

        # List public APIs under each module
        for mod in sorted(cat_modules, key=lambda m: m.filename):
            if mod.public_classes or mod.public_functions:
                lines.append(f"### `{mod.filename}`")
                lines.append("")
                if mod.public_classes:
                    for cls in mod.public_classes:
                        bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
                        dc_marker = " [dataclass]" if cls.is_dataclass else ""
                        lines.append(f"- **class** `{cls.name}{bases_str}`{dc_marker}")
                        pub_methods = [m for m in cls.methods if not m.name.startswith("_")]
                        for method in pub_methods:
                            async_marker = "async " if method.is_async else ""
                            lines.append(f"  - `{async_marker}{method.name}()`")
                if mod.public_functions:
                    for func in mod.public_functions:
                        async_marker = "async " if func.is_async else ""
                        lines.append(f"- `{async_marker}{func.name}()`")
                lines.append("")

    return "\n".join(lines)


def format_json(modules: List[ModuleInfo], stats: IndexStatistics) -> str:
    """Format the module index as JSON."""
    data = {
        "statistics": stats.to_dict(),
        "modules": [mod.to_dict() for mod in modules],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def format_text(modules: List[ModuleInfo], stats: IndexStatistics) -> str:
    """Format a plain text summary."""
    lines: List[str] = []
    lines.append("Module Index Summary")
    lines.append("=" * 40)
    lines.append(f"Total modules:          {stats.total_modules}")
    lines.append(f"Total classes:          {stats.total_classes}")
    lines.append(f"Total functions:        {stats.total_functions}")
    lines.append(f"Avg functions/module:   {stats.avg_functions_per_module:.1f}")
    lines.append(f"Docstring coverage:     {stats.docstring_coverage:.0f}%")
    lines.append(f"Stale modules:          {stats.stale_modules}")
    lines.append(f"Parse errors:           {stats.parse_errors}")
    lines.append(f"Total lines:            {stats.total_lines}")
    lines.append(f"Total size:             {stats.total_size_bytes:,} bytes")
    lines.append("")
    lines.append("By Category:")
    for cat in sorted(stats.by_category.keys()):
        lines.append(f"  {cat:20s} {stats.by_category[cat]:>4d}")
    lines.append("")
    lines.append("Modules:")
    for mod in sorted(modules, key=lambda m: m.filename):
        desc = mod.one_liner or "(no docstring)"
        lines.append(f"  {mod.filename:40s} {desc}")
    return "\n".join(lines)


def format_stats(stats: IndexStatistics) -> str:
    """Format statistics only as plain text."""
    lines: List[str] = []
    lines.append("Module Index Statistics")
    lines.append("=" * 40)
    lines.append(f"Total modules:          {stats.total_modules}")
    lines.append(f"Total classes:          {stats.total_classes}")
    lines.append(f"Total functions:        {stats.total_functions}")
    lines.append(f"Avg functions/module:   {stats.avg_functions_per_module:.1f}")
    lines.append(f"Docstring coverage:     {stats.docstring_coverage:.0f}%")
    lines.append(f"Stale modules:          {stats.stale_modules}")
    lines.append(f"Parse errors:           {stats.parse_errors}")
    lines.append(f"Total lines:            {stats.total_lines}")
    lines.append(f"Total size:             {stats.total_size_bytes:,} bytes")
    lines.append("")
    lines.append("By Category:")
    for cat in sorted(stats.by_category.keys()):
        lines.append(f"  {cat:20s} {stats.by_category[cat]:>4d}")
    return "\n".join(lines)


def format_stale(modules: List[ModuleInfo]) -> str:
    """Format only stale/undocumented modules."""
    stale = [m for m in modules if m.staleness_issues]
    if not stale:
        return "No stale modules found."

    lines: List[str] = []
    lines.append(f"Stale Modules ({len(stale)})")
    lines.append("=" * 40)
    for mod in sorted(stale, key=lambda m: m.filename):
        lines.append(f"\n{mod.filename}:")
        for issue in mod.staleness_issues:
            lines.append(f"  - {issue}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Module Index Generator — auto-catalogue lib/ with AST parsing."
    )
    parser.add_argument(
        "--lib-dir",
        default=None,
        help="Path to lib/ directory (default: auto-detect from script location)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--text", action="store_true", help="Output as plain text summary")
    parser.add_argument("--stats", action="store_true", help="Show statistics only")
    parser.add_argument("--stale", action="store_true", help="Show stale/undocumented modules only")
    parser.add_argument("--category", type=str, help="Filter by category name")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve lib directory
    if args.lib_dir:
        lib_dir = args.lib_dir
    else:
        # Auto-detect: script is in lib/, so parent is the project root
        script_dir = Path(__file__).resolve().parent
        if script_dir.name == "lib":
            lib_dir = str(script_dir)
        else:
            lib_dir = str(script_dir / "lib")

    if not Path(lib_dir).is_dir():
        print(f"Error: lib directory not found: {lib_dir}", file=sys.stderr)
        return 1

    # Scan
    modules = scan_lib_directory(lib_dir)

    if not modules:
        print("No Python modules found.", file=sys.stderr)
        return 1

    # Filter by category
    if args.category:
        cat_lower = args.category.lower()
        modules = [m for m in modules if m.category == cat_lower]
        if not modules:
            print(f"No modules found in category '{args.category}'.", file=sys.stderr)
            return 1

    stats = compute_statistics(modules)

    # Output
    if args.stale:
        print(format_stale(modules))
    elif args.stats:
        print(format_stats(stats))
    elif args.json:
        print(format_json(modules, stats))
    elif args.text:
        print(format_text(modules, stats))
    else:
        print(format_markdown(modules, stats))

    return 0


if __name__ == "__main__":
    sys.exit(main())
