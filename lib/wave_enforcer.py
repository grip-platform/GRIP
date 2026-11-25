"""
wave_enforcer.py — Mode-specific wave enforcement using gentner_enforcer.

Checks mode activations against the current wave state during RSI sessions.
Enforcement: wave misalignment violations are logged (MEDIUM severity, non-blocking).

Three checks:
1. causal_precedence — wave-aligned modes should match the current wave position
2. orthogonal_stacking — orthogonal/domain modes always pass
3. descent_consistency — descent modes should match descent phase

Uses load_mode_wave_map() as the ground truth for mode classification.
YAML definitions are the primary source; _HARDCODED_WAVE_MAP is the fallback.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from lib.wave_state import wave_position_to_w_level

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


class ModeCategory(str, Enum):
    WAVE_ALIGNED = "wave_aligned"
    ORTHOGONAL = "orthogonal"
    DOMAIN_CONTEXT = "domain_context"
    META_LOOP = "meta_loop"


@dataclass
class ModeWaveMapping:
    """Classification of a single mode in the wave protocol."""
    category: ModeCategory
    w_level: Optional[int] = None  # 1-4, only for WAVE_ALIGNED
    phase: str = "both"  # ascent, descent, both


# ── Hardcoded fallback: all 27 modes classified ───────────────────────────
# Used when YAML definitions are unavailable or lack wave metadata.

_HARDCODED_WAVE_MAP: dict[str, ModeWaveMapping] = {
    # Category A: Wave-Aligned — Ascent
    "research":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=1, phase="ascent"),
    "brainstorm": ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=1, phase="ascent"),
    "analysis":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=2, phase="ascent"),
    "synthesis":  ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=2, phase="ascent"),
    "decision":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=2, phase="ascent"),
    "architect":  ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=3, phase="ascent"),
    "code":       ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=3, phase="ascent"),
    "security":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=3, phase="ascent"),
    "testing":    ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=4, phase="ascent"),
    "review":     ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=4, phase="ascent"),
    # Category A: Wave-Aligned — Descent
    "writing":    ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=3, phase="descent"),
    "marketing":  ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=3, phase="descent"),
    "teaching":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=2, phase="descent"),
    "learning":   ModeWaveMapping(ModeCategory.WAVE_ALIGNED, w_level=1, phase="descent"),
    # Category B: Orthogonal Modifiers
    "incognito":  ModeWaveMapping(ModeCategory.ORTHOGONAL),
    "flow":       ModeWaveMapping(ModeCategory.ORTHOGONAL),
    "pair":       ModeWaveMapping(ModeCategory.ORTHOGONAL),
    "demo":       ModeWaveMapping(ModeCategory.ORTHOGONAL),
    "git-ops":    ModeWaveMapping(ModeCategory.ORTHOGONAL),
    "create":     ModeWaveMapping(ModeCategory.ORTHOGONAL),
    # Category C: Domain-Context
    "strategy":   ModeWaveMapping(ModeCategory.DOMAIN_CONTEXT),
    "business":   ModeWaveMapping(ModeCategory.DOMAIN_CONTEXT),
    "legal":      ModeWaveMapping(ModeCategory.DOMAIN_CONTEXT),
    "planning":   ModeWaveMapping(ModeCategory.DOMAIN_CONTEXT),
    # Category D: Meta/Loop
    "rsi":        ModeWaveMapping(ModeCategory.META_LOOP),
    "upskilling": ModeWaveMapping(ModeCategory.META_LOOP),
    "automation": ModeWaveMapping(ModeCategory.META_LOOP),
}

# Process-level cache for dynamically loaded map
_CACHED_MAP: dict[str, ModeWaveMapping] | None = None


def load_mode_wave_map() -> dict[str, ModeWaveMapping]:
    """Load mode-to-wave classification from YAML definitions.

    Reads wave metadata from modes/definitions/*.yaml at runtime.
    Falls back to hardcoded defaults if YAML parsing fails or
    PyYAML is unavailable.

    Single source of truth: mode YAML files, not this module.
    """
    global _CACHED_MAP
    if _CACHED_MAP is not None:
        return _CACHED_MAP

    if yaml is None:
        logger.debug("[wave] PyYAML not available, using hardcoded wave map")
        _CACHED_MAP = _HARDCODED_WAVE_MAP
        return _CACHED_MAP

    modes_dir = CLAUDE_DIR / "modes" / "definitions"
    if not modes_dir.exists():
        logger.debug("[wave] Modes dir %s not found, using hardcoded wave map", modes_dir)
        _CACHED_MAP = _HARDCODED_WAVE_MAP
        return _CACHED_MAP

    result: dict[str, ModeWaveMapping] = {}
    try:
        for yaml_file in sorted(modes_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                mode_name = data.get("name", yaml_file.stem.removesuffix("-mode"))
                wave_meta = data.get("wave")
                if not wave_meta or not isinstance(wave_meta, dict):
                    continue
                category_str = wave_meta.get("category", "")
                try:
                    category = ModeCategory(category_str)
                except ValueError:
                    logger.debug("[wave] Unknown category '%s' in %s", category_str, yaml_file.name)
                    continue
                result[mode_name] = ModeWaveMapping(
                    category=category,
                    w_level=wave_meta.get("w_level"),
                    phase=wave_meta.get("phase", "both"),
                )
            except (yaml.YAMLError, OSError) as e:
                logger.debug("[wave] Failed to parse %s: %s", yaml_file.name, e)
                continue
    except OSError:
        pass

    if not result:
        logger.debug("[wave] No wave metadata found in YAML files, using hardcoded map")
        _CACHED_MAP = _HARDCODED_WAVE_MAP
    else:
        # Merge: hardcoded provides baseline, YAML overrides
        merged = dict(_HARDCODED_WAVE_MAP)
        merged.update(result)
        _CACHED_MAP = merged
        logger.debug("[wave] Loaded %d modes from YAML (%d total with fallback)", len(result), len(merged))

    return _CACHED_MAP


@dataclass
class WaveViolation:
    """Wave enforcement violation (MEDIUM severity — logged, non-blocking)."""
    mode: str
    check: str
    message: str
    severity: str = "info"  # info, warn


def check_mode_activation(
    modes: list[str],
    wave_position: int,
    wave_phase: str,
) -> list[WaveViolation]:
    """Check mode activation against current wave state.

    Args:
        modes         : List of mode names being activated.
        wave_position : Current Fibonacci wave position (1,2,3,5,8).
        wave_phase    : Current phase ("ascent" or "descent").

    Returns:
        List of WaveViolations (MEDIUM severity — logged, non-blocking).
    """
    violations: list[WaveViolation] = []
    current_w = wave_position_to_w_level(wave_position, wave_phase)

    for mode in modes:
        mapping = load_mode_wave_map().get(mode)

        if mapping is None:
            violations.append(WaveViolation(
                mode=mode,
                check="unknown",
                message=f"Mode '{mode}' not in wave classification",
                severity="warn",
            ))
            continue

        # Orthogonal and domain-context modes always pass
        if mapping.category in (ModeCategory.ORTHOGONAL, ModeCategory.DOMAIN_CONTEXT):
            continue

        # Meta/loop modes always pass (they ARE the wave)
        if mapping.category == ModeCategory.META_LOOP:
            continue

        # Wave-aligned modes: check causal precedence
        if mapping.category == ModeCategory.WAVE_ALIGNED:
            # Check 1: Phase match
            if mapping.phase != "both" and mapping.phase != wave_phase:
                violations.append(WaveViolation(
                    mode=mode,
                    check="descent_consistency",
                    message=(
                        f"Mode '{mode}' is {mapping.phase}-phase "
                        f"but wave is in {wave_phase} phase"
                    ),
                    severity="warn",
                ))

            # Check 2: W-level match (causal precedence)
            if mapping.w_level is not None and mapping.w_level != current_w:
                direction = "ahead of" if mapping.w_level > current_w else "behind"
                violations.append(WaveViolation(
                    mode=mode,
                    check="causal_precedence",
                    message=(
                        f"Mode '{mode}' (W{mapping.w_level}) is {direction} "
                        f"current wave W{wave_position} "
                        f"(CUBE W={current_w} {wave_phase})"
                    ),
                    severity="info",
                ))

    return violations


def format_wave_hint(modes: list[str], wave_position: int, wave_phase: str) -> str:
    """Generate a wave-position hint string for mode activation output.

    Example: "[MODE: research] Wave: W1 DETECTED (ascent)"
    """
    current_w = wave_position_to_w_level(wave_position, wave_phase)
    w_names = {0: "LATENT", 1: "DETECTED", 2: "UNDERSTOOD", 3: "CODIFIED", 4: "EMBODIED"}
    suffix = "+" if wave_phase == "descent" else ""
    w_name = w_names.get(current_w, f"W{current_w}") + suffix

    mode_str = "+".join(modes)
    return f"[MODE: {mode_str}] Wave: W{wave_position} {w_name} ({wave_phase})"


def suggest_modes_for_wave(wave_position: int, wave_phase: str) -> list[str]:
    """Suggest eligible modes for the current wave position.

    Returns mode names that are wave-aligned at the current position,
    plus a note about domain/orthogonal modes being available.
    """
    current_w = wave_position_to_w_level(wave_position, wave_phase)
    suggestions = []

    for mode_name, mapping in load_mode_wave_map().items():
        if mapping.category != ModeCategory.WAVE_ALIGNED:
            continue
        if mapping.w_level != current_w:
            continue
        if mapping.phase != "both" and mapping.phase != wave_phase:
            continue
        suggestions.append(mode_name)

    return sorted(suggestions)


# ── CLI entry point ────────────────────────────────────────────────────────

def main():
    """CLI: python3 lib/wave_enforcer.py check <mode1> [mode2] [mode3]"""
    import argparse
    parser = argparse.ArgumentParser(description="Wave enforcer for GRIP modes")
    sub = parser.add_subparsers(dest="cmd")

    check_p = sub.add_parser("check", help="Check modes against wave state")
    check_p.add_argument("modes", nargs="+", help="Mode names to check")

    suggest_p = sub.add_parser("suggest", help="Suggest modes for current wave")

    args = parser.parse_args()

    # Load wave state
    wave_file = CLAUDE_DIR / "state" / "session" / "wave-position"
    if not wave_file.exists():
        print("No active wave session (.wave-position not found)")
        return

    try:
        data = json.loads(wave_file.read_text())
        position = data["position"]
        phase = data["phase"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Invalid wave state: {e}")
        return

    if args.cmd == "check":
        violations = check_mode_activation(args.modes, position, phase)
        hint = format_wave_hint(args.modes, position, phase)
        print(hint)
        if violations:
            for v in violations:
                print(f"  [{v.severity.upper()}] {v.message}")
        else:
            print("  All modes compatible with current wave position")

    elif args.cmd == "suggest":
        suggestions = suggest_modes_for_wave(position, phase)
        print(f"Wave W{position} ({phase}) — suggested modes: {', '.join(suggestions)}")
        print("Domain-context (strategy, business, legal, planning) and orthogonal modes also available.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
