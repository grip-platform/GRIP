"""Canonical path layout for the aide (formerly Copilot).

Single source of truth, no scattered ``Path.home() / ".claude"`` calls.

Note: ``DATA_DIR`` moved from ``data/copilot`` to ``data/aide`` (2026-06-21).
``STATE_DIR`` stays ``state/copilot`` — it is gitignored live runtime state
on the operator's machine, not an atomically-movable tracked directory.
"""

from __future__ import annotations

import os
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Persistent state — JSONL append logs and JSON snapshots.
STATE_DIR = CLAUDE_DIR / "state" / "copilot"
KNOWLEDGE_LOG = STATE_DIR / "knowledge.jsonl"
ENTITY_GRAPH = STATE_DIR / "entity_graph.json"
PREDICTIONS = STATE_DIR / "predictions.jsonl"
ROUTINES = STATE_DIR / "routines.json"
PREFERENCES = STATE_DIR / "preferences.json"
DECISIONS = STATE_DIR / "decisions.jsonl"
BRIEF_LOG = STATE_DIR / "brief_log.jsonl"

# Tracked configuration (not gitignored).
DATA_DIR = CLAUDE_DIR / "data" / "aide"
COMPANY_JSON = DATA_DIR / "company.json"
GATES_YAML = DATA_DIR / "copilot-gates.yaml"

# Test fixtures — anything under here is allowed to contain canned data.
FIXTURES_DIR = DATA_DIR / "fixtures"

TIMEZONE = "Africa/Johannesburg"


def ensure_state_dir() -> Path:
    """Create the state directory if missing and return it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR
