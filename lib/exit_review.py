#!/usr/bin/env python3
"""
exit_review.py — Lightweight exit review council.

Gated on session complexity: only runs when session had significant work
(>2 commits or novel insights detected). Uses multi-model council to
challenge the main session claims before persisting to memory.

Attack vectors:
1. What would disprove the main achievement claim?
2. Is there a simpler solution we missed?
3. Any regression risks from today's changes?

Verdicts: STRONG / WEAK / INCONCLUSIVE per claim.

Usage:
    python3 lib/exit_review.py              # Auto-gated
    python3 lib/exit_review.py --force      # Skip gate, always run
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))


def _count_session_commits() -> int:
    """Count commits in current session (last 4 hours)."""
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "--since=4.hours.ago"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0:
            return len([l for l in r.stdout.strip().splitlines() if l.strip()])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 0


def _has_novel_insights() -> bool:
    """Check if recent insights were detected."""
    insights_file = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"
    if not insights_file.exists():
        return False
    try:
        lines = insights_file.read_text().strip().splitlines()
        if not lines:
            return False
        last = json.loads(lines[-1])
        return last.get("criterion_met", False)
    except (json.JSONDecodeError, OSError):
        return False


def should_review(force: bool = False) -> bool:
    """Gate: only review if session had significant work."""
    if force:
        return True
    commits = _count_session_commits()
    insights = _has_novel_insights()
    return commits > 2 or insights


def review_session() -> str:
    """Run lightweight review. Returns verdict summary."""
    council_script = CLAUDE_DIR / "lib" / "multi_llm_council.py"
    if not council_script.exists():
        return "SKIPPED: multi_llm_council.py not available"

    # Get recent achievement from session context
    ctx_file = CLAUDE_DIR / "rules" / "session-context.md"
    claim = "Session work completed"
    if ctx_file.exists():
        try:
            for line in ctx_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("- Gen"):
                    claim = line.strip().lstrip("- ")
                    break
        except OSError:
            pass

    # Run council with falsification prompt
    prompt = (
        f"Evaluate this session claim for evidence quality: '{claim}'. "
        "Rate as STRONG (falsifiable, tested), WEAK (vague, untested), "
        "or INCONCLUSIVE (insufficient data). One sentence justification."
    )

    try:
        # run_text, not subprocess.run(text=True): the verdict is prose that is
        # returned and then sliced to 200 chars. A cp1252 read would both garble
        # it and let the slice land mid-sequence.
        from _compat import run_text
        r = run_text(
            [sys.executable, str(council_script), "--quick", "--prompt", prompt],
            capture_output=True, timeout=25, cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:200]
        return "SKIPPED: council returned no verdict"
    except subprocess.TimeoutExpired:
        return "SKIPPED: council timed out"
    except FileNotFoundError:
        return "SKIPPED: Python not found"


def main():
    parser = argparse.ArgumentParser(description="Exit review council")
    parser.add_argument("--force", action="store_true", help="Skip gate, always run")
    args = parser.parse_args()

    if not should_review(force=args.force):
        print("SKIPPED: session below review threshold")
        return

    verdict = review_session()
    print(verdict)


if __name__ == "__main__":
    main()
