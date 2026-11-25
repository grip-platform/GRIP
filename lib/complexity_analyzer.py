"""Algorithmic complexity analysis via AST inspection.

Provides max_loop_depth() and max_complexity_class() for the BIG-O design
principle. Used by the Principled Codebase convergence loop to enforce
O(n log n) ceiling on function nesting depth.

Threshold: max_complexity_class <= 1 (no function exceeds O(n) nesting
without documented justification).

Usage::

    tree = ast.parse(Path("module.py").read_text())
    depth = max_complexity_class(tree)  # 0=O(1), 1=O(n), 2=O(n^2)
    assert depth <= 1, f"BIG-O violation: nesting depth {depth}"
"""
from __future__ import annotations

import ast

LOOP_TYPES = (
    ast.For, ast.While, ast.ListComp, ast.SetComp,
    ast.DictComp, ast.GeneratorExp,
)


def max_loop_depth(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return maximum nesting depth of loop constructs in a function.

    Counts For, While, and comprehension expressions. Each nesting level
    adds 1. A function with no loops returns 0. A single loop returns 1.
    Nested loops return 2+.

    Lambdas and nested functions are treated as separate scopes — their
    internal loops do not contribute to the parent function's depth.
    """
    def _depth(node: ast.AST, current: int = 0) -> int:
        best = current
        for child in ast.iter_child_nodes(node):
            # Nested functions/lambdas are separate scopes
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, LOOP_TYPES):
                best = max(best, _depth(child, current + 1))
            else:
                best = max(best, _depth(child, current))
        return best
    return _depth(fn)


def max_complexity_class(tree: ast.AST) -> int:
    """Return worst-case loop nesting depth across all functions in tree.

    0 = O(1), 1 = O(n), 2 = O(n^2), etc.
    Threshold: <= 1 for the BIG-O principle.
    """
    fns = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not fns:
        return 0
    return max(max_loop_depth(fn) for fn in fns)
