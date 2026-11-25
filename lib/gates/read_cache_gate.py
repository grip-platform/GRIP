"""
Read Cache Gate (#933 Phase 2, #999 Phase 1)

Tracks file read counts per session and blocks redundant reads
(>MAX_READS_PER_FILE times). Compaction-aware: allows re-reads
when the file was modified since last read, or when the first
read is older than 10 minutes (proxy for context compaction).

Extracted from hooks/context-gate-hook.py Gate 4.

Performance: session-scoped in-memory cache eliminates per-call
disk I/O. Disk reads only on first access; disk writes only on
explicit flush_read_cache(). When cache_file is passed (tests),
bypasses in-memory cache for isolation.
"""

import json
import os
import time
from pathlib import Path


# Default read cache location (overridable for testing)
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
READ_CACHE_FILE = CLAUDE_DIR / "state" / "features" / "read-cache.json"

# Re-exported from patterns for backward compat
from .patterns import MAX_READS_PER_FILE

# Logging callback — configured by hook at import time
_log_hook = None

# In-memory cache (session-scoped, #999 Phase 1)
_CACHE: dict | None = None
_CACHE_DIRTY: bool = False


def configure_logging(log_fn):
    """Wire up the hook's log_hook function."""
    global _log_hook
    _log_hook = log_fn


def _log(level: str, message: str):
    """Log via configured callback, silently no-op if unconfigured."""
    if _log_hook:
        _log_hook(level, message)


def _load_from_disk(path: Path) -> dict:
    """Read cache JSON from disk. Returns empty cache on failure."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            _log("DEBUG", f"Read cache load failed: {e}")
    return {"reads": {}}


def _save_to_disk(cache: dict, path: Path):
    """Write cache JSON to disk."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        _log("DEBUG", f"Read cache save failed: {e}")


def load_read_cache(cache_file: Path = None) -> dict:
    """Load read cache. Uses in-memory cache when cache_file is None."""
    global _CACHE
    if cache_file is not None:
        # Test path: bypass in-memory cache for isolation
        return _load_from_disk(cache_file)
    # Production path: lazy-load into memory once
    if _CACHE is None:
        _CACHE = _load_from_disk(READ_CACHE_FILE)
    return _CACHE


def save_read_cache(cache: dict, cache_file: Path = None):
    """Save read cache. Defers to dirty flag when cache_file is None."""
    global _CACHE, _CACHE_DIRTY
    if cache_file is not None:
        # Test path: write directly to disk for assertion verification
        _save_to_disk(cache, cache_file)
        return
    # Production path: update in-memory, mark dirty (no disk write)
    _CACHE = cache
    _CACHE_DIRTY = True


def flush_read_cache(cache_file: Path = None):
    """Write in-memory cache to disk only if dirty. No-op if clean."""
    global _CACHE_DIRTY
    if cache_file is not None:
        # Test path: nothing to flush (tests write directly)
        return
    if _CACHE is not None and _CACHE_DIRTY:
        _save_to_disk(_CACHE, READ_CACHE_FILE)
        _CACHE_DIRTY = False


def invalidate_read_cache():
    """Force next load_read_cache() to re-read from disk."""
    global _CACHE, _CACHE_DIRTY
    _CACHE = None
    _CACHE_DIRTY = False


def _reset_read_cache_entry(cache: dict, key: str, mtime: float,
                             cache_file: Path = None):
    """Reset a read cache entry to fresh state."""
    global _CACHE_DIRTY
    cache["reads"][key] = {
        "count": 1,
        "first_read": time.time(),
        "last_mtime": mtime,
    }
    if cache_file is not None:
        # Test path: write directly for assertion verification
        _save_to_disk(cache, cache_file)
    else:
        # Production path: mark dirty, skip disk write
        _CACHE_DIRTY = True


def _ensure_reads_dict(cache: dict) -> None:
    """Make sure ``cache['reads']`` exists so downstream writes are safe."""
    if "reads" not in cache:
        cache["reads"] = {}


def _safe_mtime(path: str) -> float:
    """Return ``os.path.getmtime(path)`` or 0 if the file is missing."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _record_first_read(cache: dict, key: str, file_path: str, cache_file: Path | None) -> None:
    """Record the first read of a file in the session cache."""
    _ensure_reads_dict(cache)
    _reset_read_cache_entry(cache, key, _safe_mtime(file_path), cache_file)


def _parse_entry(entry) -> tuple[int, int, int]:
    """Return ``(count, first_read, last_mtime)`` tolerating the legacy int form."""
    if isinstance(entry, dict):
        return (entry.get("count", 0), entry.get("first_read", 0), entry.get("last_mtime", 0))
    return (entry, 0, 0)


def _maybe_reset_for_compaction(cache: dict, key: str, file_path: str,
                                 first_read: int, last_mtime: int,
                                 cache_file: Path | None) -> tuple[bool, float]:
    """Reset the cache entry if the file was modified or compaction has elapsed.

    Returns ``(was_reset, current_mtime)``.
    """
    current_mtime = _safe_mtime(file_path)

    modified_since_last_read = current_mtime > last_mtime and last_mtime > 0
    compaction_elapsed = first_read > 0 and (time.time() - first_read) > 600

    if modified_since_last_read:
        _log("ALLOW", f"Read cache: {file_path} modified since last read, resetting")
    elif compaction_elapsed:
        _log("ALLOW", f"Read cache: {file_path} first read >10min ago, allowing (compaction proxy)")
    else:
        return (False, current_mtime)

    _ensure_reads_dict(cache)
    _reset_read_cache_entry(cache, key, current_mtime, cache_file)
    return (True, current_mtime)


def check_read_cache(file_path: str, session_id: str,
                     max_reads: int = None,
                     cache_file: Path = None) -> str | None:
    """Check if file read exceeds threshold. Returns deny reason or None.

    Compaction-aware: allows re-reads when the file was modified since last
    read, or when the first read is older than 10 minutes (proxy for context
    compaction in long sessions).
    """
    if not file_path or not session_id:
        return None
    if max_reads is None:
        max_reads = MAX_READS_PER_FILE

    cache = load_read_cache(cache_file)
    key = f"{session_id}:{file_path}"
    entry = cache.get("reads", {}).get(key)

    if entry is None:
        _record_first_read(cache, key, file_path, cache_file)
        return None

    count, first_read, last_mtime = _parse_entry(entry)
    was_reset, current_mtime = _maybe_reset_for_compaction(
        cache, key, file_path, first_read, last_mtime, cache_file
    )
    if was_reset:
        return None

    if count >= max_reads:
        _log("BLOCK", f"Read cache: {file_path} already read {count}x")
        return f"[GUARDRAIL: READ CACHE] '{file_path}' already read {count}x this session (max: {max_reads}). Use your cached mental model. If file changed externally, user will mention it."

    _ensure_reads_dict(cache)
    cache["reads"][key] = {
        "count": count + 1,
        "first_read": first_read,
        "last_mtime": current_mtime,
    }
    save_read_cache(cache, cache_file)
    return None
