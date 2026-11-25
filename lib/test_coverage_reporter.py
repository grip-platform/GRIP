#!/usr/bin/env python3
"""Test Coverage Reporter — maps test files to source files, identifies gaps.

Scans a test directory for test_*.py files and a source directory for *.py files,
then maps which source modules have corresponding tests and which don't.

Usage:
    python3 lib/test_coverage_reporter.py
    python3 lib/test_coverage_reporter.py --json
    python3 lib/test_coverage_reporter.py --gaps-only --priority
    python3 lib/test_coverage_reporter.py --lib-dir src/ --test-dir tests/
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TestFile:
    """Represents a discovered test file."""

    path: Path
    module_name: str  # inferred source module name (e.g. "foo" from "test_foo.py")
    test_count: int = 0  # number of test functions/methods
    parse_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "module_name": self.module_name,
            "test_count": self.test_count,
            "parse_error": self.parse_error,
        }


@dataclass
class SourceFile:
    """Represents a discovered source file."""

    path: Path
    size_bytes: int = 0
    line_count: int = 0

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "line_count": self.line_count,
        }


@dataclass
class CoverageGap:
    """A source file with no corresponding test file."""

    source: SourceFile
    priority: int = 0  # higher = more important to test (based on file size)

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "priority": self.priority,
        }


@dataclass
class Orphan:
    """A test file whose corresponding source module does not exist."""

    test: TestFile
    expected_module: str

    def to_dict(self) -> dict:
        return {
            "test": self.test.to_dict(),
            "expected_module": self.expected_module,
        }


@dataclass
class CoverageReport:
    """Full coverage mapping report."""

    total_source: int = 0
    tested_count: int = 0
    gap_count: int = 0
    orphan_count: int = 0
    coverage_pct: float = 0.0
    total_test_functions: int = 0

    matched: list = field(default_factory=list)  # list of (SourceFile, TestFile) pairs
    gaps: list = field(default_factory=list)  # list of CoverageGap
    orphans: list = field(default_factory=list)  # list of Orphan
    test_files: list = field(default_factory=list)  # all TestFile objects
    source_files: list = field(default_factory=list)  # all SourceFile objects

    def to_dict(self) -> dict:
        return {
            "summary": {
                "total_source": self.total_source,
                "tested_count": self.tested_count,
                "gap_count": self.gap_count,
                "orphan_count": self.orphan_count,
                "coverage_pct": round(self.coverage_pct, 1),
                "total_test_functions": self.total_test_functions,
            },
            "matched": [
                {"source": s.to_dict(), "test": t.to_dict()}
                for s, t in self.matched
            ],
            "gaps": [g.to_dict() for g in self.gaps],
            "orphans": [o.to_dict() for o in self.orphans],
        }


# ---------------------------------------------------------------------------
# Scanner functions
# ---------------------------------------------------------------------------

def scan_source_files(lib_dir: Path) -> list[SourceFile]:
    """Scan a directory for Python source files.

    Excludes __init__.py, __pycache__, and non-.py files.
    Only scans the immediate directory (non-recursive by default).
    """
    if not lib_dir.is_dir():
        return []

    results: list[SourceFile] = []
    for p in sorted(lib_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix != ".py":
            continue
        if p.name.startswith("__"):
            continue
        if p.name.startswith("test_"):
            continue

        size = p.stat().st_size
        try:
            line_count = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            line_count = 0

        results.append(SourceFile(path=p, size_bytes=size, line_count=line_count))

    return results


def extract_module_name(test_filename: str) -> Optional[str]:
    """Extract the source module name from a test filename.

    test_foo.py -> foo
    test_foo_bar.py -> foo_bar
    Returns None if the filename doesn't match the test_*.py pattern.
    """
    if not test_filename.startswith("test_"):
        return None
    if not test_filename.endswith(".py"):
        return None
    # Strip "test_" prefix and ".py" suffix
    return test_filename[5:-3]


def count_test_functions(file_path: Path) -> tuple[int, Optional[str]]:
    """Count test functions/methods in a Python file using AST parsing.

    Returns (count, error_message). error_message is None on success.
    Counts:
    - Top-level functions starting with 'test_' or 'test'
    - Methods starting with 'test_' or 'test' inside classes
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        return 0, f"SyntaxError: {e.msg} (line {e.lineno})"
    except OSError as e:
        return 0, f"OSError: {e}"

    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") or node.name.startswith("test"):
                # Only count if name starts with test_ (standard) or is exactly
                # a test method. We use test_ prefix as the primary indicator.
                if node.name.startswith("test_") or (
                    node.name.startswith("test") and len(node.name) > 4
                    and (node.name[4].isupper() or node.name[4] == "_")
                ):
                    count += 1

    return count, None


def scan_test_files(test_dir: Path) -> list[TestFile]:
    """Scan a directory for test files (test_*.py).

    Excludes __init__.py, conftest.py, and non-test files.
    """
    if not test_dir.is_dir():
        return []

    results: list[TestFile] = []
    for p in sorted(test_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix != ".py":
            continue
        if not p.name.startswith("test_"):
            continue

        module_name = extract_module_name(p.name)
        if module_name is None:
            continue

        test_count, parse_error = count_test_functions(p)
        results.append(TestFile(
            path=p,
            module_name=module_name,
            test_count=test_count,
            parse_error=parse_error,
        ))

    return results


# ---------------------------------------------------------------------------
# Mapping and analysis
# ---------------------------------------------------------------------------

def find_source_for_module(
    module_name: str,
    source_files: list[SourceFile],
    search_dirs: Optional[list[Path]] = None,
) -> Optional[SourceFile]:
    """Find a source file matching the given module name.

    First checks the pre-scanned source_files list, then optionally searches
    additional directories.
    """
    target_name = f"{module_name}.py"

    # Check pre-scanned source files
    for sf in source_files:
        if sf.path.name == target_name:
            return sf

    # Check additional search directories
    if search_dirs:
        for d in search_dirs:
            candidate = d / target_name
            if candidate.is_file():
                size = candidate.stat().st_size
                try:
                    line_count = len(
                        candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                    )
                except OSError:
                    line_count = 0
                return SourceFile(path=candidate, size_bytes=size, line_count=line_count)

    return None


def build_coverage_report(
    source_files: list[SourceFile],
    test_files: list[TestFile],
    search_dirs: Optional[list[Path]] = None,
) -> CoverageReport:
    """Build the full coverage report by mapping tests to sources.

    Args:
        source_files: All discovered source files.
        test_files: All discovered test files.
        search_dirs: Additional directories to search for source modules
                     (used for orphan detection).
    """
    report = CoverageReport()
    report.source_files = list(source_files)
    report.test_files = list(test_files)
    report.total_source = len(source_files)
    report.total_test_functions = sum(t.test_count for t in test_files)

    # Build source name -> SourceFile index
    source_by_name: dict[str, SourceFile] = {}
    for sf in source_files:
        stem = sf.path.stem  # e.g. "foo" from "foo.py"
        source_by_name[stem] = sf

    # Track which source files have been matched
    matched_sources: set[str] = set()

    for tf in test_files:
        if tf.module_name in source_by_name:
            sf = source_by_name[tf.module_name]
            report.matched.append((sf, tf))
            matched_sources.add(tf.module_name)
        else:
            # Check additional search dirs before declaring orphan
            found = find_source_for_module(tf.module_name, [], search_dirs)
            if found:
                report.matched.append((found, tf))
                matched_sources.add(tf.module_name)
            else:
                report.orphans.append(Orphan(
                    test=tf,
                    expected_module=f"{tf.module_name}.py",
                ))

    report.tested_count = len(report.matched)
    report.orphan_count = len(report.orphans)

    # Identify gaps: source files with no matching test
    for sf in source_files:
        stem = sf.path.stem
        if stem not in matched_sources:
            report.gaps.append(CoverageGap(source=sf))

    report.gap_count = len(report.gaps)

    # Calculate coverage percentage
    if report.total_source > 0:
        report.coverage_pct = (report.tested_count / report.total_source) * 100.0
    else:
        report.coverage_pct = 0.0

    # Assign priority to gaps (larger files = higher priority)
    assign_gap_priorities(report.gaps)

    return report


def assign_gap_priorities(gaps: list[CoverageGap]) -> None:
    """Assign priority rankings to coverage gaps based on file size.

    Priority 1 = highest (largest file). Ties broken by path name.
    """
    if not gaps:
        return

    # Sort by size descending, then by path for stable ordering
    sorted_gaps = sorted(gaps, key=lambda g: (-g.source.size_bytes, str(g.source.path)))

    for i, gap in enumerate(sorted_gaps):
        gap.priority = i + 1

    # Re-sort original list by priority
    gaps.sort(key=lambda g: g.priority)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_markdown(report: CoverageReport, gaps_only: bool = False,
                    orphans_only: bool = False, summary_only: bool = False,
                    priority: bool = False) -> str:
    """Format the coverage report as markdown."""
    lines: list[str] = []

    # Summary section (always shown unless specific filter)
    if not orphans_only or summary_only:
        lines.append("# Test Coverage Report")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Source files | {report.total_source} |")
        lines.append(f"| Tested | {report.tested_count} |")
        lines.append(f"| Gaps | {report.gap_count} |")
        lines.append(f"| Orphans | {report.orphan_count} |")
        lines.append(f"| Coverage | {report.coverage_pct:.1f}% |")
        lines.append(f"| Test functions | {report.total_test_functions} |")
        lines.append("")

    if summary_only:
        return "\n".join(lines)

    # Gaps section
    if not orphans_only:
        if gaps_only or not orphans_only:
            lines.append("## Coverage Gaps")
            lines.append("")
            if report.gaps:
                if priority:
                    lines.append("| Priority | Source File | Size (bytes) | Lines |")
                    lines.append("|----------|------------|--------------|-------|")
                    for gap in report.gaps:
                        lines.append(
                            f"| {gap.priority} | `{gap.source.path.name}` "
                            f"| {gap.source.size_bytes} | {gap.source.line_count} |"
                        )
                else:
                    lines.append("| Source File | Size (bytes) | Lines |")
                    lines.append("|------------|--------------|-------|")
                    for gap in report.gaps:
                        lines.append(
                            f"| `{gap.source.path.name}` "
                            f"| {gap.source.size_bytes} | {gap.source.line_count} |"
                        )
            else:
                lines.append("No coverage gaps found.")
            lines.append("")

    if gaps_only:
        return "\n".join(lines)

    # Orphans section
    if not gaps_only:
        lines.append("## Orphan Tests")
        lines.append("")
        if report.orphans:
            lines.append("| Test File | Expected Module | Tests |")
            lines.append("|-----------|----------------|-------|")
            for orphan in report.orphans:
                lines.append(
                    f"| `{orphan.test.path.name}` "
                    f"| `{orphan.expected_module}` "
                    f"| {orphan.test.test_count} |"
                )
        else:
            lines.append("No orphan tests found.")
        lines.append("")

    # Matched section (full report only)
    if not gaps_only and not orphans_only:
        lines.append("## Matched (Source <-> Test)")
        lines.append("")
        if report.matched:
            lines.append("| Source File | Test File | Test Count |")
            lines.append("|------------|-----------|------------|")
            for sf, tf in report.matched:
                lines.append(
                    f"| `{sf.path.name}` | `{tf.path.name}` | {tf.test_count} |"
                )
        else:
            lines.append("No matched pairs found.")
        lines.append("")

    return "\n".join(lines)


def format_json(report: CoverageReport, gaps_only: bool = False,
                orphans_only: bool = False, summary_only: bool = False) -> str:
    """Format the coverage report as JSON."""
    data = report.to_dict()

    if summary_only:
        data = {"summary": data["summary"]}
    elif gaps_only:
        data = {"summary": data["summary"], "gaps": data["gaps"]}
    elif orphans_only:
        data = {"summary": data["summary"], "orphans": data["orphans"]}

    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Test Coverage Reporter — map test files to source files, identify gaps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--lib-dir",
        type=Path,
        default=None,
        help="Source directory to scan (default: lib/ relative to repo root)",
    )
    parser.add_argument(
        "--test-dir",
        type=Path,
        default=None,
        help="Test directory to scan (default: tests/ relative to repo root)",
    )
    parser.add_argument(
        "--search-dirs",
        type=Path,
        nargs="*",
        default=None,
        help="Additional directories to search for source modules (skills/, hooks/)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON instead of markdown",
    )
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help="Show only coverage gaps",
    )
    parser.add_argument(
        "--orphans",
        action="store_true",
        help="Show only orphan tests",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show only the summary",
    )
    parser.add_argument(
        "--priority",
        action="store_true",
        help="Include priority ranking in gap output (larger files = higher priority)",
    )
    return parser


def resolve_repo_root() -> Path:
    """Try to find the repo root by looking for common markers."""
    # Walk up from this file's location
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # Fallback to cwd
    return Path.cwd()


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = resolve_repo_root()

    lib_dir = args.lib_dir or (repo_root / "lib")
    test_dir = args.test_dir or (repo_root / "tests")

    search_dirs = args.search_dirs
    if search_dirs is None:
        # Default additional search directories
        candidates = [repo_root / "skills", repo_root / "hooks"]
        search_dirs = [d for d in candidates if d.is_dir()]

    # Scan
    source_files = scan_source_files(lib_dir)
    test_files = scan_test_files(test_dir)

    # Build report
    report = build_coverage_report(source_files, test_files, search_dirs)

    # Output
    if args.output_json:
        output = format_json(
            report,
            gaps_only=args.gaps_only,
            orphans_only=args.orphans,
            summary_only=args.summary,
        )
    else:
        output = format_markdown(
            report,
            gaps_only=args.gaps_only,
            orphans_only=args.orphans,
            summary_only=args.summary,
            priority=args.priority,
        )

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
