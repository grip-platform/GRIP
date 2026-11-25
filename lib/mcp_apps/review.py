"""Review-related MCP app generators: rubrics, retrospectives, checklists."""


def generate_pr_review_rubric(pr_data: dict = None) -> dict:
    """Generate grip_scoring_rubric for PR reviews.

    Args:
        pr_data: Dict with PR details (optional, generates generic rubric if None)

    Returns:
        Dict with structure: {title, description, criteria: [{id, label, weight, levels}]}
    """
    criteria = [
        {
            "id": "code_quality",
            "label": "Code Quality",
            "weight": 30,
            "levels": [
                {"score": 0, "description": "Poor: No tests, unclear logic, no docs"},
                {"score": 15, "description": "Fair: Some tests, basic clarity"},
                {"score": 30, "description": "Excellent: Full tests, clear, documented"}
            ]
        },
        {
            "id": "architecture",
            "label": "Architecture",
            "weight": 20,
            "levels": [
                {"score": 0, "description": "Poor: Violates SOLID, high coupling"},
                {"score": 10, "description": "Fair: Some patterns, moderate coupling"},
                {"score": 20, "description": "Excellent: SOLID, DRY, low coupling"}
            ]
        },
        {
            "id": "testing",
            "label": "Testing",
            "weight": 25,
            "levels": [
                {"score": 0, "description": "Poor: No tests or failing tests"},
                {"score": 12, "description": "Fair: Some tests, missing edge cases"},
                {"score": 25, "description": "Excellent: Comprehensive tests, 100% pass"}
            ]
        },
        {
            "id": "documentation",
            "label": "Documentation",
            "weight": 15,
            "levels": [
                {"score": 0, "description": "Poor: No docs, unclear intent"},
                {"score": 8, "description": "Fair: Basic docstrings"},
                {"score": 15, "description": "Excellent: Full docstrings, usage examples"}
            ]
        },
        {
            "id": "security",
            "label": "Security",
            "weight": 10,
            "levels": [
                {"score": 0, "description": "Poor: Security issues present"},
                {"score": 5, "description": "Fair: Minor concerns"},
                {"score": 10, "description": "Excellent: No security issues"}
            ]
        }
    ]

    title = "PR Review Rubric"
    description = "Score: 0-100 (80+ auto-merge eligible)"

    if pr_data:
        pr_num = pr_data.get("number", "")
        title = f"PR #{pr_num} Review"
        description = f"{pr_data.get('title', '')[:50]} | Target: 80/100"

    return {
        "title": title,
        "description": description,
        "criteria": criteria
    }


def generate_retrospective(achievements: list = None, issues: list = None, session_data: dict = None) -> dict:
    """Generate grip_retrospective from sprint data.

    Args:
        achievements: List of achievement strings (e.g., from MEMORY.md)
        issues: List of issue/blocker strings
        session_data: Dict with keys: start_date, end_date, gen_count, etc.

    Returns:
        Dict with structure: {title, sections: [{heading, items: [text]}]}
    """
    if achievements is None:
        achievements = []
    if issues is None:
        issues = []

    sections = []

    # What went well
    went_well = achievements[:10] if achievements else [
        "Placeholder: Add achievements from MEMORY.md",
        "Placeholder: Pull from recent commits",
    ]
    sections.append({
        "heading": "What Went Well",
        "items": [{"text": item} for item in went_well]
    })

    # What to improve
    to_improve = issues[:10] if issues else [
        "Placeholder: Add blockers from session history",
        "Placeholder: Review gate denials",
    ]
    sections.append({
        "heading": "What To Improve",
        "items": [{"text": item} for item in to_improve]
    })

    # Action items
    action_items = []
    if session_data and session_data.get("gen_count", 0) > 100:
        action_items.append({"text": f"Review session efficiency after {session_data['gen_count']} generations"})
    action_items.append({"text": "Schedule next sprint planning session"})

    sections.append({
        "heading": "Action Items",
        "items": action_items
    })

    title = "Sprint Retrospective"
    if session_data:
        start = session_data.get("start_date", "")
        end = session_data.get("end_date", "")
        if start and end:
            title = f"Retrospective: {start} to {end}"

    return {
        "title": title,
        "sections": sections
    }


def generate_pr_impact_rubric(impact_data: dict = None) -> dict:
    """Generate grip_scoring_rubric from PR impact analysis.

    Args:
        impact_data: Dict with risk_score, zone_scores, files_analyzed.
                     If None, returns template rubric.

    Returns:
        Dict for grip_scoring_rubric: {title, subject, criteria, tiers}
    """
    if impact_data is None:
        impact_data = {"risk_score": 0, "zones": {}, "files_analyzed": 0}

    criteria = [
        {"id": "blast_radius", "label": "Blast Radius", "scale": 5,
         "description": "How many systems are affected",
         "labels": ["Isolated", "Small", "Moderate", "Wide", "Critical"]},
        {"id": "security", "label": "Security Impact", "scale": 5,
         "description": "Auth, secrets, permissions changes",
         "labels": ["None", "Low", "Moderate", "High", "Critical"]},
        {"id": "data", "label": "Data Safety", "scale": 5,
         "description": "Database, migration, schema changes",
         "labels": ["None", "Low", "Moderate", "High", "Critical"]},
        {"id": "test_coverage", "label": "Test Coverage", "scale": 5,
         "description": "Are changes adequately tested",
         "labels": ["None", "Poor", "Adequate", "Good", "Excellent"]},
        {"id": "reversibility", "label": "Reversibility", "scale": 5,
         "description": "Can changes be easily rolled back",
         "labels": ["Irreversible", "Difficult", "Moderate", "Easy", "Trivial"]},
    ]

    tiers = [
        {"label": "Ship It", "minScore": 80, "color": "#22c55e"},
        {"label": "Review Recommended", "minScore": 50, "color": "#eab308"},
        {"label": "Careful Review Required", "minScore": 20, "color": "#f97316"},
        {"label": "Senior Review Mandatory", "minScore": 0, "color": "#ef4444"},
    ]

    return {
        "title": "PR Impact Assessment",
        "subject": f"PR ({impact_data.get('files_analyzed', 0)} files changed)",
        "criteria": criteria,
        "tiers": tiers,
    }


def generate_review_checklist_mcp(checklist_data: dict = None) -> dict:
    """Generate grip_checklist from review_checklist output.

    Args:
        checklist_data: Dict with items, risk_score, title, file_changes.
                        If None, returns empty checklist structure.

    Returns:
        Dict for grip_checklist: {title, sections}
    """
    if checklist_data is None:
        checklist_data = {"title": "Code Review", "items": [], "risk_score": 0, "file_changes": []}

    sections = []
    categories = {}
    for item in checklist_data.get("items", []):
        cat = item.get("category", "general")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    for cat, items in categories.items():
        section_items = []
        for item in items[:8]:
            sev = item.get("severity", "medium")
            sev_prefix = {"critical": "[!] ", "high": "[H] ", "medium": "", "low": ""}.get(sev, "")
            section_items.append({
                "id": item.get("id", f"item-{len(section_items)}"),
                "label": f"{sev_prefix}{item.get('label', '')}",
                "description": item.get("description", ""),
                "checked": False,
            })
        sections.append({"heading": cat.replace("_", " ").title(), "items": section_items})

    # File changes section
    file_items = []
    for fc in checklist_data.get("file_changes", [])[:10]:
        path = fc.get("path", "unknown")
        change = fc.get("change_type", "modified")
        indicator = {"added": "+", "deleted": "-", "modified": "~"}.get(change, "~")
        file_items.append({
            "id": f"file-{len(file_items)}",
            "label": f"[{indicator}] {path}",
            "checked": False,
        })
    if file_items:
        sections.append({"heading": "Changed Files", "items": file_items})

    risk = checklist_data.get("risk_score", 0)
    risk_label = "LOW" if risk < 30 else "MEDIUM" if risk < 60 else "HIGH" if risk < 80 else "CRITICAL"
    return {"title": f"{checklist_data.get('title', 'Code Review')} (Risk: {risk_label})", "sections": sections}
