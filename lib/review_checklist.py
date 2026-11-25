"""Code review checklist generator.

Generates contextual checklists based on file changes. Different change
types trigger different review items across security, performance, testing,
documentation, API, infrastructure, frontend, and database categories.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    API = "api"
    INFRASTRUCTURE = "infrastructure"
    FRONTEND = "frontend"
    DATABASE = "database"


@dataclass
class ChecklistItem:
    id: str
    label: str
    description: str
    category: Category
    severity: Severity = Severity.MEDIUM
    auto_checkable: bool = False


@dataclass
class FileChange:
    path: str
    additions: int = 0
    deletions: int = 0
    change_type: str = "modified"  # "added", "modified", "deleted"


@dataclass
class ReviewChecklist:
    title: str
    items: list[ChecklistItem] = field(default_factory=list)
    file_changes: list[FileChange] = field(default_factory=list)
    risk_score: int = 0
    summary: str = ""


# --- Category detection patterns ---

SECURITY_PATTERNS = [
    r"auth",
    r"login",
    r"password",
    r"secret",
    r"token",
    r"\.env",
    r"crypto",
    r"encrypt",
    r"decrypt",
    r"api[_-]?key",
    r"credential",
    r"permission",
    r"rbac",
    r"oauth",
    r"jwt",
    r"session",
    r"cookie",
    r"cors",
    r"csrf",
    r"xss",
    r"sanitiz",
]

PERFORMANCE_PATTERNS = [
    r"query",
    r"cache",
    r"index",
    r"optimi[sz]",
    r"pool",
    r"batch",
    r"bulk",
    r"paginate",
    r"lazy",
    r"prefetch",
    r"n\+1",
    r"loop",
    r"perf",
    r"benchmark",
    r"throttl",
    r"debounce",
]

TESTING_PATTERNS = [
    r"test[_/]",
    r"_test\.",
    r"\.test\.",
    r"spec[_/]",
    r"_spec\.",
    r"\.spec\.",
    r"mock",
    r"fixture",
    r"conftest",
    r"pytest",
    r"jest",
    r"coverage",
    r"__tests__",
]

DOCUMENTATION_PATTERNS = [
    r"readme",
    r"changelog",
    r"contributing",
    r"license",
    r"docs?/",
    r"\.md$",
    r"\.rst$",
    r"\.txt$",
    r"api[_-]?doc",
    r"swagger",
    r"openapi",
]

API_PATTERNS = [
    r"route",
    r"endpoint",
    r"controller",
    r"handler",
    r"middleware",
    r"schema",
    r"serializer",
    r"view",
    r"api/",
    r"rest",
    r"graphql",
    r"grpc",
    r"proto",
    r"swagger",
    r"openapi",
]

INFRASTRUCTURE_PATTERNS = [
    r"docker",
    r"compose",
    r"ci",
    r"cd",
    r"pipeline",
    r"workflow",
    r"deploy",
    r"terraform",
    r"ansible",
    r"k8s",
    r"kubernetes",
    r"helm",
    r"nginx",
    r"\.yml$",
    r"\.yaml$",
    r"makefile",
    r"\.toml$",
    r"\.cfg$",
    r"\.ini$",
    r"\.github/",
    r"vercel",
    r"railway",
]

FRONTEND_PATTERNS = [
    r"component",
    r"\.tsx$",
    r"\.jsx$",
    r"\.vue$",
    r"\.svelte$",
    r"\.css$",
    r"\.scss$",
    r"\.less$",
    r"\.html$",
    r"style",
    r"a11y",
    r"accessibility",
    r"aria",
    r"responsive",
    r"layout",
]

DATABASE_PATTERNS = [
    r"migrat",
    r"schema",
    r"model",
    r"\.sql$",
    r"alembic",
    r"sequelize",
    r"prisma",
    r"knex",
    r"typeorm",
    r"orm",
    r"database",
    r"table",
    r"column",
    r"foreign[_-]?key",
    r"index",
]

CATEGORY_PATTERNS: dict[Category, list[str]] = {
    Category.SECURITY: SECURITY_PATTERNS,
    Category.PERFORMANCE: PERFORMANCE_PATTERNS,
    Category.TESTING: TESTING_PATTERNS,
    Category.DOCUMENTATION: DOCUMENTATION_PATTERNS,
    Category.API: API_PATTERNS,
    Category.INFRASTRUCTURE: INFRASTRUCTURE_PATTERNS,
    Category.FRONTEND: FRONTEND_PATTERNS,
    Category.DATABASE: DATABASE_PATTERNS,
}

# --- Checklist items per category ---

CATEGORY_ITEMS: dict[Category, list[ChecklistItem]] = {
    Category.SECURITY: [
        ChecklistItem(
            "sec-01", "No hardcoded secrets",
            "Verify no API keys, passwords, or tokens are committed in plaintext.",
            Category.SECURITY, Severity.CRITICAL, True,
        ),
        ChecklistItem(
            "sec-02", "Input validation",
            "All user inputs are validated and sanitized before use.",
            Category.SECURITY, Severity.CRITICAL,
        ),
        ChecklistItem(
            "sec-03", "Authentication checks",
            "Endpoints require proper authentication where expected.",
            Category.SECURITY, Severity.HIGH,
        ),
        ChecklistItem(
            "sec-04", "Authorization verified",
            "Role-based access control is enforced for protected resources.",
            Category.SECURITY, Severity.HIGH,
        ),
        ChecklistItem(
            "sec-05", "Sensitive data handling",
            "PII and sensitive data are encrypted in transit and at rest.",
            Category.SECURITY, Severity.HIGH,
        ),
        ChecklistItem(
            "sec-06", "Dependency vulnerabilities",
            "New dependencies checked for known CVEs.",
            Category.SECURITY, Severity.MEDIUM, True,
        ),
    ],
    Category.PERFORMANCE: [
        ChecklistItem(
            "perf-01", "No N+1 queries",
            "Database queries are batched or use eager loading where appropriate.",
            Category.PERFORMANCE, Severity.HIGH,
        ),
        ChecklistItem(
            "perf-02", "Caching strategy",
            "Frequently accessed data has appropriate caching.",
            Category.PERFORMANCE, Severity.MEDIUM,
        ),
        ChecklistItem(
            "perf-03", "Pagination implemented",
            "Large result sets are paginated, not loaded entirely.",
            Category.PERFORMANCE, Severity.MEDIUM,
        ),
        ChecklistItem(
            "perf-04", "No blocking operations",
            "Async code does not contain blocking calls.",
            Category.PERFORMANCE, Severity.HIGH,
        ),
        ChecklistItem(
            "perf-05", "Resource cleanup",
            "Connections, file handles, and resources are properly closed.",
            Category.PERFORMANCE, Severity.MEDIUM,
        ),
    ],
    Category.TESTING: [
        ChecklistItem(
            "test-01", "Tests pass",
            "All existing and new tests pass locally.",
            Category.TESTING, Severity.CRITICAL, True,
        ),
        ChecklistItem(
            "test-02", "Edge cases covered",
            "Tests include boundary conditions and error paths.",
            Category.TESTING, Severity.HIGH,
        ),
        ChecklistItem(
            "test-03", "Mocks are appropriate",
            "External dependencies are mocked, internal logic is tested directly.",
            Category.TESTING, Severity.MEDIUM,
        ),
        ChecklistItem(
            "test-04", "No test pollution",
            "Tests are isolated and do not depend on execution order.",
            Category.TESTING, Severity.MEDIUM, True,
        ),
        ChecklistItem(
            "test-05", "Coverage maintained",
            "Code coverage does not decrease with this change.",
            Category.TESTING, Severity.MEDIUM, True,
        ),
    ],
    Category.DOCUMENTATION: [
        ChecklistItem(
            "doc-01", "Public API documented",
            "New public functions, classes, and modules have docstrings.",
            Category.DOCUMENTATION, Severity.MEDIUM,
        ),
        ChecklistItem(
            "doc-02", "README updated",
            "README reflects new features, changed behavior, or setup steps.",
            Category.DOCUMENTATION, Severity.LOW,
        ),
        ChecklistItem(
            "doc-03", "Changelog entry",
            "Significant changes have a changelog entry.",
            Category.DOCUMENTATION, Severity.LOW,
        ),
        ChecklistItem(
            "doc-04", "No stale comments",
            "Comments and docs match the current code behavior.",
            Category.DOCUMENTATION, Severity.LOW,
        ),
    ],
    Category.API: [
        ChecklistItem(
            "api-01", "Backward compatible",
            "API changes do not break existing clients without versioning.",
            Category.API, Severity.CRITICAL,
        ),
        ChecklistItem(
            "api-02", "Error responses consistent",
            "Error codes and messages follow the project convention.",
            Category.API, Severity.HIGH,
        ),
        ChecklistItem(
            "api-03", "Rate limiting considered",
            "New endpoints have rate limiting or throttling where appropriate.",
            Category.API, Severity.MEDIUM,
        ),
        ChecklistItem(
            "api-04", "Request validation",
            "Request payloads are validated against schema.",
            Category.API, Severity.HIGH,
        ),
        ChecklistItem(
            "api-05", "Response format documented",
            "API response structure is documented or follows existing patterns.",
            Category.API, Severity.MEDIUM,
        ),
    ],
    Category.INFRASTRUCTURE: [
        ChecklistItem(
            "infra-01", "CI pipeline unbroken",
            "Changes do not break existing CI/CD workflows.",
            Category.INFRASTRUCTURE, Severity.CRITICAL, True,
        ),
        ChecklistItem(
            "infra-02", "Secrets externalized",
            "No secrets embedded in config files; use environment variables.",
            Category.INFRASTRUCTURE, Severity.CRITICAL, True,
        ),
        ChecklistItem(
            "infra-03", "Rollback plan",
            "Deployment changes can be rolled back safely.",
            Category.INFRASTRUCTURE, Severity.HIGH,
        ),
        ChecklistItem(
            "infra-04", "Resource limits set",
            "Container/service resource limits are configured.",
            Category.INFRASTRUCTURE, Severity.MEDIUM,
        ),
    ],
    Category.FRONTEND: [
        ChecklistItem(
            "fe-01", "Accessibility verified",
            "Components have proper ARIA labels and keyboard navigation.",
            Category.FRONTEND, Severity.HIGH,
        ),
        ChecklistItem(
            "fe-02", "Responsive design",
            "UI works on mobile, tablet, and desktop viewports.",
            Category.FRONTEND, Severity.MEDIUM,
        ),
        ChecklistItem(
            "fe-03", "No console errors",
            "Browser console is clean of errors and warnings.",
            Category.FRONTEND, Severity.MEDIUM, True,
        ),
        ChecklistItem(
            "fe-04", "Loading states handled",
            "Async operations show loading indicators.",
            Category.FRONTEND, Severity.MEDIUM,
        ),
        ChecklistItem(
            "fe-05", "Error states handled",
            "Failed operations show meaningful error messages to users.",
            Category.FRONTEND, Severity.MEDIUM,
        ),
    ],
    Category.DATABASE: [
        ChecklistItem(
            "db-01", "Migration reversible",
            "Database migration has a working down/rollback step.",
            Category.DATABASE, Severity.CRITICAL,
        ),
        ChecklistItem(
            "db-02", "Indexes added",
            "New queries have appropriate indexes for performance.",
            Category.DATABASE, Severity.HIGH,
        ),
        ChecklistItem(
            "db-03", "No data loss",
            "Schema changes preserve existing data or have a migration path.",
            Category.DATABASE, Severity.CRITICAL,
        ),
        ChecklistItem(
            "db-04", "Transactions used",
            "Multi-step operations are wrapped in transactions.",
            Category.DATABASE, Severity.HIGH,
        ),
        ChecklistItem(
            "db-05", "Schema documented",
            "New tables/columns have comments or documentation.",
            Category.DATABASE, Severity.LOW,
        ),
    ],
}


def detect_categories(file_changes: list[FileChange]) -> set[Category]:
    """Detect which categories are relevant based on changed file paths."""
    categories: set[Category] = set()
    for fc in file_changes:
        path_lower = fc.path.lower()
        for category, patterns in CATEGORY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, path_lower):
                    categories.add(category)
                    break
    return categories


def calculate_risk_score(
    file_changes: list[FileChange],
    categories: set[Category],
) -> int:
    """Calculate risk score 0-100 based on changes and categories."""
    if not file_changes:
        return 0

    score = 0

    # File count contribution (max 30)
    file_count = len(file_changes)
    score += min(file_count * 3, 30)

    # Change volume contribution (max 25)
    total_changes = sum(fc.additions + fc.deletions for fc in file_changes)
    if total_changes > 500:
        score += 25
    elif total_changes > 200:
        score += 20
    elif total_changes > 100:
        score += 15
    elif total_changes > 50:
        score += 10
    elif total_changes > 10:
        score += 5

    # Category risk contribution (max 45)
    category_weights = {
        Category.SECURITY: 15,
        Category.DATABASE: 12,
        Category.API: 10,
        Category.INFRASTRUCTURE: 8,
        Category.PERFORMANCE: 7,
        Category.FRONTEND: 5,
        Category.TESTING: 3,
        Category.DOCUMENTATION: 2,
    }
    for cat in categories:
        score += category_weights.get(cat, 0)

    # Deleted files boost risk
    deleted_count = sum(1 for fc in file_changes if fc.change_type == "deleted")
    score += min(deleted_count * 5, 15)

    return min(score, 100)


def _build_summary(
    file_changes: list[FileChange],
    categories: set[Category],
    risk_score: int,
) -> str:
    """Build a human-readable summary of the review."""
    parts = []
    parts.append(f"{len(file_changes)} file(s) changed")

    total_adds = sum(fc.additions for fc in file_changes)
    total_dels = sum(fc.deletions for fc in file_changes)
    parts.append(f"+{total_adds}/-{total_dels} lines")

    if categories:
        cat_names = sorted(c.value for c in categories)
        parts.append(f"categories: {', '.join(cat_names)}")

    if risk_score >= 75:
        risk_label = "HIGH"
    elif risk_score >= 40:
        risk_label = "MEDIUM"
    else:
        risk_label = "LOW"
    parts.append(f"risk: {risk_label} ({risk_score}/100)")

    return " | ".join(parts)


def generate_checklist(file_changes: list[FileChange]) -> ReviewChecklist:
    """Generate a review checklist from file changes."""
    categories = detect_categories(file_changes)
    risk_score = calculate_risk_score(file_changes, categories)

    items: list[ChecklistItem] = []
    seen_ids: set[str] = set()
    for cat in sorted(categories, key=lambda c: c.value):
        for item in CATEGORY_ITEMS.get(cat, []):
            if item.id not in seen_ids:
                items.append(item)
                seen_ids.add(item.id)

    summary = _build_summary(file_changes, categories, risk_score)

    title = "Code Review Checklist"
    if categories:
        cat_names = sorted(c.value for c in categories)
        title = f"Code Review: {', '.join(cat_names)}"

    return ReviewChecklist(
        title=title,
        items=items,
        file_changes=file_changes,
        risk_score=risk_score,
        summary=summary,
    )


def parse_diff(diff_text: str) -> list[FileChange]:
    """Parse a unified diff to extract file changes."""
    file_changes: list[FileChange] = []

    # Split into per-file diffs
    file_diffs = re.split(r"^diff --git ", diff_text, flags=re.MULTILINE)

    for chunk in file_diffs:
        if not chunk.strip():
            continue

        # Extract file path from the "a/path b/path" line
        header_match = re.match(r"a/(.+?)\s+b/(.+)", chunk.split("\n")[0])
        if not header_match:
            continue

        a_path = header_match.group(1)
        b_path = header_match.group(2)

        # Determine change type
        is_new = "new file mode" in chunk
        is_deleted = "deleted file mode" in chunk

        if is_new:
            change_type = "added"
            path = b_path
        elif is_deleted:
            change_type = "deleted"
            path = a_path
        else:
            change_type = "modified"
            path = b_path

        # Count additions and deletions from hunk lines
        additions = 0
        deletions = 0
        in_hunk = False
        for line in chunk.split("\n"):
            if line.startswith("@@"):
                in_hunk = True
                continue
            if in_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                elif line.startswith("-") and not line.startswith("---"):
                    deletions += 1

        file_changes.append(FileChange(
            path=path,
            additions=additions,
            deletions=deletions,
            change_type=change_type,
        ))

    return file_changes


def generate_from_diff(diff_text: str) -> ReviewChecklist:
    """Generate a checklist from raw diff text."""
    file_changes = parse_diff(diff_text)
    return generate_checklist(file_changes)


def generate_from_pr(pr_number: int) -> ReviewChecklist:
    """Generate a checklist from a GitHub PR number."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch PR diff: {result.stderr.strip()}")

    return generate_from_diff(result.stdout)


# --- Formatters ---


def format_checklist_text(checklist: ReviewChecklist) -> str:
    """Format checklist as terminal text with checkboxes."""
    lines: list[str] = []
    lines.append(f"{'=' * 60}")
    lines.append(checklist.title)
    lines.append(f"{'=' * 60}")
    lines.append(f"Risk: {checklist.risk_score}/100 | {checklist.summary}")
    lines.append("")

    by_category: dict[Category, list[ChecklistItem]] = {}
    for item in checklist.items:
        by_category.setdefault(item.category, []).append(item)

    for cat in sorted(by_category, key=lambda c: c.value):
        lines.append(f"--- {cat.value.upper()} ---")
        for item in by_category[cat]:
            severity_tag = f"[{item.severity.value.upper()}]"
            lines.append(f"  [ ] {severity_tag} {item.label}")
            lines.append(f"      {item.description}")
        lines.append("")

    if checklist.file_changes:
        lines.append("--- FILES ---")
        for fc in checklist.file_changes:
            indicator = {"added": "+", "deleted": "-", "modified": "~"}.get(
                fc.change_type, "?"
            )
            lines.append(f"  {indicator} {fc.path} (+{fc.additions}/-{fc.deletions})")

    return "\n".join(lines)


def format_checklist_slack(checklist: ReviewChecklist) -> str:
    """Format checklist as Slack mrkdwn (not GitHub markdown)."""
    lines: list[str] = []
    lines.append(f"*{checklist.title}*")
    lines.append(f"Risk: {checklist.risk_score}/100 | {checklist.summary}")
    lines.append("")

    by_category: dict[Category, list[ChecklistItem]] = {}
    for item in checklist.items:
        by_category.setdefault(item.category, []).append(item)

    for cat in sorted(by_category, key=lambda c: c.value):
        lines.append(f"*{cat.value.upper()}*")
        for item in by_category[cat]:
            severity_icon = {
                Severity.CRITICAL: ":red_circle:",
                Severity.HIGH: ":large_orange_circle:",
                Severity.MEDIUM: ":large_yellow_circle:",
                Severity.LOW: ":white_circle:",
            }.get(item.severity, ":white_circle:")
            lines.append(f"{severity_icon} {item.label}")
            lines.append(f"    _{item.description}_")
        lines.append("")

    if checklist.file_changes:
        lines.append("*FILES*")
        for fc in checklist.file_changes:
            indicator = {"added": "+", "deleted": "-", "modified": "~"}.get(
                fc.change_type, "?"
            )
            lines.append(f"`{indicator}` {fc.path} (+{fc.additions}/-{fc.deletions})")

    return "\n".join(lines)


def format_checklist_json(checklist: ReviewChecklist) -> str:
    """Format checklist as JSON."""
    data = {
        "title": checklist.title,
        "risk_score": checklist.risk_score,
        "summary": checklist.summary,
        "items": [
            {
                "id": item.id,
                "label": item.label,
                "description": item.description,
                "category": item.category.value,
                "severity": item.severity.value,
                "auto_checkable": item.auto_checkable,
            }
            for item in checklist.items
        ],
        "file_changes": [
            {
                "path": fc.path,
                "additions": fc.additions,
                "deletions": fc.deletions,
                "change_type": fc.change_type,
            }
            for fc in checklist.file_changes
        ],
    }
    return json.dumps(data, indent=2)


def format_checklist_markdown(checklist: ReviewChecklist) -> str:
    """Format checklist as GitHub-flavored markdown."""
    lines: list[str] = []
    lines.append(f"# {checklist.title}")
    lines.append("")
    lines.append(f"**Risk**: {checklist.risk_score}/100 | {checklist.summary}")
    lines.append("")

    by_category: dict[Category, list[ChecklistItem]] = {}
    for item in checklist.items:
        by_category.setdefault(item.category, []).append(item)

    for cat in sorted(by_category, key=lambda c: c.value):
        lines.append(f"## {cat.value.capitalize()}")
        lines.append("")
        for item in by_category[cat]:
            severity_badge = f"`{item.severity.value.upper()}`"
            lines.append(f"- [ ] {severity_badge} **{item.label}**")
            lines.append(f"  - {item.description}")
        lines.append("")

    if checklist.file_changes:
        lines.append("## Changed Files")
        lines.append("")
        for fc in checklist.file_changes:
            indicator = {"added": "+", "deleted": "-", "modified": "~"}.get(
                fc.change_type, "?"
            )
            lines.append(
                f"- `{indicator}` `{fc.path}` (+{fc.additions}/-{fc.deletions})"
            )

    return "\n".join(lines)


def format_checklist_mcp(checklist: ReviewChecklist) -> dict:
    """Format checklist as dict for grip_checklist MCP app.

    Returns a dict with 'title' and 'sections', where each section has
    a 'heading' and 'items' list matching the grip_checklist schema.
    """
    by_category: dict[Category, list[ChecklistItem]] = {}
    for item in checklist.items:
        by_category.setdefault(item.category, []).append(item)

    sections = []
    for cat in sorted(by_category, key=lambda c: c.value):
        section_items = []
        for item in by_category[cat]:
            section_items.append({
                "id": item.id,
                "label": f"[{item.severity.value.upper()}] {item.label}",
                "description": item.description,
                "checked": False,
            })
        sections.append({
            "heading": cat.value.capitalize(),
            "items": section_items,
        })

    # Add files section if there are changes
    if checklist.file_changes:
        file_items = []
        for fc in checklist.file_changes:
            indicator = {"added": "+", "deleted": "-", "modified": "~"}.get(
                fc.change_type, "?"
            )
            file_items.append({
                "id": f"file-{fc.path.replace('/', '-').replace('.', '-')}",
                "label": f"{indicator} {fc.path} (+{fc.additions}/-{fc.deletions})",
                "checked": False,
            })
        sections.append({
            "heading": "Changed Files",
            "items": file_items,
        })

    return {
        "title": checklist.title,
        "sections": sections,
    }


FORMATTERS = {
    "text": format_checklist_text,
    "slack": format_checklist_slack,
    "json": format_checklist_json,
    "markdown": format_checklist_markdown,
    "mcp": format_checklist_mcp,
}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate code review checklists from file changes."
    )
    parser.add_argument(
        "--pr",
        type=int,
        help="GitHub PR number to generate checklist for.",
    )
    parser.add_argument(
        "--diff",
        type=str,
        help="Path to a diff file to parse.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "markdown", "mcp"],
        default="text",
        help="Output format (default: text).",
    )

    args = parser.parse_args(argv)

    if args.pr:
        checklist = generate_from_pr(args.pr)
    elif args.diff:
        with open(args.diff) as f:
            diff_text = f.read()
        checklist = generate_from_diff(diff_text)
    else:
        # Read diff from stdin
        diff_text = sys.stdin.read()
        if diff_text.strip():
            checklist = generate_from_diff(diff_text)
        else:
            parser.error("Provide --pr, --diff, or pipe a diff to stdin.")
            return 1

    formatter = FORMATTERS[args.format]
    output = formatter(checklist)
    if isinstance(output, dict):
        print(json.dumps(output, indent=2))
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
