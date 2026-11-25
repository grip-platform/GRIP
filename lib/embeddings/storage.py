#!/usr/bin/env python3
"""
Embedding Storage - Database operations, schema, and queue processing.
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - Schema initialization
    - Embedding storage and retrieval
    - Queue management
    - Novelty calculation
    - Statistics and maintenance
"""

import json
import os
import struct
import re
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING
import logging

logger = logging.getLogger(__name__)

# Hoisted so tests assert against the production string instead of a copy of
# it. Three assertions matched this text by hand; they would have kept passing
# against a stale duplicate if the message here ever changed.
KONO_WRITE_FAILED_MSG = (
    "KONO spatial write failed for embedding %s (%s: %s); "
    "dimension, grid_z and fitness_score were not set"
)

if TYPE_CHECKING:
    from .engine import EmbeddingEngine

# Import coherence gate (Gen 153)
try:
    from coherence import is_coherent, get_coherence_score
    HAS_COHERENCE = True
except ImportError:
    HAS_COHERENCE = False

# Import KONO schema (Gen 310)
try:
    from kono_schema import init_kono_schema
    HAS_KONO = True
except ImportError:
    HAS_KONO = False

# Import KONO substrate (Gen 310)
try:
    from kono import route_dimension
    HAS_KONO_SUBSTRATE = True
except ImportError:
    HAS_KONO_SUBSTRATE = False

VECTOR_DIM = 768


def _create_core_tables(conn):
    """Create main embeddings table, vector table, queue, and cache."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE, vector BLOB,
            type TEXT NOT NULL, content TEXT, metadata JSON,
            success_score REAL DEFAULT 0.5, novelty_score REAL DEFAULT 0.5,
            parent_id INTEGER, project_path TEXT, session_id TEXT,
            cross_project_hits INTEGER DEFAULT 0, priority TEXT DEFAULT 'medium',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (parent_id) REFERENCES embeddings(id))
    """)
    for col in ("type", "project_path", "success_score", "hash", "priority"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_embeddings_{col} ON embeddings({col})")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(embedding float[768])")
    conn.execute("""CREATE TABLE IF NOT EXISTS embedding_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, type TEXT NOT NULL,
        priority TEXT DEFAULT 'medium', metadata JSON, project_path TEXT, session_id TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS prompt_cache (
        hash TEXT PRIMARY KEY, vector BLOB, created_at TEXT DEFAULT (datetime('now')))""")


def _create_fts5(conn):
    """Create FTS5 full-text search table and sync triggers."""
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_fts USING fts5(
        content, content='embeddings', content_rowid='id')""")
    try:
        conn.execute("""INSERT OR IGNORE INTO embeddings_fts(rowid, content)
            SELECT id, content FROM embeddings WHERE content IS NOT NULL""")
    except Exception:
        pass
    for op, body in [
        ("insert", "INSERT INTO embeddings_fts(rowid, content) VALUES (new.id, new.content)"),
        ("update", "UPDATE embeddings_fts SET content = new.content WHERE rowid = old.id"),
        ("delete", "DELETE FROM embeddings_fts WHERE rowid = old.id"),
    ]:
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS embeddings_fts_{op}
            AFTER {op.upper()} ON embeddings BEGIN {body}; END""")


def _apply_migrations(conn):
    """Apply incremental schema migrations (ALTER TABLE, new indexes)."""
    for stmt in [
        "ALTER TABLE embeddings ADD COLUMN confirmation_count INTEGER DEFAULT 1",
        "ALTER TABLE embeddings ADD COLUMN namespace TEXT DEFAULT 'codetonight:default'",
        "CREATE INDEX IF NOT EXISTS idx_embeddings_namespace ON embeddings(namespace)",
    ]:
        try:
            conn.execute(stmt)
        except Exception:
            pass


def init_schema(engine: 'EmbeddingEngine'):
    """Initialize database schema with all tables, indexes, and triggers."""
    conn = engine.connect()
    _create_core_tables(conn)
    _create_fts5(conn)
    _apply_migrations(conn)
    if HAS_KONO:
        init_kono_schema(conn)


def _find_semantic_duplicate(
    engine: 'EmbeddingEngine',
    vector: bytes,
    embed_type: str,
    threshold: float = 0.85,
    max_candidates: int = 10,
) -> Optional[int]:
    """Find a semantically similar existing embedding (#223).

    Uses vec0 KNN search to find nearest neighbors, then checks cosine
    similarity against threshold. Returns embedding ID if a near-duplicate
    is found, None otherwise.
    """
    conn = engine.connect()
    try:
        candidates = list(conn.execute(
            """SELECT rowid, distance FROM vec_embeddings
               WHERE embedding MATCH ?
               AND k = ?""",
            (vector, max_candidates)
        ))
    except Exception:
        return None  # vec0 not available or empty table

    for rowid, distance in candidates:
        similarity = 1.0 - distance
        if similarity >= threshold:
            # Verify same type to avoid cross-type dedup
            row = list(conn.execute(
                "SELECT id, type FROM embeddings WHERE id = ?", (rowid,)
            ))
            if row and row[0][1] == embed_type:
                return row[0][0]

    return None


def store_embedding(
    engine: 'EmbeddingEngine',
    text: str,
    embed_type: str,
    metadata: Optional[Dict] = None,
    project_path: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_id: Optional[int] = None,
    success_score: float = 0.5,
    novelty_score: float = 0.5,
    priority: str = "medium",
    namespace: Optional[str] = None
) -> int:
    """Store embedding in database with optional KONO coordinates and namespace."""
    text_hash = engine._hash_text(text)
    vector = engine.embed_text(text)
    ns = namespace or (os.environ.get("GRIP_ORG", "codetonight") + ":default")

    conn = engine.connect()

    # Check for existing
    existing = list(conn.execute(
        "SELECT id FROM embeddings WHERE hash = ?",
        (text_hash,)
    ))

    if existing:
        # Exact hash match — increment confirmation count (#223)
        conn.execute(
            """UPDATE embeddings SET
               success_score = ?, updated_at = datetime('now'),
               confirmation_count = COALESCE(confirmation_count, 1) + 1
               WHERE id = ?""",
            (success_score, existing[0][0])
        )
        return existing[0][0]

    # Semantic dedup: check for near-duplicates via cosine similarity (#223)
    semantic_match = _find_semantic_duplicate(engine, vector, embed_type, threshold=0.85)
    if semantic_match is not None:
        conn.execute(
            """UPDATE embeddings SET
               confirmation_count = COALESCE(confirmation_count, 1) + 1,
               updated_at = datetime('now')
               WHERE id = ?""",
            (semantic_match,)
        )
        return semantic_match

    # Insert new embedding
    conn.execute(
        """INSERT INTO embeddings
           (hash, vector, type, content, metadata, project_path, session_id,
            parent_id, success_score, novelty_score, priority, namespace)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (text_hash, vector, embed_type, text[:500],
         json.dumps(metadata) if metadata else None,
         project_path, session_id, parent_id, success_score, novelty_score, priority, ns)
    )
    embedding_id = conn.last_insert_rowid()

    # Insert into vector table
    conn.execute(
        "INSERT INTO vec_embeddings (rowid, embedding) VALUES (?, ?)",
        (embedding_id, vector)
    )

    # Store KONO spatial coordinates (Gen 310)
    if HAS_KONO_SUBSTRATE:
        try:
            dimension = route_dimension(embed_type)
            # Calculate abstraction level from embedding variance
            vec_list = list(struct.unpack(f'{VECTOR_DIM}f', vector)) if hasattr(vector, '__len__') else []
            if vec_list:
                mean = sum(vec_list) / len(vec_list)
                variance = sum((v - mean) ** 2 for v in vec_list) / len(vec_list)
                z_level = min(9.0, max(0.0, variance * 100))
            else:
                z_level = 5.0

            conn.execute("""
                UPDATE embeddings SET
                    dimension = ?,
                    grid_z = ?,
                    fitness_score = 0.5
                WHERE id = ?
            """, (dimension, int(z_level), embedding_id))
        except Exception as exc:
            # KONO genuinely is optional, so this stays non-fatal. But the
            # block it guards is the only place fitness_score is initialised,
            # so swallowing the failure silently means every row stored while
            # it is broken keeps whatever default the schema gives it, with
            # nothing anywhere recording that it happened. Non-fatal is not
            # the same as unreported.
            logger.warning(
                KONO_WRITE_FAILED_MSG, embedding_id, type(exc).__name__, exc,
            )

    return embedding_id


def calculate_novelty(
    engine: 'EmbeddingEngine',
    text: str,
    recent_limit: int = 20
) -> Tuple[float, Dict[str, Any]]:
    """
    Calculate novelty score for text.
    
    Gen 153: Added coherence gate - gibberish returns 0.0 novelty.
    """
    metadata = {
        "coherence_passed": True,
        "coherence_score": 1.0,
        "coherence_method": "not_checked"
    }

    # COHERENCE GATE (Gen 153)
    if HAS_COHERENCE:
        coherence_score, method = get_coherence_score(text)
        metadata["coherence_score"] = round(coherence_score, 4)
        metadata["coherence_method"] = method

        if not is_coherent(text):
            metadata["coherence_passed"] = False
            return (0.0, metadata)

    vector = engine.embed_text(text)
    conn = engine.connect()

    recent = list(conn.execute(
        """SELECT e.vector FROM embeddings e
           ORDER BY e.created_at DESC LIMIT ?""",
        (recent_limit,)
    ))

    if not recent:
        return (1.0, metadata)

    max_similarity = 0.0
    for (stored_vec,) in recent:
        sim = engine._cosine_similarity(vector, stored_vec)
        max_similarity = max(max_similarity, sim)

    return (1.0 - max_similarity, metadata)


def get_priority(novelty_score: float) -> str:
    """Determine priority based on novelty score."""
    if novelty_score > 0.5:
        return "high"
    elif novelty_score > 0.3:
        return "medium"
    else:
        return "low"


def queue_for_embedding(
    engine: 'EmbeddingEngine',
    text: str,
    embed_type: str,
    priority: Optional[str] = None,
    metadata: Optional[Dict] = None,
    project_path: Optional[str] = None,
    session_id: Optional[str] = None
):
    """Queue text for later embedding processing."""
    if priority is None:
        novelty, _ = calculate_novelty(engine, text)
        priority = get_priority(novelty)

    conn = engine.connect()
    conn.execute(
        """INSERT INTO embedding_queue
           (text, type, priority, metadata, project_path, session_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (text, embed_type, priority,
         json.dumps(metadata) if metadata else None,
         project_path, session_id)
    )


def process_queue(
    engine: 'EmbeddingEngine',
    priority_filter: Optional[str] = None
) -> int:
    """Process embedding queue, storing all queued embeddings."""
    conn = engine.connect()

    if priority_filter:
        queue = list(conn.execute(
            """SELECT id, text, type, priority, metadata, project_path, session_id
               FROM embedding_queue WHERE priority = ?
               ORDER BY created_at""",
            (priority_filter,)
        ))
    else:
        queue = list(conn.execute(
            """SELECT id, text, type, priority, metadata, project_path, session_id
               FROM embedding_queue
               ORDER BY
                 CASE priority
                   WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2
                   ELSE 3
                 END,
                 created_at"""
        ))

    processed = 0
    for q_id, text, embed_type, priority, metadata_json, project_path, session_id in queue:
        metadata = json.loads(metadata_json) if metadata_json else None
        novelty, _ = calculate_novelty(engine, text)

        store_embedding(
            engine,
            text=text,
            embed_type=embed_type,
            metadata=metadata,
            project_path=project_path,
            session_id=session_id,
            novelty_score=novelty,
            priority=priority
        )

        conn.execute("DELETE FROM embedding_queue WHERE id = ?", (q_id,))
        processed += 1

    return processed


def update_success_score(engine: 'EmbeddingEngine', embedding_id: int, delta: float):
    """Update success score by delta (clamped to 0-1)."""
    conn = engine.connect()
    conn.execute(
        """UPDATE embeddings
           SET success_score = MIN(1.0, MAX(0.0, success_score + ?)),
               updated_at = datetime('now')
           WHERE id = ?""",
        (delta, embedding_id)
    )


def increment_cross_project_hits(engine: 'EmbeddingEngine', embedding_id: int):
    """Increment cross-project hits and boost success score."""
    conn = engine.connect()
    conn.execute(
        """UPDATE embeddings
           SET cross_project_hits = cross_project_hits + 1,
               success_score = MIN(1.0, success_score + 0.1),
               updated_at = datetime('now')
           WHERE id = ?""",
        (embedding_id,)
    )


def prune_old_embeddings(
    engine: 'EmbeddingEngine',
    days: int = 30,
    min_success: float = 0.3
):
    """Prune old, low-quality embeddings (preserves concepts and checkpoints)."""
    conn = engine.connect()
    conn.execute(
        """DELETE FROM embeddings
           WHERE success_score < ?
             AND cross_project_hits = 0
             AND created_at < datetime('now', ?)
             AND type NOT IN ('concept', 'checkpoint')""",
        (min_success, f'-{days} days')
    )

    conn.execute(
        """DELETE FROM vec_embeddings
           WHERE rowid NOT IN (SELECT id FROM embeddings)"""
    )


def get_stats(engine: 'EmbeddingEngine') -> Dict[str, Any]:
    """Get database statistics."""
    conn = engine.connect()

    total = list(conn.execute("SELECT COUNT(*) FROM embeddings"))[0][0]
    by_type = dict(conn.execute(
        "SELECT type, COUNT(*) FROM embeddings GROUP BY type"
    ))
    queue_size = list(conn.execute("SELECT COUNT(*) FROM embedding_queue"))[0][0]
    cache_size = list(conn.execute("SELECT COUNT(*) FROM prompt_cache"))[0][0]

    return {
        "total_embeddings": total,
        "by_type": by_type,
        "queue_size": queue_size,
        "cache_size": cache_size
    }


def _extract_plan_summary(content: str, max_length: int = 2000) -> str:
    """Extract summary from plan markdown for embedding."""
    lines = content.split("\n")
    summary_parts = []
    in_objective = False

    for line in lines:
        if re.match(r'^##\s*Objective', line, re.IGNORECASE):
            in_objective = True
            continue
        elif re.match(r'^##\s', line) and in_objective:
            in_objective = False
        elif in_objective:
            summary_parts.append(line)

    for line in lines[:30]:
        if re.match(r'^\*\*Context\*\*:', line):
            summary_parts.insert(0, line)

    summary = "\n".join(summary_parts).strip()
    return summary[:max_length] if summary else content[:max_length]


def embed_plan(
    engine: 'EmbeddingEngine',
    plan_id: str,
    plan_path: Path,
    metadata: Optional[Dict] = None
) -> int:
    """Embed plan summary for semantic search (Cortex Phase 3)."""
    content = plan_path.read_text() if plan_path.exists() else ""
    summary = _extract_plan_summary(content)

    plan_metadata = {"plan_id": plan_id}
    if metadata:
        plan_metadata.update(metadata)

    return store_embedding(
        engine,
        text=summary,
        embed_type="plan",
        metadata=plan_metadata
    )
