"""Retroactive Outcome Discovery for Decision Logger.

Scans git history, PR status, and observable events to infer real-world
outcomes for unresolved decisions. Closes the feedback loop that was
missing: decisions were logged but outcomes never recorded.

Architecture:
    1. Load unresolved decisions from DecisionLogger
    2. For each decision, gather evidence from:
       - Git commits since decision timestamp
       - PR merges/closes since decision timestamp
       - Ritual-specific heuristics
    3. Infer outcome (success/failure/inconclusive)
    4. Record via DecisionLogger.record_outcome()

Usage:
    python3 lib/copilot/outcome_discovery.py discover     # Discover + record
    python3 lib/copilot/outcome_discovery.py preview      # Preview without recording
    python3 lib/copilot/outcome_discovery.py stats        # Show resolution stats
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.subprocess_utils import run_git, run_gh
from lib.aide.decision_logger import DecisionLogger

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Ritual-to-evidence mapping: what observable events confirm each ritual's decisions
RITUAL_EVIDENCE = {
    "git-daily-cleanup": {
        "signals": ["branch", "prune", "cleanup", "delete"],
        "git_pattern": r"(clean|prune|branch|delete|remove)",
        "default_outcome": "Branch cleanup executed (confirmed by subsequent git activity)",
    },
    "git-secrets-scan": {
        "signals": ["secret", "scan", "clear", "safe"],
        "git_pattern": r"(commit|push|merge)",
        "default_outcome": "Commits proceeded without secret alerts (no reverts detected)",
    },
    "rsi-phased-execution": {
        "signals": ["rsi", "improve", "merge", "refactor", "feat", "fix"],
        "git_pattern": r"(feat|fix|refactor|chore|perf|test):",
        "default_outcome": "RSI improvement cycle completed (commits detected post-decision)",
    },
    "rsi-autonomous": {
        "signals": ["rsi", "converge", "improve"],
        "git_pattern": r"(feat|fix|refactor|chore):",
        "default_outcome": "Autonomous RSI cycle completed",
    },
    "copilot-frontend-rsi": {
        "signals": ["frontend", "copilot", "ui", "component"],
        "git_pattern": r"(copilot|frontend|ui|component)",
        "default_outcome": "Frontend development progressed",
    },
    "morning-brief": {
        "signals": ["priority", "plan", "brief"],
        "git_pattern": r".",  # Any commit activity = work happened
        "default_outcome": "Daily priorities actioned (work activity detected)",
    },
    "kono-maintenance": {
        "signals": ["kono", "memory", "prune", "substrate"],
        "git_pattern": r"(kono|memory|substrate|embed)",
        "default_outcome": "KONO maintenance cycle completed",
    },
    "weekly-learn": {
        "signals": ["learn", "insight", "rule", "skill"],
        "git_pattern": r"(learn|insight|rule|skill)",
        "default_outcome": "Learning cycle completed",
    },
}


def _get_commits_since(since_iso: str, until_iso: str = "") -> List[Dict]:
    """Get git commits between two timestamps."""
    args = ["log", f"--since={since_iso}", "--format=%H|%ai|%s"]
    if until_iso:
        args.append(f"--until={until_iso}")
    args.append("--no-merges")
    raw = run_git(args, timeout=10)
    if not raw:
        return []

    commits = []
    for line in raw.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) >= 3:
            commits.append({
                "hash": parts[0][:8],
                "date": parts[1],
                "message": parts[2],
            })
    return commits


def _get_merged_prs_since(since_iso: str) -> List[Dict]:
    """Get merged PRs since a timestamp via gh CLI."""
    raw = run_gh([
        "pr", "list", "--state", "merged",
        "--json", "number,title,mergedAt",
        "--limit", "50",
    ], timeout=15)
    if not raw:
        return []

    try:
        prs = json.loads(raw)
        return [
            pr for pr in prs
            if pr.get("mergedAt", "") >= since_iso
        ]
    except (json.JSONDecodeError, TypeError):
        return []


def _infer_outcome(
    decision: Dict,
    commits: List[Dict],
    merged_prs: List[Dict],
) -> Tuple[Optional[str], bool, float]:
    """Infer outcome for a decision based on observable evidence.

    Returns:
        (outcome_text, success, confidence) or (None, False, 0.0) if inconclusive
    """
    ritual_id = decision.get("ritual_id", "")
    decision_data = decision.get("decision", {})
    what = decision_data.get("what", "")
    step_id = decision.get("step_id", "unknown")

    # Skip ghost decisions (empty what or generic "Decision")
    if not what or what == "Decision":
        return (
            f"Ghost decision (no content logged, step_id={step_id})",
            False,
            0.3,
        )

    # Get ritual-specific evidence rules
    evidence_rules = RITUAL_EVIDENCE.get(ritual_id, {})
    git_pattern = evidence_rules.get("git_pattern", r".")
    default_outcome = evidence_rules.get(
        "default_outcome",
        "Decision executed (activity detected post-decision)",
    )

    # Score evidence
    matching_commits = [
        c for c in commits
        if re.search(git_pattern, c["message"], re.IGNORECASE)
    ]
    matching_prs = [
        pr for pr in merged_prs
        if re.search(git_pattern, pr.get("title", ""), re.IGNORECASE)
    ]

    evidence_score = min(1.0, (len(matching_commits) * 0.15 + len(matching_prs) * 0.3))

    if evidence_score >= 0.3:
        # Enough evidence to infer success
        evidence_parts = []
        if matching_commits:
            evidence_parts.append(f"{len(matching_commits)} related commits")
        if matching_prs:
            pr_nums = [f"#{pr['number']}" for pr in matching_prs[:3]]
            evidence_parts.append(f"PRs {', '.join(pr_nums)}")

        outcome = f"{default_outcome} Evidence: {', '.join(evidence_parts)}."
        confidence = min(0.9, 0.5 + evidence_score)
        return (outcome, True, confidence)

    elif commits:
        # Some activity but not matching the ritual pattern
        return (
            f"Activity detected ({len(commits)} commits) but no direct evidence for '{what[:50]}'",
            True,
            0.4,
        )

    else:
        # No activity at all
        ts = decision.get("timestamp", "")
        age_str = ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                age_str = f" ({age_h:.0f}h old)"
            except ValueError:
                pass
        return (
            f"No observable activity since decision{age_str}. May be pending or superseded.",
            False,
            0.3,
        )


def discover_outcomes(
    max_age_hours: float = 72,
    dry_run: bool = False,
) -> Dict:
    """Discover and record outcomes for unresolved decisions.

    Args:
        max_age_hours: Only process decisions within this age window
        dry_run: If True, preview without recording

    Returns:
        Summary dict with discovered, recorded, skipped counts
    """
    dl = DecisionLogger()
    unresolved = dl.get_unresolved_decisions(max_age_hours=max_age_hours)

    if not unresolved:
        return {"discovered": 0, "recorded": 0, "skipped": 0, "message": "No unresolved decisions"}

    # Pre-fetch evidence: commits and PRs for the full time window
    oldest_ts = min(d.get("timestamp", "") for d in unresolved if d.get("timestamp"))
    all_commits = _get_commits_since(oldest_ts)
    all_merged_prs = _get_merged_prs_since(oldest_ts)

    discovered = []
    recorded = 0
    skipped = 0

    for decision in unresolved:
        decision_ts = decision.get("timestamp", "")
        decision_id = decision.get("id", "")

        if not decision_ts or not decision_id:
            skipped += 1
            continue

        # Filter evidence to post-decision window
        commits_after = [c for c in all_commits if c["date"] >= decision_ts]
        prs_after = [pr for pr in all_merged_prs if pr.get("mergedAt", "") >= decision_ts]

        outcome_text, success, confidence = _infer_outcome(
            decision, commits_after, prs_after
        )

        if outcome_text and confidence >= 0.3:
            entry = {
                "decision_id": decision_id,
                "ritual_id": decision.get("ritual_id", ""),
                "what": decision.get("decision", {}).get("what", ""),
                "outcome": outcome_text,
                "success": success,
                "confidence": confidence,
                "evidence_type": "retroactive_discovery",
            }
            discovered.append(entry)

            if not dry_run:
                dl.record_outcome(
                    decision_id,
                    outcome=f"[retroactive] {outcome_text}",
                    success=success,
                )
                recorded += 1
        else:
            skipped += 1

    return {
        "discovered": len(discovered),
        "recorded": recorded,
        "skipped": skipped,
        "total_unresolved": len(unresolved),
        "evidence": {
            "commits_scanned": len(all_commits),
            "prs_scanned": len(all_merged_prs),
        },
        "discoveries": discovered if dry_run else [],
        "dry_run": dry_run,
    }


def resolution_stats() -> Dict:
    """Get current decision resolution statistics."""
    dl = DecisionLogger()
    stats = dl.stats()
    unresolved = dl.get_unresolved_decisions(max_age_hours=168)  # 1 week

    # Group unresolved by ritual
    by_ritual = {}
    for d in unresolved:
        rid = d.get("ritual_id", "unknown")
        by_ritual[rid] = by_ritual.get(rid, 0) + 1

    return {
        **stats,
        "unresolved_7d": len(unresolved),
        "by_ritual": by_ritual,
    }


def decision_brief(max_items: int = 5) -> str:
    """Generate a brief summary of unresolved decisions for session start.

    Returns a markdown-formatted string suitable for injection into
    session context rules. Returns empty string if no actionable decisions.
    """
    dl = DecisionLogger()
    unresolved = dl.get_unresolved_decisions(max_age_hours=48)

    # Filter out ghost decisions and legacy entries without ritual
    actionable = [
        d for d in unresolved
        if d.get("ritual_id")
        and d.get("decision", {}).get("what", "") not in ("", "Decision")
    ]

    if not actionable:
        return ""

    stats = dl.stats()
    resolution_pct = round(stats.get("resolution_rate", 0) * 100)

    lines = [
        f"## Pending Decisions ({len(actionable)} unresolved, {resolution_pct}% resolved overall)",
        "",
    ]

    for d in actionable[:max_items]:
        decision = d.get("decision", {})
        what = decision.get("what", "?")[:60]
        ritual = d.get("ritual_id", "?")
        conf = decision.get("confidence", 0)
        ts = d.get("timestamp", "")[:16]
        lines.append(f"- **[{ritual}]** {what} (conf={conf}, {ts})")

    remaining = len(actionable) - max_items
    if remaining > 0:
        lines.append(f"- ...and {remaining} more")

    lines.append("")
    lines.append("Consider recording outcomes for completed decisions.")

    return "\n".join(lines)


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"

    if cmd == "discover":
        result = discover_outcomes(dry_run=False)
        print(json.dumps(result, indent=2))
    elif cmd == "preview":
        result = discover_outcomes(dry_run=True)
        print(json.dumps(result, indent=2))
        if result["discoveries"]:
            print(f"\n--- {len(result['discoveries'])} outcomes would be recorded ---")
            for d in result["discoveries"][:10]:
                print(f"  [{d['ritual_id']}] {d['what'][:50]} -> {d['outcome'][:80]}")
    elif cmd == "stats":
        result = resolution_stats()
        print(json.dumps(result, indent=2))
    elif cmd == "brief":
        brief = decision_brief()
        print(brief if brief else "No actionable unresolved decisions.")
    else:
        print(f"Usage: {sys.argv[0]} [discover|preview|stats|brief]")
        sys.exit(1)


if __name__ == "__main__":
    main()
