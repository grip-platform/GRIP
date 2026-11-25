"""AST visitor for extracting Python symbols.

Walks the AST tree and extracts classes, functions, methods, and module-level
assignments with full metadata (signatures, docstrings, decorators, bases).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Optional

from lib.code_index.models import Symbol, FrameworkType
from lib.code_index.framework_detector import classify_framework_type


class SymbolVisitor(ast.NodeVisitor):
    """Extract symbols from a Python AST.

    Handles classes, functions (sync/async), methods, and tracks parent context
    for qualified name construction (module.Class.method pattern).
    """

    def __init__(self, file_path: str, source: str, module_name: str = "",
                 project: str = ""):
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.module_name = module_name
        self.project = project
        self.symbols: list[Symbol] = []
        self._class_stack: list[str] = []

    def _get_decorators(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Extract decorator names from a node."""
        decorators = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(ast.dump(dec).replace("Attribute", "").replace("(", "").replace(")", ""))
                # Simpler: try to reconstruct dotted name
                parts = []
                current = dec
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                decorators[-1] = ".".join(reversed(parts))
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    decorators.append(dec.func.id)
                elif isinstance(dec.func, ast.Attribute):
                    parts = []
                    current = dec.func
                    while isinstance(current, ast.Attribute):
                        parts.append(current.attr)
                        current = current.value
                    if isinstance(current, ast.Name):
                        parts.append(current.id)
                    decorators.append(".".join(reversed(parts)))
        return decorators

    def _get_bases(self, node: ast.ClassDef) -> list[str]:
        """Extract base class names."""
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                parts = []
                current = base
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                bases.append(".".join(reversed(parts)))
        return bases

    def _get_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Extract function signature from source."""
        try:
            start = node.lineno - 1
            # Signature may span multiple lines (parentheses)
            sig_lines = []
            paren_depth = 0
            for i in range(start, min(start + 10, len(self.source_lines))):
                line = self.source_lines[i]
                sig_lines.append(line)
                paren_depth += line.count("(") - line.count(")")
                if paren_depth <= 0 and ")" in line:
                    break
            sig = " ".join(l.strip() for l in sig_lines)
            # Extract just the parameters
            if "(" in sig and ")" in sig:
                params = sig[sig.index("("):sig.rindex(")") + 1]
                return params
        except (IndexError, ValueError):
            pass
        return ""

    def _get_docstring(self, node: ast.AST) -> str:
        """Extract docstring from a node."""
        return ast.get_docstring(node) or ""

    def _qualified_name(self, name: str) -> str:
        """Build fully qualified name: module.Class.method."""
        parts = []
        if self.module_name:
            parts.append(self.module_name)
        parts.extend(self._class_stack)
        parts.append(name)
        return ".".join(parts)

    def _end_lineno(self, node: ast.AST) -> int:
        """Get end line number, handling older Python versions."""
        return getattr(node, "end_lineno", node.lineno)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Extract class symbol and recurse into methods."""
        bases = self._get_bases(node)
        decorators = self._get_decorators(node)
        framework_type = classify_framework_type(
            node.name, bases, decorators, self.file_path
        )

        symbol = Symbol(
            name=node.name,
            qualified_name=self._qualified_name(node.name),
            symbol_type="class",
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=self._end_lineno(node),
            docstring=self._get_docstring(node),
            decorators=decorators,
            bases=bases,
            parent=self._class_stack[-1] if self._class_stack else "",
            framework_type=framework_type,
            project=self.project,
        )
        self.symbols.append(symbol)

        # Recurse into class body for methods
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef,
                        is_async: bool = False) -> None:
        """Extract function or method symbol."""
        decorators = self._get_decorators(node)
        signature = self._get_signature(node)
        symbol_type = "method" if self._class_stack else "function"
        prefix = "async " if is_async else ""

        symbol = Symbol(
            name=node.name,
            qualified_name=self._qualified_name(node.name),
            symbol_type=symbol_type,
            file_path=self.file_path,
            line_start=node.lineno,
            line_end=self._end_lineno(node),
            signature=f"{prefix}{node.name}{signature}",
            docstring=self._get_docstring(node),
            decorators=decorators,
            parent=self._class_stack[-1] if self._class_stack else "",
            project=self.project,
        )
        self.symbols.append(symbol)

        # Don't recurse into nested functions/classes within methods
        # to avoid noise — but do handle nested classes at module level
        if not self._class_stack:
            self.generic_visit(node)


def extract_symbols(file_path: str, source: str, module_name: str = "",
                    project: str = "") -> list[Symbol]:
    """Parse a Python file and extract all symbols.

    Args:
        file_path: Path to the Python file (for reference, not read here).
        source: The Python source code to parse.
        module_name: Dotted module name (e.g. "lib.code_index.visitor").
        project: Project identifier for multi-project indexing.

    Returns:
        List of Symbol objects found in the source.

    Raises nothing — returns empty list on SyntaxError.
    """
    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError):
        # ValueError: "source code string cannot contain null bytes"
        # occurs with git-crypt encrypted files
        return []

    visitor = SymbolVisitor(file_path, source, module_name, project)
    visitor.visit(tree)
    return visitor.symbols
