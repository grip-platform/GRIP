"""Backlog-related MCP app generators: data tables, kanban boards."""

from .common import CLAUDE_DIR


def generate_backlog_data_table(scored_issues: list) -> dict:
    """Generate grip_data_table from backlog prioritiser output.

    Args:
        scored_issues: List of ScoredIssue dicts from backlog_prioritiser.py

    Returns:
        Dict with structure: {title, columns, rows}
    """
    return {
        "title": "Backlog Priority Ranking",
        "columns": [
            {"id": "priority", "label": "Priority", "sortable": True},
            {"id": "issue", "label": "Issue", "sortable": True},
            {"id": "score", "label": "Score", "sortable": True},
            {"id": "reasoning", "label": "Reasoning"},
        ],
        "rows": [
            {
                "priority": s.get("priority", "P3"),
                "issue": f"#{s.get('number', 0)}: {s.get('title', '')[:40]}",
                "score": f"{s.get('total_score', 0):.0f}/100",
                "reasoning": s.get("reasoning", "")[:50],
            }
            for s in scored_issues[:30]
        ]
    }


def generate_memory_kanban() -> dict:
    """Generate grip_kanban data from MEMORY.md TODO items.

    Parses the TODO table, scores items on 5 dimensions, and maps
    to Kanban columns: Urgent | Next Up | In Progress | Review | Done.
    """
    from lib.backlog_scorer import parse_memory_todos, score_items, items_to_kanban_data
    items = parse_memory_todos()
    if not items:
        return {
            "title": "GRIP Backlog -- No Items Found",
            "columns": [
                {"id": "empty", "heading": "No TODO items in MEMORY.md", "color": "#666", "cards": []}
            ],
        }
    scored = score_items(items)
    return items_to_kanban_data(scored)


def generate_tech_debt_kanban(scan_result: dict = None) -> dict:
    """Generate grip_kanban from tech debt scan results.

    Args:
        scan_result: Dict with items list from tech_debt_mapper.
                     If None, runs scan against current repo.

    Returns:
        Dict for grip_kanban: {title, columns}
    """
    if scan_result is None:
        try:
            from tech_debt_mapper import scan_project, analyze_debt
            items = scan_project(".")
            report = analyze_debt(items)
            scan_result = {"items": [{"file": i.file, "category": i.category, "severity": i.severity,
                                       "description": i.description, "line": i.line} for i in report.items]}
        except (ImportError, Exception):
            scan_result = {"items": []}

    critical_cards = []
    high_cards = []
    medium_cards = []
    low_cards = []

    for idx, item in enumerate(scan_result.get("items", [])[:40]):
        card = {
            "id": f"debt-{idx}",
            "title": f"{item.get('category', 'unknown')}: {item.get('file', '')[:30]}",
            "description": item.get("description", "")[:80],
            "priority": item.get("severity", "medium"),
            "tags": [item.get("category", "")],
        }
        sev = item.get("severity", "medium")
        if sev == "critical":
            critical_cards.append(card)
        elif sev == "high":
            high_cards.append(card)
        elif sev == "medium":
            medium_cards.append(card)
        else:
            low_cards.append(card)

    return {
        "title": "Tech Debt Board",
        "columns": [
            {"id": "critical", "heading": "Critical", "color": "#ef4444", "cards": critical_cards[:8]},
            {"id": "high", "heading": "High", "color": "#f97316", "cards": high_cards[:8]},
            {"id": "medium", "heading": "Medium", "color": "#eab308", "cards": medium_cards[:8]},
            {"id": "low", "heading": "Low / Nice to Have", "color": "#22c55e", "cards": low_cards[:6]},
        ],
    }
