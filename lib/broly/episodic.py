"""
Broly Episodic Memory - Session history search + semantic compression.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Episodic memory retrieves past session experiences via:
    1. history-search.py hybrid search (keyword + semantic)
    2. semantic-compressor.py for context enrichment

Available at cluster+ power levels.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path.home() / ".claude"


class EpisodicMemory:
    """
    Session history retrieval with semantic compression.

    Adapts existing GRIP infrastructure:
        - history-search.py: hybrid_search() for finding past sessions
        - semantic-compressor.py: SemanticCompressor.compress() for enrichment
    """

    def __init__(self, project_path: Optional[Path] = None):
        self._project_path = project_path or Path.cwd()
        self._history_search = None
        self._compressor = None
        self._init_backends()

    def _init_backends(self) -> None:
        """Initialize history search and compressor backends."""
        lib_dir = str(CLAUDE_DIR / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)

        # History search
        try:
            import importlib
            hs = importlib.import_module("history-search")
            self._history_search = hs
        except (ImportError, Exception):
            pass

        # Semantic compressor
        try:
            import importlib
            sc = importlib.import_module("semantic-compressor")
            self._compressor = sc.SemanticCompressor(project_path=self._project_path)
        except (ImportError, Exception):
            pass

    def recall(
        self,
        query: str,
        limit: int = 10,
        enrich: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search episodic memory for past session experiences.

        Args:
            query: Search query
            limit: Max results
            enrich: Whether to enrich results with compressed context

        Returns:
            List of matching episodic memories
        """
        results = []

        # Primary: hybrid search across session history
        if self._history_search:
            try:
                hits = self._history_search.hybrid_search(
                    query=query,
                    project_path=str(self._project_path),
                    limit=limit,
                )
                for hit in hits:
                    results.append({
                        "content": hit.get("content", ""),
                        "session_id": hit.get("session_id", ""),
                        "timestamp": hit.get("timestamp", ""),
                        "score": hit.get("final_score", 0.0),
                        "source": hit.get("source", "hybrid"),
                    })
            except Exception:
                pass

        # Optional: enrich top results with compressed context
        if enrich and self._compressor and results:
            for result in results[:3]:
                session_id = result.get("session_id", "")
                if session_id:
                    try:
                        compressed = self._compressor.compress(
                            session_id=session_id,
                            max_tokens=500,
                            strategies=["decisions"],
                        )
                        result["enriched_context"] = compressed.context_text
                    except Exception:
                        pass

        # Connection 6: Feed recall results into session reflection
        if results:
            self._signal_reflection(query, results[:3])

        return results[:limit]

    def get_compressed_session(
        self,
        session_id: str = "latest",
        max_tokens: int = 2000,
        strategies: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Get compressed context for a specific session.

        Args:
            session_id: Session UUID or "latest"
            max_tokens: Token budget for compression
            strategies: Compression strategies (identity/decisions/context/all)

        Returns:
            Compressed context text or None
        """
        if not self._compressor:
            return None

        try:
            result = self._compressor.compress(
                session_id=session_id,
                max_tokens=max_tokens,
                strategies=strategies or ["all"],
            )
            return result.context_text
        except Exception:
            return None

    def _signal_reflection(
        self,
        query: str,
        top_results: List[Dict[str, Any]],
    ) -> None:
        """Connection 6: Signal session reflection when episodic recall finds relevant history.

        When past episodes are recalled, this signals the reflection subsystem
        to compare current session patterns against historical ones. This
        enables structured learning from cross-session experience.
        """
        try:
            lib_dir = str(CLAUDE_DIR / "lib")
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from converge_metrics import record_reflection_signal
            record_reflection_signal(
                trigger="episodic_recall",
                query=query,
                matched_sessions=[r.get("session_id", "") for r in top_results],
                match_scores=[r.get("score", 0.0) for r in top_results],
            )
        except (ImportError, AttributeError, Exception):
            pass  # Reflection subsystem optional

    def get_stats(self) -> Dict[str, Any]:
        """Get episodic memory statistics."""
        return {
            "history_search_available": self._history_search is not None,
            "compressor_available": self._compressor is not None,
            "project_path": str(self._project_path),
        }
