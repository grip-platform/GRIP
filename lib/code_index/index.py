"""CodeIndex — SQLite + FTS5 storage for Python symbols.

Provides incremental indexing with SHA256 file hashing, full-text search
via FTS5, and query methods for lookup, search, hierarchy, and Django
component listing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from lib.code_index.models import Symbol, FrameworkType, DjangoType
from lib.code_index.visitor import extract_symbols

logger = logging.getLogger(__name__)

# Directories to skip during indexing
SKIP_DIRS = frozenset({
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", ".venv", "target", "vendor", "Pods", ".git",
    ".gradle", ".idea", ".vscode", "coverage", ".nyc_output",
    ".tox", ".eggs", "*.egg-info", "migrations",
    ".worktrees", ".worktree",
})

# Default database path
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("HOME", os.path.expanduser("~")),
    ".claude", "data", "code-index.db"
)


class CodeIndex:
    """AST-based Python symbol index with SQLite + FTS5 storage.

    Supports multi-project indexing, incremental reindexing via file hashing,
    and full-text search with ranking.

    Args:
        db_path: Path to SQLite database. Use ":memory:" for ephemeral indexing.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self) -> None:
        """Create tables and indexes if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS code_symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                signature TEXT DEFAULT '',
                docstring TEXT DEFAULT '',
                decorators TEXT DEFAULT '',
                bases TEXT DEFAULT '',
                parent TEXT DEFAULT '',
                django_type TEXT DEFAULT 'none',
                project TEXT DEFAULT '',
                indexed_at REAL NOT NULL,
                file_hash TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS indexed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                symbol_count INTEGER DEFAULT 0,
                project TEXT DEFAULT '',
                indexed_at REAL NOT NULL,
                UNIQUE(file_path, project)
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON code_symbols(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_type ON code_symbols(symbol_type);
            CREATE INDEX IF NOT EXISTS idx_symbols_file ON code_symbols(file_path);
            CREATE INDEX IF NOT EXISTS idx_symbols_django ON code_symbols(django_type);
            CREATE INDEX IF NOT EXISTS idx_symbols_parent ON code_symbols(parent);
            CREATE INDEX IF NOT EXISTS idx_symbols_project ON code_symbols(project);
        """)

        # FTS5 virtual table for full-text search
        # Check if it exists first (CREATE VIRTUAL TABLE IF NOT EXISTS
        # isn't universally supported)
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='code_symbols_fts'"
        )
        if not cursor.fetchone():
            self.conn.execute("""
                CREATE VIRTUAL TABLE code_symbols_fts USING fts5(
                    name, qualified_name, docstring, signature,
                    content='code_symbols',
                    content_rowid='id'
                )
            """)
            # Triggers for automatic FTS sync
            self.conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS code_symbols_ai AFTER INSERT ON code_symbols BEGIN
                    INSERT INTO code_symbols_fts(rowid, name, qualified_name, docstring, signature)
                    VALUES (new.id, new.name, new.qualified_name, new.docstring, new.signature);
                END;

                CREATE TRIGGER IF NOT EXISTS code_symbols_ad AFTER DELETE ON code_symbols BEGIN
                    INSERT INTO code_symbols_fts(code_symbols_fts, rowid, name, qualified_name, docstring, signature)
                    VALUES ('delete', old.id, old.name, old.qualified_name, old.docstring, old.signature);
                END;

                CREATE TRIGGER IF NOT EXISTS code_symbols_au AFTER UPDATE ON code_symbols BEGIN
                    INSERT INTO code_symbols_fts(code_symbols_fts, rowid, name, qualified_name, docstring, signature)
                    VALUES ('delete', old.id, old.name, old.qualified_name, old.docstring, old.signature);
                    INSERT INTO code_symbols_fts(rowid, name, qualified_name, docstring, signature)
                    VALUES (new.id, new.name, new.qualified_name, new.docstring, new.signature);
                END;
            """)

        self.conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _hash_file(self, file_path: str) -> str:
        """Compute SHA256 hash of a file's contents."""
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    def _should_skip_dir(self, dir_name: str) -> bool:
        """Check if a directory should be skipped."""
        return dir_name in SKIP_DIRS or dir_name.endswith(".egg-info")

    def _track_file(self, file_path: str, file_hash: str, symbol_count: int,
                    project: str) -> None:
        """Record a file as indexed (upsert into indexed_files)."""
        self.conn.execute("""
            INSERT INTO indexed_files (file_path, file_hash, symbol_count, project, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path, project)
            DO UPDATE SET file_hash=?, symbol_count=?, indexed_at=?
        """, (file_path, file_hash, symbol_count, project, time.time(),
              file_hash, symbol_count, time.time()))

    def _cleanup_deleted_files(self, project: str, current_files: set[str]) -> int:
        """Remove symbols for files that no longer exist on disk.

        Returns the number of files cleaned up.
        """
        cursor = self.conn.execute(
            "SELECT DISTINCT file_path FROM indexed_files WHERE project = ?",
            (project,)
        )
        indexed_paths = {row["file_path"] for row in cursor.fetchall()}
        deleted = indexed_paths - current_files
        if not deleted:
            return 0

        for file_path in deleted:
            self.conn.execute(
                "DELETE FROM code_symbols WHERE file_path = ? AND project = ?",
                (file_path, project)
            )
            self.conn.execute(
                "DELETE FROM indexed_files WHERE file_path = ? AND project = ?",
                (file_path, project)
            )
        return len(deleted)

    def _module_name_from_path(self, file_path: str, project_root: str) -> str:
        """Derive dotted module name from file path relative to project root."""
        try:
            rel = os.path.relpath(file_path, project_root)
            parts = rel.replace(os.sep, "/").split("/")
            # Remove .py extension
            if parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]
            # Remove __init__
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts)
        except ValueError:
            return ""

    def index_project(self, project_path: str, project: str = "",
                      force: bool = False) -> dict:
        """Index all Python files in a project directory.

        Args:
            project_path: Root directory of the project.
            project: Project identifier (defaults to directory basename).
            force: If True, reindex all files regardless of hash.

        Returns:
            Dict with indexing statistics.
        """
        project_path = os.path.abspath(project_path)
        if not project:
            project = os.path.basename(project_path)

        stats = {"files_scanned": 0, "files_indexed": 0, "files_skipped": 0,
                 "symbols_added": 0, "files_deleted": 0, "errors": 0}
        current_files: set[str] = set()

        # Performance timing accumulators
        _t_total_start = time.perf_counter()
        _t_hashing = 0.0
        _t_incr_check = 0.0
        _t_ast = 0.0
        _t_db_insert = 0.0

        for root, dirs, files in os.walk(project_path):
            # Skip dependency/build directories (in-place modification)
            dirs[:] = [d for d in dirs if not self._should_skip_dir(d)]

            for filename in files:
                if not filename.endswith(".py"):
                    continue

                file_path = os.path.join(root, filename)
                current_files.add(file_path)
                stats["files_scanned"] += 1

                # Phase: file hashing
                _t0 = time.perf_counter()
                file_hash = self._hash_file(file_path)
                _t_hashing += time.perf_counter() - _t0

                # Phase: incremental check
                if not force:
                    _t0 = time.perf_counter()
                    cursor = self.conn.execute(
                        "SELECT file_hash FROM indexed_files WHERE file_path = ? AND project = ?",
                        (file_path, project)
                    )
                    row = cursor.fetchone()
                    _t_incr_check += time.perf_counter() - _t0
                    if row and row["file_hash"] == file_hash:
                        stats["files_skipped"] += 1
                        continue

                # Phase: AST extraction (read + parse)
                _t0 = time.perf_counter()
                try:
                    source = Path(file_path).read_text(errors="replace")
                except OSError:
                    _t_ast += time.perf_counter() - _t0
                    stats["errors"] += 1
                    continue

                module_name = self._module_name_from_path(file_path, project_path)
                symbols = extract_symbols(file_path, source, module_name, project)
                _t_ast += time.perf_counter() - _t0

                # Phase: DB insert (delete old + insert new + track)
                _t0 = time.perf_counter()
                self.conn.execute(
                    "DELETE FROM code_symbols WHERE file_path = ? AND project = ?",
                    (file_path, project)
                )

                now = time.time()
                for sym in symbols:
                    self.conn.execute("""
                        INSERT INTO code_symbols
                        (name, qualified_name, symbol_type, file_path, line_start,
                         line_end, signature, docstring, decorators, bases, parent,
                         django_type, project, indexed_at, file_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        sym.name, sym.qualified_name, sym.symbol_type,
                        sym.file_path, sym.line_start, sym.line_end,
                        sym.signature, sym.docstring,
                        ",".join(sym.decorators), ",".join(sym.bases),
                        sym.parent, sym.framework_type.value,
                        project, now, file_hash
                    ))
                    stats["symbols_added"] += 1

                self._track_file(file_path, file_hash, len(symbols), project)
                _t_db_insert += time.perf_counter() - _t0
                stats["files_indexed"] += 1

        # Phase: cleanup deleted files
        _t0 = time.perf_counter()
        deleted = self._cleanup_deleted_files(project, current_files)
        stats["files_deleted"] = deleted
        _t_cleanup = time.perf_counter() - _t0

        self.conn.commit()

        # Compute timing breakdown
        _t_total = time.perf_counter() - _t_total_start
        stats["timing"] = {
            "hashing_s": round(_t_hashing, 6),
            "incremental_check_s": round(_t_incr_check, 6),
            "ast_extraction_s": round(_t_ast, 6),
            "db_insert_s": round(_t_db_insert, 6),
            "cleanup_s": round(_t_cleanup, 6),
            "total_s": round(_t_total, 6),
            "files_per_sec": round(stats["files_indexed"] / _t_total, 1) if _t_total > 0 and stats["files_indexed"] else 0,
            "symbols_per_sec": round(stats["symbols_added"] / _t_total, 1) if _t_total > 0 and stats["symbols_added"] else 0,
        }

        # Best-effort JSONL event logging
        try:
            from lib.metrics_tracker import track_event
            track_event("code_index_completed", project=project,
                        files_scanned=stats["files_scanned"],
                        files_indexed=stats["files_indexed"],
                        files_skipped=stats["files_skipped"],
                        symbols_added=stats["symbols_added"],
                        errors=stats["errors"],
                        timing=stats["timing"], force=force)
        except Exception:
            pass

        return stats

    # ------------------------------------------------------------------
    # Query Methods
    # ------------------------------------------------------------------

    def _row_to_symbol(self, row: sqlite3.Row) -> Symbol:
        """Convert a database row to a Symbol object."""
        return Symbol(
            name=row["name"],
            qualified_name=row["qualified_name"],
            symbol_type=row["symbol_type"],
            file_path=row["file_path"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            signature=row["signature"] or "",
            docstring=row["docstring"] or "",
            decorators=row["decorators"].split(",") if row["decorators"] else [],
            bases=row["bases"].split(",") if row["bases"] else [],
            parent=row["parent"] or "",
            framework_type=FrameworkType(row["django_type"]) if row["django_type"] else FrameworkType.NONE,
            project=row["project"] or "",
        )

    def lookup(self, name: str, project: str = "") -> list[Symbol]:
        """Look up symbols by exact name or LIKE pattern.

        Args:
            name: Symbol name (supports SQL LIKE wildcards).
            project: Filter by project (empty = all projects).

        Returns:
            List of matching Symbol objects.
        """
        if "%" in name or "_" in name:
            query = "SELECT * FROM code_symbols WHERE name LIKE ?"
        else:
            query = "SELECT * FROM code_symbols WHERE name = ?"
        params: list = [name]

        if project:
            query += " AND project = ?"
            params.append(project)

        query += " ORDER BY symbol_type, name LIMIT 50"
        cursor = self.conn.execute(query, params)
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def search_fts(self, query: str, project: str = "",
                   limit: int = 30) -> list[Symbol]:
        """Full-text search across symbol names, docstrings, and signatures.

        Args:
            query: Search query (supports FTS5 syntax).
            project: Filter by project.
            limit: Max results (default 30).

        Returns:
            List of matching Symbol objects ranked by relevance.
        """
        # Add prefix matching for convenience
        fts_query = query
        if not any(c in query for c in ['"', '*', 'OR', 'AND', 'NOT']):
            # Simple query — add prefix matching
            terms = query.split()
            fts_query = " ".join(f"{t}*" for t in terms)

        sql = """
            SELECT cs.* FROM code_symbols cs
            JOIN code_symbols_fts fts ON cs.id = fts.rowid
            WHERE code_symbols_fts MATCH ?
        """
        params: list = [fts_query]

        if project:
            sql += " AND cs.project = ?"
            params.append(project)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            cursor = self.conn.execute(sql, params)
            return [self._row_to_symbol(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # FTS query syntax error — fall back to LIKE
            return self.lookup(f"%{query}%", project)

    def hierarchy(self, class_name: str, project: str = "") -> dict:
        """Get class hierarchy: the class + all its methods.

        Args:
            class_name: Name of the class.
            project: Filter by project.

        Returns:
            Dict with "class" (Symbol dict) and "methods" (list of Symbol dicts).
        """
        query = "SELECT * FROM code_symbols WHERE name = ? AND symbol_type = 'class'"
        params: list = [class_name]
        if project:
            query += " AND project = ?"
            params.append(project)

        cursor = self.conn.execute(query, params)
        class_row = cursor.fetchone()
        if not class_row:
            return {"class": None, "methods": []}

        cls = self._row_to_symbol(class_row)

        # Get all methods of this class
        method_query = "SELECT * FROM code_symbols WHERE parent = ? AND symbol_type = 'method'"
        method_params: list = [class_name]
        if project:
            method_query += " AND project = ?"
            method_params.append(project)
        method_query += " ORDER BY line_start"

        cursor = self.conn.execute(method_query, method_params)
        methods = [self._row_to_symbol(row) for row in cursor.fetchall()]

        return {"class": cls.to_dict(), "methods": [m.to_dict() for m in methods]}

    def django_list(self, django_type: str, project: str = "") -> list[Symbol]:
        """List all Django components of a given type.

        Args:
            django_type: One of model, view, serializer, admin, command, task, form, manager, middleware.
            project: Filter by project.

        Returns:
            List of matching Symbol objects.
        """
        query = "SELECT * FROM code_symbols WHERE django_type = ? AND symbol_type = 'class'"
        params: list = [django_type]
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY file_path, line_start"

        cursor = self.conn.execute(query, params)
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def framework_list(self, framework_type: str, project: str = "") -> list[Symbol]:
        """List all framework components of a given type.

        Args:
            framework_type: Component type value (e.g. model, view, blueprint, router).
            project: Filter by project.

        Returns:
            List of matching Symbol objects.
        """
        return self.django_list(framework_type, project)  # same underlying query

    def stats(self, project: str = "") -> dict:
        """Get index statistics.

        Args:
            project: Filter by project (empty = all).

        Returns:
            Dict with total counts, type breakdown, Django component counts,
            top modules, and project list.
        """
        where = "WHERE project = ?" if project else ""
        params: list = [project] if project else []

        # Total symbols
        cursor = self.conn.execute(
            f"SELECT COUNT(*) as total FROM code_symbols {where}", params
        )
        total = cursor.fetchone()["total"]

        # By type
        cursor = self.conn.execute(
            f"SELECT symbol_type, COUNT(*) as cnt FROM code_symbols {where} GROUP BY symbol_type",
            params
        )
        by_type = {row["symbol_type"]: row["cnt"] for row in cursor.fetchall()}

        # Django types
        if project:
            cursor = self.conn.execute(
                "SELECT django_type, COUNT(*) as cnt FROM code_symbols "
                "WHERE project = ? AND django_type != 'none' GROUP BY django_type",
                [project]
            )
        else:
            cursor = self.conn.execute(
                "SELECT django_type, COUNT(*) as cnt FROM code_symbols "
                "WHERE django_type != 'none' GROUP BY django_type"
            )
        django_types = {row["django_type"]: row["cnt"] for row in cursor.fetchall()}

        # Top modules (by symbol count)
        if project:
            cursor = self.conn.execute(
                "SELECT file_path, COUNT(*) as cnt FROM code_symbols "
                "WHERE project = ? GROUP BY file_path ORDER BY cnt DESC LIMIT 10",
                [project]
            )
        else:
            cursor = self.conn.execute(
                "SELECT file_path, COUNT(*) as cnt FROM code_symbols "
                "GROUP BY file_path ORDER BY cnt DESC LIMIT 10"
            )
        top_modules = [
            {"file": row["file_path"], "symbols": row["cnt"]}
            for row in cursor.fetchall()
        ]

        # Projects
        cursor = self.conn.execute(
            "SELECT DISTINCT project FROM code_symbols WHERE project != ''"
        )
        projects = [row["project"] for row in cursor.fetchall()]

        # Files indexed
        if project:
            cursor = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM indexed_files WHERE project = ?",
                [project]
            )
        else:
            cursor = self.conn.execute("SELECT COUNT(*) as cnt FROM indexed_files")
        files_indexed = cursor.fetchone()["cnt"]

        return {
            "total_symbols": total,
            "files_indexed": files_indexed,
            "by_type": by_type,
            "django_types": django_types,
            "top_modules": top_modules,
            "projects": projects,
        }

    def get_api_endpoints(self, project: str = "") -> list[dict]:
        """Extract API endpoints from indexed symbols.

        Looks for Django URL patterns, DRF viewsets, and decorated handlers.
        Returns a list suitable for populating RepoAnalysis.api_surface.
        """
        endpoints = []

        # DRF views and viewsets
        views = self.framework_list("view", project)
        for view in views:
            method = "ViewSet" if "ViewSet" in ",".join(view.bases) else "View"
            endpoints.append({
                "method": method,
                "path": view.name,
                "file": view.file_path,
                "line": view.line_start,
            })

        # Decorated API endpoints (@api_view, @action)
        if project:
            cursor = self.conn.execute(
                "SELECT * FROM code_symbols WHERE project = ? "
                "AND (decorators LIKE '%api_view%' OR decorators LIKE '%action%' "
                "OR decorators LIKE '%route%')",
                [project]
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM code_symbols WHERE "
                "decorators LIKE '%api_view%' OR decorators LIKE '%action%' "
                "OR decorators LIKE '%route%'"
            )
        for row in cursor.fetchall():
            sym = self._row_to_symbol(row)
            endpoints.append({
                "method": "handler",
                "path": sym.name,
                "file": sym.file_path,
                "line": sym.line_start,
            })

        return endpoints

    def get_overview(self, project: str = "") -> dict:
        """Get a compact overview suitable for onboarding context."""
        s = self.stats(project)
        return {
            "total_symbols": s["total_symbols"],
            "files_indexed": s["files_indexed"],
            "classes": s["by_type"].get("class", 0),
            "functions": s["by_type"].get("function", 0),
            "methods": s["by_type"].get("method", 0),
            "django_components": s["django_types"],
            "top_modules": s["top_modules"][:5],
        }

    def get_django_components(self, project: str = "") -> dict[str, int]:
        """Get Django component type counts."""
        s = self.stats(project)
        return s["django_types"]

    def get_framework_components(self, project: str = "") -> dict[str, int]:
        """Get framework component type counts."""
        return self.get_django_components(project)
