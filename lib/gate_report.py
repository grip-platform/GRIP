#!/usr/bin/env python3
"""
Gate Compliance Report Generator
=================================

Reads gate-metrics.jsonl and outputs data formatted for:
1. grip_data_table (columns + rows)
2. grip_digest (expandable cards per gate)
3. CLI summary (stdout)

Usage:
    python3 gate-report.py              # CLI summary
    python3 gate-report.py --table      # JSON for grip_data_table
    python3 gate-report.py --digest     # JSON for grip_digest
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

METRICS_FILE = Path.home() / ".claude" / "data" / "gate-metrics.jsonl"


def load_metrics() -> list[dict]:
    """Load all gate metrics from JSONL file."""
    if not METRICS_FILE.exists():
        return []

    metrics = []
    with open(METRICS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metrics.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return metrics


def aggregate(metrics: list[dict]) -> dict:
    """Aggregate metrics into summary statistics."""
    total = len(metrics)
    if total == 0:
        return {"total": 0, "blocked": 0, "allowed": 0, "approval_rate": 0,
                "by_gate": {}, "by_tool": {}, "by_session": {}}

    decisions = Counter(m.get("decision", "unknown") for m in metrics)
    blocked = decisions.get("deny", 0)
    allowed = decisions.get("allow", 0) + decisions.get("warn", 0)
    approval_rate = round((allowed / total) * 100, 1) if total > 0 else 0

    # By gate
    by_gate = defaultdict(lambda: {"total": 0, "deny": 0, "allow": 0, "warn": 0})
    for m in metrics:
        gate = m.get("gate", "unknown")
        decision = m.get("decision", "unknown")
        by_gate[gate]["total"] += 1
        if decision in ("deny", "allow", "warn"):
            by_gate[gate][decision] += 1

    # By tool
    by_tool = Counter(m.get("tool", "unknown") for m in metrics)

    # By session
    by_session = Counter(m.get("session_id", "unknown") for m in metrics)

    # Time range
    timestamps = [m.get("ts", "") for m in metrics if m.get("ts")]
    first_ts = min(timestamps) if timestamps else ""
    last_ts = max(timestamps) if timestamps else ""

    return {
        "total": total,
        "blocked": blocked,
        "allowed": allowed,
        "approval_rate": approval_rate,
        "by_gate": dict(by_gate),
        "by_tool": dict(by_tool),
        "by_session": dict(by_session),
        "first_event": first_ts,
        "last_event": last_ts,
    }


def format_data_table(agg: dict) -> dict:
    """Format for grip_data_table tool (columns + rows)."""
    rows = []
    for gate, stats in sorted(agg["by_gate"].items(), key=lambda x: x[1]["deny"], reverse=True):
        block_rate = round((stats["deny"] / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
        rows.append({
            "Gate": gate,
            "Total": stats["total"],
            "Blocked": stats["deny"],
            "Allowed": stats["allow"],
            "Warned": stats["warn"],
            "Block Rate": f"{block_rate}%"
        })

    return {
        "title": "Gate Compliance Report",
        "columns": [
            {"key": "Gate", "label": "Gate Name", "width": "30%"},
            {"key": "Total", "label": "Total Calls", "align": "right"},
            {"key": "Blocked", "label": "Blocked", "align": "right"},
            {"key": "Allowed", "label": "Allowed", "align": "right"},
            {"key": "Warned", "label": "Warned", "align": "right"},
            {"key": "Block Rate", "label": "Block Rate", "align": "right"},
        ],
        "rows": rows,
        "summary": {
            "total_events": agg["total"],
            "approval_rate": f"{agg['approval_rate']}%",
            "unique_sessions": len(agg["by_session"]),
            "period": f"{agg['first_event'][:10]} to {agg['last_event'][:10]}" if agg["first_event"] else "N/A"
        }
    }


def format_digest(agg: dict) -> dict:
    """Format for grip_digest tool (expandable cards per gate)."""
    cards = []

    # Summary card
    cards.append({
        "title": "Compliance Overview",
        "subtitle": f"{agg['total']} gate evaluations across {len(agg['by_session'])} sessions",
        "content": (
            f"Approval rate: {agg['approval_rate']}%\n"
            f"Blocked: {agg['blocked']} | Allowed: {agg['allowed']}\n"
            f"Period: {agg['first_event'][:10] if agg['first_event'] else 'N/A'} to "
            f"{agg['last_event'][:10] if agg['last_event'] else 'N/A'}"
        ),
        "status": "success" if agg["approval_rate"] > 90 else "warning"
    })

    # Per-gate cards (sorted by deny count descending)
    for gate, stats in sorted(agg["by_gate"].items(), key=lambda x: x[1]["deny"], reverse=True):
        if gate == "all-passed":
            continue  # Skip the pass-through gate

        block_rate = round((stats["deny"] / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
        status = "error" if stats["deny"] > 0 else "success"

        cards.append({
            "title": gate.replace("-", " ").title(),
            "subtitle": f"{stats['total']} evaluations | {stats['deny']} blocked",
            "content": (
                f"Allowed: {stats['allow']} | Blocked: {stats['deny']} | Warned: {stats['warn']}\n"
                f"Block rate: {block_rate}%"
            ),
            "status": status
        })

    return {
        "title": "Gate Compliance Digest",
        "cards": cards
    }


def format_cli(agg: dict) -> str:
    """Format for CLI stdout."""
    lines = []
    lines.append("Gate Compliance Report")
    lines.append("=" * 50)
    lines.append(f"Total events:    {agg['total']}")
    lines.append(f"Blocked:         {agg['blocked']}")
    lines.append(f"Allowed:         {agg['allowed']}")
    lines.append(f"Approval rate:   {agg['approval_rate']}%")
    lines.append(f"Sessions:        {len(agg['by_session'])}")
    lines.append("")
    lines.append(f"{'Gate':<25} {'Total':>6} {'Block':>6} {'Allow':>6} {'Rate':>7}")
    lines.append("-" * 55)

    for gate, stats in sorted(agg["by_gate"].items(), key=lambda x: x[1]["deny"], reverse=True):
        block_rate = round((stats["deny"] / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
        lines.append(f"{gate:<25} {stats['total']:>6} {stats['deny']:>6} {stats['allow']:>6} {block_rate:>6.1f}%")

    return "\n".join(lines)


def main():
    metrics = load_metrics()
    agg = aggregate(metrics)

    if "--table" in sys.argv:
        print(json.dumps(format_data_table(agg), indent=2))
    elif "--digest" in sys.argv:
        print(json.dumps(format_digest(agg), indent=2))
    else:
        print(format_cli(agg))


if __name__ == "__main__":
    main()
