"""
Broly Semantic Memory - Hybrid search + KONO spatial expansion.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Semantic memory provides concept-level retrieval via:
    1. EmbeddingEngine.search_hybrid() for vector + FTS5 search
    2. KONO MemorySubstrate.retrieve() for spatial relationship expansion

Available at cluster+ power levels.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path.home() / ".claude"


class SemanticMemory:
    """
    Concept-level memory retrieval with spatial expansion.

    Adapts existing GRIP infrastructure:
        - EmbeddingEngine: hybrid search across all embedding types
        - KONO MemorySubstrate: spatial coordinate retrieval for related concepts
    """

    def __init__(self):
        self._engine = None
        self._substrate = None
        self._engine_available = False
        self._kono_available = False
        self._init_backends()

    def _init_backends(self) -> None:
        """Initialize embedding engine and KONO substrate."""
        lib_dir = str(CLAUDE_DIR / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)

        # Embedding engine
        try:
            from embeddings import get_engine  # W5(a): dispatch via get_engine()
            self._engine = get_engine()
            self._engine_available = True
        except (ImportError, Exception):
            pass

        # KONO substrate
        try:
            from kono_substrate import MemorySubstrate
            self._substrate = MemorySubstrate()
            self._kono_available = True
        except (ImportError, Exception):
            pass

    def store(
        self,
        content: str,
        memory_type: str = 'episodic',
        project_path: Optional[str] = None,
    ) -> Optional[int]:
        """
        Store a memory via KONO substrate (full embedding pipeline).

        Available at CLUSTER+ power levels.

        Args:
            content: Text content to store
            memory_type: Type of memory (episodic, factual, procedural, emergent)
            project_path: Project context path

        Returns:
            Embedding ID if stored, None on failure
        """
        if self._kono_available and self._substrate:
            try:
                return self._substrate.store_session_memory(
                    content=content,
                    memory_type=memory_type,
                    project_path=project_path,
                )
            except Exception:
                pass
        return None

    def recall(
        self,
        query: str,
        limit: int = 10,
        embed_type: Optional[str] = None,
        expand_spatial: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Search semantic memory for conceptually related content.

        Delegates to MemorySubstrate.recall() for unified hybrid+spatial search.
        Falls back to direct engine search if KONO substrate unavailable.

        Args:
            query: Search query
            limit: Max results
            embed_type: Filter by embedding type (e.g., "concept", "ltm")
            expand_spatial: Whether to expand via KONO spatial search

        Returns:
            List of matching semantic memories
        """
        # Prefer substrate.recall() - single implementation for DRY
        if self._kono_available and self._substrate:
            try:
                # Map embed_type to dimension number if provided
                dim = None
                if embed_type:
                    dim_map = {
                        "factual": 1, "procedural": 4,
                        "episodic": 7, "emergent": 10,
                    }
                    dim = dim_map.get(embed_type)

                results = self._substrate.recall(
                    query=query,
                    limit=limit,
                    dimension=dim,
                    expand_spatial=expand_spatial,
                )
                # Normalize to Broly's expected format (preserving KONO spatial data)
                return [
                    {
                        "content": r.get("content", ""),
                        "type": r.get("type", r.get("dimension_name", "")),
                        "score": r.get("score", 0.0),
                        "metadata": r.get("metadata"),
                        "source": r.get("source", "hybrid_search"),
                        "fitness": r.get("fitness", 0),
                        "is_permanent": r.get("is_permanent", False),
                        "position": r.get("position"),
                        "dimension": r.get("dimension"),
                        "attestation_count": r.get("attestation_count", 0),
                    }
                    for r in results
                ]
            except Exception:
                pass

        # Fallback: direct engine search (no KONO spatial expansion)
        results = []
        if self._engine_available and self._engine:
            try:
                hits = self._engine.search_hybrid(
                    query_text=query,
                    embed_type=embed_type,
                    limit=limit,
                )
                for hit in hits:
                    results.append({
                        "content": hit.get("content", ""),
                        "type": hit.get("type", ""),
                        "score": hit.get("rrf_score", hit.get("similarity", 0.0)),
                        "metadata": hit.get("metadata"),
                        "source": "hybrid_search",
                    })
            except Exception:
                pass

        return results[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get semantic memory statistics."""
        stats = {
            "engine_available": self._engine_available,
            "kono_available": self._kono_available,
        }

        if self._engine_available and self._engine:
            try:
                engine_stats = self._engine.get_stats()
                stats["embedding_count"] = engine_stats.get("total_embeddings", 0)
            except Exception:
                pass

        if self._kono_available and self._substrate:
            try:
                kono_stats = self._substrate.get_stats()
                stats["kono_domains"] = kono_stats.get("domains", 0)
                stats["kono_topics"] = kono_stats.get("topics", 0)
            except Exception:
                pass

        return stats
