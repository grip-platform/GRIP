#!/usr/bin/env python3
"""TDD Advisory Hook — PostToolUse reminder for missing test files.

Fires after Write/Edit of source files. Checks if a corresponding test
file exists. If not, outputs an advisory reminder (Rule 14).

Advisory only — does not block. Targets the 44% gap where code PRs
ship without tests (evidence from Issue #1561, 2026-03-29).

Registered in settings.json as a PostToolUse hook matching Write|Edit.
"""
import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"

# Source directories where test files are expected
_SOURCE_DIRS = {"lib", "hooks"}

# File patterns to skip (not testable or already tests)
_SKIP_PREFIXES = ("test_", "__")
_SKIP_SUFFIXES = ("__init__.py", "conftest.py")


def _checkout_root(source_path: Path) -> Path | None:
    """The folder holding the source's nearest lib or hooks folder, such as a
    worktree's root; None when the path has neither."""
    return next((p.parent for p in source_path.parents if p.name.lower() in _SOURCE_DIRS), None)


def _find_test_file(source_path: Path) -> Path | None:
    """The test file for a source file, looked for in GRIP's tests folder and in
    the tests folder of the checkout the source sits in. A new module in a
    worktree keeps its test beside it there, so looking only in CLAUDE_DIR drew
    a false reminder (AGORA on GRIP#9798)."""
    test_name = f"test_{source_path.stem}.py"
    roots = (CLAUDE_DIR, _checkout_root(source_path))
    return next((root / "tests" / test_name for root in roots
                 if root is not None and (root / "tests" / test_name).exists()), None)


def _should_check(file_path: str) -> bool:
    """Determine if this file write warrants a test reminder."""
    p = Path(file_path)
    if p.suffix != ".py":
        return False
    if p.name.startswith(_SKIP_PREFIXES):
        return False
    if p.name in _SKIP_SUFFIXES:
        return False

    try:
        rel = p.relative_to(CLAUDE_DIR)
        return rel.parts[0].lower() in _SOURCE_DIRS
    except ValueError:
        # File outside CLAUDE_DIR, such as one in a worktree: any folder of its
        # path named lib or hooks. Compared by path parts, not by "/lib/" in the
        # text, which a Windows path written with backslashes never contains, so
        # the reminder never fired there (GRIP#9341). Compared without case, so
        # a "Lib" folder counts too (AGORA on GRIP#9798).
        return any(part.lower() in _SOURCE_DIRS for part in p.parent.parts)


def main() -> None:
    """Hook entry point — reads tool result from stdin."""
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path or not _should_check(file_path):
        return

    source = Path(file_path)
    test = _find_test_file(source)

    if test is None:
        module = source.stem
        print(
            f"[TDD ADVISORY] No test file found for '{module}'. "
            f"Expected: tests/test_{module}.py. "
            f"Rule 14 requires tests for RSI PRs."
        )


if __name__ == "__main__":
    main()
