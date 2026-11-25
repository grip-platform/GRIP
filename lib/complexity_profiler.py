"""AST-based Python code complexity profiler.

Analyzes Python code complexity using McCabe cyclomatic complexity,
identifies hotspots, and provides actionable refactoring suggestions.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FunctionMetrics:
    """Metrics for a single function or method."""

    name: str
    filepath: str
    line: int
    complexity: int
    lines_of_code: int
    parameters: int
    nested_depth: int
    is_async: bool


@dataclass
class FileMetrics:
    """Aggregate metrics for a single file."""

    filepath: str
    total_functions: int
    avg_complexity: float
    max_complexity: int
    total_loc: int
    functions: list[FunctionMetrics] = field(default_factory=list)


@dataclass
class ComplexityReport:
    """Full complexity report across multiple files."""

    files: list[FileMetrics] = field(default_factory=list)
    total_functions: int = 0
    avg_complexity: float = 0.0
    hotspots: list[FunctionMetrics] = field(default_factory=list)
    grade: str = "A"
    recommendations: list[str] = field(default_factory=list)


def _count_boolean_operators(node: ast.AST) -> int:
    """Count boolean operators (and/or) in an expression."""
    count = 0
    if isinstance(node, ast.BoolOp):
        # Each additional value beyond the first adds a branch
        count += len(node.values) - 1
    for child in ast.iter_child_nodes(node):
        count += _count_boolean_operators(child)
    return count


def calculate_complexity(node: ast.AST) -> int:
    """Calculate McCabe cyclomatic complexity for an AST node.

    Counts decision points: if, elif, for, while, except, with,
    and, or, assert, and comprehensions.

    Args:
        node: AST node (typically a FunctionDef or AsyncFunctionDef).

    Returns:
        Cyclomatic complexity score (minimum 1).
    """
    complexity = 1  # Base complexity

    for child in ast.walk(node):
        if isinstance(child, ast.If):
            complexity += 1
        elif isinstance(child, ast.For):
            complexity += 1
        elif isinstance(child, ast.AsyncFor):
            complexity += 1
        elif isinstance(child, ast.While):
            complexity += 1
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, ast.With):
            complexity += 1
        elif isinstance(child, ast.AsyncWith):
            complexity += 1
        elif isinstance(child, ast.Assert):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            # and/or each add branches per extra operand
            complexity += len(child.values) - 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # Each comprehension adds complexity for its generators and ifs
            for generator in child.generators:
                complexity += 1  # for the 'for' clause
                complexity += len(generator.ifs)  # for each 'if' clause

    return complexity


def _calculate_nested_depth(node: ast.AST) -> int:
    """Calculate maximum nesting depth within a function body."""

    def _walk_depth(n: ast.AST, current: int) -> int:
        max_depth = current
        nesting_types = (
            ast.If, ast.For, ast.AsyncFor, ast.While,
            ast.With, ast.AsyncWith, ast.Try,
        )
        # Python 3.11+ has TryStar
        try:
            nesting_types = nesting_types + (ast.TryStar,)
        except AttributeError:
            pass

        for child in ast.iter_child_nodes(n):
            if isinstance(child, nesting_types):
                depth = _walk_depth(child, current + 1)
                max_depth = max(max_depth, depth)
            else:
                depth = _walk_depth(child, current)
                max_depth = max(max_depth, depth)
        return max_depth

    return _walk_depth(node, 0)


def _count_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count function parameters excluding 'self' and 'cls'."""
    args = node.args
    count = 0
    for arg in args.args:
        if arg.arg not in ("self", "cls"):
            count += 1
    count += len(args.posonlyargs)
    count += len(args.kwonlyargs)
    if args.vararg:
        count += 1
    if args.kwarg:
        count += 1
    return count


def _count_function_loc(node: ast.AST) -> int:
    """Count lines of code in a function body (non-empty, non-comment)."""
    if not hasattr(node, "end_lineno") or not hasattr(node, "lineno"):
        return 0
    return node.end_lineno - node.lineno + 1


def analyze_function(node: ast.FunctionDef | ast.AsyncFunctionDef, filepath: str) -> FunctionMetrics:
    """Analyze a single function or method node.

    Args:
        node: AST FunctionDef or AsyncFunctionDef node.
        filepath: Path to the source file.

    Returns:
        FunctionMetrics for the function.
    """
    return FunctionMetrics(
        name=node.name,
        filepath=filepath,
        line=node.lineno,
        complexity=calculate_complexity(node),
        lines_of_code=_count_function_loc(node),
        parameters=_count_parameters(node),
        nested_depth=_calculate_nested_depth(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def analyze_file(filepath: str) -> FileMetrics:
    """Analyze all functions in a Python file.

    Args:
        filepath: Path to the Python file.

    Returns:
        FileMetrics with per-function breakdown.

    Raises:
        SyntaxError: If the file contains invalid Python syntax.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(filepath)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=filepath)

    functions: list[FunctionMetrics] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            metrics = analyze_function(node, str(filepath))
            functions.append(metrics)

    total_loc = len([line for line in source.splitlines() if line.strip()])

    if functions:
        avg_complexity = sum(f.complexity for f in functions) / len(functions)
        max_complexity = max(f.complexity for f in functions)
    else:
        avg_complexity = 0.0
        max_complexity = 0

    return FileMetrics(
        filepath=str(filepath),
        total_functions=len(functions),
        avg_complexity=round(avg_complexity, 2),
        max_complexity=max_complexity,
        total_loc=total_loc,
        functions=functions,
    )


def _matches_pattern(filepath: str, patterns: list[str]) -> bool:
    """Check if filepath matches any of the exclude patterns."""
    from fnmatch import fnmatch

    for pattern in patterns:
        if fnmatch(filepath, pattern) or fnmatch(os.path.basename(filepath), pattern):
            return True
        # Also check if any path component matches
        parts = Path(filepath).parts
        for part in parts:
            if fnmatch(part, pattern.rstrip("/*").rstrip("/")):
                return True
    return False


def _compute_grade(avg_complexity: float) -> str:
    """Compute letter grade from average complexity."""
    if avg_complexity < 5:
        return "A"
    elif avg_complexity < 10:
        return "B"
    elif avg_complexity < 20:
        return "C"
    elif avg_complexity < 40:
        return "D"
    else:
        return "F"


def _generate_recommendations(report: ComplexityReport) -> list[str]:
    """Generate actionable refactoring recommendations."""
    recs: list[str] = []
    all_functions = []
    for fm in report.files:
        all_functions.extend(fm.functions)

    high_complexity = [f for f in all_functions if f.complexity > 10]
    deep_nesting = [f for f in all_functions if f.nested_depth > 3]
    many_params = [f for f in all_functions if f.parameters > 5]
    long_functions = [f for f in all_functions if f.lines_of_code > 50]

    if high_complexity:
        names = ", ".join(f.name for f in high_complexity[:5])
        recs.append(
            f"Extract helper functions: {len(high_complexity)} function(s) have "
            f"complexity >10 ({names})"
        )
    if deep_nesting:
        names = ", ".join(f.name for f in deep_nesting[:5])
        recs.append(
            f"Use early returns/guard clauses: {len(deep_nesting)} function(s) have "
            f"nesting depth >3 ({names})"
        )
    if many_params:
        names = ", ".join(f.name for f in many_params[:5])
        recs.append(
            f"Use parameter objects: {len(many_params)} function(s) have "
            f">5 parameters ({names})"
        )
    if long_functions:
        names = ", ".join(f.name for f in long_functions[:5])
        recs.append(
            f"Split into smaller functions: {len(long_functions)} function(s) exceed "
            f"50 lines ({names})"
        )

    if not recs:
        recs.append("Codebase complexity is well-managed. No immediate action needed.")

    return recs


def analyze_directory(
    path: str,
    exclude_patterns: Optional[list[str]] = None,
) -> ComplexityReport:
    """Analyze all Python files in a directory recursively.

    Args:
        path: Directory path to scan.
        exclude_patterns: Glob patterns to exclude (e.g., ["tests/*", "venv/*"]).

    Returns:
        ComplexityReport with aggregate metrics.
    """
    if exclude_patterns is None:
        exclude_patterns = []

    root = Path(path)
    report = ComplexityReport()

    py_files: list[Path] = []
    if root.is_file() and root.suffix == ".py":
        py_files.append(root)
    elif root.is_dir():
        for py_file in sorted(root.rglob("*.py")):
            rel_path = str(py_file.relative_to(root))
            if not _matches_pattern(rel_path, exclude_patterns):
                py_files.append(py_file)

    for py_file in py_files:
        try:
            file_metrics = analyze_file(str(py_file))
            report.files.append(file_metrics)
        except (SyntaxError, UnicodeDecodeError):
            # Skip files that can't be parsed
            continue

    # Aggregate metrics
    all_functions: list[FunctionMetrics] = []
    for fm in report.files:
        all_functions.extend(fm.functions)

    report.total_functions = len(all_functions)

    if all_functions:
        report.avg_complexity = round(
            sum(f.complexity for f in all_functions) / len(all_functions), 2
        )
    else:
        report.avg_complexity = 0.0

    # Hotspot detection: complexity > 10, sorted descending, top 10
    hotspots = sorted(
        [f for f in all_functions if f.complexity > 10],
        key=lambda f: f.complexity,
        reverse=True,
    )
    report.hotspots = hotspots[:10]

    report.grade = _compute_grade(report.avg_complexity)
    report.recommendations = _generate_recommendations(report)

    return report


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_GRADE_BADGES = {
    "A": "[A] Excellent",
    "B": "[B] Good",
    "C": "[C] Needs Attention",
    "D": "[D] Refactor Needed",
    "F": "[F] Unmaintainable",
}


def format_complexity_text(report: ComplexityReport) -> str:
    """Format report as terminal text with grade badge and table.

    Args:
        report: ComplexityReport to format.

    Returns:
        Human-readable text output.
    """
    lines: list[str] = []
    badge = _GRADE_BADGES.get(report.grade, report.grade)
    lines.append(f"Complexity Report  Grade: {badge}")
    lines.append("=" * 60)
    lines.append(
        f"Files: {len(report.files)}  "
        f"Functions: {report.total_functions}  "
        f"Avg Complexity: {report.avg_complexity}"
    )
    lines.append("")

    if report.hotspots:
        lines.append("HOTSPOTS (complexity > 10)")
        lines.append("-" * 60)
        lines.append(f"{'Function':<30} {'File':<20} {'Line':>5} {'CC':>4}")
        lines.append("-" * 60)
        for h in report.hotspots:
            short_path = os.path.basename(h.filepath)
            lines.append(f"{h.name:<30} {short_path:<20} {h.line:>5} {h.complexity:>4}")
        lines.append("")

    if report.files:
        lines.append("FILE SUMMARY")
        lines.append("-" * 60)
        lines.append(f"{'File':<35} {'Funcs':>6} {'Avg CC':>7} {'Max CC':>7} {'LOC':>5}")
        lines.append("-" * 60)
        for fm in sorted(report.files, key=lambda f: f.max_complexity, reverse=True):
            short_path = os.path.basename(fm.filepath)
            lines.append(
                f"{short_path:<35} {fm.total_functions:>6} "
                f"{fm.avg_complexity:>7.1f} {fm.max_complexity:>7} {fm.total_loc:>5}"
            )
        lines.append("")

    if report.recommendations:
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 60)
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    return "\n".join(lines)


def format_complexity_slack(report: ComplexityReport) -> str:
    """Format report as Slack mrkdwn.

    Uses Slack formatting: *bold*, _italic_, `code`.

    Args:
        report: ComplexityReport to format.

    Returns:
        Slack mrkdwn formatted string.
    """
    lines: list[str] = []
    badge = _GRADE_BADGES.get(report.grade, report.grade)
    lines.append(f"*Complexity Report*  Grade: *{badge}*")
    lines.append(
        f"Files: {len(report.files)} | "
        f"Functions: {report.total_functions} | "
        f"Avg Complexity: {report.avg_complexity}"
    )
    lines.append("")

    if report.hotspots:
        lines.append("*Hotspots* (complexity > 10)")
        for h in report.hotspots:
            short_path = os.path.basename(h.filepath)
            lines.append(f"  `{h.name}` in _{short_path}_ line {h.line} - CC: *{h.complexity}*")
        lines.append("")

    if report.recommendations:
        lines.append("*Recommendations*")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    return "\n".join(lines)


def format_complexity_json(report: ComplexityReport) -> str:
    """Format report as full JSON.

    Args:
        report: ComplexityReport to format.

    Returns:
        JSON string.
    """
    data = {
        "grade": report.grade,
        "total_functions": report.total_functions,
        "avg_complexity": report.avg_complexity,
        "hotspots": [
            {
                "name": h.name,
                "filepath": h.filepath,
                "line": h.line,
                "complexity": h.complexity,
                "lines_of_code": h.lines_of_code,
                "parameters": h.parameters,
                "nested_depth": h.nested_depth,
                "is_async": h.is_async,
            }
            for h in report.hotspots
        ],
        "files": [
            {
                "filepath": fm.filepath,
                "total_functions": fm.total_functions,
                "avg_complexity": fm.avg_complexity,
                "max_complexity": fm.max_complexity,
                "total_loc": fm.total_loc,
                "functions": [
                    {
                        "name": f.name,
                        "line": f.line,
                        "complexity": f.complexity,
                        "lines_of_code": f.lines_of_code,
                        "parameters": f.parameters,
                        "nested_depth": f.nested_depth,
                        "is_async": f.is_async,
                    }
                    for f in fm.functions
                ],
            }
            for fm in report.files
        ],
        "recommendations": report.recommendations,
    }
    return json.dumps(data, indent=2)


def _complexity_color(complexity: float) -> str:
    """Map complexity to a color category for concept maps."""
    if complexity < 5:
        return "low"
    elif complexity < 10:
        return "medium"
    elif complexity < 20:
        return "high"
    else:
        return "critical"


def format_complexity_concept_map(report: ComplexityReport) -> dict:
    """Format report as a concept map dict for grip_concept_map MCP app.

    Files become nodes; functions with high complexity become linked nodes.
    Node types encode complexity levels for color coding.

    Args:
        report: ComplexityReport to format.

    Returns:
        Dict with 'title', 'nodes', and 'edges' keys.
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    for fm in report.files:
        file_id = f"file_{os.path.basename(fm.filepath).replace('.', '_')}"
        nodes.append({
            "id": file_id,
            "label": os.path.basename(fm.filepath),
            "type": _complexity_color(fm.avg_complexity),
        })

        for func in fm.functions:
            if func.complexity > 5:
                func_id = f"func_{func.name}_{func.line}"
                nodes.append({
                    "id": func_id,
                    "label": f"{func.name} (CC:{func.complexity})",
                    "type": _complexity_color(func.complexity),
                })
                edges.append({
                    "from": file_id,
                    "to": func_id,
                    "label": f"CC:{func.complexity}",
                    "style": "solid" if func.complexity < 15 else "dashed",
                })

    return {
        "title": f"Complexity Map - Grade: {report.grade}",
        "nodes": nodes,
        "edges": edges,
    }


def format_complexity_progress(report: ComplexityReport) -> dict:
    """Format report as a progress dict for grip_progress MCP app.

    Each file becomes a track with its average complexity as progress
    toward the maximum on a 0-40 scale (where 40 = grade F threshold).

    Args:
        report: ComplexityReport to format.

    Returns:
        Dict with 'title', 'style', and 'tracks' keys.
    """
    tracks: list[dict] = []
    for fm in report.files:
        grade = _compute_grade(fm.avg_complexity)
        tracks.append({
            "id": os.path.basename(fm.filepath).replace(".", "_"),
            "label": f"{os.path.basename(fm.filepath)} [{grade}]",
            "current": round(fm.avg_complexity, 1),
            "total": 40,
            "unit": "CC",
            "milestones": [
                {"at": 5, "label": "A/B"},
                {"at": 10, "label": "B/C"},
                {"at": 20, "label": "C/D"},
                {"at": 40, "label": "D/F"},
            ],
        })

    return {
        "title": f"Complexity Progress - Grade: {report.grade}",
        "style": "bar",
        "tracks": tracks,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze Python code complexity using AST-based metrics.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory to analyze (default: current directory)",
    )
    parser.add_argument(
        "--exclude",
        default="tests/*,venv/*,__pycache__/*,node_modules/*,.git/*",
        help="Comma-separated exclude patterns (default: tests/*,venv/*,...)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "concept_map", "progress"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Hotspot complexity threshold (default: 10)",
    )

    args = parser.parse_args()
    exclude_patterns = [p.strip() for p in args.exclude.split(",") if p.strip()]

    report = analyze_directory(args.path, exclude_patterns)

    # Re-apply custom threshold if different from default
    if args.threshold != 10:
        all_functions = []
        for fm in report.files:
            all_functions.extend(fm.functions)
        hotspots = sorted(
            [f for f in all_functions if f.complexity > args.threshold],
            key=lambda f: f.complexity,
            reverse=True,
        )
        report.hotspots = hotspots[:10]

    formatters = {
        "text": format_complexity_text,
        "slack": format_complexity_slack,
        "json": format_complexity_json,
        "concept_map": lambda r: json.dumps(format_complexity_concept_map(r), indent=2),
        "progress": lambda r: json.dumps(format_complexity_progress(r), indent=2),
    }

    output = formatters[args.format](report)
    print(output)


if __name__ == "__main__":
    main()
