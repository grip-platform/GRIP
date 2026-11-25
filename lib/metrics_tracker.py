"""
Usage Metrics Tracker for GRIP.

Captures skill, mode, command, and tool usage into learning/metrics.jsonl.
This is foundational for understanding what users actually use vs what exists.

Usage:
    from metrics_tracker import track_event
    track_event("skill_invoked", skill="code-mode")
    track_event("mode_activated", mode="code")
    track_event("command_invoked", command="create-pr")

    # CLI
    python3 lib/metrics_tracker.py summary
    python3 lib/metrics_tracker.py top-skills
    python3 lib/metrics_tracker.py ratio  # infra vs feature ratio
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
METRICS_PATH = CLAUDE_DIR / "learning" / "metrics.jsonl"

# PR category patterns for infra vs feature classification
INFRA_PATTERNS = [
    "chore:", "ci:", "refactor:", "test:", "docs:",
    "fix: hook", "fix: gate", "fix: lint", "fix: test",
    "chore(", "build:", "style:",
]
FEATURE_PATTERNS = [
    "feat:", "feature:", "add:", "new:",
    "fix: copilot", "fix: workflow", "fix: mode", "fix: skill",
]


def _ensure_dir():
    """Ensure the learning directory exists."""
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)


def track_event(event: str, **kwargs) -> dict:
    """Record a usage event to metrics.jsonl.

    This is the SINGLE centralized facade for all GRIP usage metrics.
    All hooks (startup, session-end, session-broadcast) delegate here.

    Writes are append-only to a JSONL file with atomic line writes.
    Concurrent access is safe because POSIX guarantees atomic writes
    under PIPE_BUF (4096 bytes) and each event line is well under that.

    Args:
        event: Event type. Supported values:
            - session_started: Session lifecycle start
            - session_ended: Session lifecycle end
            - skill_invoked: Skill tool usage (PostToolUse)
            - mode_activated: Mode switch
            - command_invoked: Command execution
            - pr_created: PR creation
            - pr_merged: PR merge
            - mcp_tool_used: MCP tool invocation (tool, server)
        **kwargs: Event-specific fields (skill, mode, branch, gen, etc.)

    Returns:
        The recorded event dict.
    """
    _ensure_dir()
    entry = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    with open(METRICS_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def classify_pr(title: str) -> str:
    """Classify a PR title as 'infra' or 'feature'.

    Uses prefix-based heuristics. Falls back to 'infra' for ambiguous titles.
    """
    title_lower = title.lower().strip()
    for pattern in FEATURE_PATTERNS:
        if title_lower.startswith(pattern):
            return "feature"
    for pattern in INFRA_PATTERNS:
        if title_lower.startswith(pattern):
            return "infra"
    # Heuristic: 'fix:' without further infra context is likely user-facing
    if title_lower.startswith("fix:"):
        return "feature"
    return "infra"


def get_pr_ratio(limit: int = 20) -> dict:
    """Calculate infra:feature ratio from recent merged PRs.

    Returns:
        {"infra": N, "feature": M, "ratio": "N:M", "consecutive_infra": K,
         "warning": bool, "prs": [...]}
    """
    import subprocess
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "merged", "--limit", str(limit),
             "--json", "number,title,mergedAt"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}

        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        return {"error": str(e)}

    classified = []
    for pr in prs:
        cat = classify_pr(pr.get("title", ""))
        classified.append({
            "number": pr["number"],
            "title": pr["title"],
            "category": cat,
            "merged": pr.get("mergedAt", ""),
        })

    infra_count = sum(1 for p in classified if p["category"] == "infra")
    feature_count = sum(1 for p in classified if p["category"] == "feature")

    # Count consecutive infra PRs from most recent
    consecutive_infra = 0
    for p in classified:
        if p["category"] == "infra":
            consecutive_infra += 1
        else:
            break

    return {
        "infra": infra_count,
        "feature": feature_count,
        "ratio": f"{infra_count}:{feature_count}",
        "consecutive_infra": consecutive_infra,
        "warning": consecutive_infra >= 3,
        "prs": classified,
    }


def get_usage_summary() -> dict:
    """Summarize all usage from metrics.jsonl."""
    if not METRICS_PATH.exists():
        return {"events": 0, "message": "No metrics recorded yet"}

    skills = Counter()
    modes = Counter()
    commands = Counter()
    events_by_type = Counter()
    total = 0

    with open(METRICS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
                event = e.get("event", "")
                events_by_type[event] += 1
                total += 1

                if event == "skill_invoked":
                    skills[e.get("skill", "unknown")] += 1
                elif event == "mode_activated":
                    modes[e.get("mode", "unknown")] += 1
                elif event == "command_invoked":
                    commands[e.get("command", "unknown")] += 1
            except (json.JSONDecodeError, KeyError):
                pass

    return {
        "events": total,
        "by_type": dict(events_by_type),
        "top_skills": dict(skills.most_common(10)),
        "top_modes": dict(modes.most_common(10)),
        "top_commands": dict(commands.most_common(10)),
    }


def format_summary(summary: dict) -> str:
    """Format summary as human-readable text."""
    if summary.get("events", 0) == 0:
        return "No usage metrics recorded yet. Events will be tracked as you use GRIP."

    lines = [f"GRIP Usage Summary ({summary['events']} events)"]
    lines.append("")

    if summary.get("top_skills"):
        lines.append("Top Skills:")
        for skill, count in summary["top_skills"].items():
            lines.append(f"  {count:3d}x  {skill}")
        lines.append("")

    if summary.get("top_modes"):
        lines.append("Top Modes:")
        for mode, count in summary["top_modes"].items():
            lines.append(f"  {count:3d}x  {mode}")
        lines.append("")

    if summary.get("top_commands"):
        lines.append("Top Commands:")
        for cmd, count in summary["top_commands"].items():
            lines.append(f"  {count:3d}x  {cmd}")

    return "\n".join(lines)


def classify_session(commit_titles: list[str], files_changed: list[str] = None) -> str:
    """Classify a session as 'meta', 'feature', or 'ops'.

    Meta: GRIP infrastructure, RSI loop, hooks, gates, tests
    Feature: user-facing skills, modes, workflows, apps, commands
    Ops: deployment, CI/CD, branch cleanup, releases

    Classification uses majority voting across commits + files.
    """
    meta_signals = 0
    feature_signals = 0
    ops_signals = 0

    meta_keywords = {"hook", "gate", "rsi", "grip", "broly", "mesh", "kono",
                     "supervisor", "budget", "convergence", "metrics", "guard"}
    feature_keywords = {"skill", "mode", "workflow", "command", "copilot",
                        "dashboard", "voice", "app", "standup", "report"}
    ops_keywords = {"deploy", "release", "ci", "cd", "cleanup", "branch",
                    "changelog", "version", "docker", "vercel"}

    for title in commit_titles:
        title_lower = title.lower()
        cat = classify_pr(title)
        if cat == "feature":
            feature_signals += 2
        else:
            # Check specifics
            if any(k in title_lower for k in ops_keywords):
                ops_signals += 1
            elif any(k in title_lower for k in meta_keywords):
                meta_signals += 2
            else:
                meta_signals += 1  # default infra = meta

    if files_changed:
        user_facing = {"skills/", "modes/", "commands/", "apps/",
                       "data/aide/workflows/"}
        infra_dirs = {"hooks/", "lib/", "tests/", "docs/"}
        for fp in files_changed:
            if any(fp.startswith(d) for d in user_facing):
                feature_signals += 1
            elif any(fp.startswith(d) for d in infra_dirs):
                meta_signals += 1

    # Majority wins (meta is default when tied or no signals)
    if feature_signals > meta_signals and feature_signals >= ops_signals:
        return "feature"
    elif ops_signals > meta_signals and ops_signals > feature_signals:
        return "ops"
    return "meta"


def get_meta_work_ratio(days: int = 7) -> dict:
    """Calculate meta-work ratio from recent session events.

    Returns:
        {"meta": N, "feature": M, "ops": O, "total": T,
         "meta_pct": float, "over_cap": bool, "cap": float}
    """
    if not METRICS_PATH.exists():
        return {"meta": 0, "feature": 0, "ops": 0, "total": 0,
                "meta_pct": 0.0, "over_cap": False, "cap": 0.25}

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts = {"meta": 0, "feature": 0, "ops": 0}

    with open(METRICS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("event") != "session_category":
                    continue
                ts = e.get("timestamp", "")
                if ts and datetime.fromisoformat(ts) < cutoff:
                    continue
                cat = e.get("category", "meta")
                if cat in counts:
                    counts[cat] += 1
            except (json.JSONDecodeError, ValueError):
                pass

    total = sum(counts.values())
    meta_pct = counts["meta"] / total if total > 0 else 0.0
    cap = 0.25  # 25% soft cap from issue #142

    return {
        **counts,
        "total": total,
        "meta_pct": round(meta_pct, 3),
        "over_cap": meta_pct > cap,
        "cap": cap,
    }


def format_ratio(ratio: dict) -> str:
    """Format PR ratio as human-readable text."""
    if "error" in ratio:
        return f"Error fetching PR data: {ratio['error']}"

    lines = [f"PR Ratio (last {len(ratio.get('prs', []))} merged PRs)"]
    lines.append(f"  Infra: {ratio['infra']} | Feature: {ratio['feature']} | Ratio: {ratio['ratio']}")
    lines.append(f"  Consecutive infra PRs: {ratio['consecutive_infra']}")

    if ratio.get("warning"):
        lines.append("")
        lines.append("  WARNING: 3+ consecutive infrastructure PRs detected.")
        lines.append("  Consider shipping a user-facing feature next.")
        lines.append("  (Goodhart's Law protection: metrics should serve users, not themselves)")

    lines.append("")
    lines.append("Recent PRs:")
    for pr in ratio.get("prs", [])[:10]:
        marker = "F" if pr["category"] == "feature" else "I"
        lines.append(f"  [{marker}] #{pr['number']}: {pr['title']}")

    return "\n".join(lines)


def main():
    """CLI interface for metrics tracker."""
    if len(sys.argv) < 2:
        print("Usage: python3 lib/metrics_tracker.py <command>")
        print("Commands: summary, top-skills, ratio, record <event> [key=value ...]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "summary":
        print(format_summary(get_usage_summary()))

    elif cmd == "top-skills":
        summary = get_usage_summary()
        if summary.get("top_skills"):
            for skill, count in summary["top_skills"].items():
                print(f"{count:3d}x  {skill}")
        else:
            print("No skill usage recorded yet.")

    elif cmd == "ratio":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(format_ratio(get_pr_ratio(limit)))

    elif cmd == "record":
        if len(sys.argv) < 3:
            print("Usage: record <event> [key=value ...]")
            sys.exit(1)
        event = sys.argv[2]
        kwargs = {}
        for arg in sys.argv[3:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                kwargs[k] = v
        entry = track_event(event, **kwargs)
        print(json.dumps(entry))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
