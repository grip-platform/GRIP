#!/usr/bin/env python3
"""Verify-block extractor — Python port of `drafts/verify.sh` awk extractor.

Single pure function:

    extract_verify_block(task_md_path: str) -> str | None

Semantics (matches verify.sh):
- Parse YAML frontmatter delimited by leading `---` and trailing `---`
- Find a line `verify: |` at column 0 inside the frontmatter
- Capture indented lines (exactly 2 spaces) until first un-indented non-blank
  line OR closing `---` OR end-of-file
- Strip the 2-space indent from each captured line
- Return the joined block (newline-terminated lines), or None if no block

CC budget: every function < 10. Pure stdlib. Type-hinted.
"""

from __future__ import annotations

from pathlib import Path

_FENCE = "---"
_VERIFY_HEAD = "verify:"
_INDENT = "  "  # exactly 2 spaces — matches awk `sub(/^  /, "")`


def _is_fence(line: str) -> bool:
    """True if the line is a frontmatter fence (`---` plus optional spaces)."""
    return line.strip() == _FENCE


def _is_verify_head(line: str) -> bool:
    """True if the line opens the `verify: |` literal block at column 0."""
    stripped = line.rstrip()
    if not stripped.startswith(_VERIFY_HEAD):
        return False
    # Accept `verify: |` and `verify:|` plus trailing whitespace variants
    tail = stripped[len(_VERIFY_HEAD):].strip()
    return tail == "|"


def _is_block_terminator(line: str) -> bool:
    """A captured block ends at a fence or any un-indented non-blank line."""
    if _is_fence(line):
        return True
    if not line.strip():
        return False  # blank lines stay inside the block
    return not line.startswith(_INDENT)


def _strip_indent(line: str) -> str:
    """Remove leading 2-space indent if present, preserve the rest verbatim."""
    return line[len(_INDENT):] if line.startswith(_INDENT) else line


def extract_verify_block(task_md_path: str) -> str | None:
    """Extract the `verify: |` block from a task markdown file.

    Returns the block text (lines joined with `\\n`) or None when no block
    is found. Mirrors the awk state machine in `drafts/verify.sh`:

        fm == 0  → before frontmatter
        fm == 1  → inside frontmatter
        fm == 2  → after frontmatter (we exit before reaching this)
        cap == 1 → currently capturing the verify block
    """
    path = Path(task_md_path)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = 0
    cap = False
    captured: list[str] = []
    for raw in text.splitlines():
        if _is_fence(raw):
            fm += 1
            if fm == 2 or cap:
                break
            continue
        if fm != 1:
            continue
        if cap:
            if _is_block_terminator(raw):
                break
            captured.append(_strip_indent(raw))
            continue
        if _is_verify_head(raw):
            cap = True
    if not captured:
        return None
    return "\n".join(captured) + "\n"


# ---------------------------------------------------------------------------
# Smoke tests — same 4 cases verify.sh handles
# ---------------------------------------------------------------------------


def _make_task(slug: str, verify_lines: list[str] | None,
               extra_keys: str = "") -> str:
    """Compose a synthetic task markdown body for smoke fixtures."""
    parts = ["---", f"id: {slug}", f"title: {slug}"]
    if verify_lines is not None:
        parts.append("verify: |")
        parts.extend(f"  {ln}" for ln in verify_lines)
    if extra_keys:
        parts.append(extra_keys)
    parts.extend(["---", "", "Body.", ""])
    return "\n".join(parts)


def _verify_case(slug: str, lines: list[str], extra: str = "") -> tuple[str, str]:
    """Build a (task_body, expected_extracted_block) pair for a verify-block fixture."""
    body = _make_task(slug, lines, extra_keys=extra)
    expected = "\n".join(lines) + "\n"
    return body, expected


def _smoke_cases() -> dict[str, tuple[str, str | None]]:
    """The 4 cases from verify.sh: pass / fail / no-block / log."""
    return {
        "pass.md": _verify_case("smoke-pass", ["set -e", "echo ok"]),
        "fail.md": _verify_case("smoke-fail", ["set -e", "exit 1"]),
        "noblock.md": (_make_task("smoke-noblock", None,
                                   extra_keys="priority: normal"), None),
        "log.md": _verify_case("smoke-log",
                                ["set -e", "echo line1", "echo line2"],
                                extra="priority: high"),
    }


def _run_smoke_tests() -> int:
    """Write 4 fixtures to a temp dir, run extractor, assert outcomes."""
    import tempfile

    cases = _smoke_cases()
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, (body, want) in cases.items():
            path = root / name
            path.write_text(body)
            got = extract_verify_block(str(path))
            ok = got == want
            print(f"  [{'OK' if ok else 'XX'}] {name}: got={got!r}")
            if not ok:
                fails += 1
                print(f"       want={want!r}")
    print(f"smoke: {len(cases) - fails}/{len(cases)} passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_run_smoke_tests())
