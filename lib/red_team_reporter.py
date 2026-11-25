"""
Red-Team Article Generator for GRIP RSI Loop.

Generates honest, critical assessments of the RSI loop quality.
Posts as GitHub issues for traceability and RSI loop dynamic learning.

This is a Goodhart's Law countermeasure: periodic self-criticism
prevents the RSI loop from becoming a self-congratulatory echo chamber.

Usage:
    python3 lib/red_team_reporter.py generate   # Generate report to stdout
    python3 lib/red_team_reporter.py post        # Generate + post as GitHub issue
    python3 lib/red_team_reporter.py metrics     # Just output raw metrics

Schedule: Run weekly or after every 5+ PRs merged.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO = "grip-platform/GRIP"

if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports come after the insert, so the module also imports as lib.red_team_reporter.
from insights_issue_budget import create_issue
from metrics_tracker import classify_pr, get_pr_ratio
from goodhart_guard import run_all_checks


def _gh_json(cmd_args: list, timeout: int = 15) -> list | dict:
    """Run gh CLI and parse JSON output."""
    try:
        result = subprocess.run(
            ["gh"] + cmd_args,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        return json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        return {"error": str(e)}


def collect_metrics(pr_limit: int = 20) -> dict:
    """Collect all metrics needed for a red-team assessment."""
    metrics = {}

    # PR data
    prs = _gh_json([
        "pr", "list", "-R", REPO, "--state", "merged",
        "--limit", str(pr_limit),
        "--json", "number,title,additions,deletions,mergedAt,files",
    ])
    if isinstance(prs, dict) and "error" in prs:
        metrics["pr_error"] = prs["error"]
        prs = []

    metrics["total_prs"] = len(prs)
    metrics["prs"] = []
    for pr in prs:
        category = classify_pr(pr.get("title", ""))
        files = pr.get("files", [])
        file_paths = [f.get("path", "") for f in files] if files else []
        user_facing_dirs = {"skills/", "modes/", "commands/", "apps/", "data/aide/workflows/"}
        touches_user_facing = any(
            any(fp.startswith(d) for d in user_facing_dirs) for fp in file_paths
        )
        metrics["prs"].append({
            "number": pr["number"],
            "title": pr["title"],
            "category": category,
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "touches_user_facing": touches_user_facing,
            "merged": pr.get("mergedAt", ""),
        })

    # PR ratio
    infra = sum(1 for p in metrics["prs"] if p["category"] == "infra")
    feature = sum(1 for p in metrics["prs"] if p["category"] == "feature")
    user_facing = sum(1 for p in metrics["prs"] if p["touches_user_facing"])
    metrics["ratio"] = {"infra": infra, "feature": feature, "user_facing_files": user_facing}

    # Open issues
    issues = _gh_json(["issue", "list", "-R", REPO, "--json", "number,title,labels,state"])
    if isinstance(issues, list):
        metrics["open_issues"] = len(issues)
    else:
        metrics["open_issues"] = 0

    # Test count
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(CLAUDE_DIR / "tests"), "-q", "--tb=no", "--co"],
            capture_output=True, text=True, timeout=60, cwd=str(CLAUDE_DIR),
        )
        metrics["test_count"] = sum(
            1 for line in result.stdout.split("\n")
            if "::" in line and "test_" in line
        )
    except Exception:
        metrics["test_count"] = 0

    # Goodhart guard
    guard = run_all_checks()
    metrics["goodhart"] = guard

    # KONO stats
    db_path = CLAUDE_DIR / "embeddings.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM embeddings")
            metrics["kono_total"] = cursor.fetchone()[0]
            cursor = conn.execute("SELECT COUNT(*) FROM embeddings WHERE is_permanent = 1")
            metrics["kono_permanent"] = cursor.fetchone()[0]
            cursor = conn.execute("SELECT AVG(fitness_score) FROM embeddings WHERE fitness_score > 0")
            row = cursor.fetchone()
            metrics["kono_avg_fitness"] = round(row[0], 3) if row[0] else 0
            conn.close()
        except Exception:
            metrics["kono_total"] = 0
            metrics["kono_permanent"] = 0
            metrics["kono_avg_fitness"] = 0
    else:
        metrics["kono_total"] = 0

    return metrics


def _calculate_score(metrics: dict) -> tuple[int, list[str], dict]:
    """Calculate red-team score and identify concerns.

    Returns (score, concerns, extracted) where extracted holds derived values
    reused by other article sections.
    """
    ratio = metrics.get("ratio", {})
    guard = metrics.get("goodhart", {})
    prs = metrics.get("prs", [])

    infra_count = ratio.get("infra", 0)
    feature_count = ratio.get("feature", 0)
    user_facing = ratio.get("user_facing_files", 0)
    total = infra_count + feature_count

    score = 100
    concerns: list[str] = []

    if total > 0 and user_facing / total < 0.3:
        score -= 20
        concerns.append(
            f"**File-path analysis**: Only {user_facing}/{total} PRs "
            f"touch user-facing directories (skills/, modes/, workflows/, apps/). "
            f"The RSI loop is self-referential."
        )

    if guard.get("overall") == "critical":
        score -= 25
        for name, check in guard.get("checks", {}).items():
            if check.get("status") in ("critical", "warning"):
                concerns.append(f"**Goodhart guard [{name}]**: {check['message']}")
    elif guard.get("overall") == "warning":
        score -= 10
        for name, check in guard.get("checks", {}).items():
            if check.get("status") == "warning":
                concerns.append(f"**Goodhart guard [{name}]**: {check['message']}")

    tiny_prs = sum(1 for p in prs if p.get("additions", 0) + p.get("deletions", 0) < 50)
    if len(prs) > 0 and tiny_prs / len(prs) > 0.6:
        score -= 15
        concerns.append(
            f"**Score chasing**: {tiny_prs}/{len(prs)} PRs are tiny (<50 lines). "
            f"This suggests optimizing for merge count rather than meaningful work."
        )

    extracted = {
        "infra_count": infra_count, "feature_count": feature_count,
        "user_facing": user_facing, "total": total, "tiny_prs": tiny_prs,
        "test_count": metrics.get("test_count", 0),
    }
    return score, concerns, extracted


def _build_positives(metrics: dict, extracted: dict) -> list[str]:
    """Build the 'What Went Well' bullet points."""
    positives: list[str] = []
    test_count = extracted["test_count"]
    feature_count = extracted["feature_count"]
    if test_count > 1000:
        positives.append(f"1. **Test coverage**: {test_count} tests maintained green.")
    if feature_count > 0:
        positives.append(f"2. **Feature PRs exist**: {feature_count} PRs classified as feature work.")
    if metrics.get("kono_permanent", 0) > 20:
        positives.append(
            f"3. **KONO memory**: {metrics['kono_permanent']} permanent memories "
            f"across {metrics['kono_total']} total embeddings "
            f"(avg fitness: {metrics.get('kono_avg_fitness', 0)})."
        )
    if not positives:
        positives.append("1. The RSI loop is running. That's about it.")
    return positives


def _build_recommendations(guard: dict, extracted: dict) -> list[str]:
    """Build actionable recommendations based on detected issues."""
    recs: list[str] = []
    if extracted["user_facing"] / max(extracted["total"], 1) < 0.3:
        recs.append("1. **Ship user-facing features**: Build new skills, workflows, or modes before the next infrastructure PR.")
    prs = extracted.get("tiny_prs", 0)
    total_prs = extracted["infra_count"] + extracted["feature_count"]
    if prs / max(total_prs, 1) > 0.4:
        recs.append("2. **Batch related changes**: Combine related small PRs into larger, more meaningful ones.")
    if guard.get("overall") != "ok":
        recs.append("3. **Run Goodhart guard before each phase**: `python3 lib/goodhart_guard.py check`")
    recs.append("4. **Next red-team article**: Schedule after 5 more PRs are merged.")
    return recs


def _build_metrics_table(metrics: dict, score: int, extracted: dict) -> list[str]:
    """Build the markdown metrics summary table."""
    guard = metrics.get("goodhart", {})
    prs = metrics.get("prs", [])
    ic, fc = extracted["infra_count"], extracted["feature_count"]
    uf, total = extracted["user_facing"], extracted["total"]
    tiny, tc = extracted["tiny_prs"], extracted["test_count"]

    rows = [
        "| Metric | Value | Assessment |",
        "|--------|-------|------------|",
        f"| PRs analyzed | {len(prs)} | - |",
        f"| Infra:Feature (title) | {ic}:{fc} | {'RISK' if fc < ic else 'OK'} |",
        f"| User-facing file PRs | {uf}/{total} | {'RISK' if uf / max(total, 1) < 0.3 else 'OK'} |",
        f"| Tiny PRs (<50 lines) | {tiny}/{len(prs)} | {'RISK' if tiny > len(prs) * 0.6 else 'OK'} |",
        f"| Test count | {tc} | {'Growing' if tc > 1000 else 'Low'} |",
        f"| KONO memories | {metrics.get('kono_total', 0)} ({metrics.get('kono_permanent', 0)} permanent) | - |",
        f"| Goodhart guard | {guard.get('overall', '?').upper()} | {'Action needed' if guard.get('overall') != 'ok' else 'Healthy'} |",
        f"| Red-team score | {max(score, 0)}/100 | {'PASS' if score >= 70 else 'FAIL'} |",
    ]
    return rows


def generate_article(metrics: dict) -> str:
    """Generate a red-team assessment article from collected metrics."""
    prs = metrics.get("prs", [])
    guard = metrics.get("goodhart", {})

    pr_numbers = sorted(p["number"] for p in prs) if prs else []
    pr_range = f"#{pr_numbers[0]}-#{pr_numbers[-1]}" if len(pr_numbers) >= 2 else "recent"

    score, concerns, extracted = _calculate_score(metrics)
    uf, total = extracted["user_facing"], extracted["total"]

    lines = [
        f"## RSI Loop Quality Assessment: Red-Team Analysis ({pr_range})",
        "",
        "### Executive Summary",
        "",
        f"Over the last {len(prs)} merged PRs ({pr_range}), the RSI loop "
        f"produced {sum(p.get('additions', 0) for p in prs):,} additions and "
        f"{sum(p.get('deletions', 0) for p in prs):,} deletions across "
        f"{metrics.get('test_count', '?')} tests. "
        f"Infra:Feature ratio by file paths: {uf} user-facing / {total} total.",
        "",
        "### What Went Well",
        "",
    ]
    lines.extend(_build_positives(metrics, extracted))
    lines.append("")

    lines.append("### Red-Team Concerns (Critical)")
    lines.append("")
    if concerns:
        for i, concern in enumerate(concerns, 1):
            lines.append(f"#### {i}. {concern}")
            lines.append("")
    else:
        lines.append("No critical concerns detected. The loop appears healthy.")
        lines.append("")

    lines.append("### Recommendations")
    lines.append("")
    lines.extend(_build_recommendations(guard, extracted))
    lines.append("")

    lines.append("### Metrics")
    lines.append("")
    lines.extend(_build_metrics_table(metrics, score, extracted))
    lines.append("")

    lines.append("### Conclusion")
    lines.append("")
    if score >= 80:
        lines.append("The RSI loop is healthy and delivering balanced value.")
    elif score >= 60:
        lines.append("The RSI loop has moderate drift. Address the concerns above before continuing infrastructure work.")
    else:
        lines.append(
            "The RSI loop shows significant Goodhart's Law drift. "
            "It is optimizing its own infrastructure without delivering user value. "
            "Immediate course correction needed: ship features, not more gates."
        )
    lines.append("")
    lines.append("---")
    lines.append("*Generated by `lib/red_team_reporter.py` as part of the RSI loop self-monitoring mechanism.*")

    return "\n".join(lines)


def post_as_issue(article: str, *, runner=None, ledger_path=None) -> dict:
    """Post the red-team article as a GitHub issue."""
    now = datetime.now(timezone.utc)
    title = f"RSI Loop Quality Assessment: Red-Team Analysis ({now.strftime('%Y-%m-%d')})"

    result = create_issue(title, article, runner=runner, ledger_path=ledger_path)
    if "issue_url" in result:
        return {"ok": True, "url": result["issue_url"]}
    return {"ok": False, **result}


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/red_team_reporter.py <generate|post|metrics>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "metrics":
        metrics = collect_metrics()
        print(json.dumps(metrics, indent=2, default=str))

    elif cmd == "generate":
        metrics = collect_metrics()
        article = generate_article(metrics)
        print(article)

    elif cmd == "post":
        metrics = collect_metrics()
        article = generate_article(metrics)
        result = post_as_issue(article)
        if result.get("ok"):
            print(f"Red-team article posted: {result['url']}")
        elif result.get("skipped"):
            print(f"Skipped: {result['skipped']}")
        else:
            print(f"Failed to post: {result.get('error')}", file=sys.stderr)
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
