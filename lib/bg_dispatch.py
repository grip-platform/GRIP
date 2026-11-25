#!/usr/bin/env python3
"""Background-agent dispatch helper — pre-fetch references into the prompt.

Origin: 2026-05-11 MESH council on the recurring envelope-close failure.
Top finding: subagents stall at "let me look at X" boundaries between
atomic deliverables. Eliminating those lookup steps eliminates the stall.

Usage (CLI):
    python3 -m lib.bg_dispatch build \\
        --goal "Build the foo module" \\
        --ref ~/.claude/lib/foo_reference.py \\
        --ref ~/.claude/data/foo-schema.json \\
        --out /tmp/agent-prompt.txt

Library API:

    from lib.bg_dispatch import inline_refs, build_prompt_with_refs

    refs_block = inline_refs(['~/.hal/apps/grip-web/package.json'])
    prompt = f"Build X.\\n\\n{refs_block}\\n\\nCommit after each atom."

Design:
- Pure stdlib, no dependencies.
- Each ref renders as <reference path="..." size_bytes="N">CONTENTS</reference>.
- Large files (>50 KB) truncate with a marker so prompts stay bounded.
- Missing files render with status="missing" so the agent sees the gap.

Falsifier (H-DISPATCH-PREFETCH): if pre-fetching refs reduces the
DISPATCH_INCOMPLETE rate by <30% over the next 5 dispatches, this helper
is not the right primitive — advance to one-atom-per-agent strict mode.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MAX_REF_BYTES = 50 * 1024
TRUNCATION_MARKER = "\n\n[…truncated for prompt-size budget…]\n"


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def _read_text(resolved: Path) -> tuple[str, int, str]:
    """Return (text, size_bytes, status). status is 'ok' | 'binary' | 'error:<msg>'."""
    try:
        raw = resolved.read_bytes()
    except OSError as e:
        return "", 0, f"error:{e}"
    try:
        return raw.decode("utf-8"), len(raw), "ok"
    except UnicodeDecodeError:
        return "", len(raw), "binary"


def _truncate(text: str, size: int) -> str:
    if size <= MAX_REF_BYTES:
        return text
    return text[: MAX_REF_BYTES - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _render_one(path: str) -> str:
    """Render a single reference as an <reference> XML-ish block."""
    resolved = _resolve(path)
    if not resolved.exists():
        return f'<reference path="{path}" status="missing"/>'
    text, size, status = _read_text(resolved)
    if status == "binary":
        return f'<reference path="{path}" size_bytes="{size}" status="binary"/>'
    if status.startswith("error"):
        return f'<reference path="{path}" status="{status}"/>'
    return (
        f'<reference path="{path}" size_bytes="{size}">\n'
        f"{_truncate(text, size)}\n"
        f"</reference>"
    )


def inline_refs(paths: list[str]) -> str:
    """Render a list of reference paths as a single concatenated block."""
    if not paths:
        return ""
    return "\n\n".join(_render_one(p) for p in paths)


def build_prompt_with_refs(goal: str, refs: list[str], header: str = "") -> str:
    """Assemble a full prompt: optional header + refs block + goal."""
    parts: list[str] = []
    if header:
        parts.append(header.strip())
    refs_block = inline_refs(refs)
    if refs_block:
        parts.append(
            "## Pre-fetched references\n\n"
            "These files are inlined so the agent never needs a 'let me look "
            "at X' lookup step.\n\n" + refs_block
        )
    parts.append("## Goal\n\n" + goal.strip())
    return "\n\n".join(parts) + "\n"


def _cli_build(args: argparse.Namespace) -> int:
    prompt = build_prompt_with_refs(
        goal=args.goal, refs=args.ref or [], header=args.header or ""
    )
    if args.out:
        Path(args.out).write_text(prompt, encoding="utf-8")
        print(f"Prompt written to {args.out} ({len(prompt)} chars)", file=sys.stderr)
    else:
        sys.stdout.write(prompt)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bg_dispatch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="Assemble a prompt with pre-fetched refs")
    build.add_argument("--goal", required=True)
    build.add_argument("--ref", action="append", help="Reference file path (repeatable)")
    build.add_argument("--header", default="")
    build.add_argument("--out", help="Write to file; if omitted, print to stdout")
    build.set_defaults(func=_cli_build)
    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
