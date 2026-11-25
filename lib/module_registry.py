#!/usr/bin/env python3
"""GRIP Module Registry -- auto-discover and catalog lib/ Python modules.

Uses AST parsing (no imports) for safe interface extraction.
Discovers functions, classes, dataclasses, CLI presence, and docstrings.
"""

import ast
import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FunctionInfo:
    """Metadata for a single function."""

    name: str
    args: List[str]
    return_annotation: Optional[str] = None
    docstring: Optional[str] = None
    is_public: bool = True


@dataclass
class ClassInfo:
    """Metadata for a single class."""

    name: str
    bases: List[str]
    fields: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_dataclass: bool = False


@dataclass
class ModuleInfo:
    """Metadata for a single Python module."""

    name: str
    path: str
    functions: List[FunctionInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    has_cli: bool = False
    docstring: Optional[str] = None


def discover_modules(lib_dir: str) -> List[str]:
    """Find all .py files in lib_dir.

    Returns sorted list of absolute paths to Python files.
    Skips __pycache__ directories.
    """
    lib_path = Path(lib_dir)
    if not lib_path.is_dir():
        return []
    modules = []
    for entry in sorted(lib_path.iterdir()):
        if entry.is_file() and entry.suffix == ".py":
            modules.append(str(entry.resolve()))
    return modules


def _extract_args(func_node: ast.FunctionDef) -> List[str]:
    """Extract argument names from a function definition."""
    args = []
    for arg in func_node.args.args:
        args.append(arg.arg)
    return args


def _extract_return_annotation(func_node: ast.FunctionDef) -> Optional[str]:
    """Extract return type annotation as string."""
    if func_node.returns is None:
        return None
    return ast.unparse(func_node.returns)


def _extract_bases(class_node: ast.ClassDef) -> List[str]:
    """Extract base class names."""
    bases = []
    for base in class_node.bases:
        bases.append(ast.unparse(base))
    return bases


def _is_dataclass(class_node: ast.ClassDef) -> bool:
    """Check if a class has the @dataclass decorator."""
    for decorator in class_node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            return True
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name) and func.id == "dataclass":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "dataclass":
                return True
    return False


def _extract_dataclass_fields(class_node: ast.ClassDef) -> List[str]:
    """Extract field names from a dataclass (annotated assignments in body)."""
    fields = []
    for node in class_node.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.append(node.target.id)
    return fields


def _extract_methods(class_node: ast.ClassDef) -> List[str]:
    """Extract method names from a class body."""
    methods = []
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(node.name)
    return methods


def _has_argparse_usage(tree: ast.Module) -> bool:
    """Detect argparse usage as CLI indicator."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "ArgumentParser":
            if isinstance(node.value, ast.Name) and node.value.id == "argparse":
                return True
        # Also detect: from argparse import ArgumentParser; ArgumentParser()
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "ArgumentParser":
                return True
    return False


def _get_docstring(node) -> Optional[str]:
    """Extract docstring from a module, class, or function node."""
    return ast.get_docstring(node)


def parse_module(filepath: str) -> Optional[ModuleInfo]:
    """Parse a Python file using AST and extract interface metadata.

    Returns None if the file cannot be parsed (syntax errors, etc.).
    """
    filepath = str(Path(filepath).resolve())
    module_name = Path(filepath).stem

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return None

    info = ModuleInfo(
        name=module_name,
        path=filepath,
        docstring=_get_docstring(tree),
        has_cli=_has_argparse_usage(tree),
    )

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_info = FunctionInfo(
                name=node.name,
                args=_extract_args(node),
                return_annotation=_extract_return_annotation(node),
                docstring=_get_docstring(node),
                is_public=not node.name.startswith("_"),
            )
            info.functions.append(func_info)

        elif isinstance(node, ast.ClassDef):
            is_dc = _is_dataclass(node)
            class_info = ClassInfo(
                name=node.name,
                bases=_extract_bases(node),
                fields=_extract_dataclass_fields(node) if is_dc else [],
                methods=_extract_methods(node),
                docstring=_get_docstring(node),
                is_dataclass=is_dc,
            )
            info.classes.append(class_info)

    return info


def build_registry(lib_dir: str) -> Dict[str, ModuleInfo]:
    """Scan all modules in lib_dir and return a registry dict.

    Keys are module names (stem), values are ModuleInfo objects.
    Modules that fail to parse are silently skipped.
    """
    registry: Dict[str, ModuleInfo] = {}
    for filepath in discover_modules(lib_dir):
        info = parse_module(filepath)
        if info is not None:
            registry[info.name] = info
    return registry


def _tokenize(name: str) -> set:
    """Split a name into lowercase tokens, handling both snake_case and CamelCase."""
    # Split on underscores first, then split camelCase parts
    parts = name.replace("_", " ").split()
    tokens = set()
    for part in parts:
        # Split camelCase: "DataProcessor" -> ["Data", "Processor"]
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)", part)
        if camel_parts:
            tokens.update(p.lower() for p in camel_parts)
        else:
            tokens.add(part.lower())
    return tokens


def search_registry(registry: Dict[str, ModuleInfo], query: str) -> List[ModuleInfo]:
    """Find modules/functions matching a query via word overlap.

    Searches module names, docstrings, function names, and class names.
    Returns modules sorted by relevance (number of query word matches).
    """
    query_words = set(query.lower().split())
    if not query_words:
        return []

    scored: List[tuple] = []

    for info in registry.values():
        score = 0
        # Search in module name
        name_words = _tokenize(info.name)
        score += len(query_words & name_words) * 3  # module name match weighted higher

        # Search in module docstring
        if info.docstring:
            doc_words = set(info.docstring.lower().split())
            score += len(query_words & doc_words)

        # Search in function names and docstrings
        for func in info.functions:
            func_words = _tokenize(func.name)
            score += len(query_words & func_words) * 2
            if func.docstring:
                fdoc_words = set(func.docstring.lower().split())
                score += len(query_words & fdoc_words)

        # Search in class names and docstrings
        for cls in info.classes:
            cls_words = _tokenize(cls.name)
            score += len(query_words & cls_words) * 2
            if cls.docstring:
                cdoc_words = set(cls.docstring.lower().split())
                score += len(query_words & cdoc_words)

        if score > 0:
            scored.append((score, info))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [info for _, info in scored]


def format_module_info(info: ModuleInfo) -> str:
    """Format a module's full interface for display."""
    lines = []
    lines.append(f"Module: {info.name}")
    lines.append(f"  Path: {info.path}")
    if info.docstring:
        lines.append(f"  Doc: {info.docstring.split(chr(10))[0]}")
    if info.has_cli:
        lines.append("  CLI: Yes (argparse)")

    if info.functions:
        lines.append("  Functions:")
        for func in info.functions:
            visibility = "public" if func.is_public else "private"
            ret = f" -> {func.return_annotation}" if func.return_annotation else ""
            args_str = ", ".join(func.args)
            lines.append(f"    {func.name}({args_str}){ret}  [{visibility}]")
            if func.docstring:
                lines.append(f"      {func.docstring.split(chr(10))[0]}")

    if info.classes:
        lines.append("  Classes:")
        for cls in info.classes:
            dc_tag = " [dataclass]" if cls.is_dataclass else ""
            bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
            lines.append(f"    {cls.name}{bases_str}{dc_tag}")
            if cls.docstring:
                lines.append(f"      {cls.docstring.split(chr(10))[0]}")
            if cls.fields:
                lines.append(f"      Fields: {', '.join(cls.fields)}")
            if cls.methods:
                lines.append(f"      Methods: {', '.join(cls.methods)}")

    return "\n".join(lines)


def format_function_list(info: ModuleInfo) -> str:
    """Format just the public functions of a module."""
    lines = [f"Public functions in {info.name}:"]
    public_funcs = [f for f in info.functions if f.is_public]
    if not public_funcs:
        lines.append("  (none)")
    else:
        for func in public_funcs:
            ret = f" -> {func.return_annotation}" if func.return_annotation else ""
            args_str = ", ".join(func.args)
            lines.append(f"  {func.name}({args_str}){ret}")
            if func.docstring:
                lines.append(f"    {func.docstring.split(chr(10))[0]}")
    return "\n".join(lines)


def format_dataclass_list(info: ModuleInfo) -> str:
    """Format just the dataclasses with their fields."""
    lines = [f"Dataclasses in {info.name}:"]
    dataclasses = [c for c in info.classes if c.is_dataclass]
    if not dataclasses:
        lines.append("  (none)")
    else:
        for cls in dataclasses:
            lines.append(f"  {cls.name}")
            if cls.docstring:
                lines.append(f"    {cls.docstring.split(chr(10))[0]}")
            if cls.fields:
                for fld in cls.fields:
                    lines.append(f"    - {fld}")
    return "\n".join(lines)


def _build_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="module_registry",
        description="GRIP Module Registry -- discover and catalog lib/ modules",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    list_cmd = subparsers.add_parser("list", help="List all discovered modules")
    list_cmd.add_argument(
        "--lib-dir", default=None, help="Path to lib/ directory"
    )

    # search
    search_cmd = subparsers.add_parser("search", help="Search modules by keyword")
    search_cmd.add_argument("query", help="Search query")
    search_cmd.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    # info
    info_cmd = subparsers.add_parser("info", help="Show full module info")
    info_cmd.add_argument("module_name", help="Module name (without .py)")
    info_cmd.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    # functions
    funcs_cmd = subparsers.add_parser(
        "functions", help="List public functions of a module"
    )
    funcs_cmd.add_argument("module_name", help="Module name (without .py)")
    funcs_cmd.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    # dataclasses
    dc_cmd = subparsers.add_parser(
        "dataclasses", help="List dataclasses of a module"
    )
    dc_cmd.add_argument("module_name", help="Module name (without .py)")
    dc_cmd.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    # stats
    stats_cmd = subparsers.add_parser("stats", help="Show registry statistics")
    stats_cmd.add_argument("--lib-dir", default=None, help="Path to lib/ directory")

    return parser


def _resolve_lib_dir(lib_dir: Optional[str]) -> str:
    """Resolve the lib directory path."""
    if lib_dir:
        return lib_dir
    # Default: look for lib/ relative to this script
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == "lib":
        return str(script_dir)
    candidate = script_dir / "lib"
    if candidate.is_dir():
        return str(candidate)
    return str(script_dir)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    lib_dir = _resolve_lib_dir(getattr(args, "lib_dir", None))
    registry = build_registry(lib_dir)

    if args.command == "list":
        if not registry:
            print("No modules found.")
            return 0
        for name in sorted(registry):
            info = registry[name]
            doc_preview = ""
            if info.docstring:
                doc_preview = f" -- {info.docstring.split(chr(10))[0]}"
            cli_tag = " [CLI]" if info.has_cli else ""
            print(f"  {name}{cli_tag}{doc_preview}")
        print(f"\n{len(registry)} modules discovered.")
        return 0

    elif args.command == "search":
        results = search_registry(registry, args.query)
        if not results:
            print(f"No modules matching '{args.query}'.")
            return 0
        for info in results:
            print(f"  {info.name}: {info.docstring.split(chr(10))[0] if info.docstring else '(no docstring)'}")
        print(f"\n{len(results)} results.")
        return 0

    elif args.command == "info":
        info = registry.get(args.module_name)
        if not info:
            print(f"Module '{args.module_name}' not found.")
            return 1
        print(format_module_info(info))
        return 0

    elif args.command == "functions":
        info = registry.get(args.module_name)
        if not info:
            print(f"Module '{args.module_name}' not found.")
            return 1
        print(format_function_list(info))
        return 0

    elif args.command == "dataclasses":
        info = registry.get(args.module_name)
        if not info:
            print(f"Module '{args.module_name}' not found.")
            return 1
        print(format_dataclass_list(info))
        return 0

    elif args.command == "stats":
        total_modules = len(registry)
        total_functions = sum(len(m.functions) for m in registry.values())
        total_public = sum(
            sum(1 for f in m.functions if f.is_public) for m in registry.values()
        )
        total_classes = sum(len(m.classes) for m in registry.values())
        total_dataclasses = sum(
            sum(1 for c in m.classes if c.is_dataclass) for m in registry.values()
        )
        total_cli = sum(1 for m in registry.values() if m.has_cli)

        print("GRIP Module Registry Statistics")
        print(f"  Modules:     {total_modules}")
        print(f"  Functions:   {total_functions} ({total_public} public)")
        print(f"  Classes:     {total_classes} ({total_dataclasses} dataclasses)")
        print(f"  CLI modules: {total_cli}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
