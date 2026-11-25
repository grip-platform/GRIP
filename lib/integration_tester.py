#!/usr/bin/env python3
"""Cross-module compatibility verification for GRIP lib/.

Tests imports, circular dependencies, schema consistency, and CLI parsing
across all Python modules in lib/ without importing them into the tester
process (uses subprocess isolation).

Usage:
    python3 lib/integration_tester.py test-imports
    python3 lib/integration_tester.py test-schemas
    python3 lib/integration_tester.py test-cli
    python3 lib/integration_tester.py full
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class TestResult:
    """Single test outcome."""

    module_name: str
    test_type: str  # "import", "schema", "cli", "circular"
    passed: bool
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class TestSuite:
    """Aggregated test run."""

    results: List[TestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0

    def add(self, result: TestResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1
        self.duration_ms += result.duration_ms


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

def discover_modules(lib_dir: str) -> List[str]:
    """Find all .py files in lib_dir, return module names (stem only).

    Skips __pycache__, __init__.py, and non-.py files.
    Results are sorted for deterministic ordering.
    """
    lib_path = Path(lib_dir)
    if not lib_path.is_dir():
        return []
    modules = []
    for f in sorted(lib_path.iterdir()):
        if (
            f.suffix == ".py"
            and f.name != "__init__.py"
            and not f.name.startswith(".")
        ):
            modules.append(f.stem)
    return modules


# ---------------------------------------------------------------------------
# Import testing (subprocess-isolated)
# ---------------------------------------------------------------------------

def test_imports(lib_dir: str) -> TestSuite:
    """Try importing each module in a subprocess. Report failures."""
    suite = TestSuite()
    modules = discover_modules(lib_dir)
    lib_abs = str(Path(lib_dir).resolve())

    for mod in modules:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0, {lib_abs!r}); import {mod}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            elapsed = (time.monotonic() - start) * 1000
            if proc.returncode == 0:
                suite.add(TestResult(mod, "import", True, duration_ms=elapsed))
            else:
                err = (proc.stderr or proc.stdout or "unknown error").strip()
                # Truncate long tracebacks
                lines = err.splitlines()
                if len(lines) > 5:
                    err = "\n".join(lines[-5:])
                suite.add(TestResult(mod, "import", False, error=err, duration_ms=elapsed))
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - start) * 1000
            suite.add(TestResult(mod, "import", False, error="timeout (10s)", duration_ms=elapsed))
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            suite.add(TestResult(mod, "import", False, error=str(exc), duration_ms=elapsed))

    return suite


# ---------------------------------------------------------------------------
# Circular import detection (pairwise subprocess)
# ---------------------------------------------------------------------------

def _find_imports_in_ast(filepath: str) -> Set[str]:
    """Parse a file's AST and return the set of local module names it imports."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return set()

    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def test_pairwise_imports(lib_dir: str) -> TestSuite:
    """Detect circular imports by testing A-then-B pairs in subprocess."""
    suite = TestSuite()
    modules = discover_modules(lib_dir)
    lib_abs = str(Path(lib_dir).resolve())
    lib_path = Path(lib_dir)

    # Build dependency graph from AST (no imports needed)
    deps: Dict[str, Set[str]] = {}
    module_set = set(modules)
    for mod in modules:
        filepath = str(lib_path / f"{mod}.py")
        raw_imports = _find_imports_in_ast(filepath)
        deps[mod] = raw_imports & module_set

    # Only test pairs where both import each other (potential circular)
    tested: Set[Tuple[str, str]] = set()
    for mod_a in modules:
        for mod_b in deps.get(mod_a, set()):
            if mod_a in deps.get(mod_b, set()):
                pair = tuple(sorted([mod_a, mod_b]))
                if pair in tested:
                    continue
                tested.add(pair)

                start = time.monotonic()
                code = (
                    f"import sys; sys.path.insert(0, {lib_abs!r}); "
                    f"import {pair[0]}; import {pair[1]}"
                )
                try:
                    proc = subprocess.run(
                        [sys.executable, "-c", code],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    elapsed = (time.monotonic() - start) * 1000
                    name = f"{pair[0]} <-> {pair[1]}"
                    if proc.returncode == 0:
                        suite.add(TestResult(name, "circular", True, duration_ms=elapsed))
                    else:
                        err = (proc.stderr or "").strip().splitlines()
                        err_msg = "\n".join(err[-3:]) if len(err) > 3 else "\n".join(err)
                        suite.add(TestResult(name, "circular", False, error=err_msg, duration_ms=elapsed))
                except subprocess.TimeoutExpired:
                    elapsed = (time.monotonic() - start) * 1000
                    suite.add(TestResult(f"{pair[0]} <-> {pair[1]}", "circular", False, error="timeout", duration_ms=elapsed))

    if not tested:
        # No mutual imports found — that is a pass (no circular risk)
        suite.add(TestResult("(none detected)", "circular", True, duration_ms=0.0))

    return suite


# ---------------------------------------------------------------------------
# Schema consistency (AST-only, no imports)
# ---------------------------------------------------------------------------

def _extract_dataclass_fields(filepath: str) -> Dict[str, Dict[str, str]]:
    """Parse file AST, find @dataclass classes, return {ClassName: {field: annotation_str}}."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            source = fh.read()
            tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return {}

    classes: Dict[str, Dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_dc = any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
            for d in node.decorator_list
        )
        if not is_dc:
            continue

        fields: Dict[str, str] = {}
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields[item.target.id] = ast.unparse(item.annotation)
        if fields:
            classes[node.name] = fields
    return classes


def _normalize_type(annotation: str) -> str:
    """Normalize a type annotation string for comparison.

    Strips Optional wrapper and normalizes common aliases.
    """
    s = annotation.strip()
    # Optional[X] -> X
    if s.startswith("Optional[") and s.endswith("]"):
        s = s[len("Optional["):-1]
    # typing aliases
    s = s.replace("typing.", "")
    return s


def test_schemas(lib_dir: str) -> TestSuite:
    """Verify that shared dataclass field names use consistent types across modules."""
    suite = TestSuite()
    lib_path = Path(lib_dir)
    modules = discover_modules(lib_dir)

    # Collect all field definitions: {field_name: [(module, class, type_str)]}
    field_registry: Dict[str, List[Tuple[str, str, str]]] = {}
    for mod in modules:
        filepath = str(lib_path / f"{mod}.py")
        classes = _extract_dataclass_fields(filepath)
        for cls_name, fields in classes.items():
            for fname, ftype in fields.items():
                field_registry.setdefault(fname, []).append((mod, cls_name, ftype))

    # Check consistency: fields appearing in 2+ classes should have compatible types
    checked = 0
    for fname, locations in field_registry.items():
        if len(locations) < 2:
            continue
        checked += 1
        types_seen: Dict[str, List[str]] = {}
        for mod, cls, ftype in locations:
            norm = _normalize_type(ftype)
            types_seen.setdefault(norm, []).append(f"{mod}.{cls}")

        if len(types_seen) == 1:
            suite.add(TestResult(
                fname,
                "schema",
                True,
                duration_ms=0.0,
            ))
        else:
            detail_parts = []
            for t, sources in types_seen.items():
                detail_parts.append(f"{t} in {', '.join(sources)}")
            suite.add(TestResult(
                fname,
                "schema",
                False,
                error=f"inconsistent types: {'; '.join(detail_parts)}",
                duration_ms=0.0,
            ))

    if checked == 0:
        suite.add(TestResult("(no shared fields)", "schema", True, duration_ms=0.0))

    return suite


# ---------------------------------------------------------------------------
# CLI parsing (--help via subprocess)
# ---------------------------------------------------------------------------

def _has_argparse(filepath: str) -> bool:
    """Check via AST whether a module uses argparse."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=filepath)
    except (SyntaxError, UnicodeDecodeError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "argparse":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "argparse":
                return True
    return False


def test_cli_parsing(lib_dir: str) -> TestSuite:
    """For modules with argparse, verify --help exits cleanly."""
    suite = TestSuite()
    lib_path = Path(lib_dir)
    modules = discover_modules(lib_dir)

    tested = 0
    for mod in modules:
        filepath = str(lib_path / f"{mod}.py")
        if not _has_argparse(filepath):
            continue
        tested += 1

        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, filepath, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            elapsed = (time.monotonic() - start) * 1000
            # argparse --help exits with 0
            if proc.returncode == 0:
                suite.add(TestResult(mod, "cli", True, duration_ms=elapsed))
            else:
                err = (proc.stderr or proc.stdout or "non-zero exit").strip()
                lines = err.splitlines()
                if len(lines) > 3:
                    err = "\n".join(lines[-3:])
                suite.add(TestResult(mod, "cli", False, error=err, duration_ms=elapsed))
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - start) * 1000
            suite.add(TestResult(mod, "cli", False, error="timeout (10s)", duration_ms=elapsed))
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            suite.add(TestResult(mod, "cli", False, error=str(exc), duration_ms=elapsed))

    if tested == 0:
        suite.add(TestResult("(no argparse modules)", "cli", True, duration_ms=0.0))

    return suite


# ---------------------------------------------------------------------------
# Full suite runner
# ---------------------------------------------------------------------------

def run_full_suite(lib_dir: str) -> TestSuite:
    """Run all test categories and merge results."""
    combined = TestSuite()
    for sub in [test_imports, test_pairwise_imports, test_schemas, test_cli_parsing]:
        sub_suite = sub(lib_dir)
        for result in sub_suite.results:
            combined.add(result)
    return combined


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_results(suite: TestSuite) -> str:
    """ASCII table of all test results."""
    if not suite.results:
        return "No tests run."

    # Column widths
    max_mod = max(len(r.module_name) for r in suite.results)
    max_type = max(len(r.test_type) for r in suite.results)
    max_mod = max(max_mod, len("Module"))
    max_type = max(max_type, len("Type"))

    header = f"{'Module':<{max_mod}}  {'Type':<{max_type}}  {'Status':<6}  {'Time':>8}  Error"
    sep = "-" * len(header)
    lines = [sep, header, sep]

    for r in suite.results:
        status = "PASS" if r.passed else "FAIL"
        time_str = f"{r.duration_ms:7.1f}ms"
        err = (r.error or "")[:80]
        lines.append(f"{r.module_name:<{max_mod}}  {r.test_type:<{max_type}}  {status:<6}  {time_str}  {err}")

    lines.append(sep)
    lines.append(format_summary(suite))
    return "\n".join(lines)


def format_summary(suite: TestSuite) -> str:
    """One-line pass/fail summary."""
    status = "PASS" if suite.failed == 0 else "FAIL"
    return (
        f"{status}: {suite.passed}/{suite.total} passed, "
        f"{suite.failed} failed, {suite.duration_ms:.1f}ms total"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="GRIP cross-module compatibility tester",
    )
    parser.add_argument(
        "command",
        choices=["test-imports", "test-schemas", "test-cli", "test-circular", "full"],
        help="Test category to run",
    )
    parser.add_argument(
        "--lib-dir",
        default=None,
        help="Path to lib/ directory (default: auto-detect from script location)",
    )

    args = parser.parse_args(argv)

    lib_dir = args.lib_dir
    if lib_dir is None:
        lib_dir = str(Path(__file__).resolve().parent)

    dispatch = {
        "test-imports": test_imports,
        "test-schemas": test_schemas,
        "test-cli": test_cli_parsing,
        "test-circular": test_pairwise_imports,
        "full": run_full_suite,
    }

    suite = dispatch[args.command](lib_dir)
    print(format_results(suite))
    return 0 if suite.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
