"""
Feature-First Enforcement for GRIP RSI Loop.

Prevents the RSI loop from drifting into infrastructure-only work by
tracking the ratio of user-facing PRs vs infrastructure PRs and
enforcing a max 2:1 infra-to-feature ratio.

Usage:
    from feature_first_enforcer import should_allow_infra_pr, get_ratio
    if not should_allow_infra_pr():
        print("Ship a user-facing feature first.")

    # CLI
    python3 lib/feature_first_enforcer.py check
    python3 lib/feature_first_enforcer.py classify "feat: add dashboard"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Max infra PRs per feature PR. 2:1 means 2 infra allowed per 1 feature.
MAX_INFRA_TO_FEATURE_RATIO = 2.0

# File paths that signal user-facing work
USER_FACING_PATHS = (
    "apps/",
    "skills/",
    "modes/",
    "commands/",
    "data/aide/workflows/",
    "scripts/daily-",
    "scripts/standup",
)

# File paths that signal infrastructure work
INFRA_PATHS = (
    "hooks/",
    "lib/",
    "tests/",
    ".github/",
    "docs/",
    "gates/",
)

# Title prefixes for classification
FEATURE_PREFIXES = ("feat:", "feature:", "add:", "new:")
INFRA_PREFIXES = ("chore:", "chore(", "refactor:", "ci:", "test:", "docs:", "build:", "style:")

# Title keywords that override prefix-based classification toward user-facing
FEATURE_KEYWORDS = (
    "skill", "mode", "workflow", "command", "copilot", "dashboard",
    "mcp", "ui", "app", "report", "standup", "notification",
)


def classify_title(title: str) -> str:
    """Classify a PR/commit title as 'feature' or 'infra'.

    Returns:
        'feature' or 'infra'
    """
    lower = title.lower().strip()

    for prefix in FEATURE_PREFIXES:
        if lower.startswith(prefix):
            return "feature"

    for prefix in INFRA_PREFIXES:
        if lower.startswith(prefix):
            return "infra"

    # Generic fix: check for infra keywords
    if lower.startswith("fix:"):
        remainder = lower[4:].strip()
        infra_fix_keywords = ("hook", "gate", "lint", "test", "ci", "build")
        if any(remainder.startswith(k) for k in infra_fix_keywords):
            return "infra"
        return "feature"

    return "infra"


def classify_files(file_paths: list) -> str:
    """Classify based on files changed. Majority wins.

    Returns:
        'feature' or 'infra'
    """
    if not file_paths:
        return "infra"

    feature_count = 0
    infra_count = 0

    for fp in file_paths:
        if any(fp.startswith(p) for p in USER_FACING_PATHS):
            feature_count += 1
        elif any(fp.startswith(p) for p in INFRA_PATHS):
            infra_count += 1

    if feature_count > 0:
        return "feature"
    if infra_count > 0:
        return "infra"
    return "infra"


def classify_pr(title: str, file_paths: list = None, labels: list = None) -> str:
    """Classify a PR as 'feature' or 'infra' using all available signals.

    Priority:
    1. Labels (if present, 'feature' or 'enhancement' = feature)
    2. Title prefix
    3. File paths (if title is ambiguous)

    Returns:
        'feature' or 'infra'
    """
    # Labels override everything
    if labels:
        lower_labels = [l.lower() for l in labels]
        if any(l in ("feature", "enhancement", "user-facing") for l in lower_labels):
            return "feature"
        if any(l in ("infra", "infrastructure", "chore", "internal") for l in lower_labels):
            return "infra"

    # Title-based classification
    title_class = classify_title(title)

    # If title says feature, trust it
    if title_class == "feature":
        return "feature"

    # If title says infra but files touch user-facing dirs, override
    if file_paths:
        file_class = classify_files(file_paths)
        if file_class == "feature":
            return "feature"

    # Check for feature keywords in title (catches "chore: add skill X")
    lower_title = title.lower()
    if any(kw in lower_title for kw in FEATURE_KEYWORDS):
        return "feature"

    return title_class


def fetch_recent_prs(limit: int = 20) -> list:
    """Fetch recent merged PRs from GitHub.

    Returns:
        List of dicts with keys: number, title, files, labels
        Empty list on error.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", "grip-platform/GRIP",
             "--state", "merged", "--limit", str(limit),
             "--json", "number,title,files,labels,mergedAt"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except Exception:
        return []


def get_ratio(limit: int = 20) -> dict:
    """Calculate current infra-to-feature ratio from recent merged PRs.

    Returns:
        {
            "infra": int,
            "feature": int,
            "ratio": float,  # infra / feature, inf if 0 features
            "ratio_str": "N:M",
            "consecutive_infra": int,
            "prs": [{"number": N, "title": str, "category": str}, ...],
            "allowed": bool,  # whether another infra PR is allowed
        }
    """
    prs = fetch_recent_prs(limit)
    return compute_ratio(prs)


def compute_ratio(prs: list) -> dict:
    """Compute ratio from a list of PR dicts (testable without GitHub).

    Args:
        prs: List of dicts with at least 'number', 'title'. Optional: 'files', 'labels'.
    """
    classified = []
    for pr in prs:
        file_paths = [f.get("path", "") for f in pr.get("files", [])] if pr.get("files") else []
        label_names = [l.get("name", "") for l in pr.get("labels", [])] if pr.get("labels") else []
        category = classify_pr(pr.get("title", ""), file_paths, label_names)
        classified.append({
            "number": pr.get("number", 0),
            "title": pr.get("title", ""),
            "category": category,
        })

    infra_count = sum(1 for p in classified if p["category"] == "infra")
    feature_count = sum(1 for p in classified if p["category"] == "feature")

    # Consecutive infra from most recent
    consecutive_infra = 0
    for p in classified:
        if p["category"] == "infra":
            consecutive_infra += 1
        else:
            break

    # Ratio calculation
    if feature_count > 0:
        ratio = infra_count / feature_count
    else:
        ratio = float("inf") if infra_count > 0 else 0.0

    allowed = _is_infra_allowed(infra_count, feature_count)

    return {
        "infra": infra_count,
        "feature": feature_count,
        "ratio": ratio,
        "ratio_str": f"{infra_count}:{feature_count}",
        "consecutive_infra": consecutive_infra,
        "prs": classified,
        "allowed": allowed,
    }


def _is_infra_allowed(infra_count: int, feature_count: int) -> bool:
    """Check if another infra PR is within the allowed ratio.

    With MAX_INFRA_TO_FEATURE_RATIO of 2.0:
    - 0 features, 0 infra -> allowed (cold start)
    - 0 features, 1+ infra -> NOT allowed (must ship a feature)
    - 1 feature, 2 infra -> NOT allowed (at limit)
    - 1 feature, 1 infra -> allowed
    - 3 features, 5 infra -> allowed (5 < 3*2 = 6)
    """
    if feature_count == 0:
        return infra_count == 0
    return infra_count < feature_count * MAX_INFRA_TO_FEATURE_RATIO


def should_allow_infra_pr(limit: int = 20) -> bool:
    """Check if another infrastructure PR is allowed given current ratio.

    This is the main gate function. Call before starting infra work.

    Returns:
        True if infra work is allowed, False if a feature should be shipped first.
    """
    ratio = get_ratio(limit)
    return ratio["allowed"]


def format_check(ratio: dict) -> str:
    """Format ratio data as a human-readable check report."""
    lines = []

    status = "PASS" if ratio["allowed"] else "BLOCKED"
    lines.append(f"Feature-First Enforcer: {status}")
    lines.append(f"  Ratio: {ratio['ratio_str']} (infra:feature), max {MAX_INFRA_TO_FEATURE_RATIO}:1")
    lines.append(f"  Consecutive infra: {ratio['consecutive_infra']}")

    if not ratio["allowed"]:
        lines.append("")
        lines.append("  BLOCKED: Too many infrastructure PRs relative to features.")
        lines.append(f"  Ship a user-facing feature before the next infra PR.")
        lines.append(f"  Current: {ratio['infra']} infra / {ratio['feature']} feature")
        lines.append(f"  Max allowed: {int(ratio['feature'] * MAX_INFRA_TO_FEATURE_RATIO)} infra for {ratio['feature']} features")

    if ratio["consecutive_infra"] >= 3:
        lines.append("")
        lines.append(f"  WARNING: {ratio['consecutive_infra']} consecutive infra PRs.")
        lines.append("  Even within ratio limits, consider alternating with features.")

    lines.append("")
    lines.append("Recent PRs:")
    for pr in ratio.get("prs", [])[:10]:
        marker = "F" if pr["category"] == "feature" else "I"
        lines.append(f"  [{marker}] #{pr['number']}: {pr['title']}")

    return "\n".join(lines)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 lib/feature_first_enforcer.py <check|classify|allow>")
        print("")
        print("Commands:")
        print("  check                  Show current ratio and enforcement status")
        print("  classify <title>       Classify a PR title as feature or infra")
        print("  allow                  Exit 0 if infra allowed, exit 1 if blocked")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        ratio = get_ratio(limit)
        print(format_check(ratio))
        sys.exit(0 if ratio["allowed"] else 1)

    elif cmd == "classify":
        if len(sys.argv) < 3:
            print("Usage: classify <title>")
            sys.exit(1)
        title = " ".join(sys.argv[2:])
        category = classify_title(title)
        print(f"{category}: {title}")

    elif cmd == "allow":
        allowed = should_allow_infra_pr()
        if allowed:
            print("ALLOWED: Infra PR within ratio limits.")
            sys.exit(0)
        else:
            print("BLOCKED: Ship a feature first.")
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
