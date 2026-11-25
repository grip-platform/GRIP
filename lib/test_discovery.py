"""GRIP Test Discovery Runner -- unified test discovery and execution."""

import ast
import fnmatch
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from enum import Enum

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))


class TestStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestFunction:
    name: str
    file_path: str
    line_number: int
    is_class_method: bool = False
    class_name: str = ""


@dataclass
class TestFile:
    path: str
    test_count: int
    tests: List[TestFunction]
    last_modified: float = 0.0


@dataclass
class TestResult:
    file_path: str
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float
    output: str = ""
    failures: List[str] = field(default_factory=list)


@dataclass
class TestSuite:
    files: List[TestFile]
    results: List[TestResult]
    total_duration: float
    total_passed: int
    total_failed: int
    total_skipped: int
    total_errors: int


@dataclass
class CoverageEntry:
    lib_file: str
    test_file: Optional[str]
    covered: bool


def parse_test_file(path: str) -> TestFile:
    """Parse a test file with AST to extract test functions and classes.

    Does not import the file -- purely static analysis.
    """
    file_path = str(path)
    tests: List[TestFunction] = []

    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return TestFile(path=file_path, test_count=0, tests=[], last_modified=0.0)

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return TestFile(path=file_path, test_count=0, tests=[], last_modified=0.0)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            tests.append(TestFunction(
                name=node.name,
                file_path=file_path,
                line_number=node.lineno,
                is_class_method=False,
                class_name="",
            ))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    tests.append(TestFunction(
                        name=item.name,
                        file_path=file_path,
                        line_number=item.lineno,
                        is_class_method=True,
                        class_name=node.name,
                    ))

    try:
        mtime = os.path.getmtime(file_path)
    except OSError:
        mtime = 0.0

    return TestFile(
        path=file_path,
        test_count=len(tests),
        tests=tests,
        last_modified=mtime,
    )


def discover_tests(test_dir: Optional[str] = None) -> List[TestFile]:
    """Scan a directory for test_*.py files and parse them.

    Args:
        test_dir: Directory to scan. Defaults to CLAUDE_DIR / "tests".

    Returns:
        Sorted list of TestFile objects.
    """
    if test_dir is None:
        test_dir = str(CLAUDE_DIR / "tests")

    test_path = Path(test_dir)
    if not test_path.is_dir():
        return []

    files: List[TestFile] = []
    for py_file in sorted(test_path.glob("test_*.py")):
        tf = parse_test_file(str(py_file))
        files.append(tf)

    return files


def _parse_pytest_output(output: str) -> Tuple[int, int, int, int, List[str]]:
    """Parse pytest -q output for pass/fail/skip/error counts and failure messages."""
    passed = failed = skipped = errors = 0
    failures: List[str] = []

    # Pytest summary line has counts in any order, e.g.:
    #   "5 passed in 0.12s"
    #   "1 failed, 1 passed in 0.03s"
    #   "3 passed, 2 failed, 1 skipped in 1.50s"
    summary_line_pattern = re.compile(r"[\d]+ (?:passed|failed|skipped|error|warning).*in\s+[\d.]+s")

    for line in output.splitlines():
        if summary_line_pattern.search(line):
            # Extract individual counts from this line
            for m in re.finditer(r"(\d+) (passed|failed|skipped|error)", line):
                count = int(m.group(1))
                kind = m.group(2)
                if kind == "passed":
                    passed = count
                elif kind == "failed":
                    failed = count
                elif kind == "skipped":
                    skipped = count
                elif kind == "error":
                    errors = count
            break

    # Extract FAILED lines
    for line in output.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("FAILED "):
            failures.append(line_stripped)

    return passed, failed, skipped, errors, failures


def _runner_python() -> str:
    """Path to an interpreter that can actually run pytest.

    A bare ``"python3"`` resolves to whatever is first on PATH -- on a machine
    where that is Homebrew's, it has no pytest. The subprocess then returns
    NORMALLY with an import error on stderr, so the ``FileNotFoundError`` arm
    below never fires (``python3`` exists; pytest is what is missing),
    ``_parse_pytest_output`` matches no summary line, and every counter reads
    zero with ``failures=[]`` -- a success-shaped answer for a run that never
    happened. That is the defect reported as GRIP#5852, and
    ``rules/never-system-python-for-grip.md`` mandate 3 forbids the bare spawn.

    Resolved per call rather than into a module-level constant, because
    ``CLAUDE_DIR`` above is read at import time while the conftest autouse
    fixture redirects it per test -- a constant would freeze whichever value
    won that race. Self-locates ``lib/`` so the import does not depend on the
    caller's sys.path.
    """
    import sys

    lib_dir = str(Path(__file__).resolve().parent)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    try:
        from _compat import grip_python
    except ImportError:
        # sys.executable is, by construction, an interpreter that runs.
        return sys.executable
    return grip_python(CLAUDE_DIR)


def run_test_file(path: str, timeout: int = 120) -> TestResult:
    """Run a single test file via pytest subprocess.

    Args:
        path: Path to the test file.
        timeout: Timeout in seconds.

    Returns:
        TestResult with parsed counts and output.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [_runner_python(), "-m", "pytest", path, "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(path) or ".",
        )
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return TestResult(
            file_path=path,
            passed=0, failed=0, skipped=0, errors=1,
            duration=duration,
            output=f"TIMEOUT after {timeout}s",
            failures=[f"Timeout: test execution exceeded {timeout}s"],
        )
    except FileNotFoundError:
        duration = time.monotonic() - start
        return TestResult(
            file_path=path,
            passed=0, failed=0, skipped=0, errors=1,
            duration=duration,
            output="pytest not found",
            failures=[f"Error: {_runner_python()} -m pytest not available"],
        )

    duration = time.monotonic() - start
    passed, failed, skipped, errors, failures = _parse_pytest_output(output)

    return TestResult(
        file_path=path,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration=duration,
        output=output,
        failures=failures,
    )


def run_suite(
    files: List[TestFile],
    parallel: int = 1,
    filter_pattern: Optional[str] = None,
    timeout: int = 120,
) -> TestSuite:
    """Run all test files, optionally in parallel.

    Args:
        files: List of discovered TestFile objects.
        parallel: Number of parallel workers (1 = sequential).
        filter_pattern: Glob pattern to filter test file names.
        timeout: Per-file timeout in seconds.

    Returns:
        TestSuite with aggregated results.
    """
    # Apply filter
    if filter_pattern:
        files = [f for f in files if fnmatch.fnmatch(Path(f.path).name, filter_pattern)]

    results: List[TestResult] = []
    suite_start = time.monotonic()

    if parallel <= 1 or len(files) <= 1:
        for tf in files:
            results.append(run_test_file(tf.path, timeout=timeout))
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(run_test_file, tf.path, timeout): tf for tf in files}
            for future in as_completed(futures):
                results.append(future.result())

    suite_duration = time.monotonic() - suite_start

    return TestSuite(
        files=files,
        results=results,
        total_duration=suite_duration,
        total_passed=sum(r.passed for r in results),
        total_failed=sum(r.failed for r in results),
        total_skipped=sum(r.skipped for r in results),
        total_errors=sum(r.errors for r in results),
    )


def compute_coverage(
    test_dir: Optional[str] = None,
    lib_dir: Optional[str] = None,
) -> List[CoverageEntry]:
    """Check which lib/*.py files have corresponding test files.

    Convention: lib/foo.py -> tests/test_foo.py

    Args:
        test_dir: Directory with test files. Defaults to CLAUDE_DIR / "tests".
        lib_dir: Directory with library files. Defaults to CLAUDE_DIR / "lib".

    Returns:
        List of CoverageEntry objects sorted by lib file name.
    """
    if test_dir is None:
        test_dir = str(CLAUDE_DIR / "tests")
    if lib_dir is None:
        lib_dir = str(CLAUDE_DIR / "lib")

    lib_path = Path(lib_dir)
    test_path = Path(test_dir)

    if not lib_path.is_dir():
        return []

    # Build set of existing test file stems
    test_stems = set()
    if test_path.is_dir():
        for tf in test_path.glob("test_*.py"):
            # test_foo.py -> foo
            stem = tf.stem
            if stem.startswith("test_"):
                test_stems.add(stem[5:])

    entries: List[CoverageEntry] = []
    for lib_file in sorted(lib_path.glob("*.py")):
        if lib_file.name.startswith("_"):
            continue
        stem = lib_file.stem
        test_name = f"test_{stem}.py"
        covered = stem in test_stems
        entries.append(CoverageEntry(
            lib_file=str(lib_file),
            test_file=str(test_path / test_name) if covered else None,
            covered=covered,
        ))

    return entries


def format_test_report(suite: TestSuite) -> str:
    """Format a TestSuite as an ASCII report."""
    lines = [
        "GRIP Test Suite Results",
        "=" * 24,
        "",
    ]

    for result in sorted(suite.results, key=lambda r: r.file_path):
        name = Path(result.file_path).name
        if result.errors > 0:
            icon = "[!]"
        elif result.failed > 0:
            icon = "[x]"
        elif result.passed == 0 and result.skipped > 0:
            icon = "[o]"
        elif result.passed == 0:
            icon = "[o]"
        else:
            icon = "[v]"

        duration_str = f"{result.duration:.1f}s"
        if result.passed == 0 and result.failed == 0 and result.errors == 0:
            duration_str = "SKIPPED"

        line = f"  {icon} {name:<40s} {result.passed:>4d} passed  {result.failed:>3d} failed  {duration_str:>8s}"
        lines.append(line)

    lines.append("")
    lines.append(
        f"Total: {suite.total_passed} passed, {suite.total_failed} failed, "
        f"{suite.total_skipped} skipped, {suite.total_errors} errors"
    )
    lines.append(f"Duration: {suite.total_duration:.1f}s")

    # Collect all failures
    all_failures = []
    for result in sorted(suite.results, key=lambda r: r.file_path):
        for f in result.failures:
            all_failures.append(f)

    if all_failures:
        lines.append("")
        lines.append("Failures:")
        for f in all_failures:
            lines.append(f"  {f}")

    return "\n".join(lines)


def format_coverage_report(coverage: List[CoverageEntry]) -> str:
    """Format coverage entries as an ASCII report."""
    lines = [
        "Test Coverage",
        "=" * 13,
        "",
    ]

    covered_count = 0
    total_count = len(coverage)

    for entry in coverage:
        lib_name = Path(entry.lib_file).name
        if entry.covered and entry.test_file:
            test_name = Path(entry.test_file).name
            icon = "v"
        else:
            test_name = "(no test file)"
            icon = "x"

        line = f"  {lib_name:<30s} -> {test_name:<35s} {icon}"
        lines.append(line)

        if entry.covered:
            covered_count += 1

    if total_count > 0:
        pct = covered_count / total_count * 100
    else:
        pct = 0.0

    lines.append("")
    lines.append(f"Coverage: {covered_count}/{total_count} files ({pct:.1f}%)")

    return "\n".join(lines)


def format_test_list(files: List[TestFile], verbose: bool = False) -> str:
    """Format discovered test files as a list.

    Args:
        files: List of TestFile objects.
        verbose: If True, list individual test function names.

    Returns:
        Formatted string.
    """
    if not files:
        return "No test files found."

    lines = [
        f"Discovered {len(files)} test files, "
        f"{sum(f.test_count for f in files)} tests total",
        "",
    ]

    for tf in files:
        name = Path(tf.path).name
        lines.append(f"  {name:<40s} {tf.test_count:>4d} tests")

        if verbose:
            for t in tf.tests:
                prefix = f"    {t.class_name}." if t.is_class_method else "    "
                lines.append(f"{prefix}{t.name} (line {t.line_number})")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Test Discovery Runner")
    parser.add_argument(
        "--test-dir", type=str, default=None,
        help="Directory to scan for tests (default: $CLAUDE_DIR/tests)",
    )
    parser.add_argument(
        "--lib-dir", type=str, default=None,
        help="Directory with library files (default: $CLAUDE_DIR/lib)",
    )

    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run test suite")
    run_p.add_argument("--parallel", type=int, default=1, help="Number of parallel workers")
    run_p.add_argument("--filter", type=str, help="Glob pattern to match test file names")
    run_p.add_argument("--timeout", type=int, default=120, help="Per-file timeout in seconds")
    run_p.add_argument("--json", action="store_true", help="Output results as JSON")

    list_p = sub.add_parser("list", help="List discovered tests")
    list_p.add_argument("--verbose", "-v", action="store_true", help="Show individual test names")

    sub.add_parser("coverage", help="Show test coverage report")

    args = parser.parse_args()

    if args.command == "run":
        files = discover_tests(args.test_dir)
        suite = run_suite(
            files,
            parallel=args.parallel,
            filter_pattern=args.filter,
            timeout=args.timeout,
        )
        if args.json:
            data = {
                "total_passed": suite.total_passed,
                "total_failed": suite.total_failed,
                "total_skipped": suite.total_skipped,
                "total_errors": suite.total_errors,
                "total_duration": round(suite.total_duration, 3),
                "results": [
                    {
                        "file": r.file_path,
                        "passed": r.passed,
                        "failed": r.failed,
                        "skipped": r.skipped,
                        "errors": r.errors,
                        "duration": round(r.duration, 3),
                        "failures": r.failures,
                    }
                    for r in suite.results
                ],
            }
            print(json.dumps(data, indent=2))
        else:
            print(format_test_report(suite))

    elif args.command == "list":
        files = discover_tests(args.test_dir)
        print(format_test_list(files, verbose=args.verbose))

    elif args.command == "coverage":
        coverage = compute_coverage(test_dir=args.test_dir, lib_dir=args.lib_dir)
        print(format_coverage_report(coverage))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
