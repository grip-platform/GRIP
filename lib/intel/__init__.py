"""Intel primitive — generic /intel-<platform> base layer.

PR1 of 5 ships the Protocol + dataclasses + capability-locked spawn helper.
PRs 2–5 add Discord, LinkedIn, WhatsApp adapters + skill/relay glue.

Origin: V>>'s 2026-05-25 /whoa-update-pls ratify. Council:
drafts/intel-primitive-2026-05-25/COUNCIL.md (PROCEED_WITH_REVISIONS).
Revisions R1–R6 baked into base.py and spawn.py.
"""
from __future__ import annotations

from .base import (
    EnrichedObservation,
    Feedback,
    IntelAdapter,
    IntelScope,
    Observation,
    RelayReceipt,
    Seed,
)
from .spawn import READ_ONLY_TOOLS, spawn_with_lock

__all__ = (
    "EnrichedObservation",
    "Feedback",
    "IntelAdapter",
    "IntelScope",
    "Observation",
    "READ_ONLY_TOOLS",
    "RelayReceipt",
    "Seed",
    "spawn_with_lock",
)
