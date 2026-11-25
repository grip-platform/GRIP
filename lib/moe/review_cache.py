"""
MoE Review Cache
================

Hash-based diff fingerprinting to avoid re-scoring identical or near-identical PRs.
Uses content hashing with configurable similarity threshold.

Cache entries expire after a configurable TTL (default 24h).
Stored in state/moe/review-cache.json.

Usage:
    from lib.moe.review_cache import ReviewCache
    cache = ReviewCache()
    hit = cache.lookup(diff_text)
    if hit:
        print(f"Cache hit: {hit}")
    else:
        result = run_expensive_review(diff_text)
        cache.store(diff_text, result)
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_CACHE_PATH = CLAUDE_DIR / "state" / "moe" / "review-cache.json"
DEFAULT_TTL_SECONDS = 86400  # 24 hours
MAX_CACHE_ENTRIES = 100


def _diff_fingerprint(diff: str) -> str:
    """Generate a stable fingerprint for a diff.

    Normalises whitespace and strips line numbers to make the fingerprint
    robust to trivial reformatting. Uses SHA-256 truncated to 16 chars.
    """
    # Normalise: strip leading/trailing whitespace per line, collapse blank lines
    lines = []
    for line in diff.splitlines():
        stripped = line.strip()
        # Skip diff metadata lines that change between runs
        if stripped.startswith("index ") or stripped.startswith("diff --git"):
            continue
        lines.append(stripped)
    normalised = "\n".join(lines)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


class ReviewCache:
    """File-backed review result cache with TTL expiry."""

    def __init__(self, cache_path: Optional[Path] = None,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.cache_path = cache_path or DEFAULT_CACHE_PATH
        self.ttl_seconds = ttl_seconds
        self._cache = self._load()

    def _load(self) -> dict:
        """Load cache from disk."""
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text())
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        """Persist cache to disk."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2))

    def _evict_expired(self):
        """Remove entries older than TTL."""
        now = time.time()
        expired = [
            k for k, v in self._cache.items()
            if now - v.get("timestamp", 0) > self.ttl_seconds
        ]
        for k in expired:
            del self._cache[k]

    def _evict_overflow(self):
        """Remove oldest entries if cache is at or exceeds max size.

        Evicts enough to leave room for one new entry.
        """
        if len(self._cache) < MAX_CACHE_ENTRIES:
            return
        # Sort by timestamp, remove oldest (keep MAX - 1 to leave room)
        sorted_keys = sorted(
            self._cache.keys(),
            key=lambda k: self._cache[k].get("timestamp", 0)
        )
        to_remove = len(self._cache) - MAX_CACHE_ENTRIES + 1
        for k in sorted_keys[:to_remove]:
            del self._cache[k]

    def lookup(self, diff: str) -> Optional[dict]:
        """Look up a cached review result by diff fingerprint.

        Returns the cached result dict if found and not expired, else None.
        """
        self._evict_expired()
        fp = _diff_fingerprint(diff)
        entry = self._cache.get(fp)
        if entry is None:
            return None
        if time.time() - entry.get("timestamp", 0) > self.ttl_seconds:
            del self._cache[fp]
            return None
        return entry.get("result")

    def store(self, diff: str, result: dict):
        """Store a review result keyed by diff fingerprint."""
        self._evict_expired()
        self._evict_overflow()
        fp = _diff_fingerprint(diff)
        self._cache[fp] = {
            "timestamp": time.time(),
            "result": result,
        }
        self._save()

    def clear(self):
        """Clear all cached entries."""
        self._cache = {}
        self._save()

    @property
    def size(self) -> int:
        """Number of entries in cache."""
        return len(self._cache)
