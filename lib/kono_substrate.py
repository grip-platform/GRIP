#!/usr/bin/env python3
"""
KONO Memory Substrate for GRIP
Orchestrates 4D memory storage, dimension routing, and evolutionary instance management.

This is the main interface for KONO operations.

Part of GRIP-KONO implementation (Gen 310).
"""

import apsw
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ensure lib/ is on sys.path for sibling imports (fixes ModuleNotFoundError
# when imported as `from lib.kono_substrate import ...` vs CLI mode)
import sys as _sys
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in _sys.path:
    _sys.path.insert(0, _lib_dir)
from kono import (
    InstanceDNA, GRIPInstance, Position4D,
    route_dimension, DIMENSION_ROUTES, DEFAULT_DISTANCE_WEIGHTS
)
from kono_counters import (
    SessionCounterStore, is_operational_content, CLAUDE_DIR,
)
from kono_evolution import KonoEvolutionMixin
from kono_lifecycle import KonoLifecycleMixin
from kono_pruning import KonoPruningMixin
from kono_query import KonoQueryMixin

DB_PATH = CLAUDE_DIR / "embeddings.db"

# FAC half of MAC/FAC (lib/smt/macfac.py). The MAC sweep (vector + FTS + RRF)
# scores candidates; the top window is then re-ordered by structural alignment
# against the query. Default on; GRIP_SMT_FAC=0 keeps plain MAC order.
FAC_ALPHA = 0.6        # weight of the normalised MAC score in the fusion
FAC_MIN_WINDOW = 20    # never re-rank fewer than this many head candidates


def _fac_candidates(head: List[Dict[str, Any]], extract) -> list:
    """(position id, MAC score normalised to the window's top, Structure).

    RRF scores sit in a narrow 0.01-0.03 band while structural scores span
    [0, 1]; dividing by the window's top keeps the two axes comparable and
    preserves the MAC ranking (the top hit is 1.0). Positions, not memory
    ids, key the candidates so nothing depends on ids being distinct strings.
    """
    top = max((float(r.get("score") or 0.0) for r in head), default=0.0)
    return [
        (f"{i:06d}",
         (float(r.get("score") or 0.0) / top) if top > 0 else 0.0,
         extract(str(r.get("id")), r.get("content") or ""))
        for i, r in enumerate(head)
    ]


def _fac_rerank(results: List[Dict[str, Any]], query: str, window: int) -> List[Dict[str, Any]]:
    """Structural re-rank of the top `window` MAC hits; MAC order past it.

    Engages only when the query itself carries a relation: with none there
    is nothing to align, and entity overlap would merely wear a structural
    badge. `score` keeps the MAC value (the min_score contract); each
    re-ranked hit gains `structural_score`, `fused_score` and
    `fac_alignment_size`. Fails open to MAC order on any error, logged — a
    re-ranker must never take recall down with it.
    """
    try:
        from smt import enabled
        if not enabled("FAC"):
            return results
        from smt.macfac import fac_rerank
        from smt.text_structure import extract_structure, has_relations
        q = extract_structure("query", query)
        if not has_relations(q):
            return results
        head, tail = results[:window], results[window:]
        ranked = fac_rerank(q, _fac_candidates(head, extract_structure),
                            top_n=len(head), alpha=FAC_ALPHA)
        for fr in ranked:
            head[int(fr.item_id)].update(
                structural_score=fr.structural_score,
                fused_score=fr.fused_score,
                fac_alignment_size=fr.alignment_size,
            )
        return [head[int(fr.item_id)] for fr in ranked] + tail
    except Exception as exc:  # fail-open to MAC order, never silently
        # `logger` is the module logger defined at the top of this file; the
        # except block covers the imports above it as well as the re-rank, so
        # a missing lib/smt is a warning and MAC order, not a crash.
        logger.warning("FAC re-rank skipped (%s: %s); MAC order kept",
                       type(exc).__name__, exc)
        return results


class MemorySubstrate(KonoLifecycleMixin, KonoEvolutionMixin, KonoPruningMixin, KonoQueryMixin):
    """
    KONO Memory Substrate - orchestrates memory storage and retrieval.

    Responsibilities:
        - Route memories to appropriate dimensions
        - Manage 4D spatial coordinates
        - Track instance fitness and reproduction
        - Handle consensus-based permanence
        - Run metabolism (fitness decay) and consolidation

    Inherits:
        KonoLifecycleMixin  -- connect, init_session, load_instance, save_instance, close
        KonoEvolutionMixin  -- attest, attest_recalled, mine, consensus,
                               run_metabolism, evolve, consolidate
        KonoPruningMixin    -- prune
        KonoQueryMixin      -- recall, get_compact_stats, get_session_summary,
                               format_session_summary, get_stats, get_permanence_events,
                               store_session_summary
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        counters: Optional[SessionCounterStore] = None,
    ):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[apsw.Connection] = None
        self.current_instance: Optional[GRIPInstance] = None
        self._decay_config: Optional[Dict[str, float]] = None

        # Session-scoped counters (injected for test isolation, default for prod)
        self._counters = counters or SessionCounterStore()
        self._permanence_events: List[Dict[str, Any]] = []

    def _ensure_instance(self) -> Optional[GRIPInstance]:
        """
        Ensure an active instance exists for attestation.

        If no current_instance is set, creates an ephemeral session instance.
        This is the critical link that was missing - without an active instance,
        attest() silently no-ops and no memories ever reach permanence.

        Returns:
            Active GRIPInstance, or None if DB unavailable
        """
        if self.current_instance:
            return self.current_instance

        try:
            instance_id = f"auto-{uuid.uuid4().hex[:12]}"
            return self.init_session(
                instance_id=instance_id,
                generation=0,
                project_path=str(Path.cwd()),
            )
        except Exception:
            return None

    def _get_decay_config(self) -> Dict[str, float]:
        """Load decay configuration from database."""
        if self._decay_config:
            return self._decay_config

        conn = self.connect()
        result = list(conn.execute(
            "SELECT param_name, param_value FROM kono_decay_config"
        ))
        self._decay_config = {row[0]: row[1] for row in result}
        return self._decay_config

    # =========================================================================
    # DIMENSION ROUTING
    # =========================================================================

    def route(self, memory_type: str) -> Tuple[int, int, int]:
        """
        Route content type to dimension range.

        Args:
            memory_type: Type of memory (factual, procedural, episodic, emergent)

        Returns:
            Tuple of (start_dim, mid_dim, end_dim)
        """
        dims = DIMENSION_ROUTES.get(memory_type, (0, 1, 2))
        return dims

    def classify_memory(
        self,
        text: str,
        embed_type: str
    ) -> Tuple[str, int]:
        """
        Classify memory type and determine dimension.

        Args:
            text: Content text
            embed_type: Embedding type from storage

        Returns:
            Tuple of (memory_type, dimension)
        """
        # Check database rules first
        conn = self.connect()
        result = list(conn.execute(
            """SELECT dimension_start, dimension_end
               FROM kono_dimension_rules
               WHERE content_type = ?""",
            (embed_type,)
        ))

        if result:
            dim_start, dim_end = result[0]
            # Use middle of range
            dimension = (dim_start + dim_end) // 2
            memory_type = self._dimension_to_type(dimension)
            return (memory_type, dimension)

        # Fallback to default routing
        dimension = route_dimension(embed_type)
        memory_type = self._dimension_to_type(dimension)
        return (memory_type, dimension)

    @staticmethod
    def _instance_from_row(row: tuple) -> GRIPInstance:
        """Construct a GRIPInstance from a kono_instances DB row.

        Row format: (id, generation, parent_id, skills_json, patterns_json,
                      preferences_json, project_context, fitness,
                      [created_at, last_active_at])
        """
        kwargs = dict(
            id=row[0],
            generation=row[1],
            parent_id=row[2],
            dna=InstanceDNA(
                skills=json.loads(row[3]) if row[3] else [],
                patterns=json.loads(row[4]) if row[4] else {},
                preferences=json.loads(row[5]) if row[5] else {},
                project_context=row[6] or "",
            ),
            fitness=row[7],
        )
        if len(row) > 8 and row[8]:
            kwargs["created_at"] = datetime.fromisoformat(row[8])
        if len(row) > 9 and row[9]:
            kwargs["last_active_at"] = datetime.fromisoformat(row[9])
        return GRIPInstance(**kwargs)

    def _dimension_to_type(self, dimension: int) -> str:
        """Map dimension number to memory type."""
        if dimension <= 2:
            return 'factual'
        elif dimension <= 5:
            return 'procedural'
        elif dimension <= 8:
            return 'episodic'
        else:
            return 'emergent'

    # =========================================================================
    # DOMAIN AND TOPIC MANAGEMENT
    # =========================================================================

    def get_or_create_domain(self, project_path: str) -> int:
        """
        Get or create x-coordinate for a project.

        Args:
            project_path: Project directory path

        Returns:
            x-coordinate for this domain
        """
        conn = self.connect()

        # Check existing
        result = list(conn.execute(
            "SELECT x_coord FROM kono_domains WHERE path = ?",
            (project_path,)
        ))

        if result:
            return result[0][0]

        # Create new - find max x_coord and increment
        max_x = list(conn.execute(
            "SELECT COALESCE(MAX(x_coord), -1) FROM kono_domains"
        ))[0][0]

        new_x = max_x + 1
        name = Path(project_path).name if project_path else "unknown"

        conn.execute(
            "INSERT INTO kono_domains (path, x_coord, name) VALUES (?, ?, ?)",
            (project_path, new_x, name)
        )

        return new_x

    def get_or_create_topic(self, topic_name: str, keywords: str = "") -> int:
        """
        Get or create y-coordinate for a topic.

        Args:
            topic_name: Topic/concept name
            keywords: Associated keywords

        Returns:
            y-coordinate for this topic
        """
        conn = self.connect()

        # Check existing
        result = list(conn.execute(
            "SELECT y_coord FROM kono_topics WHERE name = ?",
            (topic_name,)
        ))

        if result:
            return result[0][0]

        # Create new
        max_y = list(conn.execute(
            "SELECT COALESCE(MAX(y_coord), -1) FROM kono_topics"
        ))[0][0]

        new_y = max_y + 1

        conn.execute(
            "INSERT INTO kono_topics (name, y_coord, keywords) VALUES (?, ?, ?)",
            (topic_name, new_y, keywords)
        )

        return new_y

    # =========================================================================
    # MEMORY STORAGE
    # =========================================================================

    def store(
        self,
        embedding_id: int,
        position: Position4D,
        dimension: int,
        instance_id: Optional[str] = None,
        namespace: Optional[str] = None
    ) -> None:
        """
        Update embedding with KONO spatial coordinates.

        Args:
            embedding_id: ID of existing embedding
            position: 4D spatial position
            dimension: Memory dimension
            instance_id: Origin instance ID
            namespace: Memory namespace for org isolation (default: GRIP_ORG:default).
                       Stored for future filtering when schema supports it.
        """
        # Resolve namespace — now persisted in DB column (Gen 432)
        ns = namespace or (
            os.environ.get("GRIP_ORG", "codetonight") + ":default"
        )
        conn = self.connect()

        conn.execute("""
            UPDATE embeddings SET
                grid_x = ?,
                grid_y = ?,
                grid_z = ?,
                dimension = ?,
                origin_instance_id = ?,
                namespace = ?
            WHERE id = ?
        """, (
            int(position.x),
            int(position.y),
            int(position.z),
            dimension,
            instance_id or (self.current_instance.id if self.current_instance else None),
            ns,
            embedding_id
        ))

        self._counters.stored += 1

    def retrieve(
        self,
        query_position: Position4D,
        dimension: Optional[int] = None,
        radius: float = 10.0,
        limit: int = 10,
        min_fitness: float = 0.0,
        namespace: Optional[str] = None,
        include_merged: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories near a spatial position.

        Args:
            query_position: Center of search
            dimension: Filter by dimension (optional)
            radius: Search radius
            limit: Max results
            min_fitness: Minimum fitness threshold
            namespace: Memory namespace for org isolation (default: GRIP_ORG:default).
                       Reserved for future filtering when schema supports it.
            include_merged: Whether to include memories from merged namespaces
                           (requires active brain merge). Reserved for future use.

        Returns:
            List of matching embeddings with distance
        """
        conn = self.connect()
        accessible_ns = self._resolve_retrieve_namespaces(namespace, include_merged)
        sql, params = self._build_retrieve_query(
            query_position, dimension, radius, min_fitness, accessible_ns, limit
        )
        rows = list(conn.execute(sql, params))
        result = self._format_retrieve_results(rows, query_position, radius, limit)
        self._counters.recalled += len(result)
        return result

    @staticmethod
    def _resolve_retrieve_namespaces(namespace: Optional[str], include_merged: bool) -> List[str]:
        """Resolve own namespace plus any active brain-merge namespaces."""
        ns = namespace or (
            os.environ.get("GRIP_ORG", "codetonight") + ":default"
        )
        accessible_ns = [ns]
        if include_merged:
            try:
                from brain_merge import BrainMergeManager
                mgr = BrainMergeManager()
                accessible_ns.extend(mgr.get_active_merges(ns))
            except (ImportError, Exception):
                pass
        return accessible_ns

    @staticmethod
    def _squared_distance_sql() -> str:
        """SQL mirroring Position4D.distance, squared (monotone-equivalent).

        Squared rather than rooted so no sqrt() builtin is required, and
        weights come from DEFAULT_DISTANCE_WEIGHTS so this expression cannot
        drift from the Python metric. Named parameters (:qx..:qt) let each
        coordinate bind once.

        The t-term uses the query's own t against 0, because
        _format_retrieve_results builds every row position with t=0. It is
        constant across rows (so it cannot affect ordering) but it does count
        toward the radius, exactly as the Python metric does.
        """
        wx, wy, wz, wt = DEFAULT_DISTANCE_WEIGHTS
        # Association matches Python's ``w * (d ** 2)`` exactly -- writing it
        # as ``(w * d) * d`` would differ by an ULP for non-power-of-two
        # weights and desynchronise the radius cutoff from Position4D.
        return (
            f"({wx} * ((:qx - grid_x) * (:qx - grid_x))"
            f" + {wy} * ((:qy - grid_y) * (:qy - grid_y))"
            f" + {wz} * ((:qz - grid_z) * (:qz - grid_z))"
            f" + {wt} * (:qt * :qt))"
        )

    @staticmethod
    def _build_retrieve_query(
        query_position: Position4D,
        dimension: Optional[int],
        radius: float,
        min_fitness: float,
        accessible_ns: List[str],
        limit: int,
    ):
        """Build the spatial-search SQL + parameter dict.

        Ranks by DISTANCE, not fitness. Ordering by fitness inside a bounding
        box and discarding out-of-radius rows afterwards evicted near rows
        before the radius filter ever saw them; the nearest results were only
        recoverable by fetching most of the table. Distance is computed and
        ordered in SQL, so LIMIT now cuts the farthest rows rather than
        arbitrary ones.

        Ties are broken by fitness DESC then id ASC. id is
        INTEGER PRIMARY KEY AUTOINCREMENT, hence unique, so the ordering is
        a total order and identical input yields identical output.
        """
        dim_filter = "AND dimension = :dim" if dimension is not None else ""
        ns_keys = [f"ns{i}" for i in range(len(accessible_ns))]
        ns_placeholders = ",".join(f":{k}" for k in ns_keys)
        # Legacy rows stored before the namespace feature (P2a) carry NULL
        # namespace and must stay reachable (SQL NULL never matches IN).
        ns_filter = f"AND (namespace IN ({ns_placeholders}) OR namespace IS NULL)"

        dist2 = MemorySubstrate._squared_distance_sql()

        params: Dict[str, Any] = {
            "qx": query_position.x,
            "qy": query_position.y,
            "qz": query_position.z,
            "qt": query_position.t,
            "box_x_lo": query_position.x - radius,
            "box_x_hi": query_position.x + radius,
            "box_y_lo": query_position.y - radius,
            "box_y_hi": query_position.y + radius,
            "min_fitness": min_fitness,
            "radius2": radius * radius,
            "limit": limit,
        }
        params.update(dict(zip(ns_keys, accessible_ns)))
        if dimension is not None:
            params["dim"] = dimension

        sql = f"""
            SELECT id, content, metadata, grid_x, grid_y, grid_z,
                   dimension, fitness_score, is_permanent, created_at
            FROM (
                SELECT id, content, metadata, grid_x, grid_y, grid_z,
                       dimension, fitness_score, is_permanent, created_at,
                       {dist2} AS dist2
                FROM embeddings
                WHERE grid_x BETWEEN :box_x_lo AND :box_x_hi
                  AND grid_y BETWEEN :box_y_lo AND :box_y_hi
                  AND fitness_score >= :min_fitness
                  {ns_filter}
                  {dim_filter}
            )
            WHERE dist2 <= :radius2
            ORDER BY dist2 ASC, fitness_score DESC, id ASC
            LIMIT :limit
        """
        return sql, params

    @staticmethod
    def _format_retrieve_results(
        rows: List[Any], query_position: Position4D, radius: float, limit: int
    ) -> List[Dict[str, Any]]:
        """Convert DB rows to result dicts.

        Rows now arrive already radius-filtered and distance-ordered from
        SQL, so the radius check and sort here are a redundant guard rather
        than the primary filter -- they are kept because this remains the
        only place the public ``distance`` value is computed, and Python's
        sort is stable, so it preserves SQL's deterministic tiebreak.
        """
        output = []
        for row in rows:
            pos = Position4D(x=row[3], y=row[4], z=row[5], t=0)
            distance = query_position.distance(pos)

            if distance <= radius:
                output.append({
                    "id": row[0],
                    "content": row[1],
                    "metadata": json.loads(row[2]) if row[2] else None,
                    "position": pos.to_tuple(),
                    "dimension": row[6],
                    "fitness": row[7],
                    "is_permanent": bool(row[8]),
                    "created_at": row[9],
                    "distance": distance
                })

        output.sort(key=lambda x: x["distance"])
        return output[:limit]

    # =========================================================================
    # SESSION MEMORY STORAGE (Active KONO Usage)
    # =========================================================================

    def _is_duplicate_content(self, content: str, memory_type: str,
                              hours: int = 4, threshold: float = 0.7) -> bool:
        """Check if similar content was stored recently (word overlap)."""
        try:
            conn = self.connect()
            rows = conn.execute(
                """SELECT content FROM embeddings
                   WHERE type = ? AND created_at > datetime('now', ?)
                   ORDER BY created_at DESC LIMIT 20""",
                (memory_type, f'-{hours} hours'),
            ).fetchall()
            if not rows:
                return False
            new_words = set(content.lower().split())
            if not new_words:
                return False
            for (existing,) in rows:
                existing_words = set(existing.lower().split())
                if not existing_words:
                    continue
                overlap = len(new_words & existing_words)
                smaller = min(len(new_words), len(existing_words))
                if smaller > 0 and overlap / smaller >= threshold:
                    return True
            return False
        except Exception:
            return False

    def store_session_memory(
        self,
        content: str,
        memory_type: str = 'episodic',
        project_path: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> Optional[int]:
        """
        Store a memory via the full embedding pipeline (sqlite-vec + lembed/ollama).

        Generates vector embedding, stores in embeddings table, then assigns
        KONO spatial coordinates for 4D retrieval. Ensures an active instance
        exists so the memory receives its first attestation. Skips operational
        content (#770) and >70% word-overlap episodic dups within 4h (#1036).

        Args:
            content: Text content to store
            memory_type: Type of memory (episodic, factual, procedural, emergent)
            project_path: Project context path

        Returns:
            Embedding ID if stored, None on failure
        """
        if is_operational_content(content):
            logger.debug("Skipping operational content: %s", content[:60])
            return None

        is_session_summary = content.startswith('[session_summary]')
        if memory_type == 'episodic' and not is_session_summary:
            if self._is_duplicate_content(content, memory_type):
                logger.debug("Skipping duplicate episodic content: %s", content[:60])
                return None

        return self._run_store_pipeline(content, memory_type, project_path, namespace)

    def _run_store_pipeline(
        self, content: str, memory_type: str, project_path: Optional[str],
        namespace: Optional[str] = None,
    ) -> Optional[int]:
        """Generate embedding, persist row, mine spatial coords + attestation. Returns id or None.

        ``namespace`` (opt-in): when set, the row is tagged with that namespace
        instead of the default org namespace. Test fixtures use
        ``namespace='test:integration'`` so their rows are excluded from default
        recall (which only includes the operator's org + legacy + merged
        namespaces in ``_resolve_accessible_namespaces``). Defence-in-depth
        with ``is_operational_content`` — the regex catches by content pattern,
        the namespace catches by structural property.
        """
        try:
            from embeddings import get_engine
            from embeddings.storage import store_embedding
        except ImportError:
            return None

        self._ensure_instance()
        resolved_path = project_path or str(Path.cwd())

        try:
            engine = get_engine()
            embedding_id = store_embedding(
                engine,
                text=content,
                embed_type=memory_type,
                project_path=resolved_path,
                namespace=namespace,
            )
        except Exception as exc:
            # Mirror the hybrid-search read path (see recall): ONLY the
            # embedding-backend ops degrade gracefully. When the backend is
            # unavailable at runtime — e.g. the ollama daemon is down, raising
            # RuntimeError("Ollama daemon unreachable") from _ensure_model —
            # storing a memory returns None, never crashing a /save or
            # session-end hook over a missing local daemon. "None on failure".
            logger.debug("store pipeline backend unavailable: %s", exc)
            return None

        # store_embedding succeeded. mine() + the counter run OUTSIDE the guard
        # so a genuine fault here surfaces as a real error instead of being
        # masked as backend-unavailability (AGORA PR #3651, Speaker C).
        self.mine(
            content=content,
            embed_type=memory_type,
            embedding_id=embedding_id,
            project_path=resolved_path,
        )

        self._counters.stored += 1
        return embedding_id

    # =========================================================================
    # SEMANTIC RECALL HELPERS
    # =========================================================================

    def _resolve_project_grid_x(self, project_path: str) -> Optional[int]:
        """
        Resolve a project path to its grid_x coordinate via kono_domains.

        Uses longest-prefix match to handle subdirectory paths.

        Args:
            project_path: Absolute project path

        Returns:
            grid_x coordinate or None if no matching domain
        """
        conn = self.connect()
        domains = list(conn.execute(
            "SELECT path, x_coord FROM kono_domains ORDER BY LENGTH(path) DESC"
        ))

        for domain_path, x_coord in domains:
            if project_path.startswith(domain_path):
                return x_coord

        return None

    def _resolve_accessible_namespaces(
        self, namespace: Optional[str], include_merged: bool
    ) -> List[str]:
        """Resolve the set of namespaces to search across.

        Includes current org namespace, legacy namespace (codetonight),
        and any actively merged namespaces from brain_merge.
        """
        ns = namespace or (
            os.environ.get("GRIP_ORG", "codetonight") + ":default"
        )
        accessible_ns = [ns]
        legacy_ns = "codetonight:default"
        if legacy_ns != ns and legacy_ns not in accessible_ns:
            accessible_ns.append(legacy_ns)
        if include_merged:
            try:
                from brain_merge import BrainMergeManager
                mgr = BrainMergeManager()
                accessible_ns.extend(mgr.get_active_merges(ns))
            except (ImportError, Exception):
                pass
        return accessible_ns

    def _execute_hybrid_search(
        self, query: str, dimension: Optional[int], limit: int,
        filter_grid_x: Optional[int] = None,
        vector_weight: float = 0.6,
        keyword_weight: float = 0.4,
        rrf_k: int = 60,
    ) -> List[Dict[str, Any]]:
        """Run hybrid vector+FTS5 search with recency decay.

        When filter_grid_x is not None, the domain predicate is pushed DOWN
        into both search arms' SQL (vector arm: ``rowid IN (SELECT id ...
        WHERE grid_x=?)`` before the KNN LIMIT; keyword arm: ``AND
        e.grid_x=?`` before ``ORDER BY rank LIMIT``).  This is true
        filter → rank → limit, fixing #2773 at the root: the fetch →
        limit → filter ordering could drop a domain whose rows fell outside
        the global top-N for the query.  int(0) is a valid grid_x and is
        never treated as falsy-missing.

        Returns an empty list when the embeddings backend is unavailable —
        whether by missing import (sqlite-lembed retired in W6 H218 → ollama)
        or by daemon-down at query time. Callers fall back to FTS5-only
        recall in either case.
        """
        try:
            from embeddings import get_engine
            from embeddings.search import search_hybrid, apply_recency_decay
        except ImportError:
            return []

        try:
            engine = get_engine()
            embed_type = None
            if dimension is not None:
                embed_type = self._dimension_to_type(dimension)
            hybrid_hits = search_hybrid(
                engine, query_text=query, embed_type=embed_type,
                limit=limit * 2, filter_grid_x=filter_grid_x,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight,
                k=rrf_k,
            )
        except Exception as exc:
            logger.debug("hybrid search backend unavailable: %s", exc)
            return []

        if hybrid_hits:
            try:
                hybrid_hits = apply_recency_decay(hybrid_hits)
            except Exception:
                pass

        return hybrid_hits

    def _enrich_and_filter(
        self,
        hybrid_hits: List[Dict[str, Any]],
        accessible_ns: List[str],
        filter_grid_x: Optional[int],
        min_fitness: float,
        dimension: Optional[int],
        platform: Optional[str] = None,
        channel_id: Optional[str] = None,
        sender_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], set]:
        """Enrich hybrid search hits with KONO metadata and apply filters.

        Returns (results, seen_ids) tuple.
        """
        conn = self.connect()
        results: List[Dict[str, Any]] = []
        seen_ids: set = set()
        ns_placeholders = ",".join("?" for _ in accessible_ns)
        prov_clause, prov_params = self._build_provenance_filter(
            platform, channel_id, sender_id
        )

        for hit in hybrid_hits:
            emb_id = hit.get("id")
            if emb_id in seen_ids:
                continue
            enriched = self._enrich_single_hit(
                conn, hit, emb_id, accessible_ns, ns_placeholders,
                prov_clause, prov_params, filter_grid_x, min_fitness, dimension
            )
            if enriched is None:
                continue
            seen_ids.add(emb_id)
            results.append(enriched)

        return results, seen_ids

    @staticmethod
    def _build_provenance_filter(
        platform: Optional[str], channel_id: Optional[str], sender_id: Optional[str]
    ):
        """Build SQL clause + params for source_platform/channel_id/sender_id filters."""
        prov_clause = ""
        prov_params: list = []
        if platform:
            prov_clause += " AND source_platform = ?"
            prov_params.append(platform)
        if channel_id:
            prov_clause += " AND source_channel_id = ?"
            prov_params.append(channel_id)
        if sender_id:
            prov_clause += " AND source_sender_id = ?"
            prov_params.append(sender_id)
        return prov_clause, prov_params

    def _enrich_single_hit(
        self, conn, hit: Dict[str, Any], emb_id: Any, accessible_ns: List[str],
        ns_placeholders: str, prov_clause: str, prov_params: list,
        filter_grid_x: Optional[int], min_fitness: float, dimension: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Look up KONO metadata for a hit, apply filters, return enriched dict or None."""
        row = list(conn.execute(
            f"""SELECT grid_x, grid_y, grid_z, dimension, fitness_score,
                      is_permanent, attestation_count, created_at
               FROM embeddings WHERE id = ?
               AND (namespace IN ({ns_placeholders}) OR namespace IS NULL){prov_clause}""",
            (emb_id, *accessible_ns, *prov_params)
        ))
        if not row:
            return None

        r = row[0]
        fitness = r[4] if r[4] is not None else 0.5

        if filter_grid_x is not None and r[0] != filter_grid_x:
            return None
        if fitness < min_fitness:
            return None
        if dimension is not None and r[3] is not None and r[3] != dimension:
            return None

        hit_content = hit.get("content", "")
        if is_operational_content(hit_content):
            return None

        return {
            "id": emb_id,
            "content": hit_content,
            "type": hit.get("type", ""),
            "dimension": r[3],
            "dimension_name": self._dimension_to_type(r[3]) if r[3] is not None else "unknown",
            "position": (r[0], r[1], r[2]),
            "fitness": fitness,
            "is_permanent": bool(r[5]),
            "attestation_count": r[6] or 0,
            "created_at": r[7],
            "score": hit.get("recency_adjusted_score",
                             hit.get("final_score",
                                      hit.get("rrf_score",
                                               hit.get("similarity", 0.0)))),
            "source": "hybrid_search",
        }

    def _finalize_results(
        self, results: List[Dict[str, Any]], limit: int,
        min_score: float = 0.0, query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Deduplicate, sort, structurally re-rank, truncate, and attest.

        ``min_score`` applies a floor on the displayed similarity/RRF axis
        (orthogonal to ``min_fitness`` which filters the evolutionary
        fitness column at ``_enrich_single_hit``). Fail-open: if filtering
        would starve recall to <2 results, return the top 2 unfiltered so
        warm-start never goes empty. Default 0.0 preserves all existing
        callers — only opt-in callers (kono_preload) tighten the floor.

        ``query`` enables the FAC half of MAC/FAC (``_fac_rerank``): the
        top window of MAC hits is re-ordered by structural alignment with
        the query when the query carries a relation. ``score`` stays the MAC
        value, so the ``min_score`` floor keeps its meaning; the fused order
        is what the caller receives and what gets attested.
        """
        deduped = {}
        for mem in results:
            mid = mem.get("id")
            if mid not in deduped:
                deduped[mid] = mem
        results = list(deduped.values())

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        if query:
            results = _fac_rerank(results, query, max(3 * limit, FAC_MIN_WINDOW))
        if min_score > 0:
            above = [r for r in results if r.get("score", 0) >= min_score]
            # Fail-open lower bound: if the filter would starve recall to
            # <2 results, surface the top 2 unfiltered. Warm-start must
            # never go silently empty just because the query embedding
            # mismatched the corpus.
            final = (above if len(above) >= 2 else results[:2])[:limit]
        else:
            final = results[:limit]

        for mem in final:
            emb_id = mem.get("id")
            if emb_id is not None:
                self.attest_recalled(emb_id)

        # Read accounting moved into attest_recalled (lib/kono_evolution.py)
        # so that direct callers — focus_cards, the recall worker hook — count
        # reads too. Bumping here as well would double-count every read that
        # goes through recall().
        return final

    def _expand_from_results(
        self,
        seed_results: List[Dict[str, Any]],
        limit: int,
        min_fitness: float,
        seen_ids: set,
        filter_grid_x: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Expand search results via KONO spatial proximity.

        Two-phase: collect all nearby memories, then deduplicate via hash set.
        O(seeds + total_nearby) instead of nested iteration.
        """
        # Phase 1: collect all query positions from seeds
        queries = []
        for seed in seed_results:
            pos = seed.get("position")
            if not pos or (pos[0] is None and pos[1] is None):
                continue
            x, y = float(pos[0] or 0), float(pos[1] or 0)
            if x == 0 and y == 0:
                continue
            queries.append(Position4D(x=x, y=y, z=5.0, t=0))

        # Phase 2: retrieve all nearby memories (sequential DB queries, flat collection)
        all_nearby: List[Dict[str, Any]] = []
        effective_min = max(min_fitness, 0.3)
        per_query_limit = max(1, limit // (3 * max(len(queries), 1)))
        for qpos in queries:
            all_nearby.extend(self.retrieve(
                query_position=qpos, radius=5.0,
                limit=per_query_limit, min_fitness=effective_min,
            ))

        # Phase 3: deduplicate and filter in single pass (hash set lookup O(1))
        return self._dedupe_spatial(all_nearby, seen_ids, filter_grid_x)

    def _dedupe_spatial(
        self,
        memories: List[Dict[str, Any]],
        seen_ids: set,
        filter_grid_x: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Deduplicate spatial expansion results. O(n) with hash set.

        Mirrors the operational-content filter applied in `_enrich_single_hit`
        — without this, integration-test markers (INTEGRATION_TEST_MARKER_,
        CHECKPOINT_, etc.) leak into warm-start recall at score ~1.0 because
        they're perfect spatial neighbours of recent session anchors. The
        hybrid-search arm already drops them; the spatial arm must too,
        otherwise the filter is silently one-sided.
        """
        expanded = []
        for mem in memories:
            mid = mem["id"]
            if mid in seen_ids:
                continue
            content = mem.get("content", "")
            if is_operational_content(content):
                continue
            if filter_grid_x is not None:
                mem_pos = mem.get("position")
                if mem_pos and int(mem_pos[0]) != filter_grid_x:
                    continue
            seen_ids.add(mid)
            expanded.append({
                "id": mid,
                "content": content,
                "type": "spatial_expansion",
                "dimension": mem.get("dimension"),
                "dimension_name": self._dimension_to_type(mem["dimension"])
                    if mem.get("dimension") is not None else "unknown",
                "position": mem.get("position"),
                "fitness": mem.get("fitness", 0),
                "is_permanent": mem.get("is_permanent", False),
                "attestation_count": 0,
                "created_at": mem.get("created_at"),
                "score": 1.0 - (mem.get("distance", 10.0) / 10.0),
                "source": "kono_spatial",
            })
        return expanded


# Backward-compatible CLI entry point — delegates to kono_cli.py
if __name__ == "__main__":
    from kono_cli import main
    main()


