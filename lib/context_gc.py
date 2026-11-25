"""Context memory-chain GC + ledger-fold (P5).

Two halves, both keyed on a VERIFIED snapshot (P4's ``context-snapshot`` IDR):

  1. REAPER (instance #2 of the ``lib/mesh_gc.py`` pattern — NOT the generic
     ``ttl_reaper``; that abstraction was deferred to N=3 per
     ``rules/lifecycle-closed-or-expired.md`` + ``knowledge-maturation-functor.md``,
     and this second instance makes it eligible under the functor's N=2 since
     2026-10-03; its first build goes through the promotion gate).
     Reap the context-delta PREFIX strictly before the newest VERIFIED snapshot
     (the snapshot's fold already subsumes it) — all-or-nothing, because the chain
     is a linked list and a half-reaped prefix would dangle a mid-chain link.
     Fail-closed: no verified snapshot ⇒ reap nothing; any un-snapshotted
     exogenous anchor in the prefix ⇒ reap nothing. Reaped deltas MOVE to a cold
     archive (recoverable by content address) — NEVER hard-deleted. Report-split:
     ``live`` = post-snapshot deltas; ``archived_collapsed`` = a SEPARATE
     "N folded, verified-collapsed" figure, never framed as current work (the
     grip-session-mesh "1342 waiting" stale-frame lesson).

  2. LEDGER-FOLD (THE flaw fix, design §6 — non-negotiable). The R1 completeness
     ledger in ``holistic_synthesiser`` is CAP-EXEMPT (``body_budget`` subtracts
     ``len(ledger_block)``) and grows one line per memory forever — a context
     chain feeding new memory re-creates the originating unbounded-growth bug one
     layer up (live ``HOLISTIC.md`` is already 39,800/40,000). ``fold_ledger``
     collapses ledger entries PROVABLY subsumed by a verified snapshot into ONE
     counted, recoverable pointer line, so the LIVE ledger SHRINKS while staying
     R1-complete (every collapsed entry is recoverable — in the cold archive /
     the snapshot merkle). PURE: it does NOT mutate the live ``rules/HOLISTIC.md``
     — the warm-start substrate flip that applies it is P6 (bootstrap-gated,
     human merge only).

Stdlib + GRIP-lib only; imports no HAL/LLM (the P3/P5 import-isolation lint).

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §6, §8 (P5).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from lib.audit_chain import Verdict
from lib.context_chain import chain_path, read_context_chain, verify_context_chain
from lib.holistic_synthesiser import MemoryEntry, _ledger_line
from lib.idr_forest import EXOGENOUS_ANCHOR_PATTERNS
from lib.precog.idr import PrecogIDR, _canonical_json, content_addr
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

_SNAPSHOT_KIND = "context-snapshot"


# ---------------------------------------------------------------------------
# Paths (DI-overridable; default beside the chain file).
# ---------------------------------------------------------------------------

def archive_path(chain_p: Path | None = None) -> Path:
    """Cold archive for reaped deltas — beside the chain file (recoverable)."""
    return chain_path(chain_p).with_name("context-archive.jsonl")


def _audit_path() -> Path:
    env = os.environ.get("CLAUDE_CONTEXT_GC_LOG")
    if env:
        return Path(env)
    base = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    return base / "logs" / "context-gc.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _serialise(node: PrecogIDR) -> str:
    """One JSONL line, byte-identical to ``append_idr`` (re-read stable)."""
    return _canonical_json(asdict(node))


# ---------------------------------------------------------------------------
# Snapshot selection (monotone-toward-safe).
# ---------------------------------------------------------------------------

def latest_verified_snapshot(chain: list[PrecogIDR], *, path: Path | None = None) -> PrecogIDR | None:
    """The NEWEST ``context-snapshot`` whose prefix (chain ending at it) verifies
    VERIFIED. Monotone-toward-safe: returns an OLDER verified snapshot before an
    unverified newer one, and ``None`` if none verify (so the reaper fails closed).
    """
    cpath = chain_path(path)
    for node in reversed(chain):
        if node.kind == _SNAPSHOT_KIND and verify_context_chain(path=cpath, head=node.id) is Verdict.VERIFIED:
            return node
    return None


def _carries_unsnapshotted_anchor(node: PrecogIDR) -> bool:
    """A delta whose decision/inputs names an exogenous anchor (ci:/human:/
    council:/hypo:) is kept live — its anchor must not be lost to a fold that
    did not record it. Conservative + fail-closed (keep, never silently drop)."""
    blob = _canonical_json({"d": node.decision, "i": node.inputs})
    return any(prefix in blob for prefix in EXOGENOUS_ANCHOR_PATTERNS)


# ---------------------------------------------------------------------------
# Reaper (DELETION half — patient, fail-closed, recoverable, report-split).
# ---------------------------------------------------------------------------

def _append_archive(nodes: list[PrecogIDR], arch: Path) -> None:
    """Append reaped nodes to the cold archive under an exclusive flock. Runs
    BEFORE the chain rewrite so a crash leaves data in the archive, never lost."""
    arch.parent.mkdir(parents=True, exist_ok=True)
    with open(arch, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            for n in nodes:
                fh.write(_serialise(n) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _rewrite_chain(retained: list[PrecogIDR], cpath: Path) -> None:
    """Atomically replace the chain file with the retained nodes (tmp + os.replace
    — readers see old-or-new, never torn)."""
    tmp = cpath.with_suffix(".jsonl.gctmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for n in retained:
            fh.write(_serialise(n) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, cpath)


def _audit(records: list[dict], log_path: Path | None) -> None:
    """Append one JSONL line per reaped delta (the reaper's own falsifier).
    Best-effort: a logging failure must never abort GC."""
    if not records:
        return
    path = log_path or _audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        pass


def reap(
    *,
    path: Path | None = None,
    head_pointer: Path | None = None,
    archive: Path | None = None,
    log_path: Path | None = None,
) -> dict:
    """Run one GC pass. Idempotent + fail-closed. Reaps the delta PREFIX before
    the newest VERIFIED snapshot to the cold archive; rewrites the live chain to
    ``[snapshot, post-snapshot...]``. Returns the report-split summary
    ``{"live", "archived_collapsed", "snapshot", "skipped"?}``.
    """
    cpath = chain_path(path)
    chain = read_context_chain(path=cpath, head_pointer=head_pointer)
    if not chain:
        return {"live": 0, "archived_collapsed": 0, "snapshot": None}
    snap = latest_verified_snapshot(chain, path=cpath)
    if snap is None:
        return {"live": len(chain), "archived_collapsed": 0, "snapshot": None}  # fail-closed

    snap_idx = next(i for i, n in enumerate(chain) if n.id == snap.id)
    prefix = chain[:snap_idx]
    if not prefix:
        return {"live": len(chain), "archived_collapsed": 0, "snapshot": snap.id}  # idempotent
    if any(_carries_unsnapshotted_anchor(n) for n in prefix):
        # Cannot reap a partial prefix without dangling a mid-chain link, and an
        # un-snapshotted anchor must not be lost → reap nothing this pass.
        return {"live": len(chain), "archived_collapsed": 0, "snapshot": snap.id,
                "skipped": "unsnapshotted_anchor_in_prefix"}

    retained = chain[snap_idx:]
    arch = archive or archive_path(cpath)
    _append_archive(prefix, arch)   # archive FIRST (crash-safe)
    _rewrite_chain(retained, cpath)
    _audit(
        [{"ts": _now_iso(), "action": "reap_delta", "id": n.id,
          "content_addr": content_addr(asdict(n)), "snapshot": snap.id} for n in prefix],
        log_path,
    )
    return {"live": len(retained), "archived_collapsed": len(prefix), "snapshot": snap.id}


def recover(content_address: str, *, path: Path | None = None, archive: Path | None = None) -> PrecogIDR | None:
    """Fetch an archived delta by its content address (proves "never hard-delete"
    — a reaped delta is always recoverable). Returns ``None`` if absent."""
    arch = archive or archive_path(chain_path(path))
    if not arch.exists():
        return None
    for line in arch.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            # ``if k in d`` — legacy archived deltas predate optional fields
            # (e.g. ``decision_anatomy``); guard so they reconstruct (defaults)
            # rather than KeyError-dropping out of recover().
            node = PrecogIDR(**{k: d[k] for k in PrecogIDR.__dataclass_fields__ if k in d})
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if content_addr(asdict(node)) == content_address:
            return node
    return None


def gc_report(*, path: Path | None = None, head_pointer: Path | None = None,
              archive: Path | None = None) -> dict:
    """Report-split (the honest-counting half — the mesh_gc lesson). ``live`` is
    the current chain length (what warm-start folds); ``archived_collapsed`` is a
    SEPARATE figure — folded + verified-collapsed deltas in the cold archive,
    never framed as current work."""
    cpath = chain_path(path)
    live = len(read_context_chain(path=cpath, head_pointer=head_pointer))
    arch = archive or archive_path(cpath)
    archived = 0
    if arch.exists():
        archived = sum(1 for ln in arch.read_text(encoding="utf-8").splitlines() if ln.strip())
    return {"live": live, "archived_collapsed": archived}


# ---------------------------------------------------------------------------
# Ledger-fold (THE flaw fix — design §6 non-negotiable). PURE; no HOLISTIC.md write.
# ---------------------------------------------------------------------------

def fold_ledger(
    ledger_entries: list[MemoryEntry],
    collapsed_addrs: set[str],
    snapshot_ref: str,
) -> tuple[list[str], int]:
    """Collapse ledger entries PROVABLY subsumed by a verified snapshot into ONE
    counted, recoverable pointer line — so the LIVE R1 ledger SHRINKS without
    violating R1.

    ``collapsed_addrs`` = the content addresses (== ``MemoryEntry.content_hash``)
    of entries whose content is in the snapshot's merkle / cold archive. Those
    entries are replaced by a single ``[SNAPSHOT] <ref> :: N verified-collapsed``
    line; the rest stay as full ledger lines (``_ledger_line``, DRY with the
    synthesiser). Completeness invariant: every original entry is recoverable —
    a live line OR via the snapshot pointer + ``recover()``. Returns
    ``(shrunk_lines, collapsed_count)``. PURE — the live-substrate apply is P6.
    """
    kept = [e for e in ledger_entries if e.content_hash not in collapsed_addrs]
    collapsed = len(ledger_entries) - len(kept)
    lines = [_ledger_line(e) for e in kept]
    if collapsed:
        lines.append(
            f'- [SNAPSHOT] {snapshot_ref} :: {collapsed} entries '
            f'verified-collapsed, recoverable via /recall'
        )
    return lines, collapsed
