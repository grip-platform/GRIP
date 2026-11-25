#!/usr/bin/env python3
"""CC Gate Hook — PreToolUse hook measuring cyclomatic complexity.

Analyses Python files being written/edited and warns or blocks based on
trust-modulated CC thresholds.

Uses complexity_profiler.calculate_complexity() — the canonical McCabe calculator.

Thresholds (from gate_modulator.py trust levels):
    STRICT:  warn=10, block=20
    DEFAULT: warn=15, block=30
    RELAXED: warn=20, block=40

Differential enforcement (added 2026-05-12, parity with quality-pretool-hook):
    The hook compares each function in the proposed post-edit content against
    the same function (by qualified name) in the file's current content.

    A function is *introduced* if its qualified name didn't exist in old or
    its CC has gone UP versus the old version. Introduced violations always
    DENY — yolo never bypasses complexity the operator just added.

    A function is *pre-existing* if its CC is the same as or lower than the
    old version. Pre-existing block-tier violations DENY by default but pass
    under yolo with the ``yolo_quality`` preset, with an audit advisory.

Registered in settings.json as a PreToolUse hook matching Edit|Write.
"""
import ast
import json
import sys
from pathlib import Path

# Ensure lib/ is importable
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from complexity_profiler import calculate_complexity

try:
    from gates.yolo_mode import is_yolo_bypass
except ImportError:
    def is_yolo_bypass(_gate: str) -> bool:
        return False

# CC thresholds per trust level
CC_THRESHOLDS = {
    "STRICT":  {"warn": 10, "block": 20},
    "DEFAULT": {"warn": 15, "block": 30},
    "RELAXED": {"warn": 20, "block": 40},
}


def _get_trust_level() -> str:
    """Determine current trust level from Cube convergence state."""
    try:
        from gate_modulator import get_convergence_trust_level
        level = get_convergence_trust_level()
        return level.name
    except Exception:
        return "DEFAULT"


def _get_thresholds(trust_level: str) -> dict:
    """Get warn/block thresholds for trust level."""
    return CC_THRESHOLDS.get(trust_level, CC_THRESHOLDS["DEFAULT"])


_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _entry(node, prefix: str = "") -> tuple[str, int, int]:
    qname = f"{prefix}{node.name}" if prefix else node.name
    return (qname, node.lineno, calculate_complexity(node))


def _iter_class_methods(cls: ast.ClassDef):
    return (
        _entry(sub, prefix=f"{cls.name}.")
        for sub in cls.body
        if isinstance(sub, _FUNC_TYPES)
    )


def _extract_qualified_functions(source: str) -> list[tuple[str, int, int]]:
    """Return (qualified_name, line, cc) for every top-level and class-method
    function. Qualified names disambiguate same-named methods across classes
    (e.g. ``Foo.save`` vs ``Bar.save``).

    Skips nested functions inside functions — those are rare and matching
    their identity across edits is unreliable.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: list[tuple[str, int, int]] = []
    for node in tree.body:
        if isinstance(node, _FUNC_TYPES):
            results.append(_entry(node))
        elif isinstance(node, ast.ClassDef):
            results.extend(_iter_class_methods(node))
    return results


def _compute_contents(hook_input: dict) -> tuple[str, str, str] | None:
    """Resolve (file_path, old_content, new_content) for the proposed edit."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    if tool_name not in ("Write", "Edit"):
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path.endswith(".py"):
        return None

    try:
        old_content = Path(file_path).read_text()
    except (OSError, UnicodeDecodeError):
        old_content = ""

    if tool_name == "Write":
        new_content = tool_input.get("content", "")
    else:
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        if not old_string:
            return None
        if tool_input.get("replace_all"):
            new_content = old_content.replace(old_string, new_string)
        else:
            new_content = old_content.replace(old_string, new_string, 1)
        if new_content == old_content:
            return None

    if not new_content:
        return None
    return (file_path, old_content, new_content)


def _partition(old_content: str, new_content: str) -> tuple[
    list[tuple[str, int, int]], list[tuple[str, int, int]]
]:
    """Split functions in new content into (introduced, preexisting).

    A function is *introduced* when its qualified name is new OR its CC has
    gone up versus the prior version. Otherwise it is *pre-existing*.
    """
    old_cc = {name: cc for name, _, cc in _extract_qualified_functions(old_content)}
    introduced: list[tuple[str, int, int]] = []
    preexisting: list[tuple[str, int, int]] = []
    for entry in _extract_qualified_functions(new_content):
        name, _, cc = entry
        prior = old_cc.get(name)
        if prior is None or cc > prior:
            introduced.append(entry)
        else:
            preexisting.append(entry)
    return introduced, preexisting


def _block_response(name: str, cc: int, threshold: int, trust: str, lead: str) -> dict:
    # Pure modern shape: the earlier hybrid carried a stale top-level
    # {"decision": "deny"} whose invalid vocabulary risked failing root
    # validation (see lib/hook_response.py).
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{lead} Function '{name}' has cyclomatic complexity {cc} "
                f"(threshold: {threshold}, trust: {trust}). "
                f"Decompose using extract-dispatch, extract-independent-blocks, "
                f"or coordinator pattern before writing."
            ),
        },
    }


def _advisory(text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }


_PRE_BLOCK_LEAD = (
    "[CC GATE: BLOCK — PRE-EXISTING] Fix the violation or activate "
    "yolo_quality (`grip-yolo on --quality --reason '...'`) to land "
    "alongside known debt."
)


def _handle_intro_block(intro_blocks, th, trust) -> dict:
    name, _, cc = max(intro_blocks, key=lambda f: f[2])
    return _block_response(
        name, cc, th["block"], trust,
        lead="[CC GATE: BLOCK — INTRODUCED]",
    )


def _handle_pre_block(pre_blocks, th, trust) -> dict:
    name, _, cc = max(pre_blocks, key=lambda f: f[2])
    if is_yolo_bypass("cc-gate"):
        return _advisory(
            f"[YOLO BYPASS: cc-gate] pre-existing high complexity in "
            f"'{name}' (CC: {cc}, threshold: {th['block']}). Edit allowed "
            f"under yolo_quality."
        )
    return _block_response(name, cc, th["block"], trust, lead=_PRE_BLOCK_LEAD)


def _handle_warns(funcs, th, trust, file_path) -> dict | None:
    warn_funcs = [f for f in funcs if f[2] >= th["warn"]]
    if not warn_funcs:
        return None
    warn_list = ", ".join(f"{n}(CC:{c})" for n, _, c in warn_funcs)
    return _advisory(
        f"[CC GATE: WARN] High cyclomatic complexity in {file_path}: "
        f"{warn_list}. Trust: {trust}, warn threshold: {th['warn']}. "
        f"Consider decomposition."
    )


def _route(introduced, preexisting, file_path, trust, th) -> dict | None:
    intro_blocks = [f for f in introduced if f[2] >= th["block"]]
    if intro_blocks:
        return _handle_intro_block(intro_blocks, th, trust)
    pre_blocks = [f for f in preexisting if f[2] >= th["block"]]
    if pre_blocks:
        return _handle_pre_block(pre_blocks, th, trust)
    return _handle_warns(introduced + preexisting, th, trust, file_path)


def main() -> None:
    """Hook entry point — reads tool input from stdin."""
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return
    resolved = _compute_contents(hook_input)
    if not resolved:
        return
    file_path, old_content, new_content = resolved
    introduced, preexisting = _partition(old_content, new_content)
    if not introduced and not preexisting:
        return
    trust = _get_trust_level()
    response = _route(introduced, preexisting, file_path, trust, _get_thresholds(trust))
    if response:
        print(json.dumps(response))


if __name__ == "__main__":
    main()
