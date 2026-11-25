#!/usr/bin/env python3
"""AST Pattern Library — recurring code pattern catalogue for Python codebases.

Detects 8 pattern categories via AST analysis:
1. CLI patterns (argparse, __main__ blocks, --json/--help flags)
2. Error handling (try/except, custom exceptions, error codes)
3. File I/O (JSON load/save, path handling, temp files)
4. Decorator usage (@dataclass, @property, custom, factories)
5. Testing patterns (setUp/tearDown, mock, parametrize, fixtures)
6. Data structures (dataclass, NamedTuple, TypedDict, Enum)
7. Subprocess patterns (subprocess.run, Popen, shell=True antipattern)
8. Import patterns (relative/absolute, conditional, try/except ImportError)

CLI:
  python3 lib/ast_pattern_library.py              -- full pattern catalogue
  python3 lib/ast_pattern_library.py --category cli -- filter by category
  python3 lib/ast_pattern_library.py --antipatterns  -- show only flagged patterns
  python3 lib/ast_pattern_library.py --json          -- JSON output
  python3 lib/ast_pattern_library.py --stats         -- frequency summary only
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PatternMatch:
    """A single detected pattern occurrence."""

    name: str
    category: str
    file: str
    line: int
    recommendation: str = ""
    is_antipattern: bool = False
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "recommendation": self.recommendation,
            "is_antipattern": self.is_antipattern,
            "snippet": self.snippet,
        }


@dataclass
class PatternSummary:
    """Aggregated summary for a single pattern type."""

    name: str
    category: str
    count: int = 0
    files: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    recommendation: str = ""
    is_antipattern: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "count": self.count,
            "files": self.files,
            "locations": self.locations[:10],
            "recommendation": self.recommendation,
            "is_antipattern": self.is_antipattern,
        }


class PatternDetector(ast.NodeVisitor):
    """AST visitor that detects recurring code patterns."""

    def __init__(self, filepath: str, source_lines: list[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.matches: list[PatternMatch] = []

    def _match(
        self,
        name: str,
        category: str,
        line: int,
        recommendation: str = "",
        is_antipattern: bool = False,
    ) -> None:
        snippet = ""
        if 0 < line <= len(self.source_lines):
            snippet = self.source_lines[line - 1].rstrip()
        self.matches.append(
            PatternMatch(
                name=name,
                category=category,
                file=self.filepath,
                line=line,
                recommendation=recommendation,
                is_antipattern=is_antipattern,
                snippet=snippet,
            )
        )

    # --- CLI Patterns ---

    def _check_argparse_usage(self, node: ast.Call) -> None:
        """Detect argparse.ArgumentParser() calls."""
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
            if isinstance(func.value, ast.Name) and func.value.id == "argparse":
                self._match(
                    "argparse_usage",
                    "cli",
                    node.lineno,
                    "Good: argparse for CLI argument parsing.",
                )

    def _check_main_block(self, node: ast.If) -> None:
        """Detect if __name__ == '__main__' blocks."""
        test = node.test
        if isinstance(test, ast.Compare):
            left = test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                if test.comparators and isinstance(test.comparators[0], ast.Constant):
                    if test.comparators[0].value == "__main__":
                        self._match(
                            "main_block",
                            "cli",
                            node.lineno,
                            "Good: entry point guard enables both import and direct execution.",
                        )

    def _check_cli_flags(self, node: ast.Call) -> None:
        """Detect add_argument calls with --json or --help flags."""
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == "--json":
                        self._match(
                            "json_flag",
                            "cli",
                            node.lineno,
                            "Good: --json flag for machine-readable output.",
                        )
                    elif arg.value in ("--help", "-h"):
                        self._match(
                            "help_flag",
                            "cli",
                            node.lineno,
                            "Note: --help is auto-added by argparse.",
                        )

    # --- Error Handling ---

    def _check_try_except(self, node: ast.Try) -> None:
        """Detect try/except patterns and quality."""
        for handler in node.handlers:
            if handler.type is None:
                self._match(
                    "bare_except",
                    "error_handling",
                    handler.lineno,
                    "Antipattern: bare except catches SystemExit/KeyboardInterrupt. Use specific exceptions.",
                    is_antipattern=True,
                )
            elif isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                self._match(
                    "broad_except",
                    "error_handling",
                    handler.lineno,
                    "Consider: catch specific exceptions where possible.",
                )
            else:
                self._match(
                    "specific_except",
                    "error_handling",
                    handler.lineno,
                    "Good: specific exception handling.",
                )

    def _check_custom_exception(self, node: ast.ClassDef) -> None:
        """Detect custom exception classes."""
        for base in node.bases:
            base_name = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name and ("Error" in base_name or "Exception" in base_name):
                self._match(
                    "custom_exception",
                    "error_handling",
                    node.lineno,
                    "Good: custom exception class for domain-specific errors.",
                )
                break

    # --- File I/O ---

    def _check_json_io(self, node: ast.Call) -> None:
        """Detect json.load/dump/loads/dumps calls."""
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "json":
                if node.func.attr in ("load", "loads"):
                    self._match(
                        "json_load",
                        "file_io",
                        node.lineno,
                        "Good: structured data parsing.",
                    )
                elif node.func.attr in ("dump", "dumps"):
                    self._match(
                        "json_dump",
                        "file_io",
                        node.lineno,
                        "Good: structured data serialization.",
                    )

    def _check_path_handling(self, node: ast.Call) -> None:
        """Detect Path() usage and os.path calls."""
        if isinstance(node.func, ast.Name) and node.func.id == "Path":
            self._match(
                "pathlib_usage",
                "file_io",
                node.lineno,
                "Good: pathlib for cross-platform path handling.",
            )
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                if node.func.attr == "path":
                    self._match(
                        "os_path_usage",
                        "file_io",
                        node.lineno,
                        "Consider: prefer pathlib over os.path for new code.",
                    )
            elif isinstance(node.func.value, ast.Attribute):
                if (
                    isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "os"
                    and node.func.value.attr == "path"
                ):
                    self._match(
                        "os_path_usage",
                        "file_io",
                        node.lineno,
                        "Consider: prefer pathlib over os.path for new code.",
                    )

    def _check_temp_file(self, node: ast.Call) -> None:
        """Detect tempfile usage."""
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "tempfile":
                self._match(
                    "tempfile_usage",
                    "file_io",
                    node.lineno,
                    "Good: tempfile for safe temporary file handling.",
                )

    def _check_open_call(self, node: ast.Call) -> None:
        """Detect open() calls."""
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            self._match(
                "open_call",
                "file_io",
                node.lineno,
                "Consider: use pathlib read_text/write_text or context manager.",
            )

    # --- Decorator Usage ---

    def _check_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        """Detect decorator patterns."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                if dec.id == "dataclass":
                    self._match("dataclass_decorator", "decorators", dec.lineno, "Good: @dataclass for structured data.")
                elif dec.id == "property":
                    self._match("property_decorator", "decorators", dec.lineno, "Good: @property for computed attributes.")
                elif dec.id == "staticmethod":
                    self._match("staticmethod_decorator", "decorators", dec.lineno, "Good: @staticmethod for utility methods.")
                elif dec.id == "classmethod":
                    self._match("classmethod_decorator", "decorators", dec.lineno, "Good: @classmethod for alternative constructors.")
                else:
                    self._match("custom_decorator", "decorators", dec.lineno, "Custom decorator usage.")
            elif isinstance(dec, ast.Attribute):
                if dec.attr in ("setter", "deleter", "getter"):
                    self._match("property_accessor", "decorators", dec.lineno, f"Good: @x.{dec.attr} for property access control.")
                else:
                    self._match("custom_decorator", "decorators", dec.lineno, "Custom decorator usage.")
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    if dec.func.id == "dataclass":
                        self._match("dataclass_decorator", "decorators", dec.lineno, "Good: @dataclass for structured data.")
                    else:
                        self._match("decorator_factory", "decorators", dec.lineno, f"Decorator factory: @{dec.func.id}(...).")
                elif isinstance(dec.func, ast.Attribute):
                    self._match("decorator_factory", "decorators", dec.lineno, f"Decorator factory: @...{dec.func.attr}(...).")

    # --- Testing Patterns ---

    def _check_test_patterns(self, node: ast.ClassDef) -> None:
        """Detect testing patterns in test classes."""
        is_test_class = node.name.startswith("Test") or any(
            (isinstance(b, ast.Name) and b.id == "TestCase")
            or (isinstance(b, ast.Attribute) and b.attr == "TestCase")
            for b in node.bases
        )
        if not is_test_class:
            return

        for item in ast.walk(node):
            if isinstance(item, ast.FunctionDef):
                if item.name == "setUp":
                    self._match("setup_method", "testing", item.lineno, "Good: setUp for test initialization.")
                elif item.name == "tearDown":
                    self._match("teardown_method", "testing", item.lineno, "Good: tearDown for test cleanup.")

    def _check_test_function_decorators(self, node: ast.FunctionDef) -> None:
        """Detect pytest decorators on test functions."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == "parametrize":
                    self._match("parametrize", "testing", dec.lineno, "Good: @pytest.mark.parametrize for data-driven tests.")
            elif isinstance(dec, ast.Attribute):
                if dec.attr == "fixture":
                    self._match("fixture", "testing", dec.lineno, "Good: @pytest.fixture for reusable test setup.")
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == "fixture":
                    self._match("fixture", "testing", dec.lineno, "Good: @pytest.fixture for reusable test setup.")

    def _check_mock_usage(self, node: ast.Call) -> None:
        """Detect mock/patch usage."""
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ("patch", "Mock", "MagicMock", "AsyncMock"):
                self._match("mock_usage", "testing", node.lineno, "Mock/patch usage for test isolation.")
            elif isinstance(node.func.value, ast.Name) and node.func.value.id == "mock":
                if node.func.attr in ("patch", "Mock", "MagicMock"):
                    self._match("mock_usage", "testing", node.lineno, "Mock/patch usage for test isolation.")
        elif isinstance(node.func, ast.Name) and node.func.id in ("Mock", "MagicMock", "patch"):
            self._match("mock_usage", "testing", node.lineno, "Mock/patch usage for test isolation.")

    # --- Data Structures ---

    def _check_data_structures(self, node: ast.ClassDef) -> None:
        """Detect data structure definitions."""
        for base in node.bases:
            base_name = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr

            if base_name == "NamedTuple":
                self._match("namedtuple_class", "data_structures", node.lineno, "Good: NamedTuple for immutable structured data.")
            elif base_name == "TypedDict":
                self._match("typeddict_class", "data_structures", node.lineno, "Good: TypedDict for typed dictionary schemas.")
            elif base_name in ("Enum", "IntEnum", "StrEnum"):
                self._match("enum_class", "data_structures", node.lineno, "Good: Enum for enumerated constants.")

        for dec in node.decorator_list:
            is_dc = False
            if isinstance(dec, ast.Name) and dec.id == "dataclass":
                is_dc = True
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "dataclass":
                is_dc = True
            if is_dc:
                self._match("dataclass_struct", "data_structures", node.lineno, "Good: dataclass for structured data with auto-generated methods.")

    def _check_namedtuple_call(self, node: ast.Call) -> None:
        """Detect namedtuple() functional form."""
        if isinstance(node.func, ast.Name) and node.func.id == "namedtuple":
            self._match("namedtuple_func", "data_structures", node.lineno, "Consider: use class-based NamedTuple for better type annotations.")

    # --- Subprocess Patterns ---

    def _check_subprocess(self, node: ast.Call) -> None:
        """Detect subprocess usage and antipatterns."""
        if not isinstance(node.func, ast.Attribute):
            return
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            return

        attr = node.func.attr
        shell_true = self._has_shell_true(node)

        if attr == "run":
            if shell_true:
                self._match("subprocess_shell_true", "subprocess", node.lineno,
                            "Antipattern: shell=True is a security risk. Use list args instead.", is_antipattern=True)
            else:
                self._match("subprocess_run", "subprocess", node.lineno, "Good: subprocess.run with list args.")
        elif attr == "Popen":
            if shell_true:
                self._match("popen_shell_true", "subprocess", node.lineno,
                            "Antipattern: Popen with shell=True. Use list args and subprocess.run where possible.", is_antipattern=True)
            else:
                self._match("subprocess_popen", "subprocess", node.lineno,
                            "Consider: prefer subprocess.run over Popen unless streaming is needed.")
        elif attr == "call":
            self._match("subprocess_call", "subprocess", node.lineno,
                        "Consider: prefer subprocess.run over subprocess.call (returns CompletedProcess).")
        elif attr == "check_output":
            self._match("subprocess_check_output", "subprocess", node.lineno,
                        "Consider: prefer subprocess.run(capture_output=True, check=True).")

    def _has_shell_true(self, node: ast.Call) -> bool:
        """Check if a Call node has shell=True keyword argument."""
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return True
        return False

    # --- Import Patterns ---

    def _check_imports(self, node: ast.Import | ast.ImportFrom) -> None:
        """Detect import patterns."""
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                self._match("relative_import", "imports", node.lineno, "Relative import. Good for package-internal references.")
            else:
                self._match("absolute_import", "imports", node.lineno, "Absolute import.")
        elif isinstance(node, ast.Import):
            self._match("absolute_import", "imports", node.lineno, "Absolute import.")

    def _check_conditional_import(self, node: ast.If) -> None:
        """Detect conditional imports (sys.version, platform, TYPE_CHECKING)."""
        has_import = any(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body)
        if not has_import:
            return
        test_src = ast.dump(node.test)
        if "version_info" in test_src or "platform" in test_src or "TYPE_CHECKING" in test_src:
            self._match("conditional_import", "imports", node.lineno, "Conditional import for compatibility or type checking.")

    def _check_try_import(self, node: ast.Try) -> None:
        """Detect try/except ImportError pattern."""
        has_import_in_try = any(isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body)
        has_import_error_handler = any(
            isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError")
            for h in node.handlers
            if h.type is not None
        )
        if has_import_in_try and has_import_error_handler:
            self._match("try_import", "imports", node.lineno, "Good: graceful handling of optional dependency.")

    # --- Visitor Methods ---

    def visit_Call(self, node: ast.Call) -> None:
        self._check_argparse_usage(node)
        self._check_cli_flags(node)
        self._check_json_io(node)
        self._check_path_handling(node)
        self._check_temp_file(node)
        self._check_open_call(node)
        self._check_mock_usage(node)
        self._check_subprocess(node)
        self._check_namedtuple_call(node)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self._check_main_block(node)
        self._check_conditional_import(node)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._check_try_except(node)
        self._check_try_import(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_custom_exception(node)
        self._check_data_structures(node)
        self._check_test_patterns(node)
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_decorators(node)
        self._check_test_function_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._check_imports(node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_imports(node)
        self.generic_visit(node)


# --- Scanning Engine ---

SKIP_DIRS = frozenset({
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "target", "vendor", "Pods", ".git",
    ".tox", ".mypy_cache", ".pytest_cache", "egg-info",
})


def collect_python_files(root: str) -> list[str]:
    """Collect all .py files under root, skipping dependency/build dirs."""
    py_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            if fname.endswith(".py"):
                py_files.append(os.path.join(dirpath, fname))
    return sorted(py_files)


def scan_file(filepath: str) -> list[PatternMatch]:
    """Parse a single Python file and return all detected patterns."""
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    lines = source.splitlines()
    detector = PatternDetector(filepath, lines)
    detector.visit(tree)
    return detector.matches


def scan_directory(root: str) -> list[PatternMatch]:
    """Scan all Python files in a directory tree."""
    all_matches: list[PatternMatch] = []
    for filepath in collect_python_files(root):
        all_matches.extend(scan_file(filepath))
    return all_matches


def aggregate(matches: list[PatternMatch]) -> dict[str, PatternSummary]:
    """Aggregate individual matches into pattern summaries."""
    groups: dict[str, list[PatternMatch]] = defaultdict(list)
    for m in matches:
        groups[m.name].append(m)

    summaries: dict[str, PatternSummary] = {}
    for name, group in sorted(groups.items()):
        first = group[0]
        files = sorted(set(m.file for m in group))
        locations = [f"{m.file}:{m.line}" for m in group]
        summaries[name] = PatternSummary(
            name=name,
            category=first.category,
            count=len(group),
            files=files,
            locations=locations,
            recommendation=first.recommendation,
            is_antipattern=first.is_antipattern,
        )
    return summaries


# --- Output Formatting ---

CATEGORIES = {
    "cli": "CLI Patterns",
    "error_handling": "Error Handling",
    "file_io": "File I/O",
    "decorators": "Decorator Usage",
    "testing": "Testing Patterns",
    "data_structures": "Data Structures",
    "subprocess": "Subprocess Patterns",
    "imports": "Import Patterns",
}


def frequency_bar(count: int, max_count: int, width: int = 30) -> str:
    """Generate a frequency bar string."""
    if max_count == 0:
        return ""
    filled = max(1, round(count / max_count * width))
    return "#" * filled + "." * (width - filled)


def format_report(
    summaries: dict[str, PatternSummary],
    category_filter: Optional[str] = None,
    antipatterns_only: bool = False,
) -> str:
    """Format the pattern catalogue as a human-readable report."""
    filtered = dict(summaries)

    if category_filter:
        filtered = {k: v for k, v in filtered.items() if v.category == category_filter}

    if antipatterns_only:
        filtered = {k: v for k, v in filtered.items() if v.is_antipattern}

    if not filtered:
        return "No patterns found matching the filter criteria."

    max_count = max(s.count for s in filtered.values()) if filtered else 1

    by_category: dict[str, list[PatternSummary]] = defaultdict(list)
    for s in filtered.values():
        by_category[s.category].append(s)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("PATTERN LIBRARY -- Recurring Code Pattern Catalogue")
    lines.append("=" * 70)

    total_patterns = sum(s.count for s in filtered.values())
    total_files = len(set(f for s in filtered.values() for f in s.files))
    lines.append(f"\nTotal: {total_patterns} pattern occurrences across {total_files} files")
    lines.append(f"Unique patterns: {len(filtered)}")

    antipattern_count = sum(1 for s in filtered.values() if s.is_antipattern)
    if antipattern_count:
        lines.append(f"Antipatterns flagged: {antipattern_count}")

    for cat_key in CATEGORIES:
        if cat_key not in by_category:
            continue
        cat_patterns = sorted(by_category[cat_key], key=lambda s: -s.count)
        cat_label = CATEGORIES.get(cat_key, cat_key)

        lines.append(f"\n{'---' * 23}-")
        lines.append(f"  {cat_label}")
        lines.append(f"{'---' * 23}-")

        for s in cat_patterns:
            flag = " [ANTIPATTERN]" if s.is_antipattern else ""
            bar = frequency_bar(s.count, max_count)
            lines.append(f"\n  {s.name}{flag}")
            lines.append(f"    Count: {s.count:>4}  |{bar}|")
            lines.append(f"    Files: {len(s.files)}")
            if s.locations:
                shown = s.locations[:5]
                for loc in shown:
                    lines.append(f"      - {loc}")
                if len(s.locations) > 5:
                    lines.append(f"      ... and {len(s.locations) - 5} more")
            lines.append(f"    Rec:   {s.recommendation}")

    lines.append(f"\n{'=' * 70}")
    return "\n".join(lines)


def format_stats(summaries: dict[str, PatternSummary]) -> str:
    """Format frequency summary only."""
    if not summaries:
        return "No patterns detected."

    max_count = max(s.count for s in summaries.values())

    by_category: dict[str, list[PatternSummary]] = defaultdict(list)
    for s in summaries.values():
        by_category[s.category].append(s)

    lines: list[str] = []
    lines.append("PATTERN FREQUENCY SUMMARY")
    lines.append("=" * 50)

    total = sum(s.count for s in summaries.values())
    lines.append(f"Total occurrences: {total}")
    lines.append(f"Unique patterns: {len(summaries)}")
    lines.append("")

    for cat_key in CATEGORIES:
        if cat_key not in by_category:
            continue
        cat_patterns = sorted(by_category[cat_key], key=lambda s: -s.count)
        cat_label = CATEGORIES.get(cat_key, cat_key)
        cat_total = sum(s.count for s in cat_patterns)

        lines.append(f"{cat_label} ({cat_total})")
        for s in cat_patterns:
            bar = frequency_bar(s.count, max_count, width=20)
            flag = " !" if s.is_antipattern else ""
            lines.append(f"  {s.name:<30} {s.count:>4} |{bar}|{flag}")
        lines.append("")

    return "\n".join(lines)


def format_json(
    summaries: dict[str, PatternSummary],
    category_filter: Optional[str] = None,
    antipatterns_only: bool = False,
) -> str:
    """Format as JSON output."""
    filtered = dict(summaries)

    if category_filter:
        filtered = {k: v for k, v in filtered.items() if v.category == category_filter}

    if antipatterns_only:
        filtered = {k: v for k, v in filtered.items() if v.is_antipattern}

    output = {
        "total_occurrences": sum(s.count for s in filtered.values()),
        "unique_patterns": len(filtered),
        "antipatterns": sum(1 for s in filtered.values() if s.is_antipattern),
        "patterns": {k: v.to_dict() for k, v in sorted(filtered.items())},
    }
    return json.dumps(output, indent=2)


# --- CLI ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AST Pattern Library -- recurring code pattern catalogue"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Root directory to scan (default: current directory)",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORIES.keys()),
        help="Filter by pattern category",
    )
    parser.add_argument(
        "--antipatterns",
        action="store_true",
        help="Show only flagged antipatterns",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show frequency summary only",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        return 1

    matches = scan_directory(root)
    summaries = aggregate(matches)

    if args.json_output:
        print(format_json(summaries, args.category, args.antipatterns))
    elif args.stats:
        print(format_stats(summaries))
    else:
        print(format_report(summaries, args.category, args.antipatterns))

    return 0


if __name__ == "__main__":
    sys.exit(main())
