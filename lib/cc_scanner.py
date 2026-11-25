#!/usr/bin/env python3
"""CC Scanner — scans Python files for cyclomatic complexity offenders.

Used by /rsi and /sprint-loop to auto-generate decomposition issues.
Reuses complexity_profiler.calculate_complexity() as the canonical McCabe calculator.

Usage:
    python3 lib/cc_scanner.py [--threshold 15] [--format json|table] [path]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Ensure lib/ is importable
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from complexity_profiler import (
    FunctionMetrics,
    analyze_file,
    _compute_grade,
)

# Directories to always skip
SKIP_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    ".git", ".tox", ".mypy_cache", ".pytest_cache", "eggs", ".eggs",
}


@dataclass
class CCOffender:
    """A function exceeding the CC threshold."""

    file: str
    function: str
    cc: int
    grade: str
    line: int


def scan_directory(
    path: str | Path,
    threshold: int = 15,
    skip_tests: bool = False,
) -> list[CCOffender]:
    """Scan a directory for CC offenders above threshold.

    Args:
        path: Directory or file to scan.
        threshold: Minimum CC to report.
        skip_tests: Whether to skip test files.

    Returns:
        List of CCOffender sorted by CC descending.
    """
    root = Path(path).resolve()
    offenders: list[CCOffender] = []

    py_files = _collect_py_files(root, skip_tests)

    for py_file in py_files:
        try:
            file_metrics = analyze_file(str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for func in file_metrics.functions:
            if func.complexity >= threshold:
                rel_path = str(py_file.relative_to(root)) if py_file.is_relative_to(root) else str(py_file)
                offenders.append(CCOffender(
                    file=rel_path,
                    function=func.name,
                    cc=func.complexity,
                    grade=_compute_grade(func.complexity),
                    line=func.line,
                ))

    offenders.sort(key=lambda o: o.cc, reverse=True)
    return offenders


def _collect_py_files(root: Path, skip_tests: bool) -> list[Path]:
    """Collect Python files, skipping excluded directories."""
    if root.is_file():
        return [root] if root.suffix == ".py" else []

    py_files: list[Path] = []
    for py_file in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        if skip_tests and ("test_" in py_file.name or py_file.name.endswith("_test.py")):
            continue
        py_files.append(py_file)
    return py_files


def format_table(offenders: list[CCOffender]) -> str:
    """Format offenders as a terminal table."""
    if not offenders:
        return "No functions exceed the threshold."

    lines = [
        f"{'Function':<35} {'File':<35} {'Line':>5} {'CC':>4} {'Grade':>6}",
        "-" * 88,
    ]
    for o in offenders:
        short_file = o.file if len(o.file) <= 34 else "..." + o.file[-31:]
        lines.append(f"{o.function:<35} {short_file:<35} {o.line:>5} {o.cc:>4} {o.grade:>6}")

    lines.append("")
    lines.append(f"Total offenders: {len(offenders)}")
    return "\n".join(lines)


def format_json(offenders: list[CCOffender]) -> str:
    """Format offenders as JSON."""
    return json.dumps([asdict(o) for o in offenders], indent=2)


def snapshot(path: str | Path, threshold: int = 15) -> dict:
    """Generate a snapshot dict for caching (used by dashboard).

    Returns:
        Dict with timestamp, threshold, offender count, and offender list.
    """
    from datetime import datetime, timezone

    offenders = scan_directory(path, threshold)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "threshold": threshold,
        "total_offenders": len(offenders),
        "offenders": [asdict(o) for o in offenders],
    }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scan Python files for cyclomatic complexity offenders.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File or directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=15,
        help="CC threshold for reporting (default: 15)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "table"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip test files",
    )
    parser.add_argument(
        "--snapshot",
        metavar="OUTPUT",
        help="Write JSON snapshot to file (for dashboard caching)",
    )

    args = parser.parse_args()

    if args.snapshot:
        data = snapshot(args.path, args.threshold)
        Path(args.snapshot).write_text(json.dumps(data, indent=2))
        print(f"Snapshot written to {args.snapshot} ({data['total_offenders']} offenders)")
        return

    offenders = scan_directory(args.path, args.threshold, args.skip_tests)

    if args.format == "json":
        print(format_json(offenders))
    else:
        print(format_table(offenders))


if __name__ == "__main__":
    main()
