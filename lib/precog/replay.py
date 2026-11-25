"""PRECOG replay — fingerprint-keyed cache + replay-IDR emission.

H700 implication: if a prompt produces a fingerprint we have seen, and the
cached decision is still within its TTL window, we MAY emit a replay-IDR
(kind="precog-decision-replay") rather than re-reasoning. This preserves
the audit chain (a replay IDR is a real signed envelope) while avoiding
redundant high-tier reasoning cost.

Cache shape: one JSON file per fingerprint under state/precog/cache/.
File name = `<fingerprint>.json` (sha256 → 64 hex chars, filesystem-safe).
Atomicity: write to .tmp then os.replace (POSIX rename = atomic).

No LLM call, no network, no daemon. Pure file I/O.

Idempotency: writing the same (fingerprint, decision) twice is a no-op
(content-hash compare). Reads never mutate the cache.

R2 idempotency: the kernel_digest is already inside the fingerprint
(per fingerprint.py), so a kernel change auto-invalidates the cache by
producing a different key. No staleness sweep needed.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from lib.precog.idr import PrecogIDR, build_idr

# Default cache directory + TTL
_DEFAULT_CACHE_DIR = (
    Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    / "state" / "precog" / "cache"
)
_DEFAULT_TTL_SECONDS = 24 * 3600  # 24h — generous; kernel changes auto-bust


def _cache_path(fingerprint: str, cache_dir: Path | None = None) -> Path:
    """Resolve `<cache_dir>/<fingerprint>.json` without traversal risk."""
    if not fingerprint or len(fingerprint) != 64 or not all(
        c in "0123456789abcdef" for c in fingerprint
    ):
        raise ValueError(f"invalid fingerprint: {fingerprint!r}")
    target_dir = cache_dir or _DEFAULT_CACHE_DIR
    return target_dir / f"{fingerprint}.json"


def _validate_decision_dict(decision: Any) -> bool:
    """Schema-validate a cached decision before replay emission.

    Required keys with expected types:
      provider_addresses (list), effort_tier (str),
      fallback_chain (list), downstream_shape (str), dispatch_depth (int).
    Malformed decisions are treated as cache misses by callers — never crash.
    AGORA-2 fix: prevent silent shape-drift across TTL windows.
    Extended per AGORA PR #3178 C2 — full schema coverage.

    Note: ``downstream_shape`` and ``dispatch_depth`` are accepted in their
    mutated (int) form for backward compat with cache entries written before
    this extension was deployed.
    """
    if not isinstance(decision, dict):
        return False
    if not isinstance(decision.get("provider_addresses"), list):
        return False
    if not isinstance(decision.get("effort_tier"), str):
        return False
    if not isinstance(decision.get("fallback_chain"), list):
        return False
    ds = decision.get("downstream_shape")
    if not isinstance(ds, str):
        return False
    dd = decision.get("dispatch_depth")
    if not isinstance(dd, int):
        return False
    return True


def cache_decision(
    fingerprint: str,
    decision: dict[str, Any],
    *,
    original_idr_id: str,
    cache_dir: Path | None = None,
) -> Path:
    """Persist a decision keyed by fingerprint. Atomic write + content-hash dedup.

    Args:
        fingerprint: 64-char lowercase hex sha256 from compute_fingerprint.
        decision: the IDR.decision field (provider_addresses, effort_tier, ...).
        original_idr_id: the precog-decision IDR id this cache entry replays from.
        cache_dir: override target dir (test isolation).

    Returns:
        Path the cache entry was written to.

    AGORA-5 fix: docstring-promised idempotency is now real. Identical
    (fingerprint, decision, original_idr_id) writes are content-compared
    against the existing cache file and skipped if equal — preserving
    mtime/inode and removing spurious filesystem churn.
    """
    target = _cache_path(fingerprint, cache_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    new_content = {
        "fingerprint": fingerprint,
        "original_idr_id": original_idr_id,
        "decision": decision,
    }
    # Content-hash dedup: compare WITHOUT the timestamp (which is the only
    # field that changes across identical re-writes).
    if target.exists():
        try:
            existing = json.loads(target.read_text())
            existing_content = {
                k: existing.get(k)
                for k in ("fingerprint", "original_idr_id", "decision")
            }
            if existing_content == new_content:
                return target  # idempotent no-op — preserves mtime/inode
        except (json.JSONDecodeError, OSError):
            pass  # malformed existing file — overwrite

    payload = {**new_content, "cached_at": int(time.time())}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    os.replace(tmp, target)
    return target


def replay_lookup(
    fingerprint: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    cache_dir: Path | None = None,
    now: int | None = None,
) -> dict[str, Any] | None:
    """Return the cached payload iff present + fresh, else None.

    Fresh = `now - cached_at <= ttl_seconds`. Stale entries return None
    (caller decides whether to overwrite or re-reason).

    Malformed cache files are treated as a miss (never crash). The caller
    can safely re-reason and overwrite.
    """
    target = _cache_path(fingerprint, cache_dir)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    cached_at = payload.get("cached_at")
    if not isinstance(cached_at, int):
        return None
    current = now if now is not None else int(time.time())
    if current - cached_at > ttl_seconds:
        return None
    return payload


def emit_replay_idr(
    *,
    prompt: str,
    fingerprint: str,
    cached_payload: dict[str, Any],
    predecessor_idr: str | None,
    depth: int,
) -> PrecogIDR:
    """Build a kind='precog-decision-replay' IDR referencing the original.

    The replay envelope reuses the cached decision verbatim but is a NEW IDR
    in the audit chain — same `decision`, new `id`, new `audit.signature`,
    new `ts`, with `inputs.replay_of` pointing to the original IDR id.

    Why a new IDR rather than re-emitting the original: the audit chain MUST
    record that *this turn* relied on a prior decision; replaying without an
    audit footprint would break replay-traceability (R2 of the design).
    """
    decision = dict(cached_payload.get("decision", {}))
    inputs = {
        "replay_of": cached_payload.get("original_idr_id"),
        "cached_at": cached_payload.get("cached_at"),
        "prompt_sha256_partial": fingerprint[:16],  # diagnostic only
    }
    return build_idr(
        prompt=prompt,
        fingerprint=fingerprint,
        decision=decision,
        predecessor_idr=predecessor_idr,
        depth=depth,
        kind="precog-decision-replay",
        inputs=inputs,
    )
