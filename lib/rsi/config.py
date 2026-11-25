#!/usr/bin/env python3
"""RSI configuration — constants, paths, and the frozen config singleton."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from path_encoding import encode_project_path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))


@dataclass(frozen=True)
class _RSIConfig:
    """Immutable RSI orchestrator configuration.

    Path fields are derived from CLAUDE_DIR at import time.
    Numeric fields are overridable via env vars (read at import time).
    """
    # File paths
    queue_file: Path = field(default_factory=lambda: CLAUDE_DIR / "data" / "rsi-improvement-queue.json")
    metrics_file: Path = field(default_factory=lambda: CLAUDE_DIR / "data" / "rsi-metrics.jsonl")
    memory_file: Path = field(default_factory=lambda: CLAUDE_DIR / "projects" / encode_project_path(CLAUDE_DIR) / "memory" / "MEMORY.md")
    stop_signal: Path = field(default_factory=lambda: CLAUDE_DIR / "state" / "rsi" / "rsi-stop-signal")
    ralph_collect: Path = field(default_factory=lambda: CLAUDE_DIR / "state" / "rsi" / "rsi-last-collect.json")
    lock_file: Path = field(default_factory=lambda: CLAUDE_DIR / "state" / "rsi" / "rsi-active.lock")
    checkpoint_file: Path = field(default_factory=lambda: CLAUDE_DIR / "state" / "rsi" / "rsi-checkpoint.json")

    # Timeouts / thresholds
    lock_stale_seconds: int = 7200  # 2 hours
    checkpoint_stale_hours: int = 24

    # Overridable via env vars
    max_iterations: int = field(default_factory=lambda: int(os.environ.get("RSI_MAX_ITERATIONS", "10")))
    merge_threshold: int = field(default_factory=lambda: int(os.environ.get("RSI_MERGE_THRESHOLD", "85")))
    convergence_stall_count: int = 3  # consecutive low scores before halt

    # Phase scoping (Gen 463, #781)
    max_deliverables_per_session: int = 3  # Scope each session to 2-3 deliverables
    checkpoint_context_pct: float = 0.70  # Auto-checkpoint at 70% (not just 85%)
    phase_handoff_enabled: bool = True  # Enable memory handoff between phases


_CONFIG = _RSIConfig()

# Backward-compatible module-level aliases
QUEUE_FILE = _CONFIG.queue_file
METRICS_FILE = _CONFIG.metrics_file
MEMORY_FILE = _CONFIG.memory_file
STOP_SIGNAL = _CONFIG.stop_signal
RALPH_COLLECT = _CONFIG.ralph_collect
RSI_LOCK_FILE = _CONFIG.lock_file
RSI_LOCK_STALE_SECONDS = _CONFIG.lock_stale_seconds
RSI_CHECKPOINT_FILE = _CONFIG.checkpoint_file
RSI_CHECKPOINT_STALE_HOURS = _CONFIG.checkpoint_stale_hours
DEFAULT_MAX_ITERATIONS = _CONFIG.max_iterations
DEFAULT_MERGE_THRESHOLD = _CONFIG.merge_threshold
CONVERGENCE_STALL_COUNT = _CONFIG.convergence_stall_count
MAX_DELIVERABLES = _CONFIG.max_deliverables_per_session
CHECKPOINT_CONTEXT_PCT = _CONFIG.checkpoint_context_pct
PHASE_HANDOFF_ENABLED = _CONFIG.phase_handoff_enabled
