"""
Broly Unified Memory Router - Routes operations across 4 memory types.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

BrolyMemory is the single entry point for all memory operations.
It enforces power-level gating and delegates to the appropriate backend.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from .power_levels import PowerLevel, can_access_memory
from .stm import ShortTermMemory
from .ltm import LongTermMemory
from .episodic import EpisodicMemory
from .semantic import SemanticMemory


class MemoryAccessDenied(PermissionError):
    """Raised when a memory type is not available at the current power level."""
    pass


class BrolyMemory:
    """
    Unified memory router across 4 memory types.

    Routes store/recall operations to the appropriate backend
    based on power level gating.

    Memory Types:
        - STM: In-memory session-scoped (all levels)
        - LTM: Persistent MEMORY.md + embeddings (fork+)
        - Episodic: Session history search (cluster+)
        - Semantic: Concept-level hybrid + KONO spatial (cluster+)
    """

    def __init__(
        self,
        power_level: PowerLevel = PowerLevel.THREAD,
        project_path: Optional[Path] = None,
        memory_file: Optional[Path] = None,
    ):
        self._level = power_level
        self._stm = ShortTermMemory()
        self._ltm: Optional[LongTermMemory] = None
        self._episodic: Optional[EpisodicMemory] = None
        self._semantic: Optional[SemanticMemory] = None

        # Lazily initialize backends based on power level
        if can_access_memory(power_level, "ltm"):
            self._ltm = LongTermMemory(memory_file=memory_file)

        if can_access_memory(power_level, "episodic"):
            self._episodic = EpisodicMemory(project_path=project_path)

        if can_access_memory(power_level, "semantic"):
            self._semantic = SemanticMemory()

    @property
    def power_level(self) -> PowerLevel:
        return self._level

    def _check_access(self, memory_type: str) -> None:
        """Raise if memory type not accessible at current power level."""
        if not can_access_memory(self._level, memory_type):
            raise MemoryAccessDenied(
                f"Memory type '{memory_type}' requires higher power level. "
                f"Current: {self._level.value}"
            )

    # =========================================================================
    # STORE
    # =========================================================================

    def store(
        self,
        key: str,
        value: Any,
        memory_type: str = "stm",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a memory in the specified backend.

        Args:
            key: Memory key/topic
            value: Memory content
            memory_type: Target backend (stm, ltm, semantic)
            metadata: Optional metadata

        Returns:
            True if stored successfully
        """
        self._check_access(memory_type)

        if memory_type == "stm":
            self._stm.store(key, value, metadata)
            return True

        if memory_type == "ltm" and self._ltm:
            return self._ltm.store(key, str(value), metadata)

        if memory_type == "semantic" and self._semantic:
            result = self._semantic.store(
                content=str(value),
                memory_type=metadata.get('memory_type', 'episodic') if metadata else 'episodic',
                project_path=str(metadata.get('project_path', '')) if metadata else None,
            )
            return result is not None

        return False

    # =========================================================================
    # RECALL
    # =========================================================================

    def recall(
        self,
        query: str,
        memory_type: str = "stm",
        limit: int = 5,
        **kwargs,
    ) -> Any:
        """
        Recall from the specified memory backend.

        For STM: exact key lookup (returns value or None)
        For LTM/Episodic/Semantic: search-based recall (returns list)
        """
        self._check_access(memory_type)

        if memory_type == "stm":
            return self._stm.recall(query)

        if memory_type == "ltm" and self._ltm:
            return self._ltm.recall(query, limit=limit)

        if memory_type == "episodic" and self._episodic:
            return self._episodic.recall(
                query,
                limit=limit,
                enrich=kwargs.get("enrich", False),
            )

        if memory_type == "semantic" and self._semantic:
            return self._semantic.recall(
                query,
                limit=limit,
                embed_type=kwargs.get("embed_type"),
                expand_spatial=kwargs.get("expand_spatial", True),
            )

        return None

    # =========================================================================
    # MULTI-MEMORY SEARCH
    # =========================================================================

    def recall_all(
        self,
        query: str,
        limit_per_type: int = 3,
    ) -> Dict[str, Any]:
        """
        Search across all accessible memory types.

        Returns results keyed by memory type.
        """
        results = {}

        # STM always available
        stm_value = self._stm.recall(query)
        if stm_value is not None:
            results["stm"] = stm_value

        # LTM
        if self._ltm and can_access_memory(self._level, "ltm"):
            try:
                results["ltm"] = self._ltm.recall(query, limit=limit_per_type)
            except Exception:
                pass

        # Episodic
        if self._episodic and can_access_memory(self._level, "episodic"):
            try:
                results["episodic"] = self._episodic.recall(query, limit=limit_per_type)
            except Exception:
                pass

        # Semantic
        if self._semantic and can_access_memory(self._level, "semantic"):
            try:
                results["semantic"] = self._semantic.recall(query, limit=limit_per_type)
            except Exception:
                pass

        return results

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all accessible memory backends."""
        stats = {
            "power_level": self._level.value,
            "stm": self._stm.get_stats(),
        }

        if self._ltm:
            stats["ltm"] = self._ltm.get_stats()
        if self._episodic:
            stats["episodic"] = self._episodic.get_stats()
        if self._semantic:
            stats["semantic"] = self._semantic.get_stats()

        return stats
