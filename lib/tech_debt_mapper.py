"""
Technical Debt Mapper for GRIP.

Scans a codebase and produces a technical debt report with actionable
remediation priorities. Uses heuristic-based scanning (line counts, regex,
indentation) for portability across Python/JS/TS files.

Usage:
    from tech_debt_mapper import map_tech_debt, format_report_text
    report = map_tech_debt("/path/to/project")
    print(format_report_text(report))

    # CLI
    python3 lib/tech_debt_mapper.py /path/to/project
    python3 lib/tech_debt_mapper.py /path/to/project --format slack
    python3 lib/tech_debt_mapper.py /path/to/project --format json
    python3 lib/tech_debt_mapper.py /path/to/project --format summary
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

# Supported source file extensions
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".sh", ".bash", ".zsh",
    ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cpp", ".h",
    ".css", ".scss", ".less", ".html", ".vue", ".svelte",
}

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".next", "dist", "build", "__pycache__", "venv",
    ".venv", "target", "vendor", "Pods", ".git", ".tox", "coverage",
    "htmlcov", ".mypy_cache", ".pytest_cache", "egg-info",
}

# Debt categories
CATEGORIES = (
    "complexity", "duplication", "naming", "dead_code",
    "missing_tests", "deprecated_api", "large_files", "deep_nesting",
)

# Severity weights for scoring
SEVERITY_WEIGHTS = {
    "critical": 10,
    "high": 7,
    "medium": 4,
    "low": 1,
}

# TODO/FIXME patterns
TODO_PATTERN = re.compile(
    r"#\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND)\b[:\s]*(.*)",
    re.IGNORECASE,
)

# Function definition patterns (heuristic, multi-language)
FUNC_PATTERN = re.compile(
    r"^\s*(?:"
    r"def\s+\w+|"                          # Python
    r"(?:async\s+)?function\s+\w+|"        # JS/TS
    r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\(|"  # Arrow
    r"(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?\w+\s*\(|"  # Class methods
    r"func\s+\w+"                          # Go/Swift
    r")"
)

# Import patterns
IMPORT_PATTERN = re.compile(
    r"^\s*(?:"
    r"import\s|"                           # Python/JS/TS
    r"from\s+\S+\s+import|"               # Python
    r"(?:const|let|var)\s+\w+\s*=\s*require\s*\(|"  # CommonJS
    r"require\s*\(|"                       # CommonJS direct
    r"#include\s|"                         # C/C++
    r"using\s"                             # C#
    r")"
)


@dataclass
class TechDebtItem:
    """A single technical debt finding."""
    file: str
    line: int
    category: str
    severity: str
    description: str
    effort_estimate: str  # "low", "medium", "high"

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"Invalid category: {self.category}")
        if self.severity not in SEVERITY_WEIGHTS:
            raise ValueError(f"Invalid severity: {self.severity}")
        if self.effort_estimate not in ("low", "medium", "high"):
            raise ValueError(f"Invalid effort_estimate: {self.effort_estimate}")


@dataclass
class TechDebtReport:
    """Aggregated technical debt report."""
    items: List[TechDebtItem] = field(default_factory=list)
    total_score: int = 0
    hotspots: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


def _iter_source_files(path: Path):
    """Yield source files, skipping dependency/build directories."""
    if path.is_file():
        if path.suffix in SOURCE_EXTENSIONS:
            yield path
        return

    for root, dirs, files in os.walk(path):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            fpath = Path(root) / fname
            if fpath.suffix in SOURCE_EXTENSIONS:
                yield fpath


def _read_lines(filepath: Path) -> Optional[List[str]]:
    """Read file lines safely, returning None for binary/unreadable files."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="strict") as f:
            return f.readlines()
    except (UnicodeDecodeError, PermissionError, OSError):
        return None


def scan_large_files(path: Path, threshold: int = 500) -> List[TechDebtItem]:
    """Find files exceeding a line count threshold."""
    items = []
    for fpath in _iter_source_files(path):
        lines = _read_lines(fpath)
        if lines is None:
            continue
        count = len(lines)
        if count > threshold:
            severity = "critical" if count > threshold * 3 else (
                "high" if count > threshold * 2 else "medium"
            )
            items.append(TechDebtItem(
                file=str(fpath),
                line=1,
                category="large_files",
                severity=severity,
                description=f"File has {count} lines (threshold: {threshold})",
                effort_estimate="high" if count > threshold * 2 else "medium",
            ))
    return items


def scan_todo_fixme(path: Path) -> List[TechDebtItem]:
    """Find TODO/FIXME/HACK/XXX comments with context."""
    items = []
    for fpath in _iter_source_files(path):
        lines = _read_lines(fpath)
        if lines is None:
            continue
        for i, line in enumerate(lines, 1):
            match = TODO_PATTERN.search(line)
            if match:
                tag = match.group(1).upper()
                context = match.group(2).strip()[:120]
                severity = "high" if tag in ("FIXME", "BUG") else (
                    "medium" if tag in ("HACK", "XXX", "WORKAROUND") else "low"
                )
                items.append(TechDebtItem(
                    file=str(fpath),
                    line=i,
                    category="complexity",
                    severity=severity,
                    description=f"{tag}: {context}" if context else tag,
                    effort_estimate="low",
                ))
    return items


def scan_deep_nesting(path: Path, threshold: int = 5) -> List[TechDebtItem]:
    """Find deeply nested code blocks by indentation level."""
    items = []
    for fpath in _iter_source_files(path):
        lines = _read_lines(fpath)
        if lines is None:
            continue
        max_depth = 0
        max_depth_line = 1
        for i, line in enumerate(lines, 1):
            stripped = line.rstrip()
            if not stripped:
                continue
            # Calculate indentation depth
            leading = len(line) - len(line.lstrip())
            # Normalize: tabs=1 unit, spaces=4 per unit
            if "\t" in line[:leading]:
                depth = line[:leading].count("\t")
            else:
                depth = leading // 4
            if depth > max_depth:
                max_depth = depth
                max_depth_line = i

        if max_depth >= threshold:
            severity = "critical" if max_depth >= threshold + 4 else (
                "high" if max_depth >= threshold + 2 else "medium"
            )
            items.append(TechDebtItem(
                file=str(fpath),
                line=max_depth_line,
                category="deep_nesting",
                severity=severity,
                description=f"Max nesting depth: {max_depth} levels (threshold: {threshold})",
                effort_estimate="medium",
            ))
    return items


def scan_long_functions(path: Path, threshold: int = 50) -> List[TechDebtItem]:
    """Find functions exceeding a line count threshold."""
    items = []
    for fpath in _iter_source_files(path):
        lines = _read_lines(fpath)
        if lines is None:
            continue

        func_start = None
        func_name = None
        func_indent = 0

        for i, line in enumerate(lines, 1):
            stripped = line.rstrip()

            # Detect function start
            if FUNC_PATTERN.match(stripped):
                # Close previous function if open
                if func_start is not None:
                    length = i - func_start
                    if length > threshold:
                        severity = "critical" if length > threshold * 3 else (
                            "high" if length > threshold * 2 else "medium"
                        )
                        items.append(TechDebtItem(
                            file=str(fpath),
                            line=func_start,
                            category="complexity",
                            severity=severity,
                            description=f"Function '{func_name}' is {length} lines (threshold: {threshold})",
                            effort_estimate="high" if length > threshold * 2 else "medium",
                        ))
                func_start = i
                func_indent = len(line) - len(line.lstrip())
                # Extract function name heuristic
                name_match = re.search(r"(?:def|function|func)\s+(\w+)", stripped)
                if name_match:
                    func_name = name_match.group(1)
                else:
                    # Arrow function or method
                    name_match = re.search(r"(?:const|let|var)\s+(\w+)", stripped)
                    if name_match:
                        func_name = name_match.group(1)
                    else:
                        func_name = "<anonymous>"

        # Check last function in file
        if func_start is not None and lines:
            length = len(lines) - func_start + 1
            if length > threshold:
                severity = "critical" if length > threshold * 3 else (
                    "high" if length > threshold * 2 else "medium"
                )
                items.append(TechDebtItem(
                    file=str(fpath),
                    line=func_start,
                    category="complexity",
                    severity=severity,
                    description=f"Function '{func_name}' is {length} lines (threshold: {threshold})",
                    effort_estimate="high" if length > threshold * 2 else "medium",
                ))
    return items


def scan_import_complexity(path: Path, threshold: int = 15) -> List[TechDebtItem]:
    """Find files with too many imports."""
    items = []
    for fpath in _iter_source_files(path):
        lines = _read_lines(fpath)
        if lines is None:
            continue
        import_count = sum(1 for line in lines if IMPORT_PATTERN.match(line))
        if import_count > threshold:
            severity = "high" if import_count > threshold * 2 else "medium"
            items.append(TechDebtItem(
                file=str(fpath),
                line=1,
                category="complexity",
                severity=severity,
                description=f"File has {import_count} imports (threshold: {threshold})",
                effort_estimate="medium",
            ))
    return items


# Scanner registry: (function, category_tag)
SCANNERS = [
    scan_large_files,
    scan_todo_fixme,
    scan_deep_nesting,
    scan_long_functions,
    scan_import_complexity,
]


def _compute_score(items: List[TechDebtItem]) -> int:
    """Compute total debt score from items."""
    return sum(SEVERITY_WEIGHTS.get(item.severity, 0) for item in items)


def _find_hotspots(items: List[TechDebtItem], min_categories: int = 3) -> List[str]:
    """Find files that appear in multiple debt categories."""
    file_categories = {}
    for item in items:
        file_categories.setdefault(item.file, set()).add(item.category)

    hotspots = [
        f for f, cats in file_categories.items()
        if len(cats) >= min_categories
    ]
    # Sort by number of categories descending
    hotspots.sort(
        key=lambda f: len(file_categories[f]),
        reverse=True,
    )
    return hotspots


def _generate_recommendations(items: List[TechDebtItem], hotspots: List[str]) -> List[str]:
    """Generate remediation recommendations sorted by effort/impact."""
    recs = []

    # Count by category
    cat_counts = Counter(item.category for item in items)

    # Critical items first
    critical = [i for i in items if i.severity == "critical"]
    if critical:
        recs.append(
            f"Address {len(critical)} critical items first "
            f"(highest impact on code quality)"
        )

    # Hotspot recommendation
    if hotspots:
        recs.append(
            f"Refactor {len(hotspots)} hotspot file(s) that appear in 3+ "
            f"debt categories: {', '.join(Path(h).name for h in hotspots[:5])}"
        )

    # Category-specific recommendations
    if cat_counts.get("large_files", 0) > 0:
        recs.append(
            f"Split {cat_counts['large_files']} oversized file(s) into "
            f"focused modules (extract classes/functions)"
        )

    if cat_counts.get("deep_nesting", 0) > 0:
        recs.append(
            f"Reduce nesting in {cat_counts['deep_nesting']} file(s) "
            f"using early returns, guard clauses, or extraction"
        )

    if cat_counts.get("complexity", 0) > 0:
        complexity_items = [i for i in items if i.category == "complexity"]
        todo_count = sum(1 for i in complexity_items if i.description.startswith(("TODO", "FIXME", "HACK", "XXX", "BUG", "WORKAROUND")))
        func_count = sum(1 for i in complexity_items if "Function" in i.description)
        import_count = sum(1 for i in complexity_items if "imports" in i.description)
        if todo_count:
            recs.append(f"Resolve {todo_count} TODO/FIXME comments (low effort, high signal)")
        if func_count:
            recs.append(f"Break down {func_count} long function(s) into smaller units")
        if import_count:
            recs.append(f"Reduce import complexity in {import_count} file(s)")

    # Effort-based sorting: low effort first
    effort_order = {"low": 0, "medium": 1, "high": 2}
    low_effort_high_impact = [
        i for i in items
        if i.effort_estimate == "low" and i.severity in ("critical", "high")
    ]
    if low_effort_high_impact:
        recs.append(
            f"{len(low_effort_high_impact)} quick wins available "
            f"(low effort, high/critical severity)"
        )

    return recs


def map_tech_debt(
    path: str,
    categories: Optional[List[str]] = None,
) -> TechDebtReport:
    """Run all scanners and produce an aggregated debt report.

    Args:
        path: Directory or file to scan.
        categories: If provided, only include items matching these categories.

    Returns:
        TechDebtReport with items sorted by severity (critical first).
    """
    target = Path(path)
    if not target.exists():
        return TechDebtReport(
            recommendations=["Target path does not exist: " + str(target)]
        )

    all_items = []
    for scanner in SCANNERS:
        try:
            items = scanner(target)
            all_items.extend(items)
        except Exception as e:
            # Don't crash on individual scanner failure
            all_items.append(TechDebtItem(
                file=str(target),
                line=0,
                category="complexity",
                severity="low",
                description=f"Scanner {scanner.__name__} failed: {e}",
                effort_estimate="low",
            ))

    # Filter by categories if specified
    if categories:
        valid_cats = set(categories) & set(CATEGORIES)
        all_items = [i for i in all_items if i.category in valid_cats]

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_items.sort(key=lambda i: severity_order.get(i.severity, 99))

    hotspots = _find_hotspots(all_items)
    recommendations = _generate_recommendations(all_items, hotspots)
    total_score = _compute_score(all_items)

    return TechDebtReport(
        items=all_items,
        total_score=total_score,
        hotspots=hotspots,
        recommendations=recommendations,
    )


# --- Formatters ---


def format_report_text(report: TechDebtReport) -> str:
    """Format report for terminal output."""
    lines = []
    lines.append("=" * 60)
    lines.append("TECHNICAL DEBT REPORT")
    lines.append("=" * 60)
    lines.append(f"Total Score: {report.total_score}")
    lines.append(f"Items Found: {len(report.items)}")
    lines.append(f"Hotspot Files: {len(report.hotspots)}")
    lines.append("")

    if report.items:
        lines.append("--- Items (by severity) ---")
        for item in report.items:
            rel_file = item.file
            lines.append(
                f"  [{item.severity.upper():8s}] {rel_file}:{item.line} "
                f"({item.category}) {item.description}"
            )
        lines.append("")

    if report.hotspots:
        lines.append("--- Hotspot Files ---")
        for h in report.hotspots:
            lines.append(f"  {h}")
        lines.append("")

    if report.recommendations:
        lines.append("--- Recommendations ---")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_report_slack(report: TechDebtReport) -> str:
    """Format report for Slack mrkdwn (*bold* not **bold**)."""
    lines = []
    lines.append("*Technical Debt Report*")
    lines.append(f"Score: *{report.total_score}* | Items: *{len(report.items)}* | Hotspots: *{len(report.hotspots)}*")
    lines.append("")

    if report.items:
        # Group by severity
        for sev in ("critical", "high", "medium", "low"):
            sev_items = [i for i in report.items if i.severity == sev]
            if sev_items:
                lines.append(f"*{sev.upper()}* ({len(sev_items)})")
                for item in sev_items[:10]:  # Cap at 10 per severity for Slack
                    fname = Path(item.file).name
                    lines.append(f"  {fname}:{item.line} - {item.description}")
                if len(sev_items) > 10:
                    lines.append(f"  ...and {len(sev_items) - 10} more")
                lines.append("")

    if report.hotspots:
        lines.append("*Hotspot Files*")
        for h in report.hotspots[:5]:
            lines.append(f"  {Path(h).name}")
        lines.append("")

    if report.recommendations:
        lines.append("*Recommendations*")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"{i}. {rec}")

    return "\n".join(lines)


def format_report_json(report: TechDebtReport) -> str:
    """Format report as JSON with all fields."""
    data = {
        "total_score": report.total_score,
        "item_count": len(report.items),
        "hotspots": report.hotspots,
        "recommendations": report.recommendations,
        "items": [asdict(item) for item in report.items],
    }
    return json.dumps(data, indent=2)


def format_report_summary(report: TechDebtReport) -> str:
    """One-line summary of the report."""
    cat_counts = Counter(item.category for item in report.items)
    sev_counts = Counter(item.severity for item in report.items)
    parts = []
    parts.append(f"Score:{report.total_score}")
    parts.append(f"Items:{len(report.items)}")
    if sev_counts.get("critical"):
        parts.append(f"Crit:{sev_counts['critical']}")
    if sev_counts.get("high"):
        parts.append(f"High:{sev_counts['high']}")
    parts.append(f"Hotspots:{len(report.hotspots)}")
    return " | ".join(parts)


FORMATTERS = {
    "text": format_report_text,
    "slack": format_report_slack,
    "json": format_report_json,
    "summary": format_report_summary,
}


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scan a codebase for technical debt and produce a report."
    )
    parser.add_argument("path", help="Directory or file to scan")
    parser.add_argument(
        "--format", "-f",
        choices=["text", "slack", "json", "summary"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--categories", "-c",
        nargs="+",
        choices=list(CATEGORIES),
        help="Filter to specific debt categories",
    )

    args = parser.parse_args()
    report = map_tech_debt(args.path, categories=args.categories)
    formatter = FORMATTERS[args.format]
    print(formatter(report))


if __name__ == "__main__":
    main()
