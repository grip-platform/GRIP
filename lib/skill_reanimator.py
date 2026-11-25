"""Skill Reanimator — recover dead genome skills via targeted revival.

Domain origin: Conservation biology (genetic rescue),
biochemistry (enzyme reactivation).

Dead genes (fitness < 0.2) often represent useful capabilities that
were never properly exercised. The Reanimator identifies candidates,
pairs them with active high-fitness skills, and generates revival
plans.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import logging

logger = logging.getLogger(__name__)

REANIMATOR_DIR = Path.home() / ".claude" / "data" / "reanimator"
GENOME_PATH = Path.home() / ".claude" / "data" / "genome" / "genome.json"

FITNESS_DEAD = 0.2  # below this = dead
FITNESS_ACTIVE = 0.5  # above this = active/healthy
MIN_INVOCATIONS = 3  # genes with fewer invocations are candidates


@dataclass
class RevivalCandidate:
    """A dead gene identified for potential revival."""
    gene_name: str
    current_fitness: float
    invocation_count: int
    domain: str  # extracted from gene name
    skill_path: str  # path to SKILL.md if it exists
    gap_analysis: str  # what capability this gene represents
    paired_with: str  # active gene to bootstrap from
    revival_status: str = "identified"  # identified -> planned -> attempted -> revived/abandoned
    attempts: int = 0
    last_attempt: str = ""


@dataclass
class RevivalPlan:
    """Structured plan to revive a dead skill."""
    candidate: str  # gene name
    strategy: str  # how to revive
    paired_skill: str  # active skill to pair with
    steps: List[str] = field(default_factory=list)
    estimated_sessions: int = 1
    created_at: str = ""


class SkillReanimator:
    """Identifies and revives dead genome skills."""

    def __init__(self, genome_path: Path = GENOME_PATH, reanimator_dir: Path = REANIMATOR_DIR):
        self.genome_path = genome_path
        self.dir = reanimator_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.dir / "state.json"
        self._state = self._load_state()

    def _load_state(self) -> Dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"candidates": [], "plans": [], "history": [], "stats": {"scanned": 0, "revived": 0, "abandoned": 0}}

    def _save_state(self):
        import tempfile
        import os
        data = json.dumps(self._state, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, str(self._state_path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load_genome(self) -> Dict:
        if self.genome_path.exists():
            try:
                return json.loads(self.genome_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"genes": {}}

    def scan(self) -> List[RevivalCandidate]:
        """Scan genome for dead skills that are revival candidates.

        Returns candidates sorted by potential (domain coverage gaps first).
        """
        genome = self._load_genome()
        genes = genome.get("genes", {})

        dead = []
        active = []
        active_domains = set()

        for name, data in genes.items():
            fitness = data.get("fitness", data.get("_base_fitness", 0.5))
            invocations = data.get("invocation_count", 0)

            # Extract domain from gene name (e.g., "code-mode" -> "code")
            parts = name.split("-")
            domain = parts[0] if parts else "unknown"

            if fitness < FITNESS_DEAD and invocations < MIN_INVOCATIONS:
                # Check if skill file exists
                skill_path = ""
                for pattern in [
                    Path.home() / ".claude" / "skills" / name / "SKILL.md",
                    Path.home() / ".claude" / "skills" / name.replace("-", "_") / "SKILL.md",
                ]:
                    if pattern.exists():
                        skill_path = str(pattern)
                        break

                dead.append({
                    "name": name,
                    "fitness": fitness,
                    "invocations": invocations,
                    "domain": domain,
                    "skill_path": skill_path,
                })
            elif fitness >= FITNESS_ACTIVE:
                active.append({"name": name, "fitness": fitness, "domain": domain})
                active_domains.add(domain)

        # Score candidates: prioritise domains NOT covered by active genes
        candidates = []
        for d in dead:
            # Gap analysis
            if d["domain"] not in active_domains:
                gap = f"Domain '{d['domain']}' has no active genes — revival fills a coverage gap"
                priority = 2  # high priority
            else:
                gap = f"Domain '{d['domain']}' is active but this gene adds depth"
                priority = 1

            # Find best active gene to pair with (same or adjacent domain)
            paired = ""
            same_domain = [a for a in active if a["domain"] == d["domain"]]
            if same_domain:
                paired = max(same_domain, key=lambda x: x["fitness"])["name"]
            elif active:
                paired = max(active, key=lambda x: x["fitness"])["name"]

            candidates.append(RevivalCandidate(
                gene_name=d["name"],
                current_fitness=d["fitness"],
                invocation_count=d["invocations"],
                domain=d["domain"],
                skill_path=d["skill_path"],
                gap_analysis=gap,
                paired_with=paired,
            ))

        # Sort: gap-filling first, then by fitness (lowest = most neglected)
        candidates.sort(key=lambda c: (
            0 if "coverage gap" in c.gap_analysis else 1,
            c.current_fitness,
        ))

        self._state["stats"]["scanned"] = len(candidates)
        self._save_state()

        return candidates

    def create_plan(self, candidate: RevivalCandidate) -> RevivalPlan:
        """Generate a revival plan for a dead skill."""
        steps = [
            f"1. Read skill definition: {candidate.skill_path or 'No SKILL.md found — create one'}",
            f"2. Pair with active skill '{candidate.paired_with}' for context",
            f"3. Find a real task that matches domain '{candidate.domain}'",
            f"4. Invoke the skill on that task (track via genome)",
            f"5. Record outcome — if Ralph >= 80, mark as revived",
        ]

        plan = RevivalPlan(
            candidate=candidate.gene_name,
            strategy=f"Bootstrap '{candidate.gene_name}' via pairing with '{candidate.paired_with}'",
            paired_skill=candidate.paired_with,
            steps=steps,
            estimated_sessions=1,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Store plan
        self._state["plans"].append(asdict(plan))
        self._save_state()

        return plan

    def record_attempt(self, gene_name: str, success: bool, outcome: str = ""):
        """Record a revival attempt result."""
        self._state["history"].append({
            "gene": gene_name,
            "success": success,
            "outcome": outcome,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if success:
            self._state["stats"]["revived"] = self._state["stats"].get("revived", 0) + 1
        else:
            self._state["stats"]["abandoned"] = self._state["stats"].get("abandoned", 0) + 1

        self._save_state()

    def get_top_candidates(self, limit: int = 5) -> List[Dict]:
        """Get top revival candidates for display."""
        candidates = self.scan()
        return [asdict(c) for c in candidates[:limit]]

    def stats(self) -> Dict:
        """Return reanimator statistics."""
        return self._state.get("stats", {})

    def summary(self) -> str:
        """Human-readable summary for startup banner."""
        genome = self._load_genome()
        genes = genome.get("genes", {})
        total = len(genes)
        dead = sum(1 for g in genes.values()
                   if g.get("fitness", g.get("_base_fitness", 0.5)) < FITNESS_DEAD
                   and g.get("invocation_count", 0) < MIN_INVOCATIONS)
        revived = self._state.get("stats", {}).get("revived", 0)

        if dead == 0:
            return ""

        pct = dead / total * 100 if total > 0 else 0
        return f"[REANIMATOR] {dead}/{total} genes dormant ({pct:.0f}%). {revived} revived. Run: python3 lib/skill_reanimator.py scan"


def main():
    """CLI interface."""
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    reanimator = SkillReanimator()

    if cmd == "scan":
        candidates = reanimator.scan()
        print(f"Found {len(candidates)} revival candidates:\n")
        for i, c in enumerate(candidates[:10], 1):
            gap_tag = "GAP" if "coverage gap" in c.gap_analysis else "DEPTH"
            print(f"  {i}. [{gap_tag}] {c.gene_name} (fitness={c.current_fitness:.2f}, invocations={c.invocation_count})")
            print(f"     Domain: {c.domain} | Pair with: {c.paired_with}")
            print(f"     {c.gap_analysis}")
            print()
    elif cmd == "plan":
        gene = sys.argv[2] if len(sys.argv) > 2 else ""
        if not gene:
            print("Usage: skill_reanimator.py plan <gene_name>")
            sys.exit(1)
        candidates = reanimator.scan()
        target = next((c for c in candidates if c.gene_name == gene), None)
        if not target:
            print(f"Gene '{gene}' not found in candidates")
            sys.exit(1)
        plan = reanimator.create_plan(target)
        print(f"Revival Plan for '{gene}':")
        print(f"  Strategy: {plan.strategy}")
        for step in plan.steps:
            print(f"  {step}")
    elif cmd == "stats":
        print(json.dumps(reanimator.stats(), indent=2))
    elif cmd == "summary":
        s = reanimator.summary()
        print(s if s else "No dormant genes found.")
    elif cmd == "top":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        top = reanimator.get_top_candidates(limit)
        for t in top:
            print(f"  {t['gene_name']}: fitness={t['current_fitness']:.2f}, domain={t['domain']}, gap={t['gap_analysis'][:60]}")
    else:
        print(f"Usage: {sys.argv[0]} [scan|plan <gene>|stats|summary|top [N]]")
        sys.exit(1)


if __name__ == "__main__":
    main()
