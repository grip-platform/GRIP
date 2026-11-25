#!/usr/bin/env python3
"""RSI CLI interface — command-line entry point for orchestrator commands."""

import json
import sys

from .audit import audit_gate_errors
from .checkpoint import clear_checkpoint, checkpoint_resume_phase, load_checkpoint, run_closeout
from .convergence import (
    check_stop_signal, clear_stop_signal, feed_ralph_result,
    record_ralph_score, set_stop_signal,
)
from .metrics import get_session_summary
from .mining import mine_github_issues, mine_memory_todos, mine_ralph_reviews
from .pr_size import MAX_PR_FILES, MAX_PR_LINES, analyze_pr_size
from .queue import get_pending_candidates


def _cmd_mine_ralph():
    patterns = mine_ralph_reviews()
    if not patterns:
        print("No Ralph review patterns found.")
    for p in patterns:
        print(f"  [{p['severity'].upper()}] {p['pattern']}: {p['count']}x")
        for ex in p.get("examples", [])[:1]:
            print(f"    Example: {ex[:100]}...")


def _cmd_mine_issues():
    candidates = mine_github_issues()
    print(f"Found {len(candidates)} sprint-loop issues:")
    for c in candidates:
        print(f"  #{c.issue_number}: {c.title} (score: {c.priority_score:.1f})")


def _cmd_mine_memory():
    candidates = mine_memory_todos()
    print(f"Found {len(candidates)} PENDING MEMORY items:")
    for c in candidates:
        print(f"  #{c.memory_item}: {c.title} (score: {c.priority_score:.1f})")


def _cmd_list_queue():
    candidates = get_pending_candidates()
    if not candidates:
        print("Queue empty. Run mine-* commands to populate.")
    for c in candidates:
        print(f"  [{c.source}] {c.id}: {c.title} (priority: {c.priority_score:.1f})")


def _cmd_record_score():
    if len(sys.argv) < 3:
        print("Usage: record-score <score>")
        sys.exit(1)
    score = int(sys.argv[2])
    stalled, msg = record_ralph_score(score)
    print(msg)
    sys.exit(1 if stalled else 0)


def _cmd_feed_ralph():
    if len(sys.argv) < 4:
        print("Usage: feed-ralph <pr_number> <score> [comment1] [comment2] ...")
        sys.exit(1)
    pr_num = int(sys.argv[2])
    score = int(sys.argv[3])
    comments = sys.argv[4:] if len(sys.argv) > 4 else None
    stalled, msg, candidates = feed_ralph_result(pr_num, score, comments)
    print(msg)
    if candidates:
        print(f"New candidates: {[c.id for c in candidates]}")
    sys.exit(1 if stalled else 0)


def _cmd_audit_errors():
    limit_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = audit_gate_errors(limit=limit_arg)
    if result["total"] == 0:
        print("No denied events found.")
    else:
        print(f"Total denied: {result['total']}")
        print("\nCategories:")
        for cat, count in result.get("categories", {}).items():
            pct = result.get("pct", {}).get(cat, 0)
            print(f"  {cat}: {count} ({pct}%)")
        print("\nTop 10 gates:")
        for gate, count in list(result.get("top_gates", {}).items())[:10]:
            print(f"  {gate}: {count}")


def _cmd_checkpoint():
    cp = load_checkpoint()
    if not cp:
        print("No active checkpoint.")
    else:
        resume = checkpoint_resume_phase(cp)
        print(f"Checkpoint: phase={cp.phase}, resume_at={resume}")
        print(f"  Candidate: {cp.candidate_id} — {cp.candidate_title}")
        if cp.branch:
            print(f"  Branch: {cp.branch}")
        if cp.pr_number:
            print(f"  PR: #{cp.pr_number} (Ralph score: {cp.ralph_score})")
        age_str = cp.timestamp[:19] if cp.timestamp else "unknown"
        print(f"  Saved: {age_str}")


def _cmd_clear_checkpoint():
    cleared = clear_checkpoint()
    print("Checkpoint cleared." if cleared else "No checkpoint to clear.")


def _cmd_summary():
    summary = get_session_summary()
    print(json.dumps(summary, indent=2))


def _cmd_stop():
    reason = sys.argv[2] if len(sys.argv) > 2 else "user_requested"
    set_stop_signal(reason)
    print(f"Stop signal set: {reason}")


def _cmd_clear_stop():
    clear_stop_signal()
    print("Stop signal cleared.")


def _cmd_closeout():
    candidate = sys.argv[2] if len(sys.argv) > 2 else ""
    dry = "--dry-run" in sys.argv
    results = run_closeout(candidate_id=candidate, dry_run=dry)
    for r in results:
        status = "OK" if r.success else "FAIL"
        err = f" ({r.error})" if r.error else ""
        print(f"  [{status}] {r.name}: {r.elapsed_sec:.1f}s{err}")
    succeeded = sum(1 for r in results if r.success)
    print(f"\nCloseout: {succeeded}/{len(results)} steps succeeded")


def _cmd_pr_size():
    analysis = analyze_pr_size()
    print(f"Files: {analysis['file_count']} (limit: {MAX_PR_FILES})")
    print(f"Lines: {analysis['line_count']} (limit: {MAX_PR_LINES})")
    print(f"Should split: {analysis['should_split']}")
    if analysis['groups']:
        print("Domains:")
        for domain, files in sorted(analysis['groups'].items()):
            print(f"  {domain}: {len(files)} files")
    print(f"Recommendation: {analysis['recommendation']}")


# Dispatch table: command name -> handler function
_COMMANDS = {
    "mine-ralph": _cmd_mine_ralph,
    "mine-issues": _cmd_mine_issues,
    "mine-memory": _cmd_mine_memory,
    "list-queue": _cmd_list_queue,
    "record-score": _cmd_record_score,
    "feed-ralph": _cmd_feed_ralph,
    "audit-errors": _cmd_audit_errors,
    "checkpoint": _cmd_checkpoint,
    "clear-checkpoint": _cmd_clear_checkpoint,
    "closeout": _cmd_closeout,
    "summary": _cmd_summary,
    "stop": _cmd_stop,
    "clear-stop": _cmd_clear_stop,
    "pr-size": _cmd_pr_size,
}


def cli_main():
    """CLI entry point for RSI orchestrator commands."""
    if len(sys.argv) < 2:
        print("Usage: rsi_orchestrator.py <command>")
        print(f"Commands: {', '.join(_COMMANDS)}")
        sys.exit(1)

    cmd = sys.argv[1]
    handler = _COMMANDS.get(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
    handler()
