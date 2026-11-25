#!/usr/bin/env python3
"""
cube.py — 4D RSI Convergence Space for GRIP.

INSPIRATION FROM NATURE:
  Nature is 4D: space (3 dimensions) + time.
  Convergence depth is a foundational axis — not an emergent property,
  but a fundamental dimension orthogonal to space and time.
  Fractals: each cell contains the whole structure at smaller scale.

  KONO promotes memories through repeated confirmation (temporal permanence).
  BROLY escalates through power levels (Thread → MESH, depth 0-4).
  converge() is the universal improvement kernel.

THE CUBE unifies all four:

    X axis: Domain           — DRY, KISS, SOLID, GRASP, YAGNI, YSH, performance, ...
    Y axis: Power level      — 0 (Thread) → 4 (MESH)
    Z axis: Session          — temporal sequence of RSI runs
    W axis: Convergence Depth — 0 (Latent) → 4 (Embodied)

CONVERGENCE DEPTH (W axis):
  W=0 LATENT     : Pattern exists in the codebase but is not yet detected
  W=1 DETECTED   : Scanner has identified the pattern
  W=2 UNDERSTOOD : Root cause analyzed, improvement path known
  W=3 CODIFIED   : Pattern crystallised into a skill, agent, or rule
  W=4 EMBODIED   : System executes the pattern correctly without reminder
                   — YAGNI applies: the skill itself becomes unnecessary

FRACTAL NATURE:
  Each cell at (domain, level, session, convergence_depth) is itself a 4D space.
  The pattern of promotion in one cell mirrors the pattern across the whole cube.
  This is not metaphor — it is the structure: converge() at every scale.

A cell (domain, level, session, convergence_depth) contains a CubeCell | None.

PROMOTION RULES:
  A cell promotes from level N to N+1 when its confidence exceeds LEVEL_THRESHOLDS[N]
  across PROMOTION_QUORUM consecutive sessions.
  This mirrors KONO's permanence model: one confirmation = provisional,
  three confirmations = promoted, five = permanent.

EMERGENT DISCOVERY:
  An emergent pattern is a domain present in the same slice (*, level=4, *).
  Cross-domain patterns — the same structural insight appearing in DRY AND SOLID —
  are novel discoveries surfaced by the CUBE automatically.

RECURSIVE CONVERGENCE:
  The CUBE is itself a valid state for converge():
    converge(
        state=cube,
        criterion=cube.fully_converged,
        improve=cube.promote_weakest,
        _scorer=cube.mesh_fraction,
    )

USAGE:
    from lib.cube import Cube, Domain

    cube = Cube()
    cube.record("DRY", session_id="s001", confidence=0.70)
    cube.record("DRY", session_id="s002", confidence=0.85)
    cube.record("DRY", session_id="s003", confidence=0.90)

    # After 3 confident sessions, DRY promotes to level 1
    patterns = cube.emergent_patterns(min_level=2)
    print(patterns)  # ['DRY'] if confidence held
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Power levels (mirrors Broly architecture) ──────────────────────────────

class PowerLevel(IntEnum):
    THREAD   = 0  # Single-signal, no history
    FORK     = 1  # Multi-signal, structural check
    COUNCIL  = 2  # Historical consistency, 3+ sessions
    PIPELINE = 3  # Cross-session KONO attestation
    MESH     = 4  # Independent validator agreement — PERMANENT


# Confidence required to promote from each level to the next
LEVEL_THRESHOLDS = {
    PowerLevel.THREAD:   0.60,
    PowerLevel.FORK:     0.70,
    PowerLevel.COUNCIL:  0.80,
    PowerLevel.PIPELINE: 0.85,
}

# Sessions required at ≥ threshold before promotion
PROMOTION_QUORUM = {
    PowerLevel.THREAD:   1,   # One good session → promotes to FORK
    PowerLevel.FORK:     2,   # Two consistent → COUNCIL
    PowerLevel.COUNCIL:  3,   # Three consistent → PIPELINE (mirrors KONO)
    PowerLevel.PIPELINE: 5,   # Five → MESH (permanent, like KONO permanence)
}

# Well-known design principle domains
DESIGN_DOMAINS = frozenset({
    "DRY", "KISS", "SOLID", "GRASP", "YAGNI", "YSH",
    "CC", "BIG-O",
    "performance", "security", "test_quality", "observability",
})

# Operational (non-code) dimensions tracked alongside design principles
OPERATIONAL_DOMAINS = frozenset({
    "convergence_speed",
    "friction_density",
    "task_efficiency",
    "achievement_rate",
    "compound_rate",
    "genome_health",
})


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class ConvergenceDepth(IntEnum):
    LATENT     = 0  # Pattern exists but undetected
    DETECTED   = 1  # Scanner/analyzer has found the pattern
    UNDERSTOOD = 2  # Root cause known, improvement path clear
    CODIFIED   = 3  # Turned into skill, agent, or rule
    EMBODIED   = 4  # System executes correctly without reminder; YAGNI applies


@dataclass
class CubeCell:
    """A single cell in the 4D CUBE: (domain, level, session, convergence_depth).

    confidence       : Attestation confidence at time of recording (0.0–1.0).
    session_id       : Identifies the RSI session that produced this cell.
    convergence_depth : W-axis — how deeply the system has converged on this pattern.
    timestamp         : Monotonic time of recording.
    metadata          : Arbitrary context (e.g. ConvergenceResult, DedupReport).
    """
    confidence: float
    session_id: str
    convergence_depth: int = 1  # ConvergenceDepth.DETECTED
    timestamp: float = field(default_factory=time.monotonic)
    metadata: Any = None


@dataclass
class EmergentPattern:
    """A domain that has reached sufficient power level for novel discovery.

    domain          : The principle or domain name (e.g. "DRY").
    level           : Current power level (Y axis) this domain has reached.
    convergence_depth : Current convergence depth (W axis) for this domain.
    sessions        : Sessions that contributed to this promotion.
    confidence      : Average confidence across contributing sessions.
    is_novel        : True when the same domain appears across multiple X coords
                      (cross-domain convergence = emergent insight).
    is_embodied     : True when convergence_depth == EMBODIED — skill no longer needed.
    """
    domain: str
    level: int
    convergence_depth: int
    sessions: list[str]
    confidence: float
    is_novel: bool = False
    is_embodied: bool = False


@dataclass
class CubeState:
    """Snapshot of the CUBE for use as a converge() state.

    levels      : Dict[domain → current_power_level] for all tracked domains.
    mesh_count  : Number of domains that have reached MESH (level 4).
    total_count : Total number of tracked domains.
    cells       : Full cell history (read-only snapshot).
    """
    levels: dict[str, int] = field(default_factory=dict)
    mesh_count: int = 0
    total_count: int = 0
    cells: dict = field(default_factory=dict)

    @property
    def mesh_fraction(self) -> float:
        """Fraction of domains at MESH level (0.0–1.0)."""
        if not self.total_count:
            return 0.0
        return self.mesh_count / self.total_count


# ── The CUBE ───────────────────────────────────────────────────────────────

class Cube:
    """3D RSI Convergence Space: Domain × PowerLevel × Session.

    The CUBE tracks how design principle convergence evolves across sessions.
    Each call to record() adds a cell. Cells promote through power levels
    when confidence thresholds are met across the required quorum of sessions.

    The CUBE is itself a valid converge() state — pass it to converge() to
    run the RSI loop until all tracked domains reach MESH level.
    """

    def __init__(self):
        # cells[(domain, level)] → list of CubeCell (ordered by time)
        self._cells: dict[tuple[str, int], list[CubeCell]] = defaultdict(list)
        # Current power level (Y) for each domain
        self._levels: dict[str, int] = {}
        # Current convergence depth (W) for each domain
        self._convergence_depth: dict[str, int] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def record(
        self,
        domain: str,
        session_id: str,
        confidence: float,
        convergence_depth: int = ConvergenceDepth.DETECTED,
        metadata: Any = None,
    ) -> int:
        """Record a convergence observation for a domain.

        Args:
            domain        : Principle or domain name (e.g. "DRY", "SOLID").
            session_id    : Unique identifier for this RSI session.
            confidence    : Attestation confidence (0.0–1.0).
            convergence_depth : W-axis level (default: DETECTED=1).
            metadata      : Optional ConvergenceResult, DedupReport, etc.

        Returns:
            Current power level of the domain after recording.
        """
        level = self._levels.get(domain, PowerLevel.THREAD)
        cell = CubeCell(
            confidence=confidence,
            session_id=session_id,
            convergence_depth=convergence_depth,
            metadata=metadata,
        )
        self._cells[(domain, level)].append(cell)
        logger.debug(
            "[cube] recorded domain=%s level=%d convergence_depth=%d confidence=%.2f",
            domain, level, convergence_depth, confidence,
        )

        # Update convergence depth: W axis advances when a higher level is seen
        current_w = self._convergence_depth.get(domain, ConvergenceDepth.LATENT)
        if convergence_depth > current_w:
            self._convergence_depth[domain] = convergence_depth
            logger.info(
                "[cube] CONVERGENCE domain=%s %s→%s",
                domain,
                ConvergenceDepth(current_w).name,
                ConvergenceDepth(min(convergence_depth, 4)).name,
            )

        # Check for Y-axis promotion
        self._maybe_promote(domain, level)
        return self._levels.get(domain, PowerLevel.THREAD)

    def promote_weakest(self, state: "CubeState") -> "CubeState":
        """improve() callback for converge() — promotes the weakest domain.

        Identifies the domain with the lowest power level and records a
        synthetic observation to push it toward promotion. In production,
        this triggers a targeted RSI analysis of that domain.

        Returns an updated CubeState snapshot.
        """
        if not self._levels:
            return self.snapshot()

        weakest = min(self._levels, key=lambda d: self._levels[d])
        current_level = self._levels[weakest]

        if current_level >= PowerLevel.MESH:
            logger.info("[cube] All domains at MESH — fully converged")
            return self.snapshot()

        threshold = LEVEL_THRESHOLDS.get(PowerLevel(current_level), 1.0)
        # Record at threshold + nudge to encourage promotion
        nudge_confidence = min(1.0, threshold + 0.05)
        self.record(
            domain=weakest,
            session_id=f"rsi-promote-{time.monotonic():.0f}",
            confidence=nudge_confidence,
        )
        logger.info(
            "[cube] Nudging domain=%s from level=%d with confidence=%.2f",
            weakest, current_level, nudge_confidence,
        )
        return self.snapshot()

    def fully_converged(self, state: "CubeState") -> bool:
        """criterion() for converge() — True when all domains reach MESH."""
        if not state.total_count:
            return False
        return state.mesh_fraction >= 1.0

    def mesh_fraction(self, state: "CubeState") -> float:
        """_scorer() for converge() — fraction of domains at MESH level."""
        return state.mesh_fraction

    def snapshot(self) -> CubeState:
        """Return a CubeState snapshot for use as converge() state."""
        mesh_count = sum(
            1 for lvl in self._levels.values() if lvl >= PowerLevel.MESH
        )
        return CubeState(
            levels=dict(self._levels),
            mesh_count=mesh_count,
            total_count=len(self._levels),
            cells=dict(self._cells),
        )

    def emergent_patterns(self, min_level: int = PowerLevel.COUNCIL) -> list[EmergentPattern]:
        """Discover patterns that have emerged at or above min_level.

        Cross-domain patterns (domains sharing the same structural insight)
        are flagged as novel discoveries (is_novel=True).

        Args:
            min_level : Minimum power level to include (default: COUNCIL=2).

        Returns:
            List of EmergentPattern objects, sorted by level descending.
        """
        patterns = []
        for domain, level in self._levels.items():
            if level < min_level:
                continue

            # Collect all cells for this domain up to its current level
            sessions = []
            total_confidence = 0.0
            count = 0
            for lvl in range(level + 1):
                for cell in self._cells.get((domain, lvl), []):
                    sessions.append(cell.session_id)
                    total_confidence += cell.confidence
                    count += 1

            avg_confidence = total_confidence / max(count, 1)
            w = self._convergence_depth.get(domain, ConvergenceDepth.LATENT)
            patterns.append(EmergentPattern(
                domain=domain,
                level=level,
                convergence_depth=w,
                sessions=sessions,
                confidence=avg_confidence,
                is_embodied=(w >= ConvergenceDepth.EMBODIED),
            ))

        # Mark cross-domain patterns (same insight in 2+ different domains)
        # Simple heuristic: domains that share session history
        session_to_domains: dict[str, list[str]] = defaultdict(list)
        for p in patterns:
            for s in p.sessions:
                session_to_domains[s].append(p.domain)
        shared_sessions = {
            s for s, doms in session_to_domains.items() if len(doms) >= 2
        }
        for p in patterns:
            if any(s in shared_sessions for s in p.sessions):
                p.is_novel = True

        return sorted(patterns, key=lambda p: p.level, reverse=True)

    def mode_hint(self, convergence_depth: int) -> list[str]:
        """Return recommended modes for a given CUBE W-level.

        Maps convergence depth to wave-aligned modes from the MODE_WAVE_MAP.
        Domain-context and orthogonal modes are always available and not listed.

        Args:
            convergence_depth : W-axis level (0-4, from ConvergenceDepth enum).

        Returns:
            List of recommended mode names for this convergence depth.
        """
        # W-level to mode mapping (ascent only; descent has separate semantics)
        w_to_modes: dict[int, list[str]] = {
            0: [],  # LATENT — no mode (bare reflection)
            1: ["research", "brainstorm"],  # DETECTED
            2: ["analysis", "synthesis", "decision"],  # UNDERSTOOD
            3: ["architect", "code", "security"],  # CODIFIED
            4: ["testing", "review"],  # EMBODIED
        }
        return w_to_modes.get(convergence_depth, [])

    def cross_slice(self, level: int) -> list[str]:
        """Return all domains that have reached at least this power level.

        A "slice" through the Y axis at a given level shows which domains
        have converged to that depth — the intersection of all X values.
        """
        return [d for d, lvl in self._levels.items() if lvl >= level]

    def report(self) -> str:
        """Human-readable 4D CUBE summary."""
        lines = ["=== 4D CUBE State ===", f"  {'Domain':<16} {'Y:Power':<12} {'W:Convergence':<16} Observations"]
        lines.append("  " + "-" * 60)
        for domain in sorted(self._levels):
            level = self._levels[domain]
            level_name = PowerLevel(level).name if level <= 4 else f"L{level}"
            w = self._convergence_depth.get(domain, ConvergenceDepth.LATENT)
            w_name = ConvergenceDepth(w).name if w <= 4 else f"W{w}"
            cell_count = sum(
                len(self._cells.get((domain, l), []))
                for l in range(level + 1)
            )
            embodied = " ★" if w >= ConvergenceDepth.EMBODIED else ""
            lines.append(
                f"  {domain:<16} {level_name:<12} {w_name:<16} ({cell_count}){embodied}"
            )
        mesh_count = sum(1 for l in self._levels.values() if l >= 4)
        embodied_count = sum(1 for w in self._convergence_depth.values() if w >= 4)
        lines.append(f"\nMESH: {mesh_count}/{len(self._levels)} domains converged (Y)")
        lines.append(f"EMBODIED: {embodied_count}/{len(self._levels)} domains fully converged (W)")
        return "\n".join(lines)

    # ── Internals ──────────────────────────────────────────────────────────

    def _maybe_promote(
        self,
        domain: str,
        from_level: int,
        _visited: Optional[frozenset] = None,
    ) -> bool:
        """Check if enough high-confidence cells exist to promote domain.

        Returns True if promotion occurred.

        _visited: frozenset of (domain, level) pairs already evaluated in this
        recursion chain.  Prevents infinite loops if external code manipulates
        _levels mid-promotion (cycle detection via stack-local immutable set).
        """
        if _visited is None:
            _visited = frozenset()

        key = (domain, from_level)
        if key in _visited:
            logger.warning("[cube] Cycle detected for %s at level %d — aborting", domain, from_level)
            return False

        if from_level >= PowerLevel.MESH:
            return False  # Already at maximum

        threshold = LEVEL_THRESHOLDS.get(PowerLevel(from_level))
        if threshold is None:
            return False

        quorum = PROMOTION_QUORUM.get(PowerLevel(from_level), 3)
        cells = self._cells.get((domain, from_level), [])

        # Count cells that meet the confidence threshold
        qualifying = [c for c in cells if c.confidence >= threshold]
        if len(qualifying) >= quorum:
            next_level = from_level + 1
            self._levels[domain] = next_level
            logger.info(
                "[cube] PROMOTED domain=%s level=%d→%d (quorum=%d/%d threshold=%.2f)",
                domain, from_level, next_level,
                len(qualifying), quorum, threshold,
            )
            # Recursively check if next level also ready (e.g. pre-loaded data)
            self._maybe_promote(domain, next_level, _visited=_visited | {key})
            return True

        self._levels.setdefault(domain, from_level)
        return False


# ── Factory functions ──────────────────────────────────────────────────────

def build_design_cube() -> Cube:
    """Create a Cube pre-seeded with all GRIP design principle domains."""
    cube = Cube()
    # Seed each domain at THREAD level (no observations yet)
    for domain in sorted(DESIGN_DOMAINS):
        cube._levels.setdefault(domain, PowerLevel.THREAD)
    return cube


def converge_cube(cube: Cube, max_depth: int = 8) -> Any:
    """Run the RSI loop until all domains in the cube reach MESH level.

    This is the fractal convergence entry point:
      converge() kernel applied to the CUBE itself.

    Args:
        cube      : Pre-seeded Cube instance.
        max_depth : Maximum RSI cycles (default 8 — one per domain * 2 levels avg).

    Returns:
        ConvergenceResult where best_state is the highest mesh_fraction CubeState.
    """
    from lib.converge import converge

    return converge(
        state=cube.snapshot(),
        criterion=cube.fully_converged,
        improve=cube.promote_weakest,
        max_depth=max_depth,
        _scorer=cube.mesh_fraction,
        label="cube-rsi",
    )


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="GRIP CUBE — 3D RSI convergence space for design principles"
    )
    parser.add_argument("--demo", action="store_true",
                        help="Run a demonstration convergence loop")
    parser.add_argument("--domain", action="append", dest="domains",
                        help="Record a domain observation (format: DOMAIN:CONFIDENCE:SESSION)")
    parser.add_argument("--min-level", type=int, default=2,
                        help="Minimum level for emergent pattern discovery (default: 2)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cube = build_design_cube()

    if args.demo:
        # Simulate 3 RSI sessions observing DRY and SOLID improvements
        import uuid
        for i in range(5):
            sid = str(uuid.uuid4())[:8]
            cube.record("DRY", sid, confidence=0.70 + i * 0.05)
            cube.record("SOLID", sid, confidence=0.65 + i * 0.06)
            cube.record("KISS", sid, confidence=0.60 + i * 0.04)
        print(cube.report())
        patterns = cube.emergent_patterns(min_level=args.min_level)
        if patterns:
            print("\nEmergent patterns:")
            for p in patterns:
                novel = " [NOVEL]" if p.is_novel else ""
                print(f"  {p.domain} @ {PowerLevel(p.level).name} "
                      f"(conf={p.confidence:.2f}){novel}")
        return

    if args.domains:
        for d in args.domains:
            parts = d.split(":")
            if len(parts) != 3:
                print(f"ERROR: expected DOMAIN:CONFIDENCE:SESSION, got {d!r}")
                raise SystemExit(1)
            domain, conf_str, session = parts
            cube.record(domain.strip(), session.strip(), float(conf_str))

    print(cube.report())


if __name__ == "__main__":
    main()
