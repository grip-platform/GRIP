"""Phase scoping for context-heavy RSI sessions (#781).

Tracks deliverables per session, enforces scope boundaries, and
manages memory handoff between phases.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import config

logger = logging.getLogger(__name__)

STATE_DIR = config.CLAUDE_DIR / "state" / "rsi"


@dataclass
class PhaseState:
    """Tracks current phase boundaries and deliverables."""
    session_id: str = ""
    phase_name: str = ""
    deliverables_planned: List[str] = field(default_factory=list)
    deliverables_completed: List[str] = field(default_factory=list)
    context_pct: float = 0.0
    checkpoint_triggered: bool = False
    started_at: str = ""
    handoff_notes: str = ""

    @property
    def deliverable_count(self) -> int:
        return len(self.deliverables_planned)

    @property
    def completed_count(self) -> int:
        return len(self.deliverables_completed)

    @property
    def is_over_scope(self) -> bool:
        return self.deliverable_count > config.MAX_DELIVERABLES

    @property
    def needs_checkpoint(self) -> bool:
        return self.context_pct >= config.CHECKPOINT_CONTEXT_PCT


def load_phase_state() -> PhaseState:
    """Load current phase state from disk."""
    state_file = STATE_DIR / "phase-scope-state.json"
    if not state_file.exists():
        return PhaseState()
    try:
        data = json.loads(state_file.read_text())
        return PhaseState(**{k: v for k, v in data.items() if k in PhaseState.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return PhaseState()


def save_phase_state(state: PhaseState) -> None:
    """Persist phase state to disk."""
    state_file = STATE_DIR / "phase-scope-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(asdict(state), indent=2))


def begin_phase(session_id: str, phase_name: str, deliverables: List[str]) -> PhaseState:
    """Start a new phase with scoped deliverables.

    Warns if deliverables exceed MAX_DELIVERABLES but doesn't block.
    """
    state = PhaseState(
        session_id=session_id,
        phase_name=phase_name,
        deliverables_planned=deliverables[:config.MAX_DELIVERABLES],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    if len(deliverables) > config.MAX_DELIVERABLES:
        logger.warning(
            "Phase '%s' has %d deliverables (max %d). Truncating to first %d.",
            phase_name, len(deliverables), config.MAX_DELIVERABLES, config.MAX_DELIVERABLES,
        )
    save_phase_state(state)
    return state


def complete_deliverable(name: str) -> PhaseState:
    """Mark a deliverable as completed."""
    state = load_phase_state()
    if name not in state.deliverables_completed:
        state.deliverables_completed.append(name)
    save_phase_state(state)
    return state


def update_context_usage(pct: float) -> PhaseState:
    """Update context usage percentage. Returns state with checkpoint flag."""
    state = load_phase_state()
    state.context_pct = pct
    if pct >= config.CHECKPOINT_CONTEXT_PCT and not state.checkpoint_triggered:
        state.checkpoint_triggered = True
        logger.warning(
            "Context at %.0f%% (threshold %.0f%%). Auto-checkpoint triggered.",
            pct * 100, config.CHECKPOINT_CONTEXT_PCT * 100,
        )
    save_phase_state(state)
    return state


def create_handoff(notes: str) -> dict:
    """Create a handoff document for the successor session."""
    state = load_phase_state()
    state.handoff_notes = notes
    save_phase_state(state)

    handoff = {
        "phase": state.phase_name,
        "completed": state.deliverables_completed,
        "remaining": [d for d in state.deliverables_planned if d not in state.deliverables_completed],
        "context_pct": state.context_pct,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    handoff_file = STATE_DIR / "phase-handoff.json"
    handoff_file.write_text(json.dumps(handoff, indent=2))
    return handoff


def read_handoff() -> Optional[dict]:
    """Read handoff from previous session, if any."""
    handoff_file = STATE_DIR / "phase-handoff.json"
    if not handoff_file.exists():
        return None
    try:
        return json.loads(handoff_file.read_text())
    except (json.JSONDecodeError, TypeError):
        return None
