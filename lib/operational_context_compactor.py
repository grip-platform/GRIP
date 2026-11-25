"""OCF (Operational Context Fold) compactor.

Transport shim in the OCF architecture. Removes [Operational Discovery]
appendages from rules/operational-context.md and routes their content
to learning/insights.jsonl (KONO substrate).

OCF invariant:
    full-context ≡ OCF-kernel (this file) + fold-guard (hooks/) + KONO-substrate
    Fold property: ℱ(file) = file — idempotent (running compactor twice = no change)

Usage:
    grip-py lib/operational_context_compactor.py            # compact + route
    grip-py lib/operational_context_compactor.py --dry-run  # report only, write nothing
    grip-py lib/operational_context_compactor.py --check    # exit 0 ok, 1 over limit
    grip-py lib/operational_context_compactor.py --root DIR # fold DIR's copy instead

Exit codes:
    0  compaction done, dry run reported, or --check within MAX_CHARS
    1  --check found the file over MAX_CHARS (nothing written)
    2  unrecognised or conflicting arguments, or no rules/operational-context.md in
       the tree — usage printed, nothing written

Before 2026-09-22 (GRIP#6375) `main()` recognised only `--check`, so `--help` —
or a typo — fell through to the live compaction and rewrote the production file.

Design:
    - Canonical sections (up to and including "## OCF Invariant") are preserved verbatim
    - Any "### [Operational Discovery]" block is appended to insights.jsonl as a raw entry
    - Idempotent: if no discovery sections exist, file is unchanged (ℱ(file) = file)
    - The tree is `--root` when given. Otherwise it resolves through
      lib/claude_dir.get_claude_dir() at call time: $CLAUDE_DIR when it is set,
      otherwise $HOME/.claude. The file and its insights.jsonl always come from the
      same tree, so a fold and the record it routes land in one commit
    - To fold a worktree's copy, pass `--root <worktree>`. Do not set
      CLAUDE_DIR=<worktree> for that: bin/grip-py looks for its venv under
      $CLAUDE_DIR, a worktree has none, and the run exits 1 before this file is
      reached (Codex on GRIP#8378)

Version: 1.2.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

TARGET_REL = Path("rules") / "operational-context.md"
INSIGHTS_REL = Path("learning") / "insights.jsonl"
MAX_CHARS = 40_000
_DISCOVERY_PATTERN = re.compile(
    r"^---\s*\n\s*### \[Operational Discovery\].*?(?=^---\s*\n\s*### \[Operational Discovery\]|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _root_dir(root: Path | None) -> Path:
    """The tree under fold: ``root`` when given, else get_claude_dir() at call time."""
    return get_claude_dir() if root is None else root


def target_path(root: Path | None = None) -> Path:
    """The file under fold, resolved per call so a changed $CLAUDE_DIR is honoured."""
    return _root_dir(root) / TARGET_REL


def insights_path(root: Path | None = None) -> Path:
    """The KONO substrate the folded blocks are routed to, in the file's own tree."""
    return _root_dir(root) / INSIGHTS_REL


def _split(text: str) -> tuple[str, list[str]]:
    """Return (kernel_text, [discovery_block, ...])."""
    discoveries = _DISCOVERY_PATTERN.findall(text)
    kernel = _DISCOVERY_PATTERN.sub("", text).rstrip() + "\n"
    return kernel, discoveries


def _route_to_kono(blocks: list[str], insights: Path) -> int:
    """Append extracted discovery blocks to insights.jsonl. Returns count."""
    if not blocks:
        return 0
    insights.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with insights.open("a", encoding="utf-8") as fh:
        for block in blocks:
            record = {
                "timestamp": ts,
                "source": "ocf-compactor",
                "type": "operational_discovery",
                "content": block.strip(),
            }
            fh.write(json.dumps(record) + "\n")
    return len(blocks)


def compact(*, dry_run: bool = False, root: Path | None = None) -> dict:
    """Compact operational-context.md in ``root`` (default: get_claude_dir()).

    Returns summary dict with keys: before, after, routed, changed. With
    ``dry_run`` nothing is written and ``routed`` counts what WOULD be routed.
    """
    target = target_path(root)
    text = target.read_text(encoding="utf-8")
    before = len(text)

    kernel, discoveries = _split(text)
    after = len(kernel)
    changed = kernel != text

    if changed and not dry_run:
        n = _route_to_kono(discoveries, insights_path(root))
        target.write_text(kernel, encoding="utf-8")
    else:
        n = len(discoveries)

    return {"before": before, "after": after, "routed": n, "changed": changed}


def check(root: Path | None = None) -> bool:
    """Return True if file is within MAX_CHARS (no compaction needed)."""
    return len(target_path(root).read_text(encoding="utf-8")) <= MAX_CHARS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operational_context_compactor",
        description=(
            "Fold [Operational Discovery] blocks out of rules/operational-context.md "
            "into learning/insights.jsonl. With no flags it WRITES."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 0 if the file is within MAX_CHARS, 1 if over; never writes",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="report what a compaction would do; never writes",
    )
    parser.add_argument(
        "--root",
        type=Path,
        metavar="DIR",
        help=(
            "fold DIR's rules/operational-context.md, routing to DIR's "
            "learning/insights.jsonl; default $CLAUDE_DIR, else $HOME/.claude"
        ),
    )
    return parser


def _run_check(root: Path) -> int:
    ok = check(root)
    size = len(target_path(root).read_text(encoding="utf-8"))
    status = "OK" if ok else "OVER"
    print(f"[OCF] {status}: {size:,} / {MAX_CHARS:,} chars", file=sys.stderr)
    return 0 if ok else 1


def _run_compact(*, dry_run: bool, root: Path) -> int:
    result = compact(dry_run=dry_run, root=root)
    prefix = "[OCF] DRY RUN: would compact" if dry_run else "[OCF] Compacted:"
    holds = "[OCF] DRY RUN: fold-property holds" if dry_run else "[OCF] Fold-property holds"
    tail = "; nothing written." if dry_run else "."
    if result["changed"]:
        print(
            f"{prefix} {result['before']:,} → {result['after']:,} chars "
            f"({result['before'] - result['after']:,} removed, "
            f"{result['routed']} blocks routed to KONO){tail}",
            file=sys.stderr,
        )
    else:
        print(
            f"{holds}: {result['after']:,} chars, no discoveries to route{tail}",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = _root_dir(args.root)
    if not target_path(root).is_file():
        parser.error(f"no {TARGET_REL.as_posix()} under {root}")
    if args.check:
        return _run_check(root)
    return _run_compact(dry_run=args.dry_run, root=root)


if __name__ == "__main__":
    sys.exit(main())
