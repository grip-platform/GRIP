"""
wave_state.py — Hybrid wave state management for RSI sessions.

Per-session default, with KONO checkpoint at breathing pauses.
State file: $CLAUDE_DIR/.wave-position (exists only during active RSI wave).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
WAVE_STATE_FILE = CLAUDE_DIR / "state" / "session" / "wave-position"


class WavePhase(str, Enum):
    ASCENT = "ascent"
    DESCENT = "descent"


# Valid wave positions in the Fibonacci sequence
WAVE_POSITIONS = (1, 2, 3, 5, 8)


@dataclass
class WaveState:
    """Current position in the Fibonacci wave protocol.

    position   : Wave position (1, 2, 3, 5, 8) — Fibonacci sequence.
    phase      : ASCENT or DESCENT through the convergence cycle.
    session_id : Unique identifier for the RSI session owning this state.
    cube_w     : Corresponding CUBE W-axis level (0-4).
    """
    position: int = 1
    phase: str = WavePhase.ASCENT.value
    session_id: str = ""
    cube_w: int = 1

    def __post_init__(self):
        if self.position not in WAVE_POSITIONS:
            raise ValueError(
                f"Invalid wave position {self.position}. "
                f"Must be one of {WAVE_POSITIONS}"
            )
        if self.phase not in (WavePhase.ASCENT.value, WavePhase.DESCENT.value):
            raise ValueError(
                f"Invalid phase '{self.phase}'. Must be 'ascent' or 'descent'"
            )
        # Auto-correct cube_w if inconsistent with position/phase
        expected_w = wave_position_to_w_level(self.position, self.phase)
        if self.cube_w != expected_w:
            object.__setattr__(self, 'cube_w', expected_w)

    @property
    def phase_enum(self) -> WavePhase:
        return WavePhase(self.phase)

    @property
    def cube_w_name(self) -> str:
        """Human-readable CUBE W-axis name."""
        names = {0: "LATENT", 1: "DETECTED", 2: "UNDERSTOOD", 3: "CODIFIED", 4: "EMBODIED"}
        suffix = "+" if self.phase == WavePhase.DESCENT.value else ""
        return names.get(self.cube_w, f"W{self.cube_w}") + suffix

    def to_expression_context(self) -> str:
        """Generate genome expression context tag for this wave state."""
        return f"wave:{self.phase}:W{self.position}"


# ── Single source of truth for Fibonacci->CUBE W-level mapping (DRY) ─────

CUBE_W_ASCENT_MAP: dict[int, int] = {1: 1, 2: 2, 3: 3, 5: 3, 8: 4}
CUBE_W_DESCENT_MAP: dict[int, int] = {8: 4, 5: 3, 3: 2, 2: 1, 1: 0}


def wave_position_to_w_level(position: int, phase: str) -> int:
    """Map Fibonacci wave position to CUBE W-level.

    This is the canonical mapping used by both wave_state and wave_enforcer.
    """
    if phase == "ascent":
        return CUBE_W_ASCENT_MAP.get(position, 1)
    return CUBE_W_DESCENT_MAP.get(position, 1)


def save_wave_state(state: WaveState) -> Path:
    """Persist wave state to disk. Returns the state file path."""
    WAVE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(state)
    WAVE_STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")
    logger.info("[wave] Saved state: W%d %s (CUBE W=%d %s)",
                state.position, state.phase, state.cube_w, state.cube_w_name)
    return WAVE_STATE_FILE


def load_wave_state() -> Optional[WaveState]:
    """Load wave state from disk. Returns None if no active wave."""
    if not WAVE_STATE_FILE.exists():
        return None
    try:
        data = json.loads(WAVE_STATE_FILE.read_text())
        return WaveState(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("[wave] Failed to load state: %s", e)
        return None


def clear_wave_state() -> None:
    """Remove wave state file (end of RSI session)."""
    if WAVE_STATE_FILE.exists():
        WAVE_STATE_FILE.unlink()
        logger.info("[wave] Cleared wave state")


def advance_wave(state: WaveState) -> WaveState:
    """Advance to the next wave position in the current phase.

    Ascent:  W1 -> W2 -> W3 -> W5 -> W8
    Descent: W8 -> W5 -> W3 -> W2 -> W1

    Returns a new WaveState (immutable pattern).
    Raises ValueError if already at the end of the sequence.
    """
    positions = list(WAVE_POSITIONS)

    if state.phase == WavePhase.ASCENT.value:
        idx = positions.index(state.position)
        if idx >= len(positions) - 1:
            raise ValueError("Already at peak (W8). Switch to descent phase.")
        next_pos = positions[idx + 1]
        return WaveState(
            position=next_pos,
            phase=WavePhase.ASCENT.value,
            session_id=state.session_id,
            cube_w=wave_position_to_w_level(next_pos, WavePhase.ASCENT.value),
        )
    else:
        idx = positions.index(state.position)
        if idx <= 0:
            raise ValueError("Already at base (W1 descent). Wave cycle complete.")
        next_pos = positions[idx - 1]
        return WaveState(
            position=next_pos,
            phase=WavePhase.DESCENT.value,
            session_id=state.session_id,
            cube_w=wave_position_to_w_level(next_pos, WavePhase.DESCENT.value),
        )


def switch_to_descent(state: WaveState) -> WaveState:
    """Switch from ascent to descent at the peak (W8)."""
    return WaveState(
        position=state.position,
        phase=WavePhase.DESCENT.value,
        session_id=state.session_id,
        cube_w=wave_position_to_w_level(state.position, WavePhase.DESCENT.value),
    )


def checkpoint_to_kono(state: WaveState) -> dict:
    """Prepare wave state for KONO checkpoint at breathing pauses.

    Returns a dict suitable for KONO storage. The actual KONO write
    is left to the caller (avoids coupling to kono.py internals).
    """
    return {
        "type": "wave_checkpoint",
        "wave_position": state.position,
        "wave_phase": state.phase,
        "cube_w": state.cube_w,
        "session_id": state.session_id,
        "expression_context": state.to_expression_context(),
    }


def restore_from_kono(kono_data: dict) -> Optional[WaveState]:
    """Restore wave state from a KONO checkpoint.

    Returns None if the data is invalid or missing required fields.
    """
    try:
        return WaveState(
            position=kono_data["wave_position"],
            phase=kono_data["wave_phase"],
            session_id=kono_data.get("session_id", ""),
            cube_w=kono_data.get("cube_w", 1),
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("[wave] Failed to restore from KONO: %s", e)
        return None


# ── Sprint Task Wave Ordering ─────────────────────────────────────────────
# Maps task complexity to Fibonacci wave positions for sprint planning.
# Pattern proven in Gen 437: S→M→L→M (ascending complexity, concurrent descent).

class TaskSize(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"
    XLARGE = "XL"


# Ascending: small fixes first, large architecture last.
# Descending: reviews and close handled concurrently with peak.
TASK_SIZE_TO_WAVE: dict[str, int] = {
    "S": 1,   # Bug fixes, config changes, issue closes
    "M": 3,   # Test suites, moderate features
    "L": 5,   # Architectural changes, MVPs
    "XL": 8,  # Epics, multi-system refactors
}


def classify_task_size(
    lines_changed: int = 0,
    files_changed: int = 0,
    has_tests: bool = False,
    is_architectural: bool = False,
) -> TaskSize:
    """Classify a task's size based on scope indicators.

    Simple heuristic — callers can override with explicit size.
    """
    if is_architectural or lines_changed > 500 or files_changed > 8:
        return TaskSize.XLARGE if lines_changed > 1000 else TaskSize.LARGE
    if has_tests or lines_changed > 100 or files_changed > 3:
        return TaskSize.MEDIUM
    return TaskSize.SMALL


def plan_sprint_wave(
    tasks: list[dict],
) -> list[dict]:
    """Order sprint tasks into Fibonacci wave (ascending complexity).

    Each task dict should have at minimum: {"name": str, "size": "S"|"M"|"L"|"XL"}
    Returns tasks sorted by wave position (ascending), with wave metadata added.

    The descending wave (reviews, close, Slack) is implicit — it runs
    concurrently with the peak wave or after all ascent tasks complete.

    Args:
        tasks: List of dicts with "name" and "size" keys.

    Returns:
        Same list sorted by ascending wave position, with "wave_position"
        and "wave_phase" keys added to each task.
    """
    for task in tasks:
        size = task.get("size", "M")
        task["wave_position"] = TASK_SIZE_TO_WAVE.get(size, 3)
        task["wave_phase"] = "ascent"

    # Sort by wave position (ascending complexity)
    sorted_tasks = sorted(tasks, key=lambda t: t["wave_position"])
    return sorted_tasks
