#!/usr/bin/env python3
"""RSI gate error audit — classify and report denied gate events."""

import json

from . import config

# Gate-to-category mapping for error classification
GATE_CATEGORIES = {
    # Hook denials (gates working as designed)
    "hook_denial": [
        "tool-substitution", "bash-lint", "read-cache", "dependency-guardian",
        "temp-script", "env-var-protection", "mcp-surface", "convergence-stall",
        "git-index-lock", "rsi-stop", "steering-interrupt", "stash-safety",
        "context-gate", "cross-org-read", "slack-workspace-routing",
        "guest-rsi-block", "restricted-docs",
    ],
    # Security gates (SSH, DB, secrets, network)
    "security": [
        "ssh-gate", "database-protection", "secrets-detection", "network-binding",
    ],
    # Git protection (destructive ops, push gates)
    "git_protection": [
        "destructive-git", "git-push-gate",
    ],
    # Budget and rate limits
    "budget_limit": [
        "budget", "budget-gate",
    ],
    # Deployment gates
    "deployment": [
        "deploy-gate",
    ],
}

# Reverse lookup: gate_name -> category (built once, never mutated)
_GATE_TO_CATEGORY: dict[str, str] = {
    _g: _cat
    for _cat, _gates in GATE_CATEGORIES.items()
    for _g in _gates
}


def classify_gate_error(gate_name: str) -> str:
    """Classify a gate name into an error category.

    Args:
        gate_name: The gate identifier from gate-metrics.jsonl.

    Returns:
        Category string: hook_denial, security, git_protection,
        budget_limit, deployment, or unknown.
    """
    return _GATE_TO_CATEGORY.get(gate_name, "unknown")


def audit_gate_errors(limit: int = 0) -> dict:
    """Audit gate-metrics.jsonl and classify all denied events.

    Reads the gate metrics file and produces a categorized breakdown
    of all denied tool operations across the entire history.

    Args:
        limit: Max lines to process (0 = all).

    Returns:
        Dict with: total, categories (name -> count), top_gates,
        samples (category -> [example targets]), and pct breakdown.
    """
    gate_file = config.CLAUDE_DIR / "data" / "gate-metrics.jsonl"
    if not gate_file.exists():
        return {"total": 0, "categories": {}, "error": "no gate-metrics.jsonl"}

    from collections import Counter
    category_counts = Counter()
    gate_counts = Counter()
    samples: dict[str, list] = {}

    lines = gate_file.read_text().splitlines()
    if limit > 0:
        lines = lines[-limit:]

    for line in lines:
        try:
            e = json.loads(line.strip())
            if e.get("decision") != "deny":
                continue
            gate = e.get("gate", "unknown")
            tool = e.get("tool", "?")
            target = (e.get("target", "") or "")[:80]
            category = classify_gate_error(gate)

            category_counts[category] += 1
            gate_counts[gate] += 1

            if category not in samples:
                samples[category] = []
            if len(samples[category]) < 3:
                samples[category].append(f"{gate}/{tool}: {target}")
        except (json.JSONDecodeError, KeyError):
            continue

    total = sum(category_counts.values())
    pct = {}
    for cat, count in category_counts.items():
        pct[cat] = round(count / total * 100, 1) if total > 0 else 0.0

    return {
        "total": total,
        "categories": dict(category_counts.most_common()),
        "pct": pct,
        "top_gates": dict(gate_counts.most_common(10)),
        "samples": samples,
    }
