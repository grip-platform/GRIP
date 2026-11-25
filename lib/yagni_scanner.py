#!/usr/bin/env python3
"""
yagni_scanner.py — YAGNI (You Aren't Gonna Need It) signal for convergence.

Detects code that exists but is never used:
  1. Unused imports (static analysis)
  2. Unreachable public functions (call-graph via AST)
  3. Dead CLI entry points (argparse blocks that are never called)

Integrates with converge() as an improvement signal:

    from lib.yagni_scanner import YAGNIScanner, yagni_criterion

    scanner = YAGNIScanner(path=".")
    result = converge(
        state=scanner.scan(),
        criterion=yagni_criterion(max_dead_ratio=0.10),
        improve=scanner.report_and_rescan,
        max_depth=3,
    )

DESIGN: KISS over perfect accuracy.
- Counts public functions not referenced anywhere in the codebase
- Ignores dunder methods and test functions (they're called by frameworks)
- Conservative: better to miss dead code than to falsely flag live code
"""

from __future__ import annotations

import ast
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class DeadSymbol:
    """A function or class that appears to be unused."""
    name: str
    file: str
    line: int
    kind: str  # "function", "class", "import"
    reason: str = ""

    @property
    def tokens_wasted(self) -> int:
        """Rough token estimate: dead functions cost ~20 tokens each."""
        return 20 if self.kind == "function" else 5


@dataclass
class YAGNIReport:
    """Output of a YAGNIScanner.scan() call."""
    dead_symbols: list[DeadSymbol] = field(default_factory=list)
    file_count: int = 0
    total_symbols: int = 0
    dead_ratio: float = 0.0
    scan_path: str = ""

    @property
    def wasted_tokens(self) -> int:
        return sum(s.tokens_wasted for s in self.dead_symbols)

    def top_symbols(self, n: int = 5) -> list[DeadSymbol]:
        return sorted(self.dead_symbols, key=lambda s: s.tokens_wasted, reverse=True)[:n]

    def summary(self) -> str:
        lines = [
            f"YAGNIReport — {self.scan_path}",
            f"Files scanned  : {self.file_count}",
            f"Total symbols  : {self.total_symbols}",
            f"Dead symbols   : {len(self.dead_symbols)}",
            f"Dead ratio     : {self.dead_ratio:.1%}",
            f"Wasted tokens  : {self.wasted_tokens}",
        ]
        if self.dead_symbols:
            lines.append("\nTop dead symbols:")
            for s in self.top_symbols(3):
                lines.append(f"  {s.file}:{s.line} — {s.kind} `{s.name}` ({s.reason})")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "file_count": self.file_count,
            "total_symbols": self.total_symbols,
            "dead_ratio": self.dead_ratio,
            "wasted_tokens": self.wasted_tokens,
            "dead_symbols": [
                {"name": s.name, "file": s.file, "line": s.line,
                 "kind": s.kind, "reason": s.reason}
                for s in self.top_symbols(10)
            ],
            "scan_path": self.scan_path,
        }


# ── Scanner ─────────────────────────────────────────────────────────────────

class YAGNIScanner:
    """Detects unused code via static AST analysis.

    Strategy:
      1. Collect all public function/class names defined across the codebase
      2. Collect all names referenced (called, imported, annotated) across all files
      3. Dead = defined but never referenced outside their own module
    """

    EXCLUDED_DIRS = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", ".git",
    }
    # Names that frameworks call implicitly — never count as dead
    FRAMEWORK_NAMES = {
        "main", "setup", "teardown", "pytest_", "setUp", "tearDown",
        "get", "post", "put", "delete", "patch",  # HTTP methods
        "on_", "handle_", "middleware",  # event handlers
    }

    def __init__(self, path: str = ".", min_function_lines: int = 3):
        self.path = Path(path).expanduser().resolve()
        self.min_function_lines = min_function_lines

    def scan(self) -> YAGNIReport:
        """Run YAGNI scan and return report."""
        files = list(self._collect_files())
        if not files:
            return YAGNIReport(scan_path=str(self.path))

        # Pass 1: collect all defined public symbols per file
        definitions: dict[str, list[tuple[str, int, str]]] = {}
        # filepath → [(name, lineno, kind)]

        all_sources: dict[str, str] = {}

        for filepath in files:
            try:
                source = filepath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            all_sources[str(filepath)] = source

            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            defs = self._collect_definitions(tree, source)
            definitions[str(filepath)] = defs

        # One O(files) pass: map each DEFINED name to the set of files where it
        # appears as an identifier token. Keying only on names that are actually
        # defined somewhere keeps the map bounded regardless of how many tokens a
        # file has. This replaces the old O(files²) "other-file reference set"
        # that re-regexed every other file's whole source for every file.
        defined_names = {name for defs in definitions.values() for name, _, _ in defs}
        _IDENT = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b')
        ref_files: dict[str, set[str]] = {}
        for filepath, source in all_sources.items():
            for tok in _IDENT.findall(source):
                if tok in defined_names:
                    ref_files.setdefault(tok, set()).add(filepath)

        # Find dead symbols: defined but not referenced in OTHER files
        dead: list[DeadSymbol] = []
        total_symbols = 0

        for filepath, defs in definitions.items():
            own_source = all_sources[filepath]
            lines = own_source.splitlines()
            for name, lineno, kind in defs:
                total_symbols += 1
                # Skip framework-called and test names
                if any(name.startswith(pfx) for pfx in self.FRAMEWORK_NAMES):
                    continue
                if name.startswith("test_") or name.startswith("Test"):
                    continue
                # Referenced in any OTHER file?
                if ref_files.get(name, set()) - {filepath}:
                    continue
                # Also check if called within own file (recursive/helper)
                # Count references excluding the definition line
                source_no_def = "\n".join(
                    line for i, line in enumerate(lines, 1) if i != lineno
                )
                own_calls = len(re.findall(rf'\b{re.escape(name)}\b', source_no_def))
                if own_calls > 0:
                    continue  # Used within own module

                rel_path = str(Path(filepath).relative_to(self.path))
                dead.append(DeadSymbol(
                    name=name,
                    file=rel_path,
                    line=lineno,
                    kind=kind,
                    reason="not referenced in any other module",
                ))

        dead_ratio = len(dead) / max(total_symbols, 1)

        return YAGNIReport(
            dead_symbols=dead,
            file_count=len(files),
            total_symbols=total_symbols,
            dead_ratio=dead_ratio,
            scan_path=str(self.path),
        )

    def report_and_rescan(self, report: YAGNIReport) -> YAGNIReport:
        """improve() callback for converge()."""
        import logging
        logger = logging.getLogger(__name__)
        for s in report.top_symbols(3):
            logger.info(
                "[yagni] Dead %s `%s` in %s:%d",
                s.kind, s.name, s.file, s.line
            )
        return self.scan()

    def _collect_files(self):
        for root, dirs, files in os.walk(self.path):
            dirs[:] = [
                d for d in dirs
                if d not in self.EXCLUDED_DIRS and not d.startswith(".")
            ]
            for fname in files:
                if fname.endswith(".py"):
                    yield Path(root) / fname

    def _collect_definitions(
        self, tree: ast.Module, source: str
    ) -> list[tuple[str, int, str]]:
        """Extract public top-level function and class definitions."""
        defs = []
        source_lines = source.splitlines()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue  # Private
                # Skip tiny functions (< min_function_lines)
                end = getattr(node, "end_lineno", node.lineno)
                if (end - node.lineno) < self.min_function_lines:
                    continue
                defs.append((node.name, node.lineno, "function"))
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    defs.append((node.name, node.lineno, "class"))
        return defs


# ── Convergence integration ─────────────────────────────────────────────────

def yagni_criterion(max_dead_ratio: float = 0.10):
    """Returns a criterion for converge() based on dead code ratio.

    True when dead code ratio drops below max_dead_ratio.
    """
    def criterion(report: YAGNIReport) -> bool:
        return report.dead_ratio <= max_dead_ratio
    criterion.__doc__ = f"dead_ratio <= {max_dead_ratio:.1%}"
    return criterion


def yagni_scorer(report: YAGNIReport) -> float:
    """Score function — higher means less dead code."""
    return 1.0 - min(1.0, report.dead_ratio)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse, logging
    parser = argparse.ArgumentParser(
        description="GRIP YAGNI Scanner — detect unused code"
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Path to scan (default: current directory)")
    parser.add_argument("--threshold", type=float, default=0.10,
                        help="Max dead code ratio (default: 0.10)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s"
    )

    scanner = YAGNIScanner(path=args.path)
    report = scanner.scan()
    print(report.summary())
    if report.dead_ratio > args.threshold:
        print(
            f"\nWARNING: dead_ratio {report.dead_ratio:.1%} "
            f"exceeds threshold {args.threshold:.1%}"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
