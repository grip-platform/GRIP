"""nextup_mesh — delivery glue for unified real-time next_up (§4.2 / §13.4).

SRP: synthesis lives in next_step_block; this module ONLY does transport —
same-machine wake via `bin/mesh broadcast`/`inbox`, plus the cross-machine
snapshot write to the git-synced state/nextup/{enc(scope)}/ directory.

Cross-machine is git-sync-cadence-bounded & opt-in (honest — `bin/mesh` is
same-machine-only; the snapshot dir propagates via the normal git sync of
~/.claude, a PRIVATE repo: confidentiality = access control, NOT public —
V>> 2026-05-18). The no-secret screen keeps snapshots free of credentials
regardless (commit-standards, visibility-independent, defence-in-depth).

DIP: broadcaster injected (defaults to `bin/mesh`).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import next_step_block as nsb

# Canonical env-precedence for the session id (task #520 — share the drift-prone
# resolution logic). nextup keeps its OWN output contract ({raw} or ppid<ppid>,
# '/'/':' sanitised — NOT the hooks' cc- prefix), so it reuses only resolve_raw_id.
# Fail-open to an inline mirror so a missing import never breaks transport.
try:
    from mesh_identity import resolve_raw_id as _resolve_raw_id
except Exception:  # pragma: no cover - import-failure last resort only
    def _resolve_raw_id() -> str:
        return ((os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
                or (os.environ.get("CLAUDE_SESSION_ID") or "").strip())

_MESH = nsb.CLAUDE_DIR / "bin" / "mesh"
_SNAP_ROOT = nsb.CLAUDE_DIR / "state" / "nextup"
_BROADCAST_TIMEOUT_S = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine_id() -> str:
    """Stable per-machine token (hostname); '/'/':' sanitised for the filename."""
    h = platform.node() or "unknown-host"
    return h.replace("/", "-").replace(":", "-").strip("-") or "host"


def _session_id() -> str:
    # Shared env-precedence (task #520) via resolve_raw_id; nextup's own contract
    # keeps the ppid<pid> last resort + '/'/':' sanitise (NO cc- prefix).
    sid = _resolve_raw_id() or f"ppid{os.getppid()}"
    return sid.replace("/", "-").replace(":", "-")


def _default_broadcaster(message: str) -> None:
    """Best-effort same-machine mesh wake. Never raises; latency-bounded.

    Mirrors holistic_synthesiser._default_broadcaster: the snapshot is
    already durable before this is called; a missed wake self-heals via
    the periodic hook's recompute (§4.3 pull-fallback).
    """
    if not _MESH.exists():
        return
    try:
        subprocess.run(
            [str(_MESH), "broadcast", message],
            timeout=_BROADCAST_TIMEOUT_S, capture_output=True, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _screen_snapshot(snapshot: dict) -> Optional[dict]:
    """§13.4 screen: allowed-keys-only + size cap + secret-shape reject.

    Returns the screened dict, or None if it must be DROPPED (oversize or
    secret-shaped) — fail-safe: a suspect snapshot is never written.
    """
    screened = {k: v for k, v in snapshot.items() if k in nsb._SNAPSHOT_ALLOWED_KEYS}
    blob = json.dumps(screened, ensure_ascii=False, sort_keys=True)
    if len(blob) > nsb._NO_SECRET_MAX_CHARS:
        return None
    if nsb._SECRET_RE.search(blob):
        return None
    return screened


def _atomic_write(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def publish_unified_next_up(
    project_path: Path,
    broadcaster: Callable[[str], None] = _default_broadcaster,
) -> Optional[str]:
    """Compute scope+unified+digest; write the screened snapshot to the
    scope-keyed dir; best-effort broadcast the same-machine wake signal.

    Returns the digest on success; None if nothing to publish or the
    snapshot was screened out.
    """
    scope = nsb.project_scope_key(project_path)
    unified = nsb.synthesise_unified_next_up(project_path)
    if not unified or not (unified.get("remaining") or unified.get("blockers")):
        return None
    digest = nsb.nextstep_digest(unified)
    screened = _screen_snapshot({
        "remaining": unified.get("remaining", []),
        "blockers": unified.get("blockers", []),
        "notes": unified.get("notes", ""),
        "serialized_at": _now_iso(),
        "scope": scope,
    })
    if screened is None:
        return None  # fail-safe — suspect/oversize snapshot never written
    snap_path = (
        _SNAP_ROOT / nsb._enc_scope(scope) / f"{_machine_id()}--{_session_id()}.json"
    )
    if not _atomic_write(snap_path, screened):
        return None
    try:
        broadcaster(nsb.nextstep_signal(scope, digest))
    except BaseException:
        pass  # notification strictly best-effort (council R3); snapshot durable
    return digest


def consume_pending(project_path: Path) -> Optional[list]:
    """Read the mesh inbox; if any in-scope peer signal differs from our
    current unified digest, return freshly-recomputed
    unified_next_step_lines, else None. Fail-open: any error → None.
    """
    if not _MESH.exists():
        return None
    try:
        res = subprocess.run(
            [str(_MESH), "inbox"],
            timeout=_BROADCAST_TIMEOUT_S, capture_output=True, text=True, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    my_scope = nsb.project_scope_key(project_path)
    unified = nsb.synthesise_unified_next_up(project_path)  # compute ONCE
    my_digest = nsb.nextstep_digest(unified)
    for line in res.stdout.splitlines():
        parsed = nsb.parse_nextstep_signal(line)
        if not parsed:
            continue
        sig_scope, sig_digest = parsed
        if nsb.should_refresh_nextstep(sig_scope, sig_digest, my_scope, my_digest):
            # reuse the already-computed unified — NO 2nd synthesise on the
            # hot hook path (HAL #2917 concern 3; hook must not delay).
            return nsb.next_step_lines(unified)
    return None


# ---------------------------------------------------------------------------
# Mid-session self-refresh (additive — closes the deferred-YAGNI gap).
#
# consume_pending is PEER-gated, so a single long-running session whose own
# next_up evolves mid-flight never re-surfaces its OWN fresh next-step.  This
# path stamps the digest THIS session last surfaced to ITSELF (per scope+
# session) and re-emits when the freshly-synthesised own digest differs.
#
# Firewall: the stamp is keyed by _enc_scope(scope) + _session_id(), so it is
# this session's own-scope state only — it cannot read or write a peer's
# digest, and a peer's session_id maps to a different stamp file.  Fail-open
# ABSOLUTE: any stamp/synthesis error → None (the boot floor + consume_pending
# still stand).  R2-purity untouched: holistic_synthesiser.synthesise() is
# never called here.
# ---------------------------------------------------------------------------

_SELF_STAMP_ROOT = nsb.CLAUDE_DIR / "state" / "session" / "nextstep-self"


def _self_stamp_path(scope: str) -> Path:
    """Per-(scope, session) stamp file recording the last self-surfaced digest."""
    return _SELF_STAMP_ROOT / nsb._enc_scope(scope) / f"{_session_id()}.txt"


def _read_self_surfaced(scope: str) -> Optional[str]:
    """Last digest this session surfaced to itself for this scope, or None."""
    try:
        return _self_stamp_path(scope).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_self_surfaced(scope: str, digest: str) -> None:
    """Record digest as self-surfaced. Best-effort; never raises."""
    try:
        p = _self_stamp_path(scope)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(digest, encoding="utf-8")
    except OSError:
        pass


def consume_self_refresh(
    project_path: Path,
    unified: Optional[dict] = None,
    digest: Optional[str] = None,
) -> Optional[list]:
    """Re-surface THIS session's own next-step iff it changed mid-session.

    The single-session self-staleness path consume_pending cannot cover.
    ``digest`` is injected by the hot hook (publish_unified_next_up already
    returns it) so the staleness check costs NO synthesise on the common
    unchanged path (BIG-O — synthesise is O(instances×filesize)); ``unified``
    is synthesised lazily ONLY on a real change (rare).  Both absent ⇒ computed
    here (the standalone/test path).  Returns fresh '## Next Step' lines on a
    real own change (and stamps it), else None.  Fail-open: any error → None.
    """
    try:
        scope = nsb.project_scope_key(project_path)
        # Cheap digest-first staleness gate — no synthesise unless something
        # actually changed since this session last self-surfaced.
        if digest is None:
            if unified is None:
                unified = nsb.synthesise_unified_next_up(project_path)
            digest = nsb.nextstep_digest(unified)
        if not nsb.self_nextstep_stale(digest, _read_self_surfaced(scope)):
            return None
        # Stale ⇒ now we need the content. Synthesise lazily iff not injected.
        if unified is None:
            unified = nsb.synthesise_unified_next_up(project_path)
        lines = nsb.next_step_lines(unified)
        if not lines:
            return None
        _write_self_surfaced(scope, digest)
        return lines
    except BaseException:
        return None


def mark_self_surfaced(project_path: Path, digest: Optional[str] = None) -> None:
    """Record the current own digest as self-surfaced WITHOUT emitting.

    Called after a PEER emit (consume_pending fired): the peer emit already
    showed the freshest unified — which subsumes this session's own changes —
    so the self path must treat the own digest as just-surfaced, else it would
    re-emit the identical block next tick.  ``digest`` is injected by the hot
    hook to avoid re-synthesising on the busy path (BIG-O — the hook already
    computed it); absent ⇒ computed here.  Fail-open: any error → no-op.
    """
    try:
        scope = nsb.project_scope_key(project_path)
        if digest is None:
            digest = nsb.nextstep_digest(nsb.synthesise_unified_next_up(project_path))
        if digest:
            _write_self_surfaced(scope, digest)
    except BaseException:
        pass
