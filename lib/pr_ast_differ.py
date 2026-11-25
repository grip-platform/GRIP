"""Before/after AST diff for Python files in a PR.

Given the old and new source text of a file, extract a structural delta:
added symbols, removed symbols, and symbols whose signature or body hash
changed. The delta is what council members (Opus/Sonnet) get fed to reason
about structural impact — it's much cheaper than sending the raw diff
and more accurate than regex heuristics.

Pure stdlib (uses the built-in `ast` module). Safe to import without pip.

Design note: this module does NOT read from git or the filesystem. The
caller is expected to supply old+new text explicitly. This keeps the unit
tests hermetic and avoids coupling to any particular VCS workflow. The
`.github/scripts/hal_council_review.py` entry point will be responsible
for fetching old text via `git show base:path`.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SymbolSig:
    """Canonical signature of a function/class/method, comparable for equality."""
    qualified_name: str
    kind: str
    args: tuple[str, ...]
    decorators: tuple[str, ...]
    bases: tuple[str, ...]
    docstring: Optional[str]
    body_hash: str
    line_start: int
    line_end: int

    def signature_changed(self, other: "SymbolSig") -> bool:
        """True when the external surface changed (not just the body)."""
        return (
            self.args != other.args
            or self.decorators != other.decorators
            or self.bases != other.bases
        )

    def body_changed(self, other: "SymbolSig") -> bool:
        return self.body_hash != other.body_hash

    def docstring_changed(self, other: "SymbolSig") -> bool:
        return self.docstring != other.docstring


@dataclass
class SymbolChange:
    """A symbol that exists in both versions with some difference."""
    qualified_name: str
    kind: str
    signature_changed: bool = False
    body_changed: bool = False
    docstring_changed: bool = False
    old: Optional[SymbolSig] = None
    new: Optional[SymbolSig] = None

    @property
    def is_external_break(self) -> bool:
        """True if callers might need to change (signature or decorators)."""
        return self.signature_changed


@dataclass
class AstDelta:
    """Structural diff between two versions of the same Python file."""
    added: list[SymbolSig] = field(default_factory=list)
    removed: list[SymbolSig] = field(default_factory=list)
    modified: list[SymbolChange] = field(default_factory=list)
    added_imports: list[str] = field(default_factory=list)
    removed_imports: list[str] = field(default_factory=list)
    parse_error_old: Optional[str] = None
    parse_error_new: Optional[str] = None

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added or self.removed or self.modified
            or self.added_imports or self.removed_imports
        )

    @property
    def has_external_breaks(self) -> bool:
        """True if any modification would break external callers."""
        return any(m.is_external_break for m in self.modified)


def _hash_body(node: ast.AST) -> str:
    """Stable hash over a node's body, ignoring docstrings and line numbers."""
    try:
        dumped = ast.dump(node, annotate_fields=False, include_attributes=False)
    except TypeError:
        dumped = ast.dump(node)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


def _decorator_name(dec: ast.expr) -> str:
    """Render a decorator expression as a readable string."""
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return f"{_decorator_name(dec.value)}.{dec.attr}"
    if isinstance(dec, ast.Call):
        return _decorator_name(dec.func)
    try:
        return ast.unparse(dec)
    except (AttributeError, ValueError):
        return "<decorator>"


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return f"{_base_name(base.value)}.{base.attr}"
    try:
        return ast.unparse(base)
    except (AttributeError, ValueError):
        return "<base>"


def _arg_signature(args: ast.arguments) -> tuple[str, ...]:
    """Canonical argument tuple: pos-only, positional, *args, kw-only, **kwargs."""
    parts: list[str] = []
    for arg in args.posonlyargs:
        parts.append(arg.arg)
    if args.posonlyargs:
        parts.append("/")
    for arg in args.args:
        parts.append(arg.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    for arg in args.kwonlyargs:
        parts.append(arg.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return tuple(parts)


def _import_names_from_node(node: ast.AST) -> list[str]:
    """Import alias names produced by a single import/import-from node."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        prefix = f"{module}." if module else ""
        return [f"{prefix}{alias.name}" for alias in node.names]
    return []


def _extract_imports(tree: ast.Module) -> list[str]:
    """All imported names in a module, sorted and deduped."""
    imports: list[str] = []
    for node in ast.walk(tree):
        imports.extend(_import_names_from_node(node))
    return sorted(set(imports))


def _build_symbol(
    node: ast.AST,
    qualified_name: str,
    kind: str,
) -> Optional[SymbolSig]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return None

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = _arg_signature(node.args)
        bases: tuple[str, ...] = ()
    else:
        args = ()
        bases = tuple(_base_name(b) for b in node.bases)

    decorators = tuple(_decorator_name(d) for d in node.decorator_list)
    docstring = ast.get_docstring(node, clean=False)
    body_hash = _hash_body(node)
    end_line = getattr(node, "end_lineno", node.lineno) or node.lineno

    return SymbolSig(
        qualified_name=qualified_name,
        kind=kind,
        args=args,
        decorators=decorators,
        bases=bases,
        docstring=docstring,
        body_hash=body_hash,
        line_start=node.lineno,
        line_end=end_line,
    )


def extract_symbols(source: str) -> tuple[dict[str, SymbolSig], Optional[str]]:
    """Walk a Python source file and return (symbols_by_qname, parse_error).

    Symbols are keyed by qualified name (e.g. `ClassName.method_name`).
    Nested functions get dotted paths. Methods inside classes are prefixed.
    On syntax error, returns ({}, error_message) instead of raising.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {}, f"SyntaxError: {e.msg} (line {e.lineno})"

    symbols: dict[str, SymbolSig] = {}

    def _visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qname = f"{prefix}{child.name}"
                sym = _build_symbol(child, qname, "class")
                if sym is not None:
                    symbols[qname] = sym
                _visit(child, f"{qname}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}"
                kind = "method" if prefix else "function"
                sym = _build_symbol(child, qname, kind)
                if sym is not None:
                    symbols[qname] = sym
                _visit(child, f"{qname}.")

    _visit(tree, "")
    return symbols, None


def _parse_safe(
    source: str,
) -> tuple[dict[str, SymbolSig], list[str], Optional[str]]:
    """Parse source into (symbols, imports, error). Empty source is a no-op."""
    if not source:
        return {}, [], None
    symbols, error = extract_symbols(source)
    try:
        imports = _extract_imports(ast.parse(source))
    except SyntaxError:
        imports = []
    return symbols, imports, error


def _detect_symbol_change(
    qname: str,
    old: SymbolSig,
    new: SymbolSig,
) -> Optional[SymbolChange]:
    """Return a SymbolChange if old and new differ, else None."""
    sig = old.signature_changed(new)
    body = old.body_changed(new)
    doc = old.docstring_changed(new)
    if not (sig or body or doc):
        return None
    return SymbolChange(
        qualified_name=qname,
        kind=new.kind,
        signature_changed=sig,
        body_changed=body,
        docstring_changed=doc,
        old=old,
        new=new,
    )


def _compute_modified(
    old_symbols: dict[str, SymbolSig],
    new_symbols: dict[str, SymbolSig],
) -> list[SymbolChange]:
    """Symbols present in both versions with any difference."""
    common = set(old_symbols.keys()) & set(new_symbols.keys())
    changes = (
        _detect_symbol_change(q, old_symbols[q], new_symbols[q])
        for q in common
    )
    return [c for c in changes if c is not None]


def diff_ast(old_source: str, new_source: str) -> AstDelta:
    """Structural diff between two versions of a Python file."""
    old_symbols, old_imports, old_error = _parse_safe(old_source)
    new_symbols, new_imports, new_error = _parse_safe(new_source)

    old_keys = set(old_symbols.keys())
    new_keys = set(new_symbols.keys())

    return AstDelta(
        added=[new_symbols[q] for q in sorted(new_keys - old_keys)],
        removed=[old_symbols[q] for q in sorted(old_keys - new_keys)],
        modified=_compute_modified(old_symbols, new_symbols),
        added_imports=sorted(set(new_imports) - set(old_imports)),
        removed_imports=sorted(set(old_imports) - set(new_imports)),
        parse_error_old=old_error,
        parse_error_new=new_error,
    )


__all__ = [
    "SymbolSig",
    "SymbolChange",
    "AstDelta",
    "extract_symbols",
    "diff_ast",
]
