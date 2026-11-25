"""
Broly Memory Consolidator - STM-to-LTM promotion via coherence + novelty gates.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Promotes frequently-accessed STM entries to LTM when they pass:
    1. Access count threshold (default: 3)
    2. Coherence gate (is the content meaningful?)
    3. Optional novelty check (is it genuinely new information?)

Connections:
    - STM → LTM promotion (core)
    - STM → KONO cross-session permanence (#65 connection 1)
    - Promotion → Genome fitness signal (#65 connection 5)
"""

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"

from .stm import ShortTermMemory, STMEntry
from .ltm import LongTermMemory


class MemoryConsolidator:
    """
    Promotes STM entries to LTM based on access frequency and quality gates.

    Flow:
        1. Scan STM for entries with access_count >= threshold
        2. Run coherence check on each candidate
        3. Store passing entries in LTM
        4. Optionally remove promoted entries from STM
    """

    def __init__(
        self,
        stm: ShortTermMemory,
        ltm: LongTermMemory,
        min_access_count: int = 3,
        coherence_threshold: float = 0.3,
        on_promote: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self._stm = stm
        self._ltm = ltm
        self._min_access_count = min_access_count
        self._coherence_threshold = coherence_threshold
        self._coherence_checker = None
        self._on_promote = on_promote  # Callback for genome fitness signal (#65.5)
        self._init_coherence()

    def _init_coherence(self) -> None:
        """Try to load the coherence gate."""
        try:
            lib_dir = str(CLAUDE_DIR / "lib")
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from coherence import is_coherent, get_coherence_score
            self._coherence_checker = (is_coherent, get_coherence_score)
        except (ImportError, Exception):
            pass

    def _is_coherent(self, text: str) -> bool:
        """Check text coherence using the GRIP coherence gate."""
        if not self._coherence_checker:
            return True  # Pass if checker unavailable

        is_coherent_fn, _ = self._coherence_checker
        return is_coherent_fn(text, self._coherence_threshold)

    def consolidate(self, remove_promoted: bool = True) -> List[Dict[str, Any]]:
        """
        Run consolidation: promote eligible STM entries to LTM.

        Args:
            remove_promoted: Whether to remove promoted entries from STM

        Returns:
            List of promoted entry details
        """
        candidates = self._stm.get_promotion_candidates(self._min_access_count)
        promoted = []

        for entry in candidates:
            value_str = str(entry.value)

            # Gate 1: Coherence
            if not self._is_coherent(value_str):
                continue

            # Promote to LTM
            success = self._ltm.store(
                key=entry.key,
                value=value_str,
                metadata={
                    "source": "stm_consolidation",
                    "access_count": entry.access_count,
                    "stm_created_at": entry.created_at,
                    **(entry.metadata or {}),
                },
            )

            if success:
                promotion_record = {
                    "key": entry.key,
                    "access_count": entry.access_count,
                    "value_preview": value_str[:100],
                }
                promoted.append(promotion_record)

                # Connection 1: STM → KONO cross-session permanence
                self._promote_to_kono(value_str)

                # Connection 5: Promotion → Genome fitness signal
                if self._on_promote:
                    try:
                        self._on_promote(entry.key, promotion_record)
                    except Exception as e:
                        logger.debug("Genome promotion callback failed: %s", e)

                if remove_promoted:
                    self._stm.remove(entry.key)

        return promoted

    def _promote_to_kono(self, content: str) -> None:
        """Connection 1: Promote content to KONO substrate via subsystem bridge."""
        try:
            lib_dir = str(CLAUDE_DIR / "lib")
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from lib.subsystem_bridge import promote_to_kono
            promote_to_kono(content, memory_type='emergent')
        except (ImportError, Exception) as e:
            logger.debug("KONO promotion unavailable: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        """Get consolidation statistics."""
        candidates = self._stm.get_promotion_candidates(self._min_access_count)
        return {
            "pending_candidates": len(candidates),
            "min_access_count": self._min_access_count,
            "coherence_threshold": self._coherence_threshold,
            "coherence_available": self._coherence_checker is not None,
        }
