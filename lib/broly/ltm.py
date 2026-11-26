"""
Broly Long-Term Memory - Dual-write to broly-ltm.md + embeddings.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

LTM persists across sessions via two backends:
    1. broly-ltm.md (human-readable, always available)
    2. EmbeddingEngine (semantic search, optional)

Writes go to ``broly-ltm.md``, NOT to MEMORY.md. LTM entries therefore no
longer take part in the session-start auto-memory injection: MEMORY.md is
truncated head-first, so appending there evicted a curated pointer rather
than adding an entry. Entries reach a session through :meth:`recall`, not
through automatic loading. MEMORY.md is still read for entries written by
the pre-store writer, and is migrated into the store on first access.

Available at fork+ power levels.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Locked file operations — prevents concurrent-write data loss on MEMORY.md
sys.path.insert(0, str(Path(__file__).parent.parent))
from memory_lock import locked_append  # noqa: E402

CLAUDE_DIR = Path.home() / ".claude"
MEMORY_DIR = CLAUDE_DIR / "projects"

# LTM entries are written here, as a sibling of MEMORY.md — never into
# MEMORY.md itself. MEMORY.md is the always-loaded index the harness reads at
# session start and truncates to its first N lines, keeping the head and
# discarding the tail. An append there therefore does not add an entry; it
# evicts a curated pointer from the bottom. The store is searched by recall()
# and loaded by nothing, so LTM can grow without costing session context.
LTM_STORE_FILENAME = "broly-ltm.md"

# Written into the store the first time legacy MEMORY.md entries are copied
# across, so the migration runs at most once per project.
_MIGRATION_MARKER = "<!-- broly-ltm: migrated legacy entries from MEMORY.md -->"


def _encode_project_path(project_path: str) -> str:
    """Encode a project path using Claude Code's directory naming scheme.

    Claude Code encodes project paths by replacing all forward slashes and dots
    with dashes. The leading slash becomes a leading dash.

    Examples::

        >>> _encode_project_path("~/my-project")
        '-Users-you-my-project'
        >>> _encode_project_path("~/.claude")
        '-Users-you--claude'
        >>> _encode_project_path("~/my.app")
        '-home-you-my-app'

    Args:
        project_path: Absolute filesystem path (forward slashes).

    Returns:
        Encoded directory name string.
    """
    return project_path.replace("/", "-").replace(".", "-")


def _get_git_root() -> Optional[str]:
    """Detect the git repository root via ``git rev-parse``.

    Returns:
        Absolute path to repo root, or None if not in a git repo or git
        is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        pass  # slow filesystem
    except FileNotFoundError:
        pass  # git not installed
    except OSError:
        pass  # permission or other OS error
    return None


def _resolve_project_memory_path() -> Path:
    """Resolve the native Claude Code project-scoped memory path.

    Combines git root detection with path encoding to produce the same
    directory path that Claude Code uses for auto-memory storage.

    The resolved MEMORY.md is the same file Claude Code loads (first 200
    lines) at session start. LTM does NOT write here — that truncation is
    why it must not. The path is resolved to locate the memory directory,
    which is where :data:`LTM_STORE_FILENAME` lives, and to read entries the
    pre-store writer left behind.

    Resolution order:
        1. ``git rev-parse --show-toplevel`` (matches Claude Code's repo-root scoping)
        2. ``os.getcwd()`` fallback (for non-git directories)

    Returns:
        Path to ``~/.claude/projects/{encoded}/memory/MEMORY.md``
    """
    project_path = _get_git_root() or os.getcwd()
    encoded = _encode_project_path(project_path)
    return CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"


class LongTermMemory:
    """
    Persistent memory via broly-ltm.md + embedding storage.

    Store: writes to both broly-ltm.md and the embeddings DB — never to
        MEMORY.md, which is the always-loaded, head-truncated index.
    Recall: hybrid search across both backends, plus a legacy read of
        MEMORY.md for entries the pre-store writer left there.
    """

    def __init__(self, memory_file: Optional[Path] = None):
        self._memory_file = memory_file or _resolve_project_memory_path()
        self._ltm_store = self._memory_file.parent / LTM_STORE_FILENAME
        self._migrated = False
        self._engine = None
        self._engine_available = False
        self._init_engine()

    def _init_engine(self) -> None:
        """Try to initialize the embedding engine."""
        try:
            if str(Path(__file__).resolve().parent.parent) not in sys.path:  # own lib (GRIP#9857)
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from embeddings import get_engine  # W5(a): dispatch via get_engine()
            self._engine = get_engine()
            self._engine_available = True
        except (ImportError, Exception):
            self._engine_available = False

    def store(
        self,
        key: str,
        value: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a memory in LTM (dual-write).

        Args:
            key: Memory identifier/topic
            value: Memory content
            metadata: Optional metadata dict

        Returns:
            True if at least one backend succeeded
        """
        success = False

        # Backend 1: the LTM store (never the always-loaded index)
        try:
            self._append_to_store(key, value)
            success = True
        except Exception:
            pass

        # Backend 2: Embeddings
        if self._engine_available and self._engine:
            try:
                self._engine.store_embedding(
                    text=f"{key}: {value}",
                    embed_type="ltm",
                    metadata=json.dumps({
                        "key": key,
                        "source": "broly_ltm",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **(metadata or {}),
                    }),
                )
                success = True
            except Exception:
                pass

        return success

    def recall(
        self,
        query: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Recall memories matching a query.

        Uses hybrid search if embeddings available, falls back to text
        search across the LTM store (and, for legacy entries, MEMORY.md).
        """
        results = []

        # Try semantic search first
        if self._engine_available and self._engine:
            try:
                hits = self._engine.search_hybrid(
                    query_text=query,
                    embed_type="ltm",
                    limit=limit,
                )
                for hit in hits:
                    results.append({
                        "content": hit.get("content", ""),
                        "score": hit.get("rrf_score", hit.get("similarity", 0.0)),
                        "metadata": hit.get("metadata"),
                        "source": "embeddings",
                    })
            except Exception:
                pass

        # Fallback: text search across the store + legacy MEMORY.md
        if not results:
            results = self._search_store(query, limit)

        return results[:limit]

    def _append_to_store(self, key: str, value: str) -> None:
        """Append a memory entry to the LTM store.

        Writes to ``broly-ltm.md``, never to MEMORY.md. See
        :data:`LTM_STORE_FILENAME` for why appending to the always-loaded
        index destroys entries rather than adding them.
        """
        self._ltm_store.parent.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"\n### {key} ({timestamp})\n{value}\n"

        locked_append(self._ltm_store, entry)

    def _migrate_legacy_once(self) -> None:
        """Copy legacy ``###`` entries out of MEMORY.md into the store, once.

        Reading MEMORY.md is not enough to keep legacy entries reachable.
        The old writer appended them to the TAIL, and the harness truncates
        MEMORY.md head-first — so the legacy entries sit in exactly the
        region that gets discarded, and a read-only fallback preserves them
        only until the next truncation.

        Copying cannot surface anything that was not already searchable:
        :meth:`_search_store` already reads the whole of MEMORY.md, so the
        migration changes durability, never visibility. MEMORY.md itself is
        never modified. The marker makes this idempotent, and it is written
        even when there is nothing to copy so the scan runs at most once.
        """
        if self._migrated:
            return
        self._migrated = True

        try:
            store_text = (
                self._ltm_store.read_text() if self._ltm_store.exists() else ""
            )
            if _MIGRATION_MARKER in store_text:
                return

            sections = []
            if self._memory_file.exists():
                legacy = self._memory_file.read_text()
                sections = [s for s in legacy.split("\n### ")[1:] if s.strip()]

            payload = "".join(f"\n### {s.rstrip()}\n" for s in sections)
            self._ltm_store.parent.mkdir(parents=True, exist_ok=True)
            locked_append(self._ltm_store, f"{_MIGRATION_MARKER}\n{payload}")
        except Exception:
            pass

    def _search_store(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Text search across the LTM store, with a legacy read fallback.

        Searches ``broly-ltm.md`` first, then MEMORY.md. Installs predating
        the store have LTM entries written into MEMORY.md by the old writer,
        and those must stay reachable — so MEMORY.md is still READ, it is
        just never written to again.

        Results are deduplicated by content: an entry stored before the
        migration and again after it exists in both files, and without this
        one logical memory would be returned twice at identical scores.
        """
        self._migrate_legacy_once()

        results: List[Dict[str, Any]] = []
        for path in (self._ltm_store, self._memory_file):
            results.extend(self._search_one_file(path, query))

        results.sort(key=lambda r: r["score"], reverse=True)

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for result in results:
            if result["content"] in seen:
                continue
            seen.add(result["content"])
            deduped.append(result)

        return deduped[:limit]

    def _search_one_file(self, path: Path, query: str) -> List[Dict[str, Any]]:
        """Score ``###``-delimited sections of one file against a query.

        Matches sections containing ALL query words (AND logic).
        Exact substring match scores higher than per-word match.

        ``source`` names the file the hit actually came from, so a caller can
        tell a store hit from a legacy MEMORY.md hit.
        """
        if not path.exists():
            return []

        source = "store" if path == self._ltm_store else "memory_file"
        results = []
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) >= 2]

        try:
            content = path.read_text()
            sections = content.split("\n### ")

            for section in sections:
                section_lower = section.lower()
                # Exact substring match (highest relevance)
                if query_lower in section_lower:
                    results.append({
                        "content": section.strip()[:500],
                        "score": 0.8,
                        "metadata": None,
                        "source": source,
                    })
                elif query_words:
                    # Per-word match: all words must be present
                    matched = sum(1 for w in query_words if w in section_lower)
                    if matched == len(query_words):
                        results.append({
                            "content": section.strip()[:500],
                            "score": 0.5 + (0.2 * matched / max(len(query_words), 1)),
                            "metadata": None,
                            "source": source,
                        })
        except Exception:
            pass

        # Ranking is the caller's job — it merges results across files first.
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get LTM statistics.

        ``file_entries`` counts both the store and the legacy MEMORY.md
        entries, matching what :meth:`_search_store` can actually retrieve.
        """
        file_entries = 0
        for path in (self._ltm_store, self._memory_file):
            if not path.exists():
                continue
            try:
                file_entries += path.read_text().count("\n### ")
            except Exception:
                pass

        return {
            "memory_file": str(self._memory_file),
            "ltm_store": str(self._ltm_store),
            "file_entries": file_entries,
            "embeddings_available": self._engine_available,
        }
