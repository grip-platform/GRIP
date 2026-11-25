"""Multi-tier LRU for session-context retrieval.

V>>'s 6-tier escalation ladder (Rule 0 retrieval discipline applied to
context-fetch). Monotonically increasing token cost per tier; depth-6
escalation bound = tier count.

Tier table:
  0 mem        OrderedDict           ~0 tokens     <1ms     35% hit (est)
  1 kono-disk  sqlite3 + emb-key     ~50 tokens     5ms     25%
  2 grip-first KONO semantic search  ~200 tokens   200ms    20%
  3 conv-search CC chat-history       ~1k tokens   800ms    10%
  4 cc-find    CC native Find        ~2k tokens   1.5s      6%
  5 rg         ripgrep on stash/     ~5k tokens   3.0s      4%
  baseline: full JSONL re-load        ~88k tokens  — (the loss avoided)

EV/call (warm): ~472 tokens vs 88k baseline → ~186x saving IF hit-rate
distribution holds. H-LRU-1 verifies.

Hypotheses (registered 2026-05-24):
  H-LRU-1:       tier-0+1 hit-rate ≥60% warm (deadline 2026-06-24)
  H-LRU-2:       string-hash mem-key hit-rate ≥30% (gates embedding upgrade)
  H-SCROLL-CC-1: `clear && printf '\\e[3J'` does NOT drop CC context tokens
  H-SCROLL-1:    skill reduces median session start-up tokens ≥50%
  H-STASH-1:     auto-prune keeps stash dir ≤200MB after 60 days

Companion: lib/fold_compress.py (Banach fold) as the stash compressor.
Companion: lib/prefix_compaction.py (pre-idle prefix compaction — cheapens
           the unavoidable post-idle cache-miss reprocess; does NOT prevent it).
Plan:      plans/scrollback-and-stash-2026-05-24.md
"""
from __future__ import annotations

import functools
import hashlib
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

_STASH_DIR = Path.home() / ".claude" / "stash"
_INDEX_DB = _STASH_DIR / "index.db"

_TIER_NAMES = (
    "mem",
    "kono-disk",
    "grip-first",
    "conv-search",
    "cc-find",
    "rg",
)


@dataclass
class TierHit:
    """One tier's lookup result. Confidence monotone non-increasing with tier_id."""

    tier_id: int
    tier_name: str
    content: Optional[str]
    token_cost: int
    latency_ms: float
    confidence: float


@dataclass
class LRUConfig:
    """Tunable parameters; defaults match plan §4.1."""

    mem_max_entries: int = 128
    disk_max_entries: int = 4096
    confidence_threshold: float = 0.7  # escalation gate: fetch() stops at first tier >= this
    embedding_match_threshold: float = 0.85  # tier-2 semantic-quality gate (KONO similarity floor)
    rg_fallback_confidence: float = 0.72  # tier-5 weak-hit confidence (config-coupled, not magic)
    kono_timeout: float = 1.5  # tier-2 KONO timeout, passed explicitly (mirrors grip_first_common.KONO_TIMEOUT)


def _embed_key(query: str) -> str:
    """Semantic-key SHA-256 hash (16-hex prefix). MVP — string-only.

    H-LRU-2 gates upgrade to sentence-transformers embedding if string-hash
    hit-rate <30% after 30 days.
    """
    return hashlib.sha256(query.lower().encode()).hexdigest()[:16]


def _ensure_index_db() -> sqlite3.Connection:
    """Open (or create) the stash index DB. Idempotent."""
    _STASH_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(_INDEX_DB))
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS stash (
            embedding_key TEXT PRIMARY KEY,
            content TEXT,
            sha256 TEXT,
            byte_size INTEGER,
            created_at REAL
        )
        """
    )
    db.commit()
    return db


@functools.lru_cache(maxsize=1)
def _load_kono_search():
    """Lazy, memoised dual-path import of KONO semantic search (fail-open → None).

    Memoised with ``lru_cache(maxsize=1)`` so the dual-path import is attempted
    at most once per process — repeated tier-2 calls reuse the cached result
    (AGORA #3373 r2). scrollback_lru may be imported with either ``~/.claude``
    or ``~/.claude/lib`` on ``sys.path``; try both. Only ``ImportError`` is
    caught — a SyntaxError / other bug inside ``grip_first_common`` must NOT be
    silently masked as "KONO unavailable" (AGORA #3373 Speaker B). The returned
    callable has signature ``kono_search(prompt, timeout=KONO_TIMEOUT) ->
    (is_grip, score, embed_type, snippet)``; tier-2 passes ``timeout`` explicitly
    (``LRUConfig.kono_timeout``) so it does not rely on the callee default
    (AGORA #3373 r2). Returns None only when the module is genuinely absent, so
    tier-2 degrades to a clean miss.
    """
    try:
        from grip_first_common import kono_search  # PYTHONPATH=.../lib

        return kono_search
    except ImportError:
        pass
    try:
        from lib.grip_first_common import kono_search  # PYTHONPATH=...

        return kono_search
    except ImportError:
        return None


class ScrollbackLRU:
    """6-tier LRU for session-context retrieval.

    Recurse-pattern fetch: each tier evaluates confidence ≥ threshold; misses
    escalate. Hard cap at depth=6 = len(tier_fns) → no infinite loop.
    """

    def __init__(self, config: Optional[LRUConfig] = None):
        self._cfg = config or LRUConfig()
        self._mem: "OrderedDict[str, TierHit]" = OrderedDict()
        self._db = _ensure_index_db()
        self._tier_fns: List[Callable[[str, str], TierHit]] = [
            self._tier0_mem,
            self._tier1_kono_disk,
            self._tier2_grip_first,
            self._tier3_conv_search,
            self._tier4_cc_find,
            self._tier5_rg_fallback,
        ]

    def fetch(self, query: str, depth: int = 6) -> TierHit:
        """CLU-style escalation. Returns first hit ≥ threshold or miss sentinel."""
        key = _embed_key(query)
        for tier_id in range(min(depth, len(self._tier_fns))):
            hit = self._tier_fns[tier_id](query, key)
            if hit.content and hit.confidence >= self._cfg.confidence_threshold:
                self._promote(key, hit)
                return hit
        return TierHit(-1, "miss", None, 0, 0.0, 0.0)

    def stash(self, query: str, content: str) -> str:
        """Insert content into tier-1 disk. Returns SHA-256 of content bytes."""
        key = _embed_key(query)
        sha = hashlib.sha256(content.encode()).hexdigest()
        self._db.execute(
            "INSERT OR REPLACE INTO stash (embedding_key, content, sha256, byte_size, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, content, sha, len(content.encode()), time.time()),
        )
        self._db.commit()
        return sha

    def _promote(self, key: str, hit: TierHit) -> None:
        """LRU write-back: copy hot results to tier-0 (mem).

        Re-tags as tier_id=0 so subsequent fetch() reports mem-hit, not the
        original source tier. Confidence preserved; cost zeroed; latency zeroed.
        """
        promoted = TierHit(
            tier_id=0,
            tier_name="mem",
            content=hit.content,
            token_cost=0,
            latency_ms=0.0,
            confidence=hit.confidence,
        )
        self._mem[key] = promoted
        self._mem.move_to_end(key)
        if len(self._mem) > self._cfg.mem_max_entries:
            self._mem.popitem(last=False)

    def _tier0_mem(self, query: str, key: str) -> TierHit:
        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]
        return TierHit(0, "mem", None, 0, 0.0, 0.0)

    def _tier1_kono_disk(self, query: str, key: str) -> TierHit:
        t0 = time.perf_counter()
        row = self._db.execute(
            "SELECT content FROM stash WHERE embedding_key=? LIMIT 1",
            (key,),
        ).fetchone()
        latency = (time.perf_counter() - t0) * 1000
        if row:
            return TierHit(1, "kono-disk", row[0], len(row[0]) // 4, latency, 0.9)
        return TierHit(1, "kono-disk", None, 0, latency, 0.0)

    def _tier2_grip_first(self, query: str, key: str) -> TierHit:
        """grip-first KONO semantic search, gated on embedding_match_threshold.

        Two-level gating: the KONO similarity must clear
        ``embedding_match_threshold`` (semantic-quality gate, default 0.85) to
        count as a hit; the resulting confidence then clears
        ``confidence_threshold`` (escalation gate, 0.7) in fetch(). 0.85 > 0.7,
        so any semantic hit also stops escalation. KONO unavailable / timed out
        / no GRIP match -> clean miss (fail-open).
        """
        t0 = time.perf_counter()
        is_grip, score, snippet = None, 0.0, ""
        try:
            kono_search = _load_kono_search()
            if kono_search is not None:
                # timeout passed explicitly (not relying on the callee default)
                # -> (is_grip, score, embed_type, snippet).
                is_grip, score, _embed_type, snippet = kono_search(
                    query, timeout=self._cfg.kono_timeout
                )
        except Exception:
            # Tier-level fail-open (deliberate, honest tradeoff): a non-critical
            # retrieval tier must never crash the session, so ANY error during the
            # optional KONO consult -- including an unexpected runtime error inside
            # kono_search -- is degraded to a clean miss here rather than
            # propagating. This broad catch DOES mask runtime errors as misses by
            # design; the narrowed ImportError in _load_kono_search keeps a
            # missing vs broken MODULE distinguishable at import time (AGORA #3373
            # r2 Speaker B: the comment now matches the catch-all behaviour).
            is_grip, score, snippet = None, 0.0, ""
        latency = (time.perf_counter() - t0) * 1000
        if is_grip and snippet and float(score) >= self._cfg.embedding_match_threshold:
            return TierHit(2, "grip-first", snippet, len(snippet) // 4, latency, float(score))
        return TierHit(2, "grip-first", None, 0, latency, 0.0)

    def _tier3_conv_search(self, query: str, key: str) -> TierHit:
        # Deferred: CC chat-history search is a harness/skill-layer capability
        # (skills/chat-history-search) with no programmatic Python entrypoint
        # callable from this lib module. Wiring a fake that always "succeeds"
        # would violate never-false-close -- kept an explicit miss until a
        # lib-callable backend exists. Tracked: task #2023.
        return TierHit(3, "conv-search", None, 0, 0.0, 0.0)

    def _tier4_cc_find(self, query: str, key: str) -> TierHit:
        # Deferred: CC native Find is a harness tool, not reachable from the
        # lib layer. Same honest-deferral rationale as tier 3. Tracked: #2023.
        return TierHit(4, "cc-find", None, 0, 0.0, 0.0)

    def _tier5_rg_fallback(self, query: str, key: str) -> TierHit:
        """rg-analogue last resort: substring scan over stashed content.

        The stash is a SQLite table (not loose files), so the ripgrep analogue
        is an escaped LIKE scan over ``stash.content``. Reached only when
        exact-key (tier 1) and semantic (tier 2) miss. LIKE metacharacters in
        the query are escaped so a literal '%'/'_' cannot act as a wildcard.
        Confidence = LRUConfig.rg_fallback_confidence (default 0.72) -- a weak
        hit that still clears the 0.7 escalation gate; config-coupled so it
        tracks confidence_threshold instead of a hard-coded magic number
        (AGORA #3373 C2).

        A leading-wildcard LIKE is an O(n) scan by design -- this IS the rg
        analogue, a last-resort full-content fallback; LIMIT 1 returns on the
        first match. The stash is size-bounded (H-STASH-1 <=200MB), so an FTS5
        index is a deliberate YAGNI deferral, not an oversight (AGORA #3373 r2).
        """
        t0 = time.perf_counter()
        needle = query.strip()
        if not needle:
            return TierHit(5, "rg", None, 0, 0.0, 0.0)
        safe = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        row = self._db.execute(
            "SELECT content FROM stash WHERE content LIKE ? ESCAPE '\\' LIMIT 1",
            (f"%{safe}%",),
        ).fetchone()
        latency = (time.perf_counter() - t0) * 1000
        if row:
            return TierHit(
                5, "rg", row[0], len(row[0]) // 4, latency, self._cfg.rg_fallback_confidence
            )
        return TierHit(5, "rg", None, 0, latency, 0.0)
