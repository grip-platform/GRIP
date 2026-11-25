#!/usr/bin/env python3
"""
Semantic RL++ Embedding Engine
Core Python module for embedding generation, storage, and similarity search.

Uses APSW for SQLite extension loading (sqlite-vec).
Model: nomic-embed-text-v1.5 (768 dimensions)

Usage:
    from embeddings import EmbeddingEngine
    engine = EmbeddingEngine()
    engine.embed_text("hello world")
    engine._search_similar(vector, type='prompt', limit=5)
"""

import apsw
import sqlite_vec
import json
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

# Import coherence gate (Gen 153)
try:
    from coherence import is_coherent, get_coherence_score
    HAS_COHERENCE = True
except ImportError:
    HAS_COHERENCE = False

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
DB_PATH = CLAUDE_DIR / "embeddings.db"
MODEL_PATH = CLAUDE_DIR / "models" / "nomic-embed-text-v1.5.Q8_0.gguf"
VECTOR_DIM = 768
MODEL_NAME = "nomic"


class EmbeddingEngine:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[apsw.Connection] = None
        self._model_loaded = False

    def connect(self) -> apsw.Connection:
        if self.conn is not None:
            return self.conn

        self.conn = apsw.Connection(str(self.db_path))
        self.conn.enableloadextension(True)
        self.conn.loadextension(sqlite_vec.loadable_path())
        return self.conn

    def _ensure_model(self):
        if self._model_loaded:
            return

        conn = self.connect()
        try:
            result = list(conn.execute("SELECT name FROM temp.lembed_models WHERE name = ?", (MODEL_NAME,)))
            if not result:
                conn.execute(
                    "INSERT INTO temp.lembed_models(name, model) SELECT ?, lembed_model_from_file(?)",
                    (MODEL_NAME, str(MODEL_PATH))
                )
            self._model_loaded = True
        except apsw.SQLError:
            conn.execute(
                "INSERT INTO temp.lembed_models(name, model) SELECT ?, lembed_model_from_file(?)",
                (MODEL_NAME, str(MODEL_PATH))
            )
            self._model_loaded = True

    def _create_indexes(self, conn) -> None:
        """Create indexes for the embeddings table."""
        for col in ("type", "project_path", "success_score", "hash", "priority"):
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_embeddings_{col} ON embeddings({col})")

    _AUX_TABLES_DDL = [
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(embedding float[{VECTOR_DIM}])",
        """CREATE TABLE IF NOT EXISTS embedding_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            type TEXT NOT NULL, priority TEXT DEFAULT 'medium', metadata JSON,
            project_path TEXT, session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')))""",
        """CREATE TABLE IF NOT EXISTS prompt_cache (
            hash TEXT PRIMARY KEY, vector BLOB,
            created_at TEXT DEFAULT (datetime('now')))""",
    ]

    def init_schema(self):
        conn = self.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE,
                vector BLOB,
                type TEXT NOT NULL,
                content TEXT,
                metadata JSON,
                success_score REAL DEFAULT 0.5,
                novelty_score REAL DEFAULT 0.5,
                parent_id INTEGER,
                project_path TEXT,
                session_id TEXT,
                cross_project_hits INTEGER DEFAULT 0,
                priority TEXT DEFAULT 'medium',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (parent_id) REFERENCES embeddings(id)
            )
        """)
        self._create_indexes(conn)
        for ddl in self._AUX_TABLES_DDL:
            conn.execute(ddl)

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]

    def embed_text(self, text: str, use_cache: bool = True) -> bytes:
        if not text or not text.strip():
            raise ValueError("Cannot embed empty or whitespace-only text")

        text_hash = self._hash_text(text)

        if use_cache:
            conn = self.connect()
            result = list(conn.execute(
                "SELECT vector FROM prompt_cache WHERE hash = ?",
                (text_hash,)
            ))
            if result:
                return result[0][0]

        self._ensure_model()
        conn = self.connect()
        truncated = text[:2000].strip()
        if not truncated:
            raise ValueError("Text empty after truncation")
        result = list(conn.execute(
            f"SELECT lembed('{MODEL_NAME}', ?)",
            (truncated,)
        ))
        vector = result[0][0]

        if use_cache:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO prompt_cache (hash, vector) VALUES (?, ?)",
                    (text_hash, vector)
                )
            except Exception:
                pass

        return vector

    def _cosine_similarity(self, vec1: bytes, vec2: bytes) -> float:
        conn = self.connect()
        result = list(conn.execute(
            "SELECT 1 - vec_distance_cosine(?, ?)",
            (vec1, vec2)
        ))
        return result[0][0]

    def _store_embedding(
        self,
        text: str,
        embed_type: str,
        metadata: Optional[Dict] = None,
        project_path: Optional[str] = None,
        session_id: Optional[str] = None,
        parent_id: Optional[int] = None,
        success_score: float = 0.5,
        novelty_score: float = 0.5,
        priority: str = "medium"
    ) -> int:
        text_hash = self._hash_text(text)
        vector = self.embed_text(text)

        conn = self.connect()

        existing = list(conn.execute(
            "SELECT id FROM embeddings WHERE hash = ?",
            (text_hash,)
        ))

        if existing:
            conn.execute(
                """UPDATE embeddings SET
                   success_score = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (success_score, existing[0][0])
            )
            return existing[0][0]

        cursor = conn.execute(
            """INSERT INTO embeddings
               (hash, vector, type, content, metadata, project_path, session_id,
                parent_id, success_score, novelty_score, priority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (text_hash, vector, embed_type, text[:500],
             json.dumps(metadata) if metadata else None,
             project_path, session_id, parent_id, success_score, novelty_score, priority)
        )
        embedding_id = conn.last_insert_rowid()

        conn.execute(
            "INSERT INTO vec_embeddings (rowid, embedding) VALUES (?, ?)",
            (embedding_id, vector)
        )

        return embedding_id

    def _search_similar(
        self,
        query_text: str,
        embed_type: Optional[str] = None,
        limit: int = 5,
        exclude_project: Optional[str] = None,
        min_success: float = 0.0
    ) -> List[Dict[str, Any]]:
        query_vector = self.embed_text(query_text)
        return self._search_by_vector(
            query_vector, embed_type, limit, exclude_project, min_success
        )

    def _search_by_vector(
        self,
        query_vector: bytes,
        embed_type: Optional[str] = None,
        limit: int = 5,
        exclude_project: Optional[str] = None,
        min_success: float = 0.0
    ) -> List[Dict[str, Any]]:
        conn = self.connect()

        vec_results = list(conn.execute(
            """SELECT rowid, distance
               FROM vec_embeddings
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (query_vector, limit * 3)
        ))

        if not vec_results:
            return []

        results = []
        for rowid, distance in vec_results:
            row = list(conn.execute(
                """SELECT id, type, content, metadata, success_score,
                          project_path, session_id, cross_project_hits, created_at
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
                "similarity": similarity,
                "is_cross_project": exclude_project and r[5] != exclude_project
            })

            if len(results) >= limit:
                break

        return results

    def _calculate_novelty(self, text: str, recent_limit: int = 20) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate novelty score for text.

        Gen 153: Added coherence gate - gibberish returns 0.0 novelty.

        Args:
            text: Text to calculate novelty for
            recent_limit: Number of recent embeddings to compare against

        Returns:
            Tuple of (novelty_score, metadata_dict)
            metadata_dict contains: coherence_passed, coherence_score, coherence_method
        """
        metadata = {
            "coherence_passed": True,
            "coherence_score": 1.0,
            "coherence_method": "not_checked"
        }

        # COHERENCE GATE (Gen 153) - reject gibberish before embedding
        if HAS_COHERENCE:
            coherence_score, method = get_coherence_score(text)
            metadata["coherence_score"] = round(coherence_score, 4)
            metadata["coherence_method"] = method

            if not is_coherent(text):
                metadata["coherence_passed"] = False
                return (0.0, metadata)  # Incoherent = not novel (not worth learning)

        vector = self.embed_text(text)
        conn = self.connect()

        recent = list(conn.execute(
            """SELECT e.vector FROM embeddings e
               ORDER BY e.created_at DESC LIMIT ?""",
            (recent_limit,)
        ))

        if not recent:
            return (1.0, metadata)

        max_similarity = 0.0
        for (stored_vec,) in recent:
            sim = self._cosine_similarity(vector, stored_vec)
            max_similarity = max(max_similarity, sim)

        return (1.0 - max_similarity, metadata)

    def _get_priority(self, novelty_score: float) -> str:
        if novelty_score > 0.5:
            return "high"
        elif novelty_score > 0.3:
            return "medium"
        else:
            return "low"

    def _queue_for_embedding(
        self,
        text: str,
        embed_type: str,
        priority: Optional[str] = None,
        metadata: Optional[Dict] = None,
        project_path: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        if priority is None:
            novelty, _ = self._calculate_novelty(text)
            priority = self._get_priority(novelty)

        conn = self.connect()
        conn.execute(
            """INSERT INTO embedding_queue
               (text, type, priority, metadata, project_path, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (text, embed_type, priority,
             json.dumps(metadata) if metadata else None,
             project_path, session_id)
        )

    def _process_queue(self, priority_filter: Optional[str] = None) -> int:
        conn = self.connect()

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
            novelty, _ = self._calculate_novelty(text)

            self._store_embedding(
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

    def _update_success_score(self, embedding_id: int, delta: float):
        conn = self.connect()
        conn.execute(
            """UPDATE embeddings
               SET success_score = MIN(1.0, MAX(0.0, success_score + ?)),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (delta, embedding_id)
        )

    def _increment_cross_project_hits(self, embedding_id: int):
        conn = self.connect()
        conn.execute(
            """UPDATE embeddings
               SET cross_project_hits = cross_project_hits + 1,
                   success_score = MIN(1.0, success_score + 0.1),
                   updated_at = datetime('now')
               WHERE id = ?""",
            (embedding_id,)
        )

    def _prune_old_embeddings(self, days: int = 30, min_success: float = 0.3):
        conn = self.connect()
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

    def _get_stats(self) -> Dict[str, Any]:
        conn = self.connect()

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

    def _get_dynamic_threshold(self, concept_type: str, default: float = 0.45) -> float:
        """Get threshold from database or return default."""
        try:
            conn = self.connect()
            result = list(conn.execute(
                'SELECT current_threshold FROM threshold_config WHERE concept_type = ?',
                (concept_type,)
            ))
            return result[0][0] if result else default
        except:
            return default

    def _classify_against_concepts(
        self,
        text: str,
        concept_type: str,
        threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        if threshold is None:
            threshold = self._get_dynamic_threshold(concept_type)

        query_vector = self.embed_text(text)
        conn = self.connect()

        concepts = list(conn.execute(
            """SELECT id, vector, content, metadata FROM embeddings
               WHERE type = ?""",
            (concept_type,)
        ))

        results = []
        for emb_id, stored_vec, content, metadata_json in concepts:
            similarity = self._cosine_similarity(query_vector, stored_vec)
            if similarity >= threshold:
                metadata = json.loads(metadata_json) if metadata_json else {}
                results.append({
                    "id": emb_id,
                    "name": metadata.get("name", "unknown"),
                    "category": metadata.get("category", concept_type),
                    "content": content,
                    "similarity": round(similarity, 4)
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results

    def _detect_checkpoint(self, text: str, threshold: Optional[float] = None) -> Optional[Dict[str, Any]]:
        results = self._classify_against_concepts(text, "checkpoint", threshold)
        if results:
            return results[0]
        return None

    def _classify_intent(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_intent", threshold)

    def _classify_feedback(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_feedback", threshold)

    def _classify_solution(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_solution", threshold)

    def _classify_workflow(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_workflow", threshold)

    def _match_agent(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_agent", threshold)

    def _match_skill(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_skill", threshold)

    def _match_command(self, text: str, threshold: Optional[float] = None) -> List[Dict[str, Any]]:
        return self._classify_against_concepts(text, "concept_command", threshold)

    def _should_trigger_checkpoint(self, text: str) -> Tuple[bool, Optional[str]]:
        checkpoint = self._detect_checkpoint(text)
        if checkpoint:
            return True, checkpoint["name"]
        return False, None

    def _get_full_classification(self, text: str) -> Dict[str, Any]:
        intent = self._classify_intent(text)
        feedback = self._classify_feedback(text)
        checkpoint = self._detect_checkpoint(text)
        workflow = self._classify_workflow(text)
        agent = self._match_agent(text)
        skill = self._match_skill(text)
        command = self._match_command(text)
        novelty, coherence_meta = self._calculate_novelty(text)
        priority = self._get_priority(novelty)

        return {
            "intent": intent[:3] if intent else [],
            "feedback": feedback[:2] if feedback else [],
            "checkpoint": checkpoint,
            "workflow": workflow[:1] if workflow else [],
            "suggested_agent": agent[0] if agent else None,
            "suggested_skill": skill[0] if skill else None,
            "suggested_command": command[0] if command else None,
            "novelty_score": round(novelty, 4),
            "priority": priority,
            "should_process_queue": checkpoint is not None,
            "coherence": coherence_meta  # Gen 153
        }

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
            self._model_loaded = False


def _add_text_subparser(sub, name, help_text, extra_args=None):
    """Add a subparser with a required 'text' arg and optional extras."""
    p = sub.add_parser(name, help=help_text)
    p.add_argument("text", help="Text input")
    for arg_args, arg_kwargs in (extra_args or []):
        p.add_argument(*arg_args, **arg_kwargs)
    return p


def _build_parser():
    """Build CLI argument parser."""
    import argparse
    parser = argparse.ArgumentParser(description="Semantic RL++ Embedding Engine")
    sub = parser.add_subparsers(dest="command", help="Commands")

    sub.add_parser("init", help="Initialise database schema")
    sub.add_parser("stats", help="Show database statistics")

    _add_text_subparser(sub, "embed", "Generate embedding for text", [
        (["--type"], {"default": "prompt", "help": "Embedding type"}),
        (["--project"], {"help": "Project path"}),
        (["--session"], {"help": "Session ID"}),
        (["--store"], {"action": "store_true", "help": "Store in database"})])

    _add_text_subparser(sub, "search", "Search similar embeddings", [
        (["--type"], {"help": "Filter by type"}),
        (["--limit"], {"type": int, "default": 5, "help": "Max results"}),
        (["--exclude-project"], {"help": "Exclude this project"}),
        (["--cross-project"], {"action": "store_true", "help": "Only cross-project"})])

    _add_text_subparser(sub, "novelty", "Calculate novelty score")

    _add_text_subparser(sub, "queue", "Queue text for embedding", [
        (["--type"], {"default": "prompt", "help": "Embedding type"}),
        (["--priority"], {"choices": ["high", "medium", "low"]}),
        (["--project"], {"help": "Project path"})])

    process_p = sub.add_parser("process", help="Process embedding queue")
    process_p.add_argument("--priority", choices=["high", "medium", "low"])

    prune_p = sub.add_parser("prune", help="Prune old low-quality embeddings")
    prune_p.add_argument("--days", type=int, default=30, help="Age threshold")
    prune_p.add_argument("--min-success", type=float, default=0.3)

    _add_text_subparser(sub, "checkpoint", "Detect checkpoint in text", [
        (["--threshold"], {"type": float, "default": 0.7})])
    _add_text_subparser(sub, "classify", "Full classification of text")
    _add_text_subparser(sub, "intent", "Classify intent")
    _add_text_subparser(sub, "feedback", "Classify feedback")
    _add_text_subparser(sub, "match", "Match agent/skill/command", [
        (["--type"], {"choices": ["agent", "skill", "command"], "default": "agent"})])

    return parser


def _run_core_command(engine, cmd, args):
    """Handle core embedding commands (embed, search, novelty, queue, process)."""
    if cmd == "embed":
        if args.store:
            emb_id = engine._store_embedding(
                text=args.text, embed_type=args.type,
                project_path=args.project, session_id=args.session)
            return {"status": "ok", "id": emb_id}
        vector = engine.embed_text(args.text)
        return {"status": "ok", "dimensions": VECTOR_DIM, "bytes": len(vector)}
    if cmd == "search":
        exclude = args.exclude_project
        if args.cross_project and not exclude:
            exclude = os.getcwd()
        results = engine._search_similar(
            query_text=args.text, embed_type=args.type,
            limit=args.limit, exclude_project=exclude)
        return {"status": "ok", "results": results}
    if cmd == "novelty":
        score, coherence_meta = engine._calculate_novelty(args.text)
        return {"status": "ok", "novelty_score": round(score, 4),
                "priority": engine._get_priority(score), "coherence": coherence_meta}
    if cmd == "queue":
        engine._queue_for_embedding(
            text=args.text, embed_type=args.type,
            priority=args.priority, project_path=args.project)
        return {"status": "ok", "message": "Queued for embedding"}
    if cmd == "process":
        return {"status": "ok", "processed": engine._process_queue(args.priority)}
    return None


def _run_command(engine, args):
    """Dispatch CLI command to engine method."""
    cmd = args.command
    engine.init_schema()
    if cmd == "init":
        return {"status": "ok", "message": "Schema initialised"}
    if cmd == "stats":
        return {"status": "ok", **engine._get_stats()}
    if cmd == "prune":
        engine._prune_old_embeddings(args.days, args.min_success)
        return {"status": "ok", "message": "Pruning complete"}
    if cmd == "checkpoint":
        cp = engine._detect_checkpoint(args.text, args.threshold)
        return {"status": "ok", "triggered": bool(cp), "checkpoint": cp}
    if cmd == "classify":
        return {"status": "ok", **engine._get_full_classification(args.text)}
    if cmd == "intent":
        return {"status": "ok", "intents": engine._classify_intent(args.text)}
    if cmd == "feedback":
        return {"status": "ok", "feedbacks": engine._classify_feedback(args.text)}
    if cmd == "match":
        match_fn = {"agent": engine.match_agent, "skill": engine.match_skill,
                     "command": engine.match_command}[args.type]
        return {"status": "ok", "matches": match_fn(args.text)}
    return _run_core_command(engine, cmd, args)


def main():
    parser = _build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    engine = EmbeddingEngine()
    try:
        result = _run_command(engine, args)
        if result:
            print(json.dumps(result, indent=2))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
