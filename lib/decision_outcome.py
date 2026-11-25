"""Decision Outcome Closure — automatic decision-to-outcome linking.

Scans git history to find evidence for/against past decisions, closing
the learning loop so the agent can answer "did that approach work?"

Cross-domain insight: evidence-based medicine -> software engineering.
Clinical trials track treatment outcomes; we track decision outcomes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DECISION_LOG_PATH = CLAUDE_DIR / "cache" / "decision-outcomes.jsonl"


class OutcomeVerdict(str):
    """Verdict for a decision outcome."""
    SUCCESS = "success"      # Decision led to positive outcome
    PARTIAL = "partial"      # Some benefit, some issues
    FAILURE = "failure"      # Decision was wrong or harmful
    REVERTED = "reverted"    # Decision was explicitly undone
    PENDING = "pending"      # Not enough evidence yet


@dataclass
class Decision:
    """A recorded decision with optional outcome."""
    id: str                          # Unique ID (e.g., "d-20260225-001")
    description: str                  # What was decided
    rationale: str = ""               # Why it was decided
    alternatives: list[str] = field(default_factory=list)  # What was NOT chosen
    timestamp: str = ""               # When decided
    context: str = ""                 # Session/PR/issue context
    outcome_verdict: str = "pending"  # OutcomeVerdict
    outcome_evidence: list[str] = field(default_factory=list)  # Evidence for verdict
    outcome_timestamp: str = ""       # When outcome was determined
    tags: list[str] = field(default_factory=list)  # e.g., ["architecture", "DRY"]


@dataclass
class OutcomeMatch:
    """A potential outcome match for a decision."""
    decision_id: str
    evidence_type: str  # "commit", "pr_merge", "pr_close", "test_pass", "test_fail", "revert"
    evidence_text: str
    confidence: float   # 0-1
    source: str         # git SHA, PR number, etc.


class DecisionOutcomeTracker:
    """Tracks decisions and automatically links them to outcomes."""

    def __init__(self, log_path: Path | str | None = None):
        self._log_path = Path(log_path) if log_path else DECISION_LOG_PATH
        self._decisions: dict[str, Decision] = {}
        self.load()

    def record_decision(self, description: str, rationale: str = "",
                        alternatives: list[str] | None = None,
                        context: str = "", tags: list[str] | None = None) -> Decision:
        """Record a new decision."""
        # Generate ID from timestamp
        now = datetime.now(timezone.utc)
        count = sum(1 for d in self._decisions.values()
                    if d.timestamp.startswith(now.strftime("%Y-%m-%d")))
        decision_id = f"d-{now.strftime('%Y%m%d')}-{count + 1:03d}"

        decision = Decision(
            id=decision_id,
            description=description,
            rationale=rationale,
            alternatives=alternatives or [],
            timestamp=now.isoformat(),
            context=context,
            tags=tags or [],
        )
        self._decisions[decision_id] = decision
        logger.info("[decision] Recorded: %s — %s", decision_id, description[:60])
        return decision

    def scan_for_outcomes(self, repo_path: str = ".", hours: int = 168) -> list[OutcomeMatch]:
        """Scan git history for evidence that matches pending decisions.

        Looks for:
        - Commits mentioning decision keywords
        - Reverts of commits related to decisions
        - PR merges/closes that reference decision context
        - Test results that validate/invalidate decisions

        Returns matches sorted by confidence descending.
        """
        pending = [d for d in self._decisions.values() if d.outcome_verdict == "pending"]
        if not pending:
            return []

        # Get recent git log
        try:
            result = subprocess.run(
                ["git", "log", f"--since={hours} hours ago", "--max-count=200",
                 "--format=%H|%aI|%s"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            if result.returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line or "|" not in line:
                continue
            parts = line.split("|", 2)
            if len(parts) >= 3:
                commits.append({"sha": parts[0], "timestamp": parts[1], "message": parts[2]})

        matches = []
        for decision in pending:
            for match in self._match_decision(decision, commits):
                matches.append(match)

        return sorted(matches, key=lambda m: m.confidence, reverse=True)

    def _match_decision(self, decision: Decision, commits: list[dict]) -> list[OutcomeMatch]:
        """Find evidence in commits that relates to a specific decision."""
        matches = []

        # Extract keywords from decision description (3+ char words)
        keywords = set(
            w.lower() for w in re.findall(r'\b\w{3,}\b', decision.description)
        ) - {"the", "and", "for", "that", "this", "with", "from", "not", "was", "has", "are"}

        if not keywords:
            return matches

        for commit in commits:
            message = commit["message"].lower()

            # Count keyword matches
            matched_keywords = [k for k in keywords if k in message]
            if len(matched_keywords) < 2:
                continue

            keyword_ratio = len(matched_keywords) / len(keywords)
            confidence = min(0.9, keyword_ratio)

            # Boost confidence for specific patterns
            evidence_type = "commit"
            if re.search(r"revert|rollback|undo", message):
                evidence_type = "revert"
                confidence = min(0.95, confidence + 0.2)
            elif re.search(r"fix|broken|bug", message):
                evidence_type = "test_fail"
                confidence = min(0.85, confidence + 0.1)
            elif re.search(r"feat|complete|ship|merge", message):
                evidence_type = "pr_merge"
                confidence = min(0.9, confidence + 0.15)

            matches.append(OutcomeMatch(
                decision_id=decision.id,
                evidence_type=evidence_type,
                evidence_text=commit["message"],
                confidence=round(confidence, 2),
                source=commit["sha"][:8],
            ))

        return matches

    def apply_outcomes(self, matches: list[OutcomeMatch],
                       min_confidence: float = 0.5) -> list[Decision]:
        """Apply outcome matches to pending decisions.

        Only applies matches above min_confidence.
        Returns list of decisions that were updated.
        """
        updated = []

        for match in matches:
            if match.confidence < min_confidence:
                continue

            decision = self._decisions.get(match.decision_id)
            if not decision or decision.outcome_verdict != "pending":
                continue

            # Determine verdict from evidence type
            verdict_map = {
                "pr_merge": "success",
                "commit": "partial",
                "test_fail": "failure",
                "revert": "reverted",
            }

            decision.outcome_verdict = verdict_map.get(match.evidence_type, "partial")
            decision.outcome_evidence.append(
                f"[{match.evidence_type}] {match.evidence_text} (conf={match.confidence})"
            )
            decision.outcome_timestamp = datetime.now(timezone.utc).isoformat()
            updated.append(decision)

            logger.info("[decision] Resolved %s -> %s (conf=%.2f)",
                        decision.id, decision.outcome_verdict, match.confidence)

        return updated

    def get_pending(self) -> list[Decision]:
        """Return all pending decisions."""
        return [d for d in self._decisions.values() if d.outcome_verdict == "pending"]

    def get_resolved(self) -> list[Decision]:
        """Return all resolved decisions."""
        return [d for d in self._decisions.values() if d.outcome_verdict != "pending"]

    def success_rate(self, tags: list[str] | None = None) -> float:
        """Calculate success rate for resolved decisions, optionally filtered by tags."""
        resolved = self.get_resolved()
        if tags:
            resolved = [d for d in resolved if any(t in d.tags for t in tags)]
        if not resolved:
            return 0.0
        successes = sum(1 for d in resolved if d.outcome_verdict in ("success", "partial"))
        return successes / len(resolved)

    def summary(self) -> str:
        """Human-readable summary."""
        pending = self.get_pending()
        resolved = self.get_resolved()
        total = len(self._decisions)

        if not total:
            return "No decisions recorded."

        lines = [
            f"Decision Outcomes ({total} total):",
            f"  Pending:  {len(pending)}",
            f"  Resolved: {len(resolved)}",
        ]

        if resolved:
            rate = self.success_rate()
            lines.append(f"  Success rate: {rate:.0%}")

            # Breakdown by verdict
            verdicts: dict[str, int] = {}
            for d in resolved:
                verdicts[d.outcome_verdict] = verdicts.get(d.outcome_verdict, 0) + 1
            for v, c in sorted(verdicts.items()):
                lines.append(f"    {v}: {c}")

        return "\n".join(lines)

    def save(self) -> None:
        """Persist decisions to JSONL."""
        from lib.memory_lock import locked_write

        lines = []
        for d in self._decisions.values():
            data = {
                "id": d.id, "description": d.description,
                "rationale": d.rationale, "alternatives": d.alternatives,
                "timestamp": d.timestamp, "context": d.context,
                "outcome_verdict": d.outcome_verdict,
                "outcome_evidence": d.outcome_evidence,
                "outcome_timestamp": d.outcome_timestamp,
                "tags": d.tags,
            }
            lines.append(json.dumps(data))

        content = "\n".join(lines) + "\n" if lines else ""
        locked_write(self._log_path, content)

    def load(self) -> None:
        """Load decisions from JSONL."""
        if not self._log_path.exists():
            return

        try:
            text = self._log_path.read_text(encoding="utf-8").strip()
        except OSError:
            return

        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                decision = Decision(
                    id=data["id"],
                    description=data.get("description", ""),
                    rationale=data.get("rationale", ""),
                    alternatives=data.get("alternatives", []),
                    timestamp=data.get("timestamp", ""),
                    context=data.get("context", ""),
                    outcome_verdict=data.get("outcome_verdict", "pending"),
                    outcome_evidence=data.get("outcome_evidence", []),
                    outcome_timestamp=data.get("outcome_timestamp", ""),
                    tags=data.get("tags", []),
                )
                self._decisions[decision.id] = decision
            except (json.JSONDecodeError, KeyError):
                continue


# CLI
def main():
    import argparse
    import sys

    # Fix imports when running standalone
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="GRIP Decision Outcome Tracker")
    parser.add_argument("command", choices=["summary", "pending", "scan", "record", "json"],
                        nargs="?", default="summary")
    parser.add_argument("--description", help="Decision description (for record)")
    parser.add_argument("--rationale", default="", help="Decision rationale")
    parser.add_argument("--hours", type=int, default=168, help="Scan window in hours")
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument("--apply", action="store_true", help="Apply outcome matches")
    args = parser.parse_args()

    tracker = DecisionOutcomeTracker()

    if args.command == "summary":
        print(tracker.summary())
    elif args.command == "pending":
        for d in tracker.get_pending():
            print(f"  [{d.id}] {d.description[:60]}")
    elif args.command == "scan":
        matches = tracker.scan_for_outcomes(args.repo, args.hours)
        if not matches:
            print("No outcome matches found.")
        else:
            for m in matches[:10]:
                print(f"  [{m.decision_id}] {m.evidence_type}: {m.evidence_text[:50]} (conf={m.confidence})")
            if args.apply:
                updated = tracker.apply_outcomes(matches)
                tracker.save()
                print(f"\nApplied {len(updated)} outcome(s).")
    elif args.command == "record":
        if not args.description:
            print("ERROR: --description required")
            raise SystemExit(1)
        d = tracker.record_decision(args.description, args.rationale)
        tracker.save()
        print(f"Recorded: {d.id} — {d.description}")
    elif args.command == "json":
        data = {
            "total": len(tracker._decisions),
            "pending": len(tracker.get_pending()),
            "resolved": len(tracker.get_resolved()),
            "success_rate": tracker.success_rate(),
        }
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
