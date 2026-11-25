"""Cross-Session Skill Apprenticeship System.

Captures successful skill usage patterns and exports them as
"apprentice records" that new sessions can query to replicate
success.

Domain origin: Anthropology (master-apprentice knowledge transfer),
neuroscience (procedural memory consolidation).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional
import json
import hashlib

APPRENTICE_DIR = Path.home() / ".claude" / "data" / "apprentice"
APPRENTICE_INDEX = APPRENTICE_DIR / "index.json"


@dataclass
class ApprenticeRecord:
    """A captured pattern of successful skill usage."""
    skill_name: str
    session_id: str
    timestamp: str

    # What was the problem?
    problem_type: str  # e.g., "code_fix", "feature_build", "refactor"
    problem_description: str  # brief description

    # How was the skill invoked?
    parameters: Dict = field(default_factory=dict)
    invocation_sequence: List[str] = field(default_factory=list)  # ordered list of tools/actions

    # What was the outcome?
    ralph_score: float = 0.0
    velocity: float = 0.0
    errors_encountered: List[str] = field(default_factory=list)
    recovery_patterns: List[str] = field(default_factory=list)  # how errors were fixed

    # Metadata
    confidence: float = 0.0  # how replicable is this pattern
    times_replicated: int = 0  # how many times this pattern was successfully reused


class SkillApprentice:
    """Manages apprenticeship records for cross-session knowledge transfer."""

    def __init__(self, apprentice_dir: Path = APPRENTICE_DIR):
        self.dir = apprentice_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()

    def _load_index(self) -> Dict:
        """Load the apprentice index."""
        idx_path = self.dir / "index.json"
        if idx_path.exists():
            try:
                return json.loads(idx_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"records": [], "stats": {"total": 0, "by_skill": {}}}

    def _save_index(self):
        """Save the apprentice index atomically."""
        import tempfile
        import os
        idx_path = self.dir / "index.json"
        data = json.dumps(self._index, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, str(idx_path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def record_success(
        self,
        skill_name: str,
        session_id: str,
        problem_type: str,
        problem_description: str,
        parameters: Dict = None,
        invocation_sequence: List[str] = None,
        ralph_score: float = 0.0,
        velocity: float = 0.0,
        errors_encountered: List[str] = None,
        recovery_patterns: List[str] = None,
    ) -> str:
        """Record a successful skill usage pattern.

        Only records if ralph_score >= 80 (quality gate).
        Returns record ID or empty string if rejected.
        """
        if ralph_score < 80:
            return ""

        record = ApprenticeRecord(
            skill_name=skill_name,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            problem_type=problem_type,
            problem_description=problem_description,
            parameters=parameters or {},
            invocation_sequence=invocation_sequence or [],
            ralph_score=ralph_score,
            velocity=velocity,
            errors_encountered=errors_encountered or [],
            recovery_patterns=recovery_patterns or [],
            confidence=min(1.0, ralph_score / 100),
        )

        # Generate deterministic ID from content
        content_hash = hashlib.sha256(
            f"{skill_name}:{problem_type}:{problem_description[:100]}".encode()
        ).hexdigest()[:12]
        record_id = f"apr-{content_hash}"

        # Save record file
        record_path = self.dir / f"{record_id}.json"
        record_path.write_text(json.dumps(asdict(record), indent=2))

        # Update index
        existing = next(
            (r for r in self._index["records"] if r["id"] == record_id), None
        )
        if existing:
            existing["ralph_score"] = max(
                existing.get("ralph_score", 0), ralph_score
            )
            existing["times_seen"] = existing.get("times_seen", 0) + 1
        else:
            self._index["records"].append({
                "id": record_id,
                "skill_name": skill_name,
                "problem_type": problem_type,
                "ralph_score": ralph_score,
                "timestamp": record.timestamp,
                "times_seen": 1,
            })

        # Update stats
        self._index["stats"]["total"] = len(self._index["records"])
        skill_stats = self._index["stats"].setdefault("by_skill", {})
        skill_stats[skill_name] = skill_stats.get(skill_name, 0) + 1

        self._save_index()
        return record_id

    def query(
        self,
        skill_name: str = "",
        problem_type: str = "",
        limit: int = 5,
    ) -> List[Dict]:
        """Query apprentice records for guidance.

        Returns matching records sorted by confidence (highest first).
        """
        matches = []
        for entry in self._index["records"]:
            if skill_name and entry.get("skill_name") != skill_name:
                continue
            if problem_type and entry.get("problem_type") != problem_type:
                continue

            # Load full record
            record_path = self.dir / f"{entry['id']}.json"
            if record_path.exists():
                try:
                    full = json.loads(record_path.read_text())
                    full["id"] = entry["id"]
                    matches.append(full)
                except (json.JSONDecodeError, OSError):
                    pass

        # Sort by confidence (derived from ralph_score)
        matches.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return matches[:limit]

    def suggest_for_task(self, task_description: str) -> List[Dict]:
        """Suggest apprentice records relevant to a task description.

        Uses keyword matching against problem descriptions and types.
        """
        keywords = set(task_description.lower().split())
        scored = []

        for entry in self._index["records"]:
            record_path = self.dir / f"{entry['id']}.json"
            if not record_path.exists():
                continue
            try:
                full = json.loads(record_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            # Score by keyword overlap
            desc_words = set(full.get("problem_description", "").lower().split())
            type_words = set(full.get("problem_type", "").lower().split("_"))
            overlap = len(keywords & (desc_words | type_words))

            if overlap > 0:
                full["relevance_score"] = overlap
                full["id"] = entry["id"]
                scored.append(full)

        scored.sort(
            key=lambda x: (x.get("relevance_score", 0), x.get("confidence", 0)),
            reverse=True,
        )
        return scored[:5]

    def mark_replicated(self, record_id: str, success: bool = True):
        """Mark a record as successfully replicated (or not)."""
        record_path = self.dir / f"{record_id}.json"
        if record_path.exists():
            try:
                data = json.loads(record_path.read_text())
                data["times_replicated"] = data.get("times_replicated", 0) + (
                    1 if success else 0
                )
                data["confidence"] = min(
                    1.0, data.get("confidence", 0.5) + (0.1 if success else -0.1)
                )
                record_path.write_text(json.dumps(data, indent=2))
            except (json.JSONDecodeError, OSError):
                pass

    def stats(self) -> Dict:
        """Return apprenticeship statistics."""
        return self._index.get("stats", {"total": 0, "by_skill": {}})

    def summary(self) -> str:
        """Human-readable summary for startup banner."""
        stats = self.stats()
        total = stats.get("total", 0)
        if total == 0:
            return ""

        by_skill = stats.get("by_skill", {})
        top_skills = sorted(by_skill.items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{s[0]}({s[1]})" for s in top_skills)

        return f"[APPRENTICE] {total} patterns captured. Top skills: {top_str}"


def main():
    """CLI interface."""
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    app = SkillApprentice()

    if cmd == "stats":
        print(json.dumps(app.stats(), indent=2))
    elif cmd == "query":
        skill = sys.argv[2] if len(sys.argv) > 2 else ""
        results = app.query(skill_name=skill)
        for r in results:
            print(
                f"  [{r.get('id', '')}] {r.get('skill_name', '')} "
                f"| {r.get('problem_type', '')} "
                f"| score={r.get('ralph_score', 0)} "
                f"| conf={r.get('confidence', 0):.2f}"
            )
    elif cmd == "suggest":
        desc = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        results = app.suggest_for_task(desc)
        for r in results:
            print(
                f"  [{r.get('id', '')}] {r.get('skill_name', '')} "
                f"-- {r.get('problem_description', '')[:60]} "
                f"(relevance={r.get('relevance_score', 0)})"
            )
    elif cmd == "summary":
        s = app.summary()
        print(s if s else "No apprentice records yet.")
    else:
        print(f"Usage: {sys.argv[0]} [stats|query [skill]|suggest <description>|summary]")
        sys.exit(1)


if __name__ == "__main__":
    main()
