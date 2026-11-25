#!/usr/bin/env python3
"""
warm_memory.py — Compressed warm layer for recent session history.

Implements the Birdhouse/MacCubeFACE tiered summarisation pattern:
    - Verbatim: last 1-2 sessions (full detail)
    - Summarised: sessions 3-7 (compressed summaries)
    - Durable facts: sessions 8+ (only promoted facts survive)

The warm layer sits between active context (STM) and cold KONO storage,
providing queryable compressed recent history.

Storage: ~/.claude/cache/warm-memory.jsonl
Each entry has: session_id, tier (verbatim|summarised|durable), content,
timestamp, and a quality score.

Usage:
    python3 lib/warm_memory.py ingest SESSION_ID "Session summary..."
    python3 lib/warm_memory.py recall QUERY [--limit 5]
    python3 lib/warm_memory.py rotate              # Tier demotion
    python3 lib/warm_memory.py stats
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
WARM_MEMORY_FILE = CLAUDE_DIR / "cache" / "warm-memory.jsonl"

# Tier thresholds (by age in sessions)
TIER_VERBATIM_MAX = 2     # Last 2 sessions: full detail
TIER_SUMMARISED_MAX = 7   # Sessions 3-7: compressed
# Sessions 8+: durable facts only (or promoted to KONO)

# Max entries before forced rotation
MAX_ENTRIES = 50


class WarmMemoryEntry:
    """A single warm memory entry."""

    def __init__(
        self,
        session_id: str,
        content: str,
        tier: str = "verbatim",
        timestamp: Optional[str] = None,
        quality_score: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.session_id = session_id
        self.content = content
        self.tier = tier
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.quality_score = quality_score
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "content": self.content,
            "tier": self.tier,
            "timestamp": self.timestamp,
            "quality_score": self.quality_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WarmMemoryEntry":
        return cls(
            session_id=d.get("session_id", ""),
            content=d.get("content", ""),
            tier=d.get("tier", "verbatim"),
            timestamp=d.get("timestamp"),
            quality_score=d.get("quality_score", 0.5),
            metadata=d.get("metadata", {}),
        )


class WarmMemoryLayer:
    """
    Compressed warm layer between active context and cold KONO storage.

    Implements three-tier summarisation:
        verbatim   -> full session summaries (recent)
        summarised -> compressed key facts (middle)
        durable    -> only promoted facts (old, pre-KONO)
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self._path = storage_path or WARM_MEMORY_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> List[WarmMemoryEntry]:
        """Load all warm memory entries."""
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text().strip().splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(WarmMemoryEntry.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries

    def _save_all(self, entries: List[WarmMemoryEntry]) -> None:
        """Save all entries back to storage."""
        lines = [json.dumps(e.to_dict()) for e in entries]
        self._path.write_text("\n".join(lines) + "\n" if lines else "")

    def ingest(
        self,
        session_id: str,
        content: str,
        quality_score: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Ingest a new session summary into the warm layer.

        Automatically sets tier to 'verbatim' for new entries.
        """
        entry = WarmMemoryEntry(
            session_id=session_id,
            content=content,
            tier="verbatim",
            quality_score=quality_score,
            metadata=metadata,
        )

        entries = self._load_all()

        # Dedup: replace existing entry for same session
        entries = [e for e in entries if e.session_id != session_id]
        entries.append(entry)

        self._save_all(entries)

        return {"ok": True, "session_id": session_id, "tier": "verbatim"}

    def recall(
        self,
        query: str,
        limit: int = 5,
        tier: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recall warm memories matching a query.

        Uses keyword matching with tier-weighted scoring:
            verbatim (1.0x), summarised (0.8x), durable (0.6x)
        """
        entries = self._load_all()
        if tier:
            entries = [e for e in entries if e.tier == tier]

        query_words = set(w.lower() for w in query.split() if len(w) >= 2)
        if not query_words:
            # No query: return most recent
            entries.sort(key=lambda e: e.timestamp, reverse=True)
            return [e.to_dict() for e in entries[:limit]]

        # Score by keyword match + tier weight + quality
        tier_weights = {"verbatim": 1.0, "summarised": 0.8, "durable": 0.6}
        scored = []
        for entry in entries:
            content_lower = entry.content.lower()
            matched = sum(1 for w in query_words if w in content_lower)
            if matched == 0:
                continue
            match_ratio = matched / len(query_words)
            tier_weight = tier_weights.get(entry.tier, 0.5)
            score = match_ratio * tier_weight * entry.quality_score
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e.to_dict() | {"recall_score": round(s, 3)}
                for s, e in scored[:limit]]

    def rotate(self) -> Dict[str, Any]:
        """
        Rotate warm memory tiers based on age.

        Entries are ordered by timestamp (newest first).
        The newest TIER_VERBATIM_MAX stay verbatim.
        The next (TIER_SUMMARISED_MAX - TIER_VERBATIM_MAX) become summarised.
        Older entries become durable (compressed to key facts).
        Entries beyond MAX_ENTRIES are pruned.
        """
        entries = self._load_all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)

        promoted_to_kono = 0
        demoted = {"verbatim": 0, "summarised": 0, "durable": 0, "pruned": 0}

        for i, entry in enumerate(entries):
            if i < TIER_VERBATIM_MAX:
                if entry.tier != "verbatim":
                    demoted["verbatim"] += 1
                entry.tier = "verbatim"
            elif i < TIER_SUMMARISED_MAX:
                if entry.tier == "verbatim":
                    # Compress: extract key facts
                    entry.content = self._compress_to_summary(entry.content)
                    demoted["summarised"] += 1
                entry.tier = "summarised"
            else:
                if entry.tier != "durable":
                    entry.content = self._extract_durable_facts(entry.content)
                    demoted["durable"] += 1
                entry.tier = "durable"

        # Prune excess entries
        if len(entries) > MAX_ENTRIES:
            pruned = entries[MAX_ENTRIES:]
            entries = entries[:MAX_ENTRIES]
            demoted["pruned"] = len(pruned)

            # Promote pruned durable entries to KONO before deletion
            promoted_to_kono = self._promote_to_kono(pruned)

        self._save_all(entries)

        return {
            "ok": True,
            "total": len(entries),
            "demoted": demoted,
            "promoted_to_kono": promoted_to_kono,
        }

    def _compress_to_summary(self, content: str) -> str:
        """Compress verbatim content to key facts summary."""
        lines = content.strip().splitlines()
        # Keep lines that look like achievements, decisions, or facts
        key_patterns = [
            r"^[-*]\s",           # Bullet points
            r"^\d+\.",            # Numbered items
            r"(completed|merged|fixed|created|decided|achieved)",
            r"PR\s*#\d+",        # PR references
            r"(feat|fix|chore):", # Conventional commits
        ]
        key_lines = []
        for line in lines:
            for pattern in key_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    key_lines.append(line.strip())
                    break

        if key_lines:
            return "\n".join(key_lines[:10])
        # Fallback: first 3 lines
        return "\n".join(lines[:3])

    def _extract_durable_facts(self, content: str) -> str:
        """Extract only durable facts from content."""
        lines = content.strip().splitlines()
        # Keep only lines with strong fact indicators
        fact_patterns = [
            r"PR\s*#\d+",
            r"(merged|deployed|released)",
            r"(decided|chose|selected)",
        ]
        facts = []
        for line in lines:
            for pattern in fact_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    facts.append(line.strip()[:200])
                    break

        return "\n".join(facts[:5]) if facts else content[:100]

    def _promote_to_kono(self, entries: List[WarmMemoryEntry]) -> int:
        """Promote pruned entries to KONO as durable memories."""
        count = 0
        try:
            from kono_substrate import MemorySubstrate
            substrate = MemorySubstrate()
            for entry in entries:
                if entry.content.strip():
                    result = substrate.store_session_memory(
                        content=f"[Warm Memory] {entry.content}",
                        memory_type="episodic",
                    )
                    if result is not None:
                        count += 1
        except (ImportError, Exception):
            pass
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get warm memory statistics."""
        entries = self._load_all()
        tiers = {"verbatim": 0, "summarised": 0, "durable": 0}
        for e in entries:
            tiers[e.tier] = tiers.get(e.tier, 0) + 1

        return {
            "total": len(entries),
            "tiers": tiers,
            "storage_path": str(self._path),
            "storage_size_kb": round(self._path.stat().st_size / 1024, 1)
            if self._path.exists() else 0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Warm Memory Layer")
    sub = parser.add_subparsers(dest="command")

    ing = sub.add_parser("ingest", help="Ingest a session summary")
    ing.add_argument("session_id", help="Session ID")
    ing.add_argument("content", help="Session summary content")
    ing.add_argument("--score", type=float, default=0.5, help="Quality score")

    rec = sub.add_parser("recall", help="Recall from warm memory")
    rec.add_argument("query", help="Search query")
    rec.add_argument("--limit", type=int, default=5, help="Max results")
    rec.add_argument("--tier", choices=["verbatim", "summarised", "durable"])

    sub.add_parser("rotate", help="Rotate tier assignments")
    sub.add_parser("stats", help="Show statistics")

    args = parser.parse_args()
    layer = WarmMemoryLayer()

    if args.command == "ingest":
        result = layer.ingest(args.session_id, args.content, args.score)
        print(json.dumps(result, indent=2))

    elif args.command == "recall":
        results = layer.recall(args.query, limit=args.limit,
                               tier=getattr(args, 'tier', None))
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "rotate":
        result = layer.rotate()
        print(json.dumps(result, indent=2))

    elif args.command == "stats":
        print(json.dumps(layer.get_stats(), indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
