#!/usr/bin/env python3
"""
gate_modulator.py -- Permanent DEFAULT gate enforcement.

The model is NEVER TRUSTED. Trust modulation removed.
All gates operate at DEFAULT level permanently.

Origin: Falsification sprint H-RS04 proved the trust layer was never
exercised (always RELAXED from inception). V>> directive: "There's no
reason to trust the tool. So never trust it."

DEFAULT = permanent baseline. Not STRICT (causes oscillation per H-RS02).
Not RELAXED (meaningless -- never constrained anything).

PARAMOUNT: Hard gates remain absolute (dependency guardian, destructive
git, secrets detection, SSH blocking).
"""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class TrustLevel(IntEnum):
    """Gate strictness level. Kept for backward compatibility."""
    STRICT = 0
    DEFAULT = 1
    RELAXED = 2


@dataclass(frozen=True)
class GateThresholds:
    """Thresholds for soft gates. Permanent DEFAULT values."""
    read_cache_max: int = 2
    temp_script_action: str = "deny"
    skill_creation_action: str = "warn"
    complexity_ceiling: str = "n^2"


_DEFAULT_THRESHOLDS = GateThresholds()


def get_convergence_trust_level(cube_cache_path: Path = None) -> TrustLevel:
    """Return DEFAULT. Always. The model is never trusted."""
    return TrustLevel.DEFAULT


def get_gate_thresholds(cube_cache_path: Path = None) -> GateThresholds:
    """Return permanent DEFAULT thresholds."""
    return _DEFAULT_THRESHOLDS


def modulation_report(cube_cache_path: Path = None) -> str:
    """Report gate enforcement state."""
    return (
        "Trust Level: DEFAULT (permanent -- model is never trusted)\n"
        f"  Read cache max: {_DEFAULT_THRESHOLDS.read_cache_max}\n"
        f"  Temp scripts: {_DEFAULT_THRESHOLDS.temp_script_action}\n"
        f"  Skill creation: {_DEFAULT_THRESHOLDS.skill_creation_action}\n"
        f"  Complexity ceiling: O({_DEFAULT_THRESHOLDS.complexity_ceiling})"
    )


if __name__ == "__main__":
    print(modulation_report())
