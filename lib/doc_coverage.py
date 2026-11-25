#!/usr/bin/env python3
"""Scan Python codebases for undocumented public functions, classes, and modules.

Produces coverage reports showing documented vs missing docstrings with quality scoring.
"""
from __future__ import annotations

import ast
import json
import fnmatch
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DocItem:
    """A single documentable item found via AST scanning."""

    name: str
    kind: str  # function, class, module, method
    filepath: str
    line: int
    has_docstring: bool
    docstring_quality: float = 0.0


@dataclass
class DocCoverageReport:
    """Aggregated documentation coverage report."""

    total: int = 0
    documented: int = 0
    undocumented: int = 0
    coverage_pct: float = 0.0
    items: list[DocItem] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Docstring quality scoring
# ---------------------------------------------------------------------------

_PARAM_MARKERS = ("Args:", "Parameters:", "Params:", ":param ", "param:", "Arguments:")
_RETURN_MARKERS = ("Returns:", "Return:", ":returns:", ":return:", "-> ", ":rtype:")
_EXAMPLE_MARKERS = ("Example:", "Examples:", ">>>", ".. code-block::", "Usage:")


def score_docstring(docstring: Optional[str], kind: str = "function") -> float:
    """Score docstring quality on a 0-1.0 scale.

    Checks:
      - description present (0.3)
      - parameters documented (0.3 for function/method, 0.15 for class)
      - return type documented (0.2 for function/method, 0.15 for class)
      - examples present (0.2 for function/method, 0.1 for class)
      - class: __init__ params (0.15), attributes (0.15)
    """
    if not docstring or not docstring.strip():
        return 0.0

    text = docstring.strip()
    score = 0.0

    # 1. Description present (non-empty first line)
    first_line = text.split("\n")[0].strip()
    if first_line:
        score += 0.3

    if kind in ("function", "method"):
        # 2. Parameters documented
        if any(marker in text for marker in _PARAM_MARKERS):
            score += 0.3
        # 3. Return type documented
        if any(marker in text for marker in _RETURN_MARKERS):
            score += 0.2
        # 4. Examples present
        if any(marker in text for marker in _EXAMPLE_MARKERS):
            score += 0.2

    elif kind == "class":
        # 2. Parameters / __init__ documented
        if any(marker in text for marker in _PARAM_MARKERS):
            score += 0.15
        # 3. Attributes documented
        if any(kw in text for kw in ("Attributes:", "Attrs:", ":ivar ", ":var ")):
            score += 0.15
        # 4. Return type (less relevant for classes, reuse for init docs)
        if any(marker in text for marker in _RETURN_MARKERS):
            score += 0.15
        # 5. Examples
        if any(marker in text for marker in _EXAMPLE_MARKERS):
            score += 0.1
        # Cap at 1.0 — a really thorough class docstring can max out
        score = min(score, 1.0)

    elif kind == "module":
        # Modules: description is most important
        # Reward longer descriptions
        lines = [l for l in text.split("\n") if l.strip()]
        if len(lines) >= 3:
            score += 0.3
        elif len(lines) >= 2:
            score += 0.15
        # Examples in module docstring
        if any(marker in text for marker in _EXAMPLE_MARKERS):
            score += 0.2
        score = min(score, 1.0)

    return round(min(score, 1.0), 2)


def quality_label(score: float) -> str:
    """Map a quality score to a human label."""
    if score < 0.3:
        return "poor"
    if score < 0.6:
        return "basic"
    if score < 0.8:
        return "good"
    return "excellent"


# ---------------------------------------------------------------------------
# AST-based scanning
# ---------------------------------------------------------------------------

def _is_public(name: str) -> bool:
    """Return True if the name is considered public (not _private)."""
    if name.startswith("__") and name.endswith("__"):
        return True  # dunder methods like __init__
    return not name.startswith("_")


def _get_docstring(node: ast.AST) -> Optional[str]:
    """Extract the docstring from an AST node, or None."""
    return ast.get_docstring(node)


def scan_file(filepath: str) -> list[DocItem]:
    """Parse a single Python file and return all public documentable items.

    Finds:
      - Module-level docstring
      - Top-level public functions
      - Top-level public classes
      - Public methods within classes
    """
    filepath = str(filepath)
    items: list[DocItem] = []

    try:
        source = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return items

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return items

    # Module docstring
    mod_doc = _get_docstring(tree)
    has_mod_doc = mod_doc is not None and bool(mod_doc.strip())
    mod_quality = score_docstring(mod_doc, "module") if has_mod_doc else 0.0
    items.append(DocItem(
        name=Path(filepath).stem,
        kind="module",
        filepath=filepath,
        line=1,
        has_docstring=has_mod_doc,
        docstring_quality=mod_quality,
    ))

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            if _is_public(node.name):
                doc = _get_docstring(node)
                has_doc = doc is not None and bool(doc.strip())
                quality = score_docstring(doc, "function") if has_doc else 0.0
                items.append(DocItem(
                    name=node.name,
                    kind="function",
                    filepath=filepath,
                    line=node.lineno,
                    has_docstring=has_doc,
                    docstring_quality=quality,
                ))

        elif isinstance(node, ast.ClassDef):
            if _is_public(node.name):
                doc = _get_docstring(node)
                has_doc = doc is not None and bool(doc.strip())
                quality = score_docstring(doc, "class") if has_doc else 0.0
                items.append(DocItem(
                    name=node.name,
                    kind="class",
                    filepath=filepath,
                    line=node.lineno,
                    has_docstring=has_doc,
                    docstring_quality=quality,
                ))

                # Scan methods within the class
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _is_public(child.name):
                            mdoc = _get_docstring(child)
                            has_mdoc = mdoc is not None and bool(mdoc.strip())
                            mq = score_docstring(mdoc, "method") if has_mdoc else 0.0
                            items.append(DocItem(
                                name=f"{node.name}.{child.name}",
                                kind="method",
                                filepath=filepath,
                                line=child.lineno,
                                has_docstring=has_mdoc,
                                docstring_quality=mq,
                            ))

    return items


def scan_directory(
    path: str,
    exclude_patterns: Optional[list[str]] = None,
    skip_tests: bool = True,
) -> list[DocItem]:
    """Recursively scan a directory for Python files and return all public items.

    Args:
        path: Root directory to scan.
        exclude_patterns: Glob patterns to exclude (e.g. ["tests/*", "venv/*"]).
        skip_tests: If True, skip files matching test_*.py or *_test.py.
    """
    exclude_patterns = exclude_patterns or []
    root = Path(path)
    items: list[DocItem] = []

    if not root.is_dir():
        # Single file
        if root.is_file() and root.suffix == ".py":
            return scan_file(str(root))
        return items

    for py_file in sorted(root.rglob("*.py")):
        rel = str(py_file.relative_to(root))

        # Check exclude patterns
        skip = False
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(rel, pattern):
                skip = True
                break
        if skip:
            continue

        # Skip test files
        if skip_tests:
            basename = py_file.name
            if basename.startswith("test_") or basename.endswith("_test.py"):
                continue

        items.extend(scan_file(str(py_file)))

    return items


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _generate_recommendations(coverage_pct: float, items: list[DocItem]) -> list[str]:
    """Generate actionable recommendations based on coverage analysis."""
    recs: list[str] = []

    if coverage_pct < 30:
        recs.append("Critical: Coverage below 30%. Start by documenting all public classes and module docstrings.")
    elif coverage_pct < 50:
        recs.append("Low coverage. Prioritize documenting public API functions and classes.")
    elif coverage_pct < 70:
        recs.append("Moderate coverage. Focus on adding parameter and return documentation to existing docstrings.")
    elif coverage_pct < 90:
        recs.append("Good coverage. Fill remaining gaps and improve docstring quality scores.")
    else:
        recs.append("Excellent coverage. Maintain quality and add examples where missing.")

    # Find undocumented classes (high impact)
    undoc_classes = [i for i in items if i.kind == "class" and not i.has_docstring]
    if undoc_classes:
        recs.append(f"Document {len(undoc_classes)} undocumented class(es): {', '.join(c.name for c in undoc_classes[:5])}")

    # Find undocumented modules
    undoc_modules = [i for i in items if i.kind == "module" and not i.has_docstring]
    if undoc_modules:
        recs.append(f"Add module docstrings to {len(undoc_modules)} file(s).")

    # Find poor quality docstrings
    poor_docs = [i for i in items if i.has_docstring and i.docstring_quality < 0.3]
    if poor_docs:
        recs.append(f"Improve {len(poor_docs)} poor-quality docstring(s) (score < 0.3).")

    return recs


def analyze_coverage(
    path: str,
    exclude: Optional[list[str]] = None,
    skip_tests: bool = True,
) -> DocCoverageReport:
    """Analyze documentation coverage for a Python codebase.

    Args:
        path: File or directory to scan.
        exclude: Glob patterns to exclude.
        skip_tests: Skip test files.

    Returns:
        DocCoverageReport with coverage metrics and recommendations.
    """
    items = scan_directory(path, exclude_patterns=exclude, skip_tests=skip_tests)

    total = len(items)
    documented = sum(1 for i in items if i.has_docstring)
    undocumented = total - documented
    coverage_pct = round((documented / total * 100) if total > 0 else 0.0, 1)

    recs = _generate_recommendations(coverage_pct, items)

    return DocCoverageReport(
        total=total,
        documented=documented,
        undocumented=undocumented,
        coverage_pct=coverage_pct,
        items=items,
        recommendations=recs,
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _coverage_bar(pct: float, width: int = 30) -> str:
    """Generate a text-based coverage bar."""
    filled = int(pct / 100 * width)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {pct:.1f}%"


def format_coverage_text(report: DocCoverageReport) -> str:
    """Format report for terminal output with coverage bar."""
    lines: list[str] = []
    lines.append("Documentation Coverage Report")
    lines.append("=" * 40)
    lines.append("")
    lines.append(f"  Total items:      {report.total}")
    lines.append(f"  Documented:       {report.documented}")
    lines.append(f"  Undocumented:     {report.undocumented}")
    lines.append(f"  Coverage:         {_coverage_bar(report.coverage_pct)}")
    lines.append("")

    # Group undocumented by file
    undoc = [i for i in report.items if not i.has_docstring]
    if undoc:
        lines.append("Undocumented Items")
        lines.append("-" * 40)
        by_file: dict[str, list[DocItem]] = {}
        for item in undoc:
            by_file.setdefault(item.filepath, []).append(item)

        for filepath, file_items in sorted(by_file.items()):
            lines.append(f"  {filepath}")
            for item in sorted(file_items, key=lambda x: x.line):
                lines.append(f"    L{item.line}: {item.kind} {item.name}")
        lines.append("")

    # Quality summary for documented items
    documented = [i for i in report.items if i.has_docstring]
    if documented:
        lines.append("Docstring Quality")
        lines.append("-" * 40)
        for label_name in ("poor", "basic", "good", "excellent"):
            count = sum(1 for i in documented if quality_label(i.docstring_quality) == label_name)
            if count:
                lines.append(f"  {label_name}: {count}")
        lines.append("")

    if report.recommendations:
        lines.append("Recommendations")
        lines.append("-" * 40)
        for rec in report.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def format_coverage_slack(report: DocCoverageReport) -> str:
    """Format report for Slack using mrkdwn (*bold*, _italic_)."""
    lines: list[str] = []
    lines.append("*Documentation Coverage Report*")
    lines.append("")

    pct = report.coverage_pct
    if pct >= 80:
        emoji = ":white_check_mark:"
    elif pct >= 50:
        emoji = ":warning:"
    else:
        emoji = ":x:"

    lines.append(f"{emoji} *Coverage: {pct:.1f}%* ({report.documented}/{report.total} items)")
    lines.append("")

    # Undocumented summary
    undoc = [i for i in report.items if not i.has_docstring]
    if undoc:
        lines.append(f"*{report.undocumented} undocumented items:*")
        by_kind: dict[str, int] = {}
        for item in undoc:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        for kind, count in sorted(by_kind.items()):
            lines.append(f"  - {count} {kind}(s)")
        lines.append("")

    # Quality breakdown
    documented = [i for i in report.items if i.has_docstring]
    if documented:
        lines.append("*Quality breakdown:*")
        for label_name in ("excellent", "good", "basic", "poor"):
            count = sum(1 for i in documented if quality_label(i.docstring_quality) == label_name)
            if count:
                lines.append(f"  - _{label_name}_: {count}")
        lines.append("")

    if report.recommendations:
        lines.append("*Recommendations:*")
        for rec in report.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def format_coverage_json(report: DocCoverageReport) -> str:
    """Format report as full JSON."""
    data = {
        "total": report.total,
        "documented": report.documented,
        "undocumented": report.undocumented,
        "coverage_pct": report.coverage_pct,
        "items": [
            {
                "name": i.name,
                "kind": i.kind,
                "filepath": i.filepath,
                "line": i.line,
                "has_docstring": i.has_docstring,
                "docstring_quality": i.docstring_quality,
                "quality_label": quality_label(i.docstring_quality) if i.has_docstring else None,
            }
            for i in report.items
        ],
        "recommendations": report.recommendations,
    }
    return json.dumps(data, indent=2)


def format_coverage_gap(report: DocCoverageReport) -> dict:
    """Format report as a dict matching grip_gap_visualiser MCP app schema.

    Returns dimensions with current/desired scores per file.
    """
    # Group items by file
    by_file: dict[str, list[DocItem]] = {}
    for item in report.items:
        by_file.setdefault(item.filepath, []).append(item)

    dimensions: list[dict] = []
    for filepath, file_items in sorted(by_file.items()):
        total = len(file_items)
        documented = sum(1 for i in file_items if i.has_docstring)
        current_score = round((documented / total * 10) if total > 0 else 0, 1)

        # Average quality for documented items
        doc_items = [i for i in file_items if i.has_docstring]
        avg_quality = 0.0
        if doc_items:
            avg_quality = sum(i.docstring_quality for i in doc_items) / len(doc_items)

        basename = Path(filepath).name
        dim_id = basename.replace(".", "_")

        dimensions.append({
            "id": dim_id,
            "label": basename,
            "current": current_score,
            "desired": 10,
            "currentText": f"{documented}/{total} documented (avg quality: {avg_quality:.1f})",
            "desiredText": "Full documentation with good+ quality",
        })

    return {
        "title": "Documentation Coverage Gap Analysis",
        "mode": "quantitative",
        "scale": {"min": 0, "max": 10},
        "dimensions": dimensions,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entrypoint."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Scan Python codebases for documentation coverage.",
    )
    parser.add_argument("path", nargs="?", default=".", help="Path to scan (default: current directory)")
    parser.add_argument("--exclude", nargs="*", default=[], help="Glob patterns to exclude")
    parser.add_argument("--format", dest="fmt", default="text", choices=["text", "slack", "json", "gap"],
                        help="Output format (default: text)")
    parser.add_argument("--include-tests", action="store_true", help="Include test files in scan")

    args = parser.parse_args()

    report = analyze_coverage(args.path, exclude=args.exclude, skip_tests=not args.include_tests)

    if args.fmt == "text":
        print(format_coverage_text(report))
    elif args.fmt == "slack":
        print(format_coverage_slack(report))
    elif args.fmt == "json":
        print(format_coverage_json(report))
    elif args.fmt == "gap":
        print(json.dumps(format_coverage_gap(report), indent=2))


if __name__ == "__main__":
    main()
