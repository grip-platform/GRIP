"""CLI entry point for the code index.

Usage:
    python3 -m lib.code_index.cli index /path/to/project [--alias name]
    python3 -m lib.code_index.cli lookup ClassName [--project name]
    python3 -m lib.code_index.cli search "query" [--project name]
    python3 -m lib.code_index.cli hierarchy ClassName [--project name]
    python3 -m lib.code_index.cli django models [--project name]
    python3 -m lib.code_index.cli stats [--project name]
"""

from __future__ import annotations

import json
import sys
import time

from lib.code_index.index import CodeIndex
from lib.code_index.models import DjangoType, FrameworkType


def _format_symbol_row(sym: dict) -> str:
    """Format a symbol as a compact display line."""
    loc = f"{sym['file']}:{sym['lines'].split('-')[0]}"
    sig = f" {sym['signature']}" if sym.get("signature") else ""
    doc = f" -- {sym['docstring'][:80]}" if sym.get("docstring") else ""
    return f"  {sym['type']:8s} {sym['name']}{sig}{doc}\n           {loc}"


def cmd_index(idx: CodeIndex, args: list[str]) -> None:
    """Index a project directory."""
    if not args:
        print("Usage: index /path/to/project [--alias name] [--force]")
        sys.exit(1)

    project_path = args[0]
    alias = ""
    force = False
    i = 1
    while i < len(args):
        if args[i] == "--alias" and i + 1 < len(args):
            alias = args[i + 1]
            i += 2
        elif args[i] == "--force":
            force = True
            i += 1
        else:
            i += 1

    stats = idx.index_project(project_path, project=alias, force=force)
    timing = stats.get("timing", {})
    elapsed = timing.get("total_s", 0)

    print(f"Indexed {stats['files_indexed']} files "
          f"({stats['files_skipped']} unchanged, {stats['files_deleted']} deleted) "
          f"in {elapsed:.2f}s")
    print(f"  Symbols: {stats['symbols_added']} added, {stats['errors']} errors")

    if timing and elapsed > 0.1:
        print(f"  Timing breakdown:")
        print(f"    Hashing:        {timing.get('hashing_s', 0):.3f}s")
        print(f"    Incr check:     {timing.get('incremental_check_s', 0):.3f}s")
        print(f"    AST extraction: {timing.get('ast_extraction_s', 0):.3f}s")
        print(f"    DB insert:      {timing.get('db_insert_s', 0):.3f}s")
        print(f"    Cleanup:        {timing.get('cleanup_s', 0):.3f}s")
        print(f"  Throughput: {timing.get('files_per_sec', 0):.0f} files/s, "
              f"{timing.get('symbols_per_sec', 0):.0f} symbols/s")


def cmd_lookup(idx: CodeIndex, args: list[str]) -> None:
    """Look up a symbol by name."""
    if not args:
        print("Usage: lookup SymbolName [--project name]")
        sys.exit(1)

    name = args[0]
    project = _extract_project(args)
    results = idx.lookup(name, project)
    if not results:
        print(f"No symbols found matching '{name}'")
        return
    print(f"Found {len(results)} symbol(s):")
    for sym in results:
        print(_format_symbol_row(sym.to_dict()))


def cmd_search(idx: CodeIndex, args: list[str]) -> None:
    """Full-text search for symbols."""
    if not args:
        print("Usage: search 'query' [--project name]")
        sys.exit(1)

    query = args[0]
    project = _extract_project(args)
    results = idx.search_fts(query, project)
    if not results:
        print(f"No results for '{query}'")
        return
    print(f"Found {len(results)} result(s):")
    for sym in results:
        print(_format_symbol_row(sym.to_dict()))


def cmd_hierarchy(idx: CodeIndex, args: list[str]) -> None:
    """Show class hierarchy with methods."""
    if not args:
        print("Usage: hierarchy ClassName [--project name]")
        sys.exit(1)

    class_name = args[0]
    project = _extract_project(args)
    result = idx.hierarchy(class_name, project)
    if not result["class"]:
        print(f"Class '{class_name}' not found")
        return

    cls = result["class"]
    bases = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
    print(f"class {cls['name']}{bases}  [{cls['file']}:{cls['lines']}]")
    if cls.get("docstring"):
        print(f"  \"\"\"{cls['docstring'][:120]}\"\"\"")
    for method in result["methods"]:
        decs = " ".join(f"@{d}" for d in method.get("decorators", []) if d)
        sig = method.get("signature", "")
        print(f"  {'  ' + decs + ' ' if decs else ''}def {sig or method['name']}")


def cmd_django(idx: CodeIndex, args: list[str]) -> None:
    """List Django components by type."""
    if not args:
        print(f"Usage: django <{'|'.join(t.value for t in DjangoType if t != DjangoType.NONE)}> [--project name]")
        sys.exit(1)

    django_type = args[0].lower()
    project = _extract_project(args)
    results = idx.django_list(django_type, project)
    if not results:
        print(f"No Django {django_type}s found")
        return
    print(f"Found {len(results)} {django_type}(s):")
    for sym in results:
        print(f"  {sym.name:40s} {sym.file_path}:{sym.line_start}")


def cmd_framework(idx: CodeIndex, args: list[str]) -> None:
    """List framework components by type."""
    if not args:
        print(f"Usage: framework <{'|'.join(t.value for t in FrameworkType if t != FrameworkType.NONE)}> [--project name]")
        sys.exit(1)

    fw_type = args[0].lower()
    project = _extract_project(args)
    results = idx.framework_list(fw_type, project)
    if not results:
        print(f"No {fw_type} components found")
        return
    print(f"Found {len(results)} {fw_type}(s):")
    for sym in results:
        print(f"  {sym.name:40s} {sym.file_path}:{sym.line_start}")


def cmd_stats(idx: CodeIndex, args: list[str]) -> None:
    """Show index statistics."""
    project = _extract_project(args)
    s = idx.stats(project)
    print(f"Total symbols: {s['total_symbols']:,}")
    print(f"Files indexed: {s['files_indexed']:,}")
    if s["by_type"]:
        print("By type:")
        for t, c in sorted(s["by_type"].items()):
            print(f"  {t}: {c:,}")
    if s["django_types"]:
        print("Django components:")
        for t, c in sorted(s["django_types"].items()):
            print(f"  {t}: {c:,}")
    if s["projects"]:
        print(f"Projects: {', '.join(s['projects'])}")


def _extract_project(args: list[str]) -> str:
    """Extract --project value from args."""
    for i, arg in enumerate(args):
        if arg == "--project" and i + 1 < len(args):
            return args[i + 1]
    return ""


def main() -> None:
    if len(sys.argv) < 2:
        print("Commands: index, lookup, search, hierarchy, django, framework, stats")
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    idx = CodeIndex()
    try:
        commands = {
            "index": cmd_index,
            "reindex": cmd_index,  # alias
            "lookup": cmd_lookup,
            "search": cmd_search,
            "hierarchy": cmd_hierarchy,
            "django": cmd_django,
            "framework": cmd_framework,
            "stats": cmd_stats,
        }
        if command not in commands:
            print(f"Unknown command: {command}")
            print(f"Available: {', '.join(commands)}")
            sys.exit(1)
        commands[command](idx, args)
    finally:
        idx.close()


if __name__ == "__main__":
    main()
