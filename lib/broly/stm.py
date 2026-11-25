"""
Broly Short-Term Memory - In-memory store with recency tracking.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

STM is available at all power levels. It provides fast, session-scoped
key-value storage with access counting for consolidation decisions.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class STMEntry:
    """A single short-term memory entry."""
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ShortTermMemory:
    """
    In-memory key-value store with recency tracking.

    Features:
        - O(1) store/recall by key
        - Access counting (for consolidation promotion)
        - Capacity limit with LRU eviction
        - Bulk retrieval of promotion candidates
    """

    def __init__(self, capacity: int = 100):
        self._store: Dict[str, STMEntry] = {}
        self._capacity = capacity

    def store(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Store a value in STM."""
        if key in self._store:
            entry = self._store[key]
            entry.value = value
            entry.last_accessed = time.time()
            entry.access_count += 1
            if metadata:
                entry.metadata.update(metadata)
        else:
            if len(self._store) >= self._capacity:
                self._evict_lru()
            self._store[key] = STMEntry(
                key=key,
                value=value,
                metadata=metadata or {},
            )

    def recall(self, key: str) -> Optional[Any]:
        """Recall a value from STM. Returns None if not found."""
        entry = self._store.get(key)
        if entry is None:
            return None
        entry.last_accessed = time.time()
        entry.access_count += 1
        return entry.value

    def has(self, key: str) -> bool:
        """Check if key exists in STM."""
        return key in self._store

    def get_entry(self, key: str) -> Optional[STMEntry]:
        """Get the full STM entry (without incrementing access count)."""
        return self._store.get(key)

    def get_promotion_candidates(self, min_access_count: int = 3) -> List[STMEntry]:
        """Get entries accessed enough times to warrant LTM promotion."""
        return [
            entry for entry in self._store.values()
            if entry.access_count >= min_access_count
        ]

    def remove(self, key: str) -> bool:
        """Remove an entry from STM."""
        if key in self._store:
            del self._store[key]
            return True
        return False

    def clear(self) -> None:
        """Clear all STM entries."""
        self._store.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get STM statistics."""
        entries = list(self._store.values())
        return {
            "count": len(entries),
            "capacity": self._capacity,
            "total_accesses": sum(e.access_count for e in entries),
            "promotion_candidates": len(self.get_promotion_candidates()),
        }

    def _evict_lru(self) -> None:
        """Evict the least recently accessed entry."""
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        del self._store[lru_key]
