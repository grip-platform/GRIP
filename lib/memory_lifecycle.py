#!/usr/bin/env python3
"""
memory_lifecycle.py — Session lifecycle manager for Broly tiered memory.

Activates the dormant BrolyMemory (STM/LTM/Episodic/Semantic) by wiring
it into the GRIP session lifecycle:
    1. init_session()  — called at session start (hooks/startup/grip.sh)
    2. store()         — called during session to route memories to correct tier
    3. recall()        — called during session for multi-tier recall
    4. end_session()   — called at session end (hooks/session-end.sh)
       Runs MemoryConsolidator (STM -> LTM -> KONO promotion),
       then triggers metabolism (fitness decay + permanence + pruning).

Singleton pattern: one instance per session, thread-safe via module-level state.

Usage:
    python3 lib/memory_lifecycle.py init          # Session start
    python3 lib/memory_lifecycle.py store KEY VAL  # Store to STM
    python3 lib/memory_lifecycle.py recall QUERY   # Multi-tier recall
    python3 lib/memory_lifecycle.py end            # Session end consolidation
    python3 lib/memory_lifecycle.py stats          # Memory statistics
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

logger = logging.getLogger(__name__)

# Session state file — tracks whether lifecycle is initialised
STATE_FILE = CLAUDE_DIR / "state" / "memory" / "lifecycle.json"


class MemoryLifecycle:
    """
    Singleton coordinator for Broly tiered memory across session lifecycle.

    Responsibilities:
        - Initialise BrolyMemory with appropriate power level
        - Route store/recall operations
        - Run consolidation at session end
        - Trigger metabolism for memory aging
    """

    _instance: Optional["MemoryLifecycle"] = None

    def __init__(self, power_level: str = "fork"):
        self._power_level_name = power_level
        self._memory = None
        self._consolidator = None
        self._initialised = False
        self._session_start_time = time.time()
        self._store_count = 0
        self._recall_count = 0
        self._errors: List[str] = []

    @classmethod
    def get_instance(cls, power_level: str = "fork") -> "MemoryLifecycle":
        """Get or create the singleton lifecycle instance."""
        if cls._instance is None:
            cls._instance = cls(power_level=power_level)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def init_session(self) -> Dict[str, Any]:
        """
        Initialise Broly tiered memory for this session.

        Called from hooks/startup/grip.sh at session start.

        Returns:
            Dict with init status and stats.
        """
        result = {"ok": False, "errors": []}

        try:
            from broly.power_levels import resolve_power_level
            from broly.memory import BrolyMemory

            level = resolve_power_level(self._power_level_name)
            self._memory = BrolyMemory(power_level=level)
            self._initialised = True

            result["ok"] = True
            result["power_level"] = level.value
            result["stats"] = self._memory.get_stats()

        except ImportError as e:
            msg = f"Broly import failed: {e}"
            self._errors.append(msg)
            result["errors"].append(msg)
        except Exception as e:
            msg = f"Memory init failed: {e}"
            self._errors.append(msg)
            result["errors"].append(msg)

        # Persist state
        self._save_state()

        return result

    def store(
        self,
        key: str,
        value: Any,
        memory_type: str = "stm",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a memory in the appropriate tier.

        Args:
            key: Memory key/topic
            value: Memory content
            memory_type: Target tier (stm, ltm, semantic)
            metadata: Optional metadata

        Returns:
            True if stored successfully
        """
        if not self._initialised or not self._memory:
            return False

        try:
            result = self._memory.store(key, value, memory_type, metadata)
            if result:
                self._store_count += 1
            return result
        except Exception as e:
            self._errors.append(f"Store failed ({memory_type}): {e}")
            return False

    def recall(
        self,
        query: str,
        memory_type: Optional[str] = None,
        limit: int = 5,
    ) -> Any:
        """
        Recall from specified tier or search all tiers.

        Args:
            query: Search query or key
            memory_type: Specific tier, or None for all-tier search
            limit: Max results per tier

        Returns:
            Results from the specified tier or dict of all-tier results.
        """
        if not self._initialised or not self._memory:
            return None

        try:
            self._recall_count += 1
            if memory_type:
                return self._memory.recall(query, memory_type=memory_type, limit=limit)
            return self._memory.recall_all(query, limit_per_type=limit)
        except Exception as e:
            self._errors.append(f"Recall failed: {e}")
            return None

    def end_session(self) -> Dict[str, Any]:
        """
        Run session-end consolidation and metabolism.

        Called from hooks/session-end.sh at session end.

        Steps:
            1. Run MemoryConsolidator (STM -> LTM -> KONO)
            2. Run KONO metabolism (fitness decay)
            3. Run KONO consolidation (attestation -> permanence)
            4. Emit summary

        Returns:
            Dict with consolidation results.
        """
        result = {
            "ok": False,
            "promoted": [],
            "metabolism_affected": 0,
            "consolidated": 0,
            "session_duration_s": time.time() - self._session_start_time,
            "store_count": self._store_count,
            "recall_count": self._recall_count,
            "errors": [],
        }

        # Step 1: Consolidate STM -> LTM
        promoted = self._run_consolidation()
        result["promoted"] = promoted

        # Step 2: Run KONO metabolism (fitness decay)
        metabolism_count = self._run_metabolism()
        result["metabolism_affected"] = metabolism_count

        # Step 3: Run KONO consolidation (attestation -> permanence)
        consolidated_count = self._run_kono_consolidation()
        result["consolidated"] = consolidated_count

        result["ok"] = True
        result["errors"] = self._errors

        # Clean up state
        self._cleanup_state()

        return result

    def _run_consolidation(self) -> List[Dict[str, Any]]:
        """Run STM -> LTM promotion via MemoryConsolidator."""
        if not self._initialised or not self._memory:
            return []

        try:
            from broly.consolidator import MemoryConsolidator

            stm = self._memory._stm
            ltm = self._memory._ltm

            if not ltm:
                return []

            consolidator = MemoryConsolidator(
                stm=stm,
                ltm=ltm,
                min_access_count=3,
            )
            return consolidator.consolidate(remove_promoted=True)

        except ImportError:
            self._errors.append("Broly consolidator not available")
            return []
        except Exception as e:
            self._errors.append(f"Consolidation failed: {e}")
            return []

    def _run_metabolism(self) -> int:
        """Run KONO fitness decay on stale memories."""
        try:
            from kono_substrate import MemorySubstrate
            substrate = MemorySubstrate()
            return substrate.run_metabolism()
        except ImportError:
            return 0
        except Exception as e:
            self._errors.append(f"Metabolism failed: {e}")
            return 0

    def _run_kono_consolidation(self) -> int:
        """Run KONO attestation -> permanence promotion."""
        try:
            from kono_substrate import MemorySubstrate
            substrate = MemorySubstrate()
            return substrate.consolidate()
        except ImportError:
            return 0
        except Exception as e:
            self._errors.append(f"KONO consolidation failed: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get lifecycle statistics."""
        stats = {
            "initialised": self._initialised,
            "power_level": self._power_level_name,
            "session_duration_s": time.time() - self._session_start_time,
            "store_count": self._store_count,
            "recall_count": self._recall_count,
            "error_count": len(self._errors),
        }

        if self._memory:
            stats["memory"] = self._memory.get_stats()

        return stats

    def _save_state(self) -> None:
        """Persist lifecycle state to disk."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "initialised": self._initialised,
            "power_level": self._power_level_name,
            "session_start_time": self._session_start_time,
            "pid": os.getpid(),
        }
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except OSError:
            pass

    def _cleanup_state(self) -> None:
        """Remove state file at session end."""
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ============================================================================
# CLI Interface
# ============================================================================

def main() -> None:
    """CLI entry point for session lifecycle management."""
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Memory Lifecycle Manager")
    parser.add_argument("command", choices=["init", "store", "recall", "end", "stats"])
    parser.add_argument("args", nargs="*", default=[])
    parser.add_argument("--power-level", default="fork", help="Broly power level")
    parser.add_argument("--type", default="stm", help="Memory type for store/recall")
    parser.add_argument("--limit", type=int, default=5, help="Max results for recall")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    lifecycle = MemoryLifecycle.get_instance(power_level=args.power_level)

    if args.command == "init":
        result = lifecycle.init_session()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            status = "OK" if result["ok"] else "FAILED"
            print(f"[MEMORY LIFECYCLE] {status}")
            if result.get("stats"):
                stm = result["stats"].get("stm", {})
                print(f"  STM: {stm.get('count', 0)}/{stm.get('capacity', 100)}")

    elif args.command == "store":
        if len(args.args) < 2:
            print("Usage: memory_lifecycle.py store KEY VALUE", file=sys.stderr)
            sys.exit(1)
        key, value = args.args[0], " ".join(args.args[1:])
        ok = lifecycle.store(key, value, memory_type=args.type)
        print(json.dumps({"ok": ok}))

    elif args.command == "recall":
        query = " ".join(args.args) if args.args else ""
        results = lifecycle.recall(query, memory_type=args.type, limit=args.limit)
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "end":
        result = lifecycle.end_session()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"[MEMORY LIFECYCLE] Session end")
            print(f"  Promoted: {len(result['promoted'])} STM -> LTM")
            print(f"  Metabolism: {result['metabolism_affected']} decayed")
            print(f"  Consolidated: {result['consolidated']} -> permanent")
            print(f"  Duration: {result['session_duration_s']:.0f}s")
            print(f"  Stores: {result['store_count']}, Recalls: {result['recall_count']}")

    elif args.command == "stats":
        stats = lifecycle.get_stats()
        print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
