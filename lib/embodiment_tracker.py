"""Embodiment Tracker -- tracks per-principle convergence depth across sessions.

Determines when a design principle has been permanently internalized (W:4 EMBODIED
sustained for N sessions with zero violations), enabling automatic pruning of
enforcement scaffolding.

Persistence: JSONL at cache/embodiment-tracker.jsonl
Integration: Cube ConvergenceDepth enum, quality_fast.py QualitySignal, converge_dna Gene
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure lib/ siblings are importable when run as a standalone script
if __name__ == "__main__":
    _project_root = str(Path(__file__).resolve().parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

from lib.cube import ConvergenceDepth

logger = logging.getLogger(__name__)

EMBODIMENT_SESSIONS_THRESHOLD = 10  # Sessions at W:4 + 0 violations to qualify
ARCHIVE_DIR_NAME = ".archived"
DORMANT_FITNESS = 0.3

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass
class PrincipleState:
    """Per-principle tracking state."""

    principle: str  # DRY, KISS, BIG-O, YAGNI, SOLID, GRASP, YSH, CC, GRIP-FIRST
    current_depth: int = 0  # ConvergenceDepth value
    sessions_at_embodied: int = 0  # Consecutive sessions at W:4 with 0 violations
    total_violations: int = 0  # Lifetime violation count
    violation_history: list[dict[str, Any]] = field(default_factory=list)
    last_session_id: str = ""
    archived_skill: str = ""  # If non-empty, the skill has been archived


class EmbodimentTracker:
    """Tracks embodiment progress for design principles."""

    def __init__(self, cache_dir: Path | str | None = None, skills_dir: Path | str | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir else CLAUDE_DIR / "cache"
        self._skills_dir = Path(skills_dir) if skills_dir else CLAUDE_DIR / "skills"
        self._cache_file = self._cache_dir / "embodiment-tracker.jsonl"
        self._states: dict[str, PrincipleState] = {}
        self.load()

    def record_session(
        self,
        principle: str,
        session_id: str,
        violation_count: int,
        convergence_depth: int,
    ) -> None:
        """Record a session observation for a principle.

        If violation_count == 0 and convergence_depth >= EMBODIED:
            Increment sessions_at_embodied
        Else:
            Reset sessions_at_embodied to 0 (streak broken)
        """
        state = self._states.get(principle)
        if state is None:
            state = PrincipleState(principle=principle)
            self._states[principle] = state

        # Clamp to valid ConvergenceDepth range (0-4)
        convergence_depth = max(0, min(4, convergence_depth))
        state.current_depth = convergence_depth
        state.total_violations += violation_count
        state.last_session_id = session_id

        if violation_count > 0:
            state.violation_history.append({
                "session_id": session_id,
                "count": violation_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        if violation_count == 0 and convergence_depth >= ConvergenceDepth.EMBODIED:
            state.sessions_at_embodied += 1
        else:
            state.sessions_at_embodied = 0

    def get_candidates(self) -> list[PrincipleState]:
        """Return principles that qualify for embodiment pruning.

        Criteria: sessions_at_embodied >= EMBODIMENT_SESSIONS_THRESHOLD
        """
        return [
            s for s in self._states.values()
            if s.sessions_at_embodied >= EMBODIMENT_SESSIONS_THRESHOLD
        ]

    def archive_skill(
        self, skill_name: str, principle: str, reason: str = "embodied"
    ) -> dict[str, Any]:
        """Move a skill to skills/.archived/ with resurrection metadata.

        Creates metadata.json in the archived skill directory.
        Returns the metadata dict.
        """
        src = self._skills_dir / skill_name
        archive_dir = self._skills_dir / ARCHIVE_DIR_NAME
        dest = archive_dir / skill_name

        archive_dir.mkdir(parents=True, exist_ok=True)

        state = self._states.get(principle)
        metadata = {
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "principle": principle,
            "sessions_embodied": state.sessions_at_embodied if state else 0,
            "convergence_depth": state.current_depth if state else 0,
            "can_resurrect": True,
            "original_path": f"skills/{skill_name}",
        }

        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
            shutil.rmtree(src)
        else:
            dest.mkdir(parents=True, exist_ok=True)

        # Write metadata into the archived directory
        metadata_file = dest / "metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2))

        if state:
            state.archived_skill = skill_name

        logger.info(
            "[embodiment] Archived skill=%s principle=%s reason=%s",
            skill_name, principle, reason,
        )
        return metadata

    def resurrect_skill(self, skill_name: str) -> bool:
        """Restore an archived skill for teaching/descent phases.

        Moves from skills/.archived/{name}/ back to skills/{name}/.
        Updates metadata: can_resurrect=false, resurrected_at=timestamp.
        Returns True if successful.
        """
        archive_dir = self._skills_dir / ARCHIVE_DIR_NAME
        src = archive_dir / skill_name
        dest = self._skills_dir / skill_name

        if not src.is_dir():
            return False

        # Update metadata before moving
        metadata_file = src / "metadata.json"
        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text())
            except (json.JSONDecodeError, OSError):
                metadata = {}
            metadata["can_resurrect"] = False
            metadata["resurrected_at"] = datetime.now(timezone.utc).isoformat()
            metadata_file.write_text(json.dumps(metadata, indent=2))

        shutil.copytree(src, dest, dirs_exist_ok=True)
        shutil.rmtree(src)

        # Clear archived_skill on matching states
        for state in self._states.values():
            if state.archived_skill == skill_name:
                state.archived_skill = ""

        logger.info("[embodiment] Resurrected skill=%s", skill_name)
        return True

    def check_descent_references(self, wave_position: int = 0) -> list[str]:
        """During wave descent (W:3+ TEACHING), surface archived skills as teaching references.

        Only returns skills where can_resurrect=True and wave is in descent phase.
        Wave descent means wave_position > 0 (positive = descent/recovery phase).
        """
        if wave_position <= 0:
            return []

        archive_dir = self._skills_dir / ARCHIVE_DIR_NAME
        if not archive_dir.is_dir():
            return []

        references = []
        for entry in sorted(archive_dir.iterdir()):
            if not entry.is_dir():
                continue
            metadata_file = entry / "metadata.json"
            if not metadata_file.exists():
                continue
            try:
                metadata = json.loads(metadata_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if metadata.get("can_resurrect", False):
                references.append(entry.name)

        return references

    def update_genome(self, genome: Any, skill_name: str, action: str = "archive") -> None:
        """Update genome when archiving/resurrecting.

        archive: Set gene fitness to 0.3 (dormant tier)
        resurrect: Restore gene fitness to NEUTRAL_FITNESS (0.5)
        """
        from lib.converge_dna_core import NEUTRAL_FITNESS

        genes = getattr(genome, "genes", None)
        if genes is None:
            return

        gene = genes.get(skill_name)
        if gene is None:
            return

        if action == "archive":
            gene._base_fitness = DORMANT_FITNESS
            logger.info(
                "[embodiment] Genome: %s fitness -> %.1f (dormant)",
                skill_name, DORMANT_FITNESS,
            )
        elif action == "resurrect":
            gene._base_fitness = NEUTRAL_FITNESS
            logger.info(
                "[embodiment] Genome: %s fitness -> %.1f (restored)",
                skill_name, NEUTRAL_FITNESS,
            )

    def save(self) -> None:
        """Persist state to cache/embodiment-tracker.jsonl."""
        from lib.memory_lock import locked_write

        lines = []
        for state in self._states.values():
            lines.append(json.dumps(asdict(state), default=str))

        content = "\n".join(lines) + "\n" if lines else ""
        locked_write(self._cache_file, content)

    def load(self) -> None:
        """Load state from cache/embodiment-tracker.jsonl."""
        if not self._cache_file.exists():
            return

        try:
            text = self._cache_file.read_text(encoding="utf-8").strip()
        except OSError:
            return

        if not text:
            return

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                principle = data.get("principle", "")
                if not principle:
                    continue
                self._states[principle] = PrincipleState(
                    principle=principle,
                    current_depth=data.get("current_depth", 0),
                    sessions_at_embodied=data.get("sessions_at_embodied", 0),
                    total_violations=data.get("total_violations", 0),
                    violation_history=data.get("violation_history", []),
                    last_session_id=data.get("last_session_id", ""),
                    archived_skill=data.get("archived_skill", ""),
                )
            except (json.JSONDecodeError, TypeError):
                continue

    def summary(self) -> str:
        """Human-readable summary of embodiment progress."""
        if not self._states:
            return "No embodiment data recorded."

        lines = ["Embodiment Progress:"]
        for principle in sorted(self._states):
            s = self._states[principle]
            depth_name = ConvergenceDepth(min(s.current_depth, 4)).name if s.current_depth <= 4 else f"W{s.current_depth}"
            archived = " [ARCHIVED]" if s.archived_skill else ""
            candidate = " [CANDIDATE]" if s.sessions_at_embodied >= EMBODIMENT_SESSIONS_THRESHOLD else ""
            lines.append(
                f"  {principle:<8} W:{s.current_depth} ({depth_name}) "
                f"streak={s.sessions_at_embodied}/{EMBODIMENT_SESSIONS_THRESHOLD} "
                f"violations={s.total_violations}{archived}{candidate}"
            )

        candidates = self.get_candidates()
        if candidates:
            lines.append(f"\nPruning candidates: {', '.join(c.principle for c in candidates)}")

        return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Embodiment Tracker")
    parser.add_argument("command", choices=["summary", "candidates", "archive", "resurrect"],
                        help="Operation to perform")
    parser.add_argument("--skill", help="Skill name (for archive/resurrect)")
    parser.add_argument("--principle", help="Principle name (for archive)")
    parser.add_argument("--reason", default="embodied", help="Archive reason")
    args = parser.parse_args()

    tracker = EmbodimentTracker()

    if args.command == "summary":
        print(tracker.summary())
    elif args.command == "candidates":
        candidates = tracker.get_candidates()
        if not candidates:
            print("No embodiment candidates.")
        else:
            for c in candidates:
                sessions = c.sessions_at_embodied
                print(f"  {c.principle}: {sessions} sessions at EMBODIED")
    elif args.command == "archive":
        if not args.skill or not args.principle:
            print("ERROR: --skill and --principle required for archive")
            raise SystemExit(1)
        metadata = tracker.archive_skill(args.skill, args.principle, args.reason)
        tracker.save()
        print(f"Archived: {args.skill} (reason: {metadata['reason']})")
    elif args.command == "resurrect":
        if not args.skill:
            print("ERROR: --skill required for resurrect")
            raise SystemExit(1)
        if tracker.resurrect_skill(args.skill):
            tracker.save()
            print(f"Resurrected: {args.skill}")
        else:
            print(f"Not found in archive: {args.skill}")


if __name__ == "__main__":
    main()
