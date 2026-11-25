#!/usr/bin/env python3
"""Batch Test Validator — parallel test runner with aggregated results and badge generation.

Discovers and runs all test files in parallel, aggregates pass/fail/skip/error counts,
generates SVG badges, and outputs results in terminal, JSON, or markdown format.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileResult:
    """Result of running a single test file."""

    path: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    failure_details: str = ""
    returncode: int = 0
    crashed: bool = False
    crash_output: str = ""


@dataclass
class AggregateResult:
    """Aggregated results across all test files."""

    file_results: list = field(default_factory=list)
    total_passed: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    total_duration: float = 0.0
    wall_duration: float = 0.0

    @property
    def total_tests(self) -> int:
        return self.total_passed + self.total_failed + self.total_skipped + self.total_errors

    @property
    def status(self) -> str:
        if not self.file_results:
            return "no_tests"
        if self.total_failed == 0 and self.total_errors == 0:
            return "passed"
        if self.total_passed == 0:
            return "failed"
        return "partial"


def discover_test_files(
    root: str, pattern: Optional[str] = None
) -> list[str]:
    """Find all test_*.py files under root/tests/."""
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        return []

    files = []
    for entry in sorted(os.listdir(tests_dir)):
        if entry.startswith("test_") and entry.endswith(".py"):
            if pattern is None or fnmatch.fnmatch(entry, pattern):
                files.append(os.path.join(tests_dir, entry))
    return files


def run_single_test(filepath: str, root: str) -> FileResult:
    """Run a single test file via pytest and parse results."""
    result = FileResult(path=filepath)
    start = time.monotonic()

    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                filepath,
                "-v",
                "--tb=short",
                "--no-header",
                "-q",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
        )
        result.returncode = proc.returncode
        output = proc.stdout + proc.stderr

        # Parse the summary line: "X passed, Y failed, Z skipped, W errors"
        result.passed, result.failed, result.skipped, result.errors = _parse_pytest_summary(
            output
        )

        # Capture failure details
        if result.failed > 0 or result.errors > 0:
            result.failure_details = _extract_failures(output)

    except subprocess.TimeoutExpired:
        result.crashed = True
        result.crash_output = "Test file timed out after 120s"
        result.errors = 1
    except Exception as exc:
        result.crashed = True
        result.crash_output = str(exc)
        result.errors = 1

    result.duration = time.monotonic() - start
    return result


def _parse_pytest_summary(output: str) -> tuple[int, int, int, int]:
    """Extract counts from pytest output summary line."""
    passed = failed = skipped = errors = 0

    # Look for lines like "5 passed", "2 failed", "1 skipped", "1 error"
    # The summary is typically on the last non-empty line or a line with these keywords
    for line in output.splitlines():
        line_lower = line.lower().strip()
        if "passed" in line_lower or "failed" in line_lower or "error" in line_lower or "skipped" in line_lower:
            import re

            m = re.findall(r"(\d+)\s+(passed|failed|skipped|error|errors|warnings?|deselected)", line_lower)
            for count_str, label in m:
                count = int(count_str)
                if label == "passed":
                    passed = count
                elif label == "failed":
                    failed = count
                elif label == "skipped":
                    skipped = count
                elif label in ("error", "errors"):
                    errors = count

    return passed, failed, skipped, errors


def _extract_failures(output: str) -> str:
    """Extract failure/error tracebacks from pytest output."""
    lines = output.splitlines()
    failure_lines = []
    capturing = False

    for line in lines:
        if line.startswith("FAILED") or line.startswith("ERROR") or "FAILURES" in line or "ERRORS" in line:
            capturing = True
        if capturing:
            failure_lines.append(line)
        if capturing and line.startswith("=") and ("passed" in line or "failed" in line or "error" in line):
            break

    return "\n".join(failure_lines) if failure_lines else ""


def run_tests(
    root: str,
    pattern: Optional[str] = None,
    workers: int = 4,
    fail_fast: bool = False,
) -> AggregateResult:
    """Discover and run tests with configurable parallelism."""
    files = discover_test_files(root, pattern)
    aggregate = AggregateResult()

    if not files:
        return aggregate

    wall_start = time.monotonic()

    if fail_fast:
        # Sequential with early exit
        for filepath in files:
            result = run_single_test(filepath, root)
            aggregate.file_results.append(result)
            aggregate.total_passed += result.passed
            aggregate.total_failed += result.failed
            aggregate.total_skipped += result.skipped
            aggregate.total_errors += result.errors
            aggregate.total_duration += result.duration
            if result.failed > 0 or result.errors > 0:
                break
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_single_test, f, root): f for f in files}
            for future in as_completed(futures):
                result = future.result()
                aggregate.file_results.append(result)
                aggregate.total_passed += result.passed
                aggregate.total_failed += result.failed
                aggregate.total_skipped += result.skipped
                aggregate.total_errors += result.errors
                aggregate.total_duration += result.duration

    # Sort results by file path for deterministic output
    aggregate.file_results.sort(key=lambda r: r.path)
    aggregate.wall_duration = time.monotonic() - wall_start
    return aggregate


def generate_badge_svg(status: str, passed: int, failed: int, total: int) -> str:
    """Generate an SVG badge string for test results."""
    if status == "passed":
        color = "#4c1"
        label = f"{passed} passed"
    elif status == "failed":
        color = "#e05d44"
        label = f"{failed} failed"
    elif status == "partial":
        color = "#dfb317"
        label = f"{passed}/{total} passed"
    else:
        color = "#9f9f9f"
        label = "no tests"

    label_width = max(len(label) * 7 + 10, 50)
    subject = "tests"
    subject_width = 42
    total_width = subject_width + label_width

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20">
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <mask id="a">
    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
  </mask>
  <g mask="url(#a)">
    <rect width="{subject_width}" height="20" fill="#555"/>
    <rect x="{subject_width}" width="{label_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#b)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{subject_width / 2}" y="15" fill="#010101" fill-opacity=".3">{subject}</text>
    <text x="{subject_width / 2}" y="14">{subject}</text>
    <text x="{subject_width + label_width / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{subject_width + label_width / 2}" y="14">{label}</text>
  </g>
</svg>"""


def format_terminal(aggregate: AggregateResult) -> str:
    """Format results for terminal display."""
    lines = []
    lines.append("=" * 60)
    lines.append("BATCH TEST VALIDATOR RESULTS")
    lines.append("=" * 60)

    for fr in aggregate.file_results:
        name = os.path.basename(fr.path)
        status = "PASS" if fr.failed == 0 and fr.errors == 0 else "FAIL"
        if fr.crashed:
            status = "CRASH"
        lines.append(
            f"  {status:5s}  {name:40s}  "
            f"{fr.passed}P {fr.failed}F {fr.skipped}S {fr.errors}E  "
            f"{fr.duration:.2f}s"
        )

    lines.append("-" * 60)
    lines.append(
        f"Total: {aggregate.total_passed} passed, "
        f"{aggregate.total_failed} failed, "
        f"{aggregate.total_skipped} skipped, "
        f"{aggregate.total_errors} errors"
    )
    lines.append(f"Files: {len(aggregate.file_results)}")
    lines.append(f"Wall time: {aggregate.wall_duration:.2f}s")
    lines.append(f"CPU time:  {aggregate.total_duration:.2f}s")
    lines.append(f"Status: {aggregate.status.upper()}")

    # Failure details
    failures = [fr for fr in aggregate.file_results if fr.failure_details or fr.crash_output]
    if failures:
        lines.append("")
        lines.append("FAILURE DETAILS:")
        lines.append("-" * 60)
        for fr in failures:
            name = os.path.basename(fr.path)
            lines.append(f"\n--- {name} ---")
            if fr.crash_output:
                lines.append(f"CRASH: {fr.crash_output}")
            if fr.failure_details:
                lines.append(fr.failure_details)

    return "\n".join(lines)


def format_json(aggregate: AggregateResult) -> str:
    """Format results as JSON."""
    data = {
        "status": aggregate.status,
        "summary": {
            "total": aggregate.total_tests,
            "passed": aggregate.total_passed,
            "failed": aggregate.total_failed,
            "skipped": aggregate.total_skipped,
            "errors": aggregate.total_errors,
            "files": len(aggregate.file_results),
            "wall_duration": round(aggregate.wall_duration, 3),
            "cpu_duration": round(aggregate.total_duration, 3),
        },
        "files": [
            {
                "path": fr.path,
                "passed": fr.passed,
                "failed": fr.failed,
                "skipped": fr.skipped,
                "errors": fr.errors,
                "duration": round(fr.duration, 3),
                "crashed": fr.crashed,
                "failure_details": fr.failure_details if fr.failure_details else None,
                "crash_output": fr.crash_output if fr.crash_output else None,
            }
            for fr in aggregate.file_results
        ],
    }
    return json.dumps(data, indent=2)


def format_markdown(aggregate: AggregateResult) -> str:
    """Format results as markdown summary."""
    lines = []
    lines.append("# Test Results")
    lines.append("")
    lines.append(
        f"**Status**: {aggregate.status.upper()} | "
        f"**Passed**: {aggregate.total_passed} | "
        f"**Failed**: {aggregate.total_failed} | "
        f"**Skipped**: {aggregate.total_skipped} | "
        f"**Errors**: {aggregate.total_errors}"
    )
    lines.append("")
    lines.append(f"**Files**: {len(aggregate.file_results)} | "
                 f"**Wall time**: {aggregate.wall_duration:.2f}s | "
                 f"**CPU time**: {aggregate.total_duration:.2f}s")
    lines.append("")

    if aggregate.file_results:
        lines.append("| File | Passed | Failed | Skipped | Errors | Time |")
        lines.append("|------|--------|--------|---------|--------|------|")
        for fr in aggregate.file_results:
            name = os.path.basename(fr.path)
            status_marker = "" if (fr.failed == 0 and fr.errors == 0) else " :x:"
            lines.append(
                f"| {name}{status_marker} | {fr.passed} | {fr.failed} | "
                f"{fr.skipped} | {fr.errors} | {fr.duration:.2f}s |"
            )

    failures = [fr for fr in aggregate.file_results if fr.failure_details or fr.crash_output]
    if failures:
        lines.append("")
        lines.append("## Failures")
        for fr in failures:
            name = os.path.basename(fr.path)
            lines.append(f"\n### {name}")
            lines.append("```")
            if fr.crash_output:
                lines.append(f"CRASH: {fr.crash_output}")
            if fr.failure_details:
                lines.append(fr.failure_details)
            lines.append("```")

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Batch Test Validator — parallel test runner with badge generation"
    )
    parser.add_argument(
        "--root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Project root directory (default: auto-detected)",
    )
    parser.add_argument(
        "--pattern",
        default=None,
        help="Glob pattern to filter test files (e.g. 'test_converge*')",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first test file failure",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output results as markdown",
    )
    parser.add_argument(
        "--badge",
        action="store_true",
        help="Output SVG badge",
    )

    args = parser.parse_args(argv)
    aggregate = run_tests(
        root=args.root,
        pattern=args.pattern,
        workers=args.workers,
        fail_fast=args.fail_fast,
    )

    if args.badge:
        print(
            generate_badge_svg(
                aggregate.status,
                aggregate.total_passed,
                aggregate.total_failed,
                aggregate.total_tests,
            )
        )
    elif args.json_output:
        print(format_json(aggregate))
    elif args.markdown:
        print(format_markdown(aggregate))
    else:
        print(format_terminal(aggregate))

    # Exit code: 0 if all passed, 1 if any failures
    return 0 if aggregate.status in ("passed", "no_tests") else 1


if __name__ == "__main__":
    sys.exit(main())
