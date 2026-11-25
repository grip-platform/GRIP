"""Classify heatmap findings into fix_now / fix_later / investigate.

Used by the automatic improvement pipeline to determine which issues
can be safely auto-fixed vs which need human review.

Part of the GRIP automatic improvement MVP (Gen 276).
"""

import re

# Categories safe for automatic fixing (no human review needed)
FIX_NOW_PATTERNS = {
    "count_drift": [
        r"count.*drift",
        r"stats.*mismatch",
        r"skills?\s*:\s*\d+.*actual",
        r"agents?\s*:\s*\d+.*actual",
    ],
    "dead_code_ref": [
        r"dead\s*code",
        r"deprecated.*reference",
        r"removed.*still\s*referenced",
        r"deleted.*still\s*import",
    ],
    "stale_doc_ref": [
        r"stale.*doc",
        r"stale.*reference",
        r"outdated.*reference",
        r"documentation.*non-existent",
        r"mentions.*removed",
    ],
}

# Categories requiring human review
FIX_LATER_PATTERNS = {
    "monolith_split": [
        r"monolith",
        r"too\s*large",
        r"should.*split",
        r"single.*responsibility",
    ],
    "architectural_refactor": [
        r"architect",
        r"restructur",
        r"redesign",
        r"major.*refactor",
    ],
    "api_change": [
        r"api.*change",
        r"breaking.*change",
        r"public.*interface",
    ],
    "security_concern": [
        r"security",
        r"vulnerab",
        r"secret",
        r"credential",
    ],
}


def classify_finding(finding: dict) -> str:
    """Classify a single heatmap finding.

    Args:
        finding: dict with keys: component, touch_count, description
    Returns:
        "fix_now" | "fix_later" | "investigate"
    """
    description = finding.get("description", "").lower()
    component = finding.get("component", "").lower()
    text = f"{component} {description}"

    # Check fix_now patterns first
    for _category, patterns in FIX_NOW_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            return "fix_now"

    # Check fix_later patterns
    for _category, patterns in FIX_LATER_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            return "fix_later"

    # Default: investigate (human decides)
    return "investigate"


def get_fix_category(finding: dict) -> str:
    """Return the specific category for a finding."""
    description = finding.get("description", "").lower()
    component = finding.get("component", "").lower()
    text = f"{component} {description}"

    for category, patterns in FIX_NOW_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            return category

    for category, patterns in FIX_LATER_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            return category

    return "unknown"


def suggest_fix_command(finding: dict) -> str:
    """Suggest a fix command for fix_now items."""
    category = get_fix_category(finding)

    if category == "count_drift":
        return '${CLAUDE_DIR:-$HOME/.claude}/scripts/grip-stats.sh --fix'

    if category == "dead_code_ref":
        file_path = finding.get("file_path", "")
        if file_path:
            return f'# Review and remove dead references in {file_path}'
        return "# Scan for and remove dead code references"

    if category == "stale_doc_ref":
        return "# Update documentation to remove stale references"

    return ""


def classify_heatmap_output(heatmap_lines: list) -> list:
    """Parse and classify heatmap output lines.

    Args:
        heatmap_lines: Raw output lines from improvement-analyzer.py check
    Returns:
        List of dicts with: component, touch_count, description, classification, category
    """
    findings = []
    for line in heatmap_lines:
        line = line.strip()
        if not line:
            continue

        # Parse heatmap format: "component(N): description" or "component(N)"
        match = re.match(r"^(\w[\w\s-]*?)\((\d+)\):?\s*(.*)?$", line)
        if not match:
            continue

        component = match.group(1).strip()
        touch_count = int(match.group(2))
        description = (match.group(3) or "").strip()

        finding = {
            "component": component,
            "touch_count": touch_count,
            "description": description,
        }
        finding["classification"] = classify_finding(finding)
        finding["category"] = get_fix_category(finding)
        finding["fix_command"] = suggest_fix_command(finding)

        findings.append(finding)

    return findings


# CLI interface
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Self-test with sample data
        test_findings = [
            {"component": "docs", "touch_count": 11, "description": "count drift in CLAUDE.md"},
            {"component": "hooks", "touch_count": 7, "description": "monolith context-gate-hook.py"},
            {"component": "lib", "touch_count": 3, "description": "deprecated reference to tool-monitor"},
            {"component": "tests", "touch_count": 2, "description": "unknown pattern"},
        ]
        for f in test_findings:
            cls = classify_finding(f)
            cat = get_fix_category(f)
            print(f"  {f['component']}({f['touch_count']}): {cls} ({cat})")
    else:
        # Read lines from stdin
        lines = sys.stdin.readlines()
        results = classify_heatmap_output(lines)
        print(json.dumps(results, indent=2))
