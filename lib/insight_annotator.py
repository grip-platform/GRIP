"""W8 — self-documenting `# INSIGHT[<fp>]:` annotations in source code.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W8. When the propagator
writes a canonical-file edit, this module ALSO emits a `# INSIGHT[<fp>]:`
annotation at the source-code site the insight refers to. Existing
quality gates parse the annotation and respect the documented intent —
turning insights into enforceable bounds rather than passive observations.

Design constraints:
- AST-safe: post-write re-parse confirms no syntax break (Python only).
- Atomic: tempfile + os.replace; partial writes leave the original intact.
- Density-capped: scales with file LOC. Tiny files allow 2 annotations
  (one for an insight + one for its inverse falsifier); legacy mega-files
  ceiling at 30 to prevent dilution of the gate-respect signal.
- Pure stdlib: no third-party dependencies.

Annotation format::

    # INSIGHT[<12-16 hex chars>]: <hook text>

The fingerprint must match an entry in ``learning/insights.jsonl`` or
``data/converge-fixed-points.jsonl`` for the reconciler (W8.3) to
accept it as live; orphaned fingerprints are flagged in the drift report.
"""
from __future__ import annotations

import ast
import os
import re
import tempfile
from pathlib import Path

# Annotation regex (matches `# INSIGHT[abc123def456]: hook content`).
INSIGHT_RE = re.compile(r"#\s*INSIGHT\[([0-9a-f]{12,16})\]:\s*(.+)")


def density_cap(file_loc: int) -> int:
    """Annotation density cap: scales with file LOC.

    Floor 2 → tiny files (≤100 LOC) carry at most 2 annotations.
    Slope 1/50 → mid-size files (e.g. 500 LOC → 10 max).
    Ceiling 30 → mega-files (>1500 LOC) capped to prevent dilution.

    Formula: ``cap(loc) = max(2, min(30, loc // 50))``.
    """
    return max(2, min(30, file_loc // 50))


def count_annotations(text: str) -> int:
    """Count `# INSIGHT[<fp>]:` annotations in the file content."""
    return len(INSIGHT_RE.findall(text))


def parse_annotations(text: str) -> list[tuple[int, str, str]]:
    """Yield ``(line_number, fingerprint, hook)`` for every annotation."""
    out: list[tuple[int, str, str]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        match = INSIGHT_RE.search(line)
        if match:
            out.append((line_num, match.group(1), match.group(2).strip()))
    return out


def _is_python_file(path: Path) -> bool:
    return path.suffix == ".py"


def _ast_parses(text: str) -> bool:
    """Return True iff ``text`` parses as valid Python."""
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def _atomic_write(target: Path, content: str) -> None:
    """tempfile-in-same-dir + os.replace = atomic on POSIX."""
    dir_ = target.parent
    fd, tmp_path_str = tempfile.mkstemp(dir=str(dir_), prefix=".insight-tmp-", suffix=".swap")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise


def _detect_indent(line: str) -> str:
    """Mirror the leading whitespace of ``line``."""
    return line[: len(line) - len(line.lstrip())]


def annotate_source(
    target: Path,
    insertion_line: int,
    fingerprint: str,
    hook: str,
) -> bool:
    """Insert `# INSIGHT[<fp>]: <hook>` immediately above ``insertion_line``.

    Returns True if the annotation was inserted, False if it was rejected
    (density cap exceeded, target missing, AST break, or a syntactic
    annotation collision at the target line).

    The 1-based ``insertion_line`` is the line the annotation should
    appear ABOVE — typically the first line of the offending construct
    (nested loop, function with KISS overrun, etc.). Indentation is
    matched to the target line.
    """
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    if insertion_line < 1 or insertion_line > len(lines) + 1:
        return False
    if count_annotations(content) >= density_cap(len(lines)):
        return False
    # Skip if an annotation with the SAME fingerprint already lives within
    # 2 lines of the insertion point (idempotent re-annotation).
    for offset in (1, 2):
        if insertion_line - offset >= 1:
            existing = lines[insertion_line - offset - 1]
            match = INSIGHT_RE.search(existing)
            if match and match.group(1) == fingerprint:
                return False

    target_idx = insertion_line - 1  # 0-based
    indent = _detect_indent(lines[target_idx]) if target_idx < len(lines) else ""
    annotation = f"{indent}# INSIGHT[{fingerprint}]: {hook}"

    new_lines = lines[:target_idx] + [annotation] + lines[target_idx:]
    new_content = "\n".join(new_lines)
    if content.endswith("\n"):
        new_content += "\n"

    if _is_python_file(target) and not _ast_parses(new_content):
        return False
    _atomic_write(target, new_content)
    return True
