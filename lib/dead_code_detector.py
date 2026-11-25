#!/usr/bin/env python3
"""Dead code detector for GRIP lib/ modules.

Finds unused functions, classes, and imports across lib/ using AST analysis.
Pure Python, stdlib only.
"""
from __future__ import annotations

import ast
import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Definition:
    """A function, class, or import definition."""
    name: str
    file_path: str
    line_number: int
    def_type: str  # "function" | "class" | "import"


@dataclass
class Reference:
    """A reference to a name (call, import, attribute access)."""
    name: str
    file_path: str
    line_number: int
    ref_type: str  # "call" | "import" | "attribute"


@dataclass
class DeadCodeFinding:
    """A single dead code finding with confidence level."""
    definition: Definition
    confidence: str  # "high" | "medium" | "low"
    reason: str


@dataclass
class DeadCodeReport:
    """Summary report of dead code analysis."""
    findings: List[DeadCodeFinding]
    total_definitions: int
    total_dead: int
    dead_percentage: float
    by_confidence: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AST Visitors
# ---------------------------------------------------------------------------

class DefinitionVisitor(ast.NodeVisitor):
    """Collects function, class, and import definitions from a module."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.definitions: List[Definition] = []
        self._in_class: bool = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not self._should_skip(node.name):
            self.definitions.append(Definition(
                name=node.name,
                file_path=self.file_path,
                line_number=node.lineno,
                def_type="function",
            ))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not self._should_skip(node.name):
            self.definitions.append(Definition(
                name=node.name,
                file_path=self.file_path,
                line_number=node.lineno,
                def_type="class",
            ))
        old = self._in_class
        self._in_class = True
        self.generic_visit(node)
        self._in_class = old

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            # Only track top-level imports (not inside functions)
            if not self._should_skip(name):
                self.definitions.append(Definition(
                    name=name,
                    file_path=self.file_path,
                    line_number=node.lineno,
                    def_type="import",
                ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.names:
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname if alias.asname else alias.name
                if not self._should_skip(name):
                    self.definitions.append(Definition(
                        name=name,
                        file_path=self.file_path,
                        line_number=node.lineno,
                        def_type="import",
                    ))

    def _should_skip(self, name: str) -> bool:
        """Skip private names, dunder methods, and names inside classes."""
        if self._in_class:
            return True
        if name.startswith("_"):
            return True
        return False


class ReferenceVisitor(ast.NodeVisitor):
    """Collects all name references in a module."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.references: List[Reference] = []
        self._in_definition = False

    def visit_Name(self, node: ast.Name) -> None:
        self.references.append(Reference(
            name=node.id,
            file_path=self.file_path,
            line_number=node.lineno,
            ref_type="call" if isinstance(getattr(node, '_parent', None), ast.Call) else "attribute",
        ))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.references.append(Reference(
            name=node.attr,
            file_path=self.file_path,
            line_number=node.lineno,
            ref_type="attribute",
        ))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            self.references.append(Reference(
                name=name,
                file_path=self.file_path,
                line_number=node.lineno,
                ref_type="import",
            ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.names:
            for alias in node.names:
                if alias.name == "*":
                    continue
                self.references.append(Reference(
                    name=alias.name,
                    file_path=self.file_path,
                    line_number=node.lineno,
                    ref_type="import",
                ))


def _add_parent_refs(tree: ast.AST) -> None:
    """Add parent references to AST nodes for context detection."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def _is_in_main_block(node: ast.AST) -> bool:
    """Check if a node is inside an `if __name__ == '__main__'` block."""
    parent = getattr(node, '_parent', None)
    while parent is not None:
        if isinstance(parent, ast.If):
            test = parent.test
            if isinstance(test, ast.Compare):
                left = test.left
                if (isinstance(left, ast.Name) and left.id == "__name__"
                        and len(test.comparators) == 1):
                    comp = test.comparators[0]
                    if isinstance(comp, ast.Constant) and comp.value == "__main__":
                        return True
        parent = getattr(parent, '_parent', None)
    return False


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def scan_definitions(file_path: str) -> List[Definition]:
    """Scan a Python file and return all top-level definitions."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    _add_parent_refs(tree)

    # Filter out definitions inside __main__ blocks
    visitor = DefinitionVisitor(file_path)
    visitor.visit(tree)

    results = []
    for defn in visitor.definitions:
        # Find the AST node for this definition to check __main__
        skip = False
        for node in ast.walk(tree):
            if (hasattr(node, 'lineno') and node.lineno == defn.line_number
                    and hasattr(node, 'name') if hasattr(node, 'name') else False):
                if getattr(node, 'name', None) == defn.name and _is_in_main_block(node):
                    skip = True
                    break
        if not skip:
            results.append(defn)

    return results


def scan_references(file_path: str) -> List[Reference]:
    """Scan a Python file and return all name references."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    _add_parent_refs(tree)
    visitor = ReferenceVisitor(file_path)
    visitor.visit(tree)
    return visitor.references


def _collect_python_files(directory: str) -> List[str]:
    """Collect all .py files in a directory recursively."""
    files = []
    for root, dirs, filenames in os.walk(directory):
        # Skip dependency/build directories
        dirs[:] = [d for d in dirs if d not in {
            "node_modules", ".next", "dist", "build", "__pycache__",
            "venv", ".venv", "target", "vendor", "Pods", ".git",
        }]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                files.append(os.path.join(root, fn))
    return files


def build_reference_index(lib_dir: str, extra_dirs: Optional[List[str]] = None) -> Dict[str, List[Reference]]:
    """Build an index of all name references across directories.

    Returns a dict mapping name -> list of Reference objects.
    """
    index: Dict[str, List[Reference]] = {}
    dirs_to_scan = [lib_dir]
    if extra_dirs:
        dirs_to_scan.extend(extra_dirs)

    for scan_dir in dirs_to_scan:
        if not os.path.isdir(scan_dir):
            continue
        for fpath in _collect_python_files(scan_dir):
            for ref in scan_references(fpath):
                if ref.name not in index:
                    index[ref.name] = []
                index[ref.name].append(ref)

    return index


def _is_cli_entry_point(file_path: str) -> bool:
    """Check if a file has a __main__ block (CLI entry point)."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Compare):
                left = test.left
                if (isinstance(left, ast.Name) and left.id == "__name__"
                        and len(test.comparators) == 1):
                    comp = test.comparators[0]
                    if isinstance(comp, ast.Constant) and comp.value == "__main__":
                        return True
    return False


def find_dead_code(
    lib_dir: str,
    test_dir: Optional[str] = None,
    extra_scan_dirs: Optional[List[str]] = None,
) -> DeadCodeReport:
    """Find dead code in lib/ directory.

    Args:
        lib_dir: Directory containing library modules to analyze.
        test_dir: Optional test directory to include in reference scanning.
        extra_scan_dirs: Additional directories to scan for references.

    Returns:
        DeadCodeReport with findings sorted by confidence.
    """
    lib_dir = os.path.abspath(lib_dir)

    # Collect all definitions from lib/
    all_definitions: List[Definition] = []
    for fpath in _collect_python_files(lib_dir):
        all_definitions.extend(scan_definitions(fpath))

    # Build reference index across lib/ + tests/ + extra dirs
    scan_dirs = []
    if test_dir and os.path.isdir(test_dir):
        scan_dirs.append(test_dir)
    if extra_scan_dirs:
        scan_dirs.extend(extra_scan_dirs)

    ref_index = build_reference_index(lib_dir, extra_dirs=scan_dirs)

    # Also scan for references in non-lib Python files at project root
    # (scripts, configs, etc.)

    findings: List[DeadCodeFinding] = []

    for defn in all_definitions:
        refs = ref_index.get(defn.name, [])

        # Filter out self-references (definition line itself)
        external_refs = [
            r for r in refs
            if not (r.file_path == defn.file_path and r.line_number == defn.line_number)
        ]

        # For import definitions, check if the name is used within the
        # same file (beyond the import line). An import that is used
        # locally is alive regardless of cross-file references.
        if defn.def_type == "import":
            same_file_uses = [
                r for r in external_refs
                if r.file_path == defn.file_path and r.ref_type != "import"
            ]
            if same_file_uses:
                # Import is used locally -- not dead
                continue
            # Import unused in its own file -- high confidence dead import
            findings.append(DeadCodeFinding(
                definition=defn,
                confidence="high",
                reason=f"Unused import '{defn.name}' in {os.path.basename(defn.file_path)}",
            ))
            continue

        # For function/class definitions: cross-file analysis
        # References in other files
        other_file_refs = [
            r for r in external_refs
            if r.file_path != defn.file_path
        ]

        # References only in test files
        test_only_refs = []
        lib_refs = []
        for r in other_file_refs:
            if test_dir and os.path.abspath(r.file_path).startswith(os.path.abspath(test_dir)):
                test_only_refs.append(r)
            else:
                lib_refs.append(r)

        # Same-file references (excluding the definition line)
        same_file_refs = [
            r for r in external_refs
            if r.file_path == defn.file_path
        ]

        if not external_refs:
            # No references at all -- high confidence dead
            findings.append(DeadCodeFinding(
                definition=defn,
                confidence="high",
                reason=f"No references found anywhere for '{defn.name}'",
            ))
        elif not other_file_refs and same_file_refs:
            # Only referenced in own file
            findings.append(DeadCodeFinding(
                definition=defn,
                confidence="medium",
                reason=f"'{defn.name}' only referenced in its own file ({os.path.basename(defn.file_path)})",
            ))
        elif other_file_refs and not lib_refs and test_only_refs:
            # Only referenced in tests
            findings.append(DeadCodeFinding(
                definition=defn,
                confidence="low",
                reason=f"'{defn.name}' only referenced in test files",
            ))

    # Sort: high first, then medium, then low
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (confidence_order.get(f.confidence, 3), f.definition.file_path, f.definition.line_number))

    # Compute summary
    total_defs = len(all_definitions)
    total_dead = len(findings)
    dead_pct = (total_dead / total_defs * 100) if total_defs > 0 else 0.0

    by_confidence: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        by_confidence[f.confidence] = by_confidence.get(f.confidence, 0) + 1

    return DeadCodeReport(
        findings=findings,
        total_definitions=total_defs,
        total_dead=total_dead,
        dead_percentage=round(dead_pct, 1),
        by_confidence=by_confidence,
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_report(report: DeadCodeReport, min_confidence: str = "low") -> str:
    """Format report as human-readable ASCII text."""
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    min_level = confidence_order.get(min_confidence, 2)

    lines = []
    lines.append("=" * 70)
    lines.append("DEAD CODE REPORT")
    lines.append("=" * 70)
    lines.append(f"Total definitions scanned: {report.total_definitions}")
    lines.append(f"Potentially dead:          {report.total_dead}")
    lines.append(f"Dead percentage:           {report.dead_percentage}%")
    lines.append("")
    lines.append("By confidence:")
    for level in ("high", "medium", "low"):
        count = report.by_confidence.get(level, 0)
        lines.append(f"  {level:8s}: {count}")
    lines.append("")

    filtered = [
        f for f in report.findings
        if confidence_order.get(f.confidence, 2) <= min_level
    ]

    if not filtered:
        lines.append("No dead code found at the specified confidence level.")
    else:
        current_confidence = None
        for finding in filtered:
            if finding.confidence != current_confidence:
                current_confidence = finding.confidence
                lines.append("-" * 70)
                lines.append(f"  [{current_confidence.upper()} CONFIDENCE]")
                lines.append("-" * 70)

            defn = finding.definition
            rel_path = defn.file_path
            lines.append(f"  {defn.def_type:8s}  {defn.name}")
            lines.append(f"           {rel_path}:{defn.line_number}")
            lines.append(f"           {finding.reason}")
            lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def _definition_to_dict(d: Definition) -> dict:
    return {"name": d.name, "file_path": d.file_path, "line_number": d.line_number, "def_type": d.def_type}


def _finding_to_dict(f: DeadCodeFinding) -> dict:
    return {
        "definition": _definition_to_dict(f.definition),
        "confidence": f.confidence,
        "reason": f.reason,
    }


def format_json(report: DeadCodeReport) -> str:
    """Format report as JSON."""
    data = {
        "total_definitions": report.total_definitions,
        "total_dead": report.total_dead,
        "dead_percentage": report.dead_percentage,
        "by_confidence": report.by_confidence,
        "findings": [_finding_to_dict(f) for f in report.findings],
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dead code detector for Python modules",
    )
    parser.add_argument(
        "--lib-dir",
        default="lib",
        help="Directory to scan for definitions (default: lib)",
    )
    parser.add_argument(
        "--test-dir",
        default=None,
        help="Test directory to include in reference scanning",
    )
    parser.add_argument(
        "--extra-dir",
        action="append",
        default=[],
        help="Additional directories to scan for references (can be repeated)",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["high", "medium", "low"],
        default="low",
        help="Minimum confidence level to report (default: low)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON",
    )

    args = parser.parse_args(argv)

    if not os.path.isdir(args.lib_dir):
        print(f"Error: directory '{args.lib_dir}' does not exist", file=sys.stderr)
        return 1

    report = find_dead_code(
        lib_dir=args.lib_dir,
        test_dir=args.test_dir,
        extra_scan_dirs=args.extra_dir if args.extra_dir else None,
    )

    if args.json_output:
        print(format_json(report))
    else:
        print(format_report(report, min_confidence=args.min_confidence))

    return 0


if __name__ == "__main__":
    sys.exit(main())
