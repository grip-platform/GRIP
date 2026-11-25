"""
Goodhart's Law Protection for GRIP RSI Loop.

"When a measure becomes a target, it ceases to be a good measure."

This guard monitors the RSI loop's behavior patterns and flags drift:
- Infra-to-feature ratio imbalance
- Score-chasing behavior (many small PRs for easy wins)
- Self-referential improvement loops (GRIP improving GRIP only)

Usage:
    python3 lib/goodhart_guard.py check     # Run all checks
    python3 lib/goodhart_guard.py ratio     # Just PR ratio
    python3 lib/goodhart_guard.py report    # Full report for red-team article

Designed to be called by the RSI loop before each phase to ensure
the loop is delivering user value, not just optimizing metrics.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_tracker import classify_pr, get_pr_ratio, get_meta_work_ratio


def check_pr_ratio(threshold: int = 3) -> dict:
    """Check if consecutive infra PRs exceed threshold.

    Args:
        threshold: Max consecutive infra PRs before warning.

    Returns:
        {"status": "ok"|"warning"|"critical", "message": str, "data": dict}
    """
    ratio = get_pr_ratio(limit=20)
    if "error" in ratio:
        return {"status": "error", "message": ratio["error"], "data": ratio}

    consecutive = ratio["consecutive_infra"]
    if consecutive >= threshold * 2:
        status = "critical"
        message = (
            f"CRITICAL: {consecutive} consecutive infrastructure PRs. "
            f"The RSI loop has not shipped a user-facing feature in {consecutive} PRs. "
            f"Stop and build something that helps the USER."
        )
    elif consecutive >= threshold:
        status = "warning"
        message = (
            f"WARNING: {consecutive} consecutive infrastructure PRs (threshold: {threshold}). "
            f"Consider shipping a user-facing feature next."
        )
    else:
        status = "ok"
        message = f"PR ratio healthy: {ratio['ratio']} (infra:feature), {consecutive} consecutive infra."

    return {"status": status, "message": message, "data": ratio}


def check_pr_size_pattern() -> dict:
    """Detect score-chasing: many tiny PRs vs substantial changes.

    Small PRs (< 50 lines changed) in bulk suggest optimizing for
    merge count rather than meaningful work.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "merged", "--limit", "10",
             "--json", "number,title,additions,deletions"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip()}

        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        return {"status": "error", "message": str(e)}

    tiny_count = 0
    for pr in prs:
        total_lines = pr.get("additions", 0) + pr.get("deletions", 0)
        if total_lines < 50:
            tiny_count += 1

    if tiny_count >= 7:
        return {
            "status": "warning",
            "message": (
                f"{tiny_count}/10 recent PRs are tiny (<50 lines). "
                f"Consider batching related changes into larger, more meaningful PRs."
            ),
            "tiny_count": tiny_count,
        }

    return {
        "status": "ok",
        "message": f"{tiny_count}/10 recent PRs are tiny. Within healthy range.",
        "tiny_count": tiny_count,
    }


def check_self_referential() -> dict:
    """Detect self-referential improvement loops.

    If all recent PRs modify only GRIP infrastructure files (hooks, gates,
    lib, tests) without touching user-facing code (skills, modes, workflows,
    apps), the loop is self-referential.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "merged", "--limit", "10",
             "--json", "number,title,files"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip()}

        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        return {"status": "error", "message": str(e)}

    user_facing_dirs = {"skills/", "modes/", "commands/", "apps/", "data/aide/workflows/"}
    infra_only_count = 0

    for pr in prs:
        files = pr.get("files", [])
        if not files:
            continue
        file_paths = [f.get("path", "") for f in files]
        touches_user_facing = any(
            any(fp.startswith(d) for d in user_facing_dirs)
            for fp in file_paths
        )
        if not touches_user_facing:
            infra_only_count += 1

    if infra_only_count >= 8:
        return {
            "status": "critical",
            "message": (
                f"{infra_only_count}/10 recent PRs touch ONLY infrastructure files. "
                f"Zero user-facing changes (skills, modes, workflows, apps). "
                f"The RSI loop is self-referential — improving itself without user benefit."
            ),
            "infra_only": infra_only_count,
        }
    elif infra_only_count >= 5:
        return {
            "status": "warning",
            "message": f"{infra_only_count}/10 recent PRs are infra-only. Mix in user-facing work.",
            "infra_only": infra_only_count,
        }

    return {
        "status": "ok",
        "message": f"{infra_only_count}/10 recent PRs are infra-only. Healthy mix.",
        "infra_only": infra_only_count,
    }


def check_meta_work_cap(cap: float = 0.25, days: int = 7) -> dict:
    """Check if meta-work exceeds the soft cap.

    Meta-work includes: GRIP infrastructure, RSI loop improvements, hooks,
    gates, test-only PRs, documentation-only sessions.

    Args:
        cap: Maximum acceptable meta-work ratio (default 25%).
        days: Lookback window in days.

    Returns:
        {"status": "ok"|"warning"|"critical", "message": str, "data": dict}
    """
    ratio = get_meta_work_ratio(days=days)

    if ratio["total"] < 5:
        return {
            "status": "ok",
            "message": f"Insufficient session data ({ratio['total']} sessions in {days}d). Skipping meta-work check.",
            "data": ratio,
        }

    if ratio["meta_pct"] > cap * 1.5:
        return {
            "status": "critical",
            "message": (
                f"CRITICAL: {ratio['meta_pct']:.0%} meta-work in last {days}d "
                f"(cap: {cap:.0%}). {ratio['meta']}/{ratio['total']} sessions are meta-improvements. "
                f"Feature sessions: {ratio['feature']}. "
                f"STOP meta-work and ship user-facing features."
            ),
            "data": ratio,
        }
    elif ratio["over_cap"]:
        return {
            "status": "warning",
            "message": (
                f"WARNING: {ratio['meta_pct']:.0%} meta-work in last {days}d "
                f"(cap: {cap:.0%}). Prioritize user-facing features."
            ),
            "data": ratio,
        }

    return {
        "status": "ok",
        "message": (
            f"Meta-work ratio healthy: {ratio['meta_pct']:.0%} "
            f"({ratio['meta']}/{ratio['total']} sessions in {days}d, cap: {cap:.0%})."
        ),
        "data": ratio,
    }


def check_complexity_ceiling() -> dict:
    """Check if system complexity is growing without pruning.

    Counts skills, modes, agents, commands. Warns if any exceed ceilings
    and unused skills exist (per pruning-unused-skills skill).

    Ceilings (from issue #142 success criteria — net-zero or negative growth):
        skills: 135 (current ~131 + small buffer)
        modes: 28 (current 26 + small buffer)
        agents: 18 (current 16 + small buffer)
        commands: 55 (current 51 + small buffer)
    """
    ceilings = {"skills": 135, "modes": 28, "agents": 18, "commands": 55}
    counts = {}

    # Count actual items
    skills_dir = CLAUDE_DIR / "skills"
    modes_dir = CLAUDE_DIR / "modes"
    agents_dir = CLAUDE_DIR / "agents"
    commands_dir = CLAUDE_DIR / "commands"

    if skills_dir.exists():
        counts["skills"] = len([d for d in skills_dir.iterdir() if d.is_dir()])
    else:
        counts["skills"] = 0

    if modes_dir.exists():
        counts["modes"] = len([f for f in modes_dir.glob("*.yaml")])
    else:
        counts["modes"] = 0

    if agents_dir.exists():
        counts["agents"] = len([f for f in agents_dir.glob("*.md")])
    else:
        counts["agents"] = 0

    if commands_dir.exists():
        counts["commands"] = len([f for f in commands_dir.glob("*.md")])
    else:
        counts["commands"] = 0

    breaches = []
    for component, ceiling in ceilings.items():
        actual = counts.get(component, 0)
        if actual > ceiling:
            breaches.append(f"{component}: {actual}/{ceiling}")

    if breaches:
        return {
            "status": "warning",
            "message": (
                f"Complexity ceiling breached: {', '.join(breaches)}. "
                f"Run pruning-unused-skills before adding new ones. "
                f"Net-zero growth target: prune before creating."
            ),
            "counts": counts,
            "ceilings": ceilings,
        }

    return {
        "status": "ok",
        "message": (
            f"Complexity within ceilings. Skills: {counts['skills']}/{ceilings['skills']}, "
            f"Modes: {counts['modes']}/{ceilings['modes']}, "
            f"Agents: {counts['agents']}/{ceilings['agents']}, "
            f"Commands: {counts['commands']}/{ceilings['commands']}."
        ),
        "counts": counts,
        "ceilings": ceilings,
    }


def run_all_checks() -> dict:
    """Run all Goodhart protection checks.

    Returns:
        {"overall": "ok"|"warning"|"critical", "checks": [...]}
    """
    checks = [
        ("pr_ratio", check_pr_ratio()),
        ("pr_size", check_pr_size_pattern()),
        ("self_referential", check_self_referential()),
        ("meta_work_cap", check_meta_work_cap()),
        ("complexity_ceiling", check_complexity_ceiling()),
    ]

    overall = "ok"
    for name, result in checks:
        if result["status"] == "critical":
            overall = "critical"
        elif result["status"] == "warning" and overall != "critical":
            overall = "warning"

    return {
        "overall": overall,
        "checks": {name: result for name, result in checks},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def pre_flight_check() -> tuple[bool, str]:
    """Gate check for automated PR flows.

    Returns:
        (allowed, reason) -- critical = blocked, warning = logged, ok = silent.
    """
    result = run_all_checks()
    overall = result["overall"]

    if overall == "critical":
        summary = "; ".join(
            c["message"] for c in result["checks"].values()
            if c["status"] == "critical"
        )
        return False, f"Goodhart gate BLOCKED: {summary}"

    if overall == "warning":
        summary = "; ".join(
            c["message"] for c in result["checks"].values()
            if c["status"] == "warning"
        )
        logger.warning("Goodhart warning: %s", summary)

    return True, ""


def format_report(results: dict) -> str:
    """Format check results as readable report."""
    status_icons = {"ok": "PASS", "warning": "WARN", "critical": "FAIL", "error": "ERR"}
    lines = [f"Goodhart Guard Report ({results.get('timestamp', 'now')[:19]}Z)"]
    lines.append(f"Overall: {status_icons.get(results['overall'], '?')} ({results['overall']})")
    lines.append("")

    for name, check in results.get("checks", {}).items():
        icon = status_icons.get(check["status"], "?")
        lines.append(f"  [{icon}] {name}: {check['message']}")

    if results["overall"] != "ok":
        lines.append("")
        lines.append("Action needed: Ship user-facing features before continuing infrastructure work.")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/goodhart_guard.py <check|ratio|report>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        results = run_all_checks()
        print(format_report(results))
        # Exit non-zero if critical
        if results["overall"] == "critical":
            sys.exit(2)
        elif results["overall"] == "warning":
            sys.exit(1)

    elif cmd == "ratio":
        result = check_pr_ratio()
        print(result["message"])

    elif cmd == "report":
        results = run_all_checks()
        print(json.dumps(results, indent=2, default=str))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
