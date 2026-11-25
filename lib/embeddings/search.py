#!/usr/bin/env python3
"""
Embedding Search - Hybrid search (vector + FTS5 + RRF fusion).
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - Vector similarity search
    - FTS5 keyword search
    - Reciprocal Rank Fusion (RRF)
    - Recency decay
    - Plan search

Inspired by Cortex hybrid search architecture (Tomas Hjertefolger).
"""

import json
import os
import apsw
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import EmbeddingEngine


# Telemetry: env-gated JSONL append. OFF by default (zero I/O cost when unset).
# Opt-in: export KONO_SEARCH_TELEMETRY=1
_TELEMETRY_ENV = "KONO_SEARCH_TELEMETRY"
_TELEMETRY_LOG = Path(__file__).resolve().parents[2] / "logs" / "search-hybrid.jsonl"


def _telemetry_enabled() -> bool:
    """Per-call check (cheap), so tests can flip env mid-process."""
    return os.environ.get(_TELEMETRY_ENV, "").lower() in ("1", "true", "yes")


def _emit_telemetry(
    query_len: int,
    vector_results: List[Dict[str, Any]],
    keyword_results: List[Dict[str, Any]],
    fused: List[Dict[str, Any]],
    vector_weight: float,
    keyword_weight: float,
    k: int,
) -> None:
    """Append one JSONL record. RANKS not scores (Goodhart-Sentinel).

    No query text, no content, no IDs — privacy + portability.
    Fail-silent: telemetry MUST NOT perturb the caller's result.
    """
    try:
        vec_ids = {r["id"] for r in vector_results}
        fts_ids = {r["id"] for r in keyword_results}
        overlap = len(vec_ids & fts_ids)
        if fused:
            top_id = fused[0]["id"]
            if top_id in vec_ids and top_id in fts_ids:
                top1_arm = "both"
            elif top_id in vec_ids:
                top1_arm = "vector"
            elif top_id in fts_ids:
                top1_arm = "fts"
            else:
                top1_arm = "unknown"
        else:
            top1_arm = "none"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query_len": query_len,
            "n_vector_hits": len(vector_results),
            "n_fts_hits": len(keyword_results),
            "overlap_count": overlap,
            "top1_arm": top1_arm,
            "weights": {"vector": vector_weight, "keyword": keyword_weight},
            "rrf_k": k,
        }
        _TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _TELEMETRY_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        # Telemetry is observational; never propagate errors to the caller.
        pass


def search_similar(
    engine: 'EmbeddingEngine',
    query_text: str,
    embed_type: Optional[str] = None,
    limit: int = 5,
    exclude_project: Optional[str] = None,
    min_success: float = 0.0
) -> List[Dict[str, Any]]:
    """Search by text similarity (vector only)."""
    query_vector = engine.embed_text(query_text)
    return search_by_vector(
        engine, query_vector, embed_type, limit, exclude_project, min_success
    )


def search_by_vector(
    engine: 'EmbeddingEngine',
    query_vector: bytes,
    embed_type: Optional[str] = None,
    limit: int = 5,
    exclude_project: Optional[str] = None,
    min_success: float = 0.0,
    filter_grid_x: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Search by raw embedding vector.

    When filter_grid_x is not None the KNN is constrained to that domain's
    rows via a ``rowid IN (SELECT id FROM embeddings WHERE grid_x=?)``
    pre-filter applied BEFORE the distance ORDER+LIMIT (sqlite-vec metadata
    filtering) — true filter → rank → limit, fixing #2773 in the vector arm.
    int(0) is a valid grid_x and is never treated as falsy-missing.
    """
    conn = engine.connect()

    # sqlite-vec hard-caps KNN k at 4096; an uncapped limit*3 (reachable via
    # search_hybrid's own limit*3, i.e. 9x the caller limit) raised SQLError
    # -> empty result, which was itself part of the #2773 non-monotonic
    # symptom (large limit -> 0). Clamp to the engine ceiling.
    knn_k = min(limit * 3, 4096)

    if filter_grid_x is not None:
        vec_results = list(conn.execute(
            """SELECT rowid, distance
               FROM vec_embeddings
               WHERE embedding MATCH ?
                 AND rowid IN (SELECT id FROM embeddings WHERE grid_x = ?)
               ORDER BY distance
               LIMIT ?""",
            (query_vector, filter_grid_x, knn_k)
        ))
    else:
        vec_results = list(conn.execute(
            """SELECT rowid, distance
               FROM vec_embeddings
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (query_vector, knn_k)
        ))

    if not vec_results:
        return []

    results = []
    for rowid, distance in vec_results:
        row = list(conn.execute(
            """SELECT id, type, content, metadata, success_score,
                      project_path, session_id, cross_project_hits, created_at,
                      fitness_score
               FROM embeddings WHERE id = ?""",
            (rowid,)
        ))

        if not row:
            continue

        r = row[0]

        if embed_type and r[1] != embed_type:
            continue
        if exclude_project and r[5] == exclude_project:
            continue
        if r[4] < min_success:
            continue

        similarity = 1 - distance

        results.append({
            "id": r[0],
            "type": r[1],
            "content": r[2],
            "metadata": json.loads(r[3]) if r[3] else None,
            "success_score": r[4],
            "project_path": r[5],
            "session_id": r[6],
            "cross_project_hits": r[7],
            "created_at": r[8],
            "fitness_score": r[9] if len(r) > 9 else 0.5,
            "similarity": similarity,
            "is_cross_project": exclude_project and r[5] != exclude_project
        })

        if len(results) >= limit:
            break

    return results


def _keyword_search_fts5(
    engine: 'EmbeddingEngine',
    query: str,
    embed_type: Optional[str] = None,
    limit: int = 20,
    filter_grid_x: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    FTS5 full-text search on content.
    Part of hybrid search implementation inspired by Cortex.

    When filter_grid_x is not None an ``AND e.grid_x = ?`` predicate is
    applied in the JOINed query BEFORE ``ORDER BY rank LIMIT`` — true
    filter → rank → limit, fixing #2773 in the keyword arm. int(0) is a
    valid grid_x.

    Both optional predicates (type, grid_x) are bound via parameters, and
    ``params`` is built so exactly one bound value is appended per emitted
    ``?`` — the placeholder/parameter count matches by construction in
    every branch (no ternary param packing, no f-string value injection).
    """
    conn = engine.connect()
    type_filter = "AND type = ?" if embed_type else ""
    grid_filter = "AND e.grid_x = ?" if filter_grid_x is not None else ""
    # One appended bind per emitted placeholder, in placeholder order:
    #   MATCH ?  ->  {type_filter}?  ->  {grid_filter}?  ->  LIMIT ?
    params: List[Any] = [query]
    if embed_type:
        params.append(embed_type)
    if filter_grid_x is not None:
        params.append(filter_grid_x)
    params.append(limit)

    try:
        results = list(conn.execute(f"""
            SELECT e.id, e.type, e.content, e.metadata,
                   e.success_score, e.project_path, e.session_id,
                   e.created_at, fts.rank
            FROM embeddings_fts fts
            JOIN embeddings e ON fts.rowid = e.id
            WHERE embeddings_fts MATCH ?
            {type_filter}
            {grid_filter}
            ORDER BY rank
            LIMIT ?
        """, params))
    except apsw.SQLError:
        return []

    return [
        {
            "id": r[0], "type": r[1], "content": r[2],
            "metadata": json.loads(r[3]) if r[3] else None,
            "success_score": r[4], "project_path": r[5],
            "session_id": r[6], "created_at": r[7],
            "fts_rank": r[8]
        }
        for r in results
    ]


def _rrf_fusion(
    vector_results: List[Dict],
    keyword_results: List[Dict],
    vector_weight: float = 0.6,
    keyword_weight: float = 0.4,
    k: int = 60,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) algorithm.
    Inspired by Cortex's hybrid search architecture.
    
    RRF score = Σ (weight / (k + rank))
    Standard k=60 from Cormack et al. 2009.
    """
    scores = {}

    # Score vector results
    for rank, result in enumerate(vector_results, start=1):
        emb_id = result["id"]
        rrf_score = vector_weight / (k + rank)
        if emb_id not in scores:
            scores[emb_id] = {"data": result, "score": 0}
        scores[emb_id]["score"] += rrf_score

    # Score keyword results
    for rank, result in enumerate(keyword_results, start=1):
        emb_id = result["id"]
        rrf_score = keyword_weight / (k + rank)
        if emb_id not in scores:
            scores[emb_id] = {"data": result, "score": 0}
        scores[emb_id]["score"] += rrf_score

    # Sort by combined score
    ranked = sorted(
        scores.items(),
        key=lambda x: x[1]["score"],
        reverse=True
    )

    # Gen 310: Apply fitness weighting to final scores
    results = []
    for item in ranked[:limit]:
        result = {**item[1]["data"], "rrf_score": item[1]["score"]}
        fitness = result.get("fitness_score", 0.5)
        result["final_score"] = result["rrf_score"] * (1 + 0.1 * fitness)
        results.append(result)

    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results


def search_hybrid(
    engine: 'EmbeddingEngine',
    query_text: str,
    embed_type: Optional[str] = None,
    limit: int = 5,
    vector_weight: float = 0.6,
    keyword_weight: float = 0.4,
    k: int = 60,
    filter_grid_x: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Hybrid search combining vector similarity and keyword matching.
    Uses Reciprocal Rank Fusion (RRF) to merge results.

    Inspired by Cortex's hybrid search architecture.

    When filter_grid_x is not None the domain predicate is pushed into both
    arms' SQL before their LIMIT (see search_by_vector / _keyword_search_fts5)
    so project-scoped recall is filter → rank → limit, not the fetch → limit
    → filter ordering that caused #2773. int(0) is a valid grid_x.
    """
    engine._ensure_model()

    # Vector search
    query_vector = engine.embed_text(query_text)
    vector_results = search_by_vector(
        engine,
        query_vector,
        embed_type=embed_type,
        limit=limit * 3,
        filter_grid_x=filter_grid_x
    )

    # FTS5 keyword search
    keyword_results = _keyword_search_fts5(
        engine,
        query_text,
        embed_type=embed_type,
        limit=limit * 3,
        filter_grid_x=filter_grid_x
    )

    # Apply RRF fusion
    fused = _rrf_fusion(
        vector_results,
        keyword_results,
        vector_weight,
        keyword_weight,
        k,
        limit
    )

    # Telemetry: env-gated, post-fusion, fail-silent. MUST NOT perturb result.
    if _telemetry_enabled():
        _emit_telemetry(
            query_len=len(query_text),
            vector_results=vector_results,
            keyword_results=keyword_results,
            fused=fused,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            k=k,
        )

    return fused


def apply_recency_decay(
    results: List[Dict[str, Any]],
    half_life_days: float = 7.0
) -> List[Dict[str, Any]]:
    """
    Apply exponential recency decay to search results.
    Inspired by Cortex's 7-day recency weighting.
    
    Formula: decay_factor = 2^(-days_old / half_life)
    """
    now = datetime.now(timezone.utc)

    for result in results:
        created_str = result.get("created_at")
        if not created_str:
            continue

        try:
            if 'Z' in created_str:
                created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            elif '+' in created_str or created_str.endswith('+00:00'):
                created = datetime.fromisoformat(created_str)
            else:
                created = datetime.fromisoformat(created_str).replace(tzinfo=timezone.utc)

            days_old = (now - created).total_seconds() / 86400
            decay_factor = 2 ** (-days_old / half_life_days)

            original_score = result.get("rrf_score", result.get("similarity", 1.0))
            result["recency_adjusted_score"] = original_score * decay_factor
            result["decay_factor"] = round(decay_factor, 4)
            result["days_old"] = round(days_old, 2)
        except (ValueError, TypeError):
            result["recency_adjusted_score"] = result.get("rrf_score", result.get("similarity", 1.0))
            result["decay_factor"] = 1.0
            result["days_old"] = 0.0

    results.sort(key=lambda x: x.get("recency_adjusted_score", 0), reverse=True)
    return results


def search_plans(
    engine: 'EmbeddingEngine',
    query: str,
    limit: int = 5,
    apply_recency: bool = True
) -> List[Dict[str, Any]]:
    """
    Search plans using hybrid RRF search.
    Part of Cortex Phase 3 - plan-session lineage system.
    """
    results = search_hybrid(
        engine,
        query_text=query,
        embed_type="plan",
        limit=limit
    )

    if apply_recency:
        results = apply_recency_decay(results, half_life_days=7.0)

    return results
