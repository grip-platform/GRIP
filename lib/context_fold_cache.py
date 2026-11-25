"""Context-Fold Cache — Banach fold memoisation for GRIP's re-derived STATIC context.

Sibling of ``lib/prompt_fold_cache.py`` (the PROMPT cache). Where that memoises
user-prompt dispatches keyed on ``sha256(fold(prompt))`` (whitespace-variant
reuse), THIS memoises expensive DETERMINISTIC derivations FROM static GRIP
context — the holistic-kernel synthesis (a pure reduction of the memory
substrate, re-run every ``/save`` + periodic mesh hook), a grounding/council
prompt, any pure function of slow-changing files — with an LRU-bounded,
content-addressed, replayable store.

Composes the proven primitives, never reimplements them:
  - ``lib/fold_compress.fold`` / ``is_fixed_point`` — the Banach fixed-point key.
  - the content-addressed atomic-blob + JSONL-record storage shape.

DELIBERATE, MEASURED key-mode default (dogfood domain #4, 2026-07-05):
  key_mode="raw"    -> key = sha256(source)            (~0.07 ms on 237 KB)  [DEFAULT]
  key_mode="folded" -> key = fold(source).final_sha256 (~14   ms on 237 KB)

  Folding a large STATIC substrate to build the key is a NET LOSS when the
  memoised derivation is CHEAP. Skipping GRIP's holistic synthesis (1.469 ms
  median) via a *folded* key COSTS 14.089 ms of fold => -12.62 ms/hit. A *raw*
  key keeps the saving positive: pay 0.067 ms hash, skip 1.469 ms => +~1.40
  ms/hit (compute/latency, small but real). Numbers reproduced by
  ``scripts/benchmark_context_fold_cache.py``.

  The ``folded`` key pays off ONLY when the derivation cost >> the fold cost —
  i.e. an LLM call (100s of ms + tokens), where paying ~14 ms to skip the whole
  provider call is hugely net-positive. That LLM token-saving is the MECHANISM
  this cache provides (a hit skips the entire ``deriver`` call, tokens and all).
  No live GRIP path re-issues a deterministic LLM call with an identical folded
  prompt today (verified 2026-07-05: ``fold``/``run_or_get`` have no production
  consumer; the holistic synthesiser is pure Python, not an LLM call), so the
  token figure is a MECHANISM proof (calls-skipped), never a live measurement —
  matching the prior art's honesty boundary
  (``artifacts/banach-fold-memoisation-ultraplan/``: "speedups are wall-clock
  samples ... large but not constants").

Idempotence (Banach fixed point): the folded key is a fixed point of the fold
contraction — ``is_fixed_point(fold(x).text)`` is True — so ``derive(x)`` then
``derive(x)`` yield the SAME key and the second is a hit; the cache is
idempotent. The key is a pure function of the source content, so a one-byte
source change flips the key => automatic miss, never stale reuse.

Storage root: ``$GRIP_CONTEXT_FOLD_CACHE_DIR`` or
``<claude_dir>/state/cache/context-fold/`` (``state/**`` is gitignored).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import fcntl  # noqa: E402  -- POSIX fcntl, or the LockFileEx lock on Windows
from claude_dir import get_claude_dir  # noqa: E402
from fold_compress import fold, is_fixed_point  # noqa: E402

SCHEMA_VERSION = "context-fold-cache/0.1"
CACHE_DIR_ENV = "GRIP_CONTEXT_FOLD_CACHE_DIR"
INDEX_NAME = "index.json"
BLOBS_NAME = "blobs"
RECORDS_NAME = "records.jsonl"
DEFAULT_MAX_ENTRIES = 256

KeyMode = Literal["raw", "folded"]
Route = Literal["fresh", "hit"]


@dataclass(frozen=True)
class DerivationRequest:
    """One cacheable derivation from a static context source."""

    source: bytes
    task_type: str
    key_mode: KeyMode = "raw"
    ttl_seconds: Optional[int] = None


@dataclass(frozen=True)
class DerivationDecision:
    """Replayable outcome of one cache pass."""

    route: Route
    cache_key: str
    key_mode: KeyMode
    folded_sha256: str
    fold_iterations: int
    output: Optional[bytes]
    reason: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_cache_dir(cache_dir: Optional[Path] = None) -> Path:
    """Env override -> explicit arg -> <claude_dir>/state/cache/context-fold."""
    if cache_dir is not None:
        root = Path(cache_dir)
    elif os.environ.get(CACHE_DIR_ENV, "").strip():
        root = Path(os.environ[CACHE_DIR_ENV]).expanduser()
    else:
        root = get_claude_dir() / "state" / "cache" / "context-fold"
    (root / BLOBS_NAME).mkdir(parents=True, exist_ok=True)
    return root


def _key_and_input(source: bytes, key_mode: KeyMode) -> Tuple[str, bytes, str, int]:
    """(cache_key, effective_input, folded_sha256, fold_iterations).

    raw:    key = sha256(source); the deriver sees the raw source; the fold is
            NOT computed on the hot path (that is the whole point — see module
            docstring's measured net-loss finding).
    folded: key = fold(source).final_sha256; the deriver sees the canonical
            folded text (whitespace-variant sources collapse to one entry).
    """
    if key_mode == "folded":
        fr = fold(source)
        return fr.final_sha256, fr.text, fr.final_sha256, fr.iterations
    return _sha(source), source, "", 0


def _blob_write(root: Path, data: bytes) -> str:
    """Content-addressed atomic write; returns the sha256 address."""
    sha = _sha(data)
    dest = root / BLOBS_NAME / f"{sha}.bin"
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    return sha


def _blob_read(root: Path, sha: str) -> Optional[bytes]:
    """Hash-revalidated read: a corrupted/absent blob reads as None."""
    path = root / BLOBS_NAME / f"{sha}.bin"
    if not path.exists():
        return None
    data = path.read_bytes()
    return data if _sha(data) == sha else None


def _load_index(root: Path) -> dict:
    path = root / INDEX_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # a torn index never poisons the cache — rebuild empty


def _save_index(root: Path, index: dict) -> None:
    path = root / INDEX_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _append_record(root: Path, record: dict) -> None:
    with open(root / RECORDS_NAME, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _ttl_ok(entry: dict, request: DerivationRequest, now_epoch: float) -> bool:
    """Expired if EITHER the stored ttl or the reader's ttl bound is exceeded."""
    age = now_epoch - float(entry.get("created_epoch", 0.0))
    bounds = [b for b in (entry.get("ttl_seconds"), request.ttl_seconds) if b]
    return all(age <= bound for bound in bounds)


def _evict_lru(index: dict, root: Path, max_entries: int) -> None:
    """Bound the index to max_entries by least-recently-used; drop orphan blobs."""
    while len(index) > max_entries:
        victim = min(index, key=lambda k: index[k].get("last_used_epoch", 0.0))
        dropped = index.pop(victim)
        sha = dropped.get("output_sha256", "")
        still_referenced = any(e.get("output_sha256") == sha for e in index.values())
        if sha and not still_referenced:
            blob = root / BLOBS_NAME / f"{sha}.bin"
            blob.unlink(missing_ok=True)


def derive(
    request: DerivationRequest,
    deriver: Callable[[bytes], bytes],
    *,
    cache_dir: Optional[Path] = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    now: Optional[float] = None,
) -> DerivationDecision:
    """Content-hash memoise ``deriver`` over ``request.source``.

    Hit  -> return the cached output; the ``deriver`` is NOT called (the saving:
            all of its compute — and, for an LLM deriver, all of its tokens).
    Miss -> ``deriver(effective_input)`` runs, its output is stored, returned.

    ``deriver`` MUST be a deterministic pure function of its input for the cache
    to be sound (same input -> same output). Verified for GRIP's holistic
    synthesis; the caller owns this contract for other derivers.
    """
    root = resolve_cache_dir(cache_dir)
    now_epoch = time.time() if now is None else float(now)
    key, effective_input, folded_sha, iters = _key_and_input(
        request.source, request.key_mode)
    index = _load_index(root)

    def finish(route: Route, output: Optional[bytes], reason: str) -> DerivationDecision:
        _append_record(root, {
            "v": SCHEMA_VERSION, "cache_key": key, "key_mode": request.key_mode,
            "route": route, "task_type": request.task_type,
            "folded_sha256": folded_sha, "fold_iterations": iters,
            "output_sha256": _sha(output) if output is not None else None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch)),
        })
        return DerivationDecision(
            route=route, cache_key=key, key_mode=request.key_mode,
            folded_sha256=folded_sha, fold_iterations=iters,
            output=output, reason=reason)

    entry = index.get(key)
    if entry is not None and _ttl_ok(entry, request, now_epoch):
        cached = _blob_read(root, entry.get("output_sha256", ""))
        if cached is not None:
            entry["last_used_epoch"] = now_epoch
            _save_index(root, index)
            return finish("hit", cached, "hit:key_match")

    output = deriver(effective_input)
    if not isinstance(output, bytes):
        raise ValueError("deriver must return bytes")
    output_sha = _blob_write(root, output)
    index[key] = {
        "output_sha256": output_sha, "folded_sha256": folded_sha,
        "key_mode": request.key_mode, "task_type": request.task_type,
        "fold_iterations": iters, "ttl_seconds": request.ttl_seconds,
        "created_epoch": now_epoch, "last_used_epoch": now_epoch,
    }
    _evict_lru(index, root, max_entries)
    _save_index(root, index)
    reason = "miss:ttl_expired" if entry is not None else "miss:no_entry"
    return finish("fresh", output, reason)


def is_idempotent_key(source: bytes) -> bool:
    """Banach anchor: the folded key text is a fixed point of the fold
    contraction, so a second ``derive`` over the same source keys identically.
    Single-step probe (``is_fixed_point`` on the converged fold text)."""
    return is_fixed_point(fold(source).text)


def stats(cache_dir: Optional[Path] = None) -> dict:
    """Aggregate metrics from the index."""
    root = resolve_cache_dir(cache_dir)
    index = _load_index(root)
    modes: dict = {}
    for entry in index.values():
        modes[entry.get("key_mode", "?")] = modes.get(entry.get("key_mode", "?"), 0) + 1
    return {
        "entries": len(index),
        "distinct_outputs": len({e.get("output_sha256") for e in index.values()}),
        "by_key_mode": modes,
        "cache_dir": str(root),
    }


def clear_local(cache_dir: Optional[Path] = None) -> dict:
    """Delete the index, blobs, and records. Returns counts for the CLI."""
    root = resolve_cache_dir(cache_dir)
    index_path, records_path = root / INDEX_NAME, root / RECORDS_NAME
    n_entries = len(_load_index(root))
    blobs = list((root / BLOBS_NAME).glob("*.bin"))
    for blob in blobs:
        blob.unlink()
    for path in (index_path, records_path):
        if path.exists():
            path.unlink()
    return {"entries_deleted": n_entries, "blobs_deleted": len(blobs)}


__all__ = [
    "DerivationRequest", "DerivationDecision", "KeyMode",
    "derive", "is_idempotent_key", "resolve_cache_dir", "stats", "clear_local",
]
