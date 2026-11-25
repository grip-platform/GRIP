#!/usr/bin/env python3
"""
KonoPruningMixin — memory pruning and cleanup.

Extracted from kono_substrate.py (H013 SOLID decomposition, Phase 2).
ISP boundary: prune (1 method). Separated from KonoEvolutionMixin
to keep each mixin at <=7 public methods.
"""

from typing import Any, Dict, List, Optional


class KonoPruningMixin:
    """
    Mixin: memory pruning and cleanup.

    ISP boundary: prune (1 method). Separated from KonoEvolutionMixin
    to keep each mixin at <=7 public methods.
    """

    def prune(
        self,
        project_path: Optional[str] = None,
        below_fitness: Optional[float] = None,
        memory_id: Optional[int] = None,
        include_permanent: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Prune memories from the substrate.

        Deletes from: embeddings, kono_attestations, vec_embeddings (best-effort).

        Args:
            project_path: Filter to specific project domain
            below_fitness: Delete memories below this fitness threshold
            memory_id: Delete a specific memory by ID
            include_permanent: Allow deleting permanent memories (default: protected)
            dry_run: Show candidates without deleting

        Returns:
            Dict with count of affected memories and details
        """
        conn = self.connect()

        where, params, error = self._resolve_prune_filter(
            project_path, below_fitness, memory_id, include_permanent
        )
        if error is not None:
            return error

        candidates = list(conn.execute(
            f"SELECT id, content, grid_x, fitness_score, is_permanent FROM embeddings WHERE {where}",
            tuple(params)
        ))

        if dry_run:
            return self._format_prune_dry_run(candidates)

        ids = [c[0] for c in candidates]
        if not ids:
            return {"pruned": 0}

        return self._execute_prune_deletes(conn, ids, where, params)

    def _resolve_prune_filter(
        self,
        project_path: Optional[str],
        below_fitness: Optional[float],
        memory_id: Optional[int],
        include_permanent: bool,
    ):
        """Build WHERE clause + params for prune. Returns (where, params, error_or_None)."""
        conditions: List[str] = []
        params: List[Any] = []

        if memory_id is not None:
            conditions.append("id = ?")
            params.append(memory_id)
        else:
            if project_path is not None:
                grid_x = self._resolve_project_grid_x(project_path)
                if grid_x is None:
                    return None, None, {"pruned": 0, "error": f"No domain found for: {project_path}"}
                conditions.append("grid_x = ?")
                params.append(grid_x)

            if below_fitness is not None:
                conditions.append("fitness_score < ?")
                params.append(below_fitness)

        if not include_permanent:
            conditions.append("is_permanent = 0")

        if not conditions:
            return None, None, {"pruned": 0, "error": "No filter specified (would delete everything)"}

        return " AND ".join(conditions), params, None

    @staticmethod
    def _format_prune_dry_run(candidates: List[Any]) -> Dict[str, Any]:
        """Format dry-run output with candidate metadata."""
        return {
            "dry_run": True,
            "candidates": len(candidates),
            "memories": [
                {
                    "id": c[0],
                    "content": c[1][:150] if c[1] else "",
                    "grid_x": c[2],
                    "fitness": c[3],
                    "is_permanent": bool(c[4]),
                }
                for c in candidates
            ],
        }

    @staticmethod
    def _execute_prune_deletes(conn, ids: List[Any], where: str, params: List[Any]) -> Dict[str, Any]:
        """Delete candidate rows from vec_embeddings, kono_attestations, embeddings."""
        placeholders = ",".join("?" * len(ids))

        vec_deleted = 0
        try:
            conn.execute(
                f"DELETE FROM vec_embeddings WHERE rowid IN ({placeholders})",
                tuple(ids)
            )
            vec_deleted = list(conn.execute("SELECT changes()"))[0][0]
        except Exception:
            pass  # Orphaned vectors are harmless

        conn.execute(
            f"DELETE FROM kono_attestations WHERE embedding_id IN ({placeholders})",
            tuple(ids)
        )
        att_deleted = list(conn.execute("SELECT changes()"))[0][0]

        conn.execute(
            f"DELETE FROM embeddings WHERE {where}",
            tuple(params)
        )
        emb_deleted = list(conn.execute("SELECT changes()"))[0][0]

        return {
            "pruned": emb_deleted,
            "attestations_deleted": att_deleted,
            "vectors_deleted": vec_deleted,
            "ids": ids,
        }
