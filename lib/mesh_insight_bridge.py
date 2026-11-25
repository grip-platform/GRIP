#!/usr/bin/env python3
"""mesh_insight_bridge.py — real-time CROSS-MACHINE learning propagation.

Rides V>>'s grip-session-mesh L1 bus (not the local-only presence inbox):

  PRODUCER  broadcast_insight() appends a kind="insight" frame to the local
            mesh-client outbound queue (~/.grip-session-mesh/outbound/<node>.jsonl).
            mesh-client drains it (~1s) -> L1 bus -> L2 Tailscale relay forwards
            to peer machines -> peer bus writes inbound/<uuid>.jsonl.

  CONSUMER  ingest_insight() takes a received frame and appends the insight to
            THIS machine's learning/insights.jsonl with ingested=False (so the
            existing KONO/learn pipeline picks it up), deduped by fingerprint,
            tagged origin_machine. It writes DIRECTLY — never via the tapped
            persist_insight — so ingesting a peer insight can NEVER re-broadcast
            it. That direct-write is the core loop-prevention.

Loop/storm prevention (4 layers): origin_machine guard (broadcast only LOCAL
insights) · fingerprint dedup on ingest · the L1 bus's own msg-id LRU · ingested
insights written direct (bypass the broadcasting tap).

Fail-soft everywhere: if the mesh is absent/unreachable, local learning is
byte-for-byte unaffected.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from claude_dir import get_claude_dir
    CLAUDE_DIR = get_claude_dir()
except Exception:  # pragma: no cover - defensive
    CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

MESH_DIR = Path(os.environ.get("CLAUDE_MESH_DIR", str(Path.home() / ".grip-session-mesh")))
INSIGHTS_JSONL = CLAUDE_DIR / "learning" / "insights.jsonl"
INSIGHT_KIND = "insight"


def _local_node(mesh_dir: Path | None = None) -> str:
    """Resolve this machine's mesh-client node name (e.g. 'blackbook').

    Order: the mesh-client launchd arg (authoritative — it IS what the running
    client drains for) -> a single <name>.session file -> short hostname.
    """
    plist = Path.home() / "Library" / "LaunchAgents" / "com.grip.mesh-client.plist"
    if plist.exists():
        try:
            import plistlib
            with open(plist, "rb") as fh:
                args = plistlib.load(fh).get("ProgramArguments", [])
            if args and not str(args[-1]).endswith(".js"):
                return str(args[-1])
        except Exception:
            pass
    mesh = Path(mesh_dir) if mesh_dir else MESH_DIR
    try:
        sessions = list(mesh.glob("*.session"))
        if len(sessions) == 1:
            return sessions[0].stem
    except Exception:
        pass
    return socket.gethostname().split(".")[0]


def _fallback_fingerprint(text: str) -> str:
    """Lightweight fingerprint used ONLY when a frame lacks one. Deliberately does
    not import learning_detector (avoids the lembed SIGSEGV / circular-import
    surface in the always-on consumer). Cross-machine dedup relies on the
    fingerprint carried IN the frame (computed by the sender's learning_detector),
    so this fallback only ever guards a malformed frame."""
    norm = " ".join((text or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _shield_public(text: str) -> str:
    """Redact operator/client PII from ONE free-text field before it leaves this
    machine over the mesh, via the canonical public shield.

    FAIL-OPEN (this is the operator's OWN cross-machine mesh, per issue #3820):
    if the denylist is missing / git-crypt-locked / the shield import fails, the
    original text is returned unchanged — a shield-unusable state must NEVER
    crash the outbound path or block local↔peer learning propagation. Only the
    free-text insight body/context is passed here; structural metadata
    (fingerprint, tags, origin_machine, created_at, ids) is never routed through
    the shield (redacting a dedup hash or timestamp would break dedup + ordering)."""
    if not text:
        return text
    try:
        from redaction_shield import Shield, load_denylist
        return Shield(load_denylist(), profile="public").redact(text).text
    except Exception:
        return text  # fail-open: mesh keeps flowing even with no usable denylist


def _append_jsonl(path: Path, record: dict) -> bool:
    """flock-guarded atomic append of one JSON record. Fail-soft."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fp:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def _existing_fingerprints(path: Path) -> set:
    fps = set()
    if not path.exists():
        return fps
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    fp = json.loads(line).get("fingerprint")
                    if fp:
                        fps.add(fp)
                except Exception:
                    continue
    except OSError:
        pass
    return fps


def broadcast_insight(
    text: str,
    context: str = "",
    tags=None,
    fingerprint: str | None = None,
    origin_machine: str | None = None,
    *,
    mesh_dir: Path | None = None,
    local_node: str | None = None,
) -> bool:
    """PRODUCER. Queue a kind="insight" frame for the local mesh-client to send.

    Broadcasts ONLY locally-originated insights (origin guard = loop layer 1).
    Returns True if a frame was queued, False on any fail-soft skip.
    """
    node = local_node or _local_node(mesh_dir)
    origin = origin_machine or node
    if origin != node:
        return False  # never rebroadcast a peer insight
    text = (text or "").strip()
    if not text:
        return False
    mesh = Path(mesh_dir) if mesh_dir else MESH_DIR
    outbox_dir = mesh / "outbound"
    if not outbox_dir.exists():
        return False  # mesh not present — local learning unaffected
    # Shield the FREE-TEXT fields at the compose boundary, on the PUBLIC profile,
    # before serialising the frame outbound to peer machines. Structural metadata
    # below (tags, fingerprint, origin_machine, created_at) is left verbatim —
    # the fingerprint is the cross-machine dedup key and MUST stay stable, tags
    # are enum-ish routing labels, created_at is an ordering timestamp. The
    # fingerprint is computed from the ORIGINAL text (line above / caller-supplied)
    # so peers dedup on identity regardless of any shielding difference.
    body = json.dumps(
        {
            "text": _shield_public(text),
            "context": _shield_public(context),
            "tags": list(tags or []),
            "fingerprint": fingerprint or _fallback_fingerprint(text),
            "origin_machine": node,
            "created_at": _now_iso(),
        },
        ensure_ascii=False,
    )
    frame = {"to": "broadcast", "kind": INSIGHT_KIND, "body": body}
    return _append_jsonl(outbox_dir / f"{node}.jsonl", frame)


def ingest_insight(
    frame,
    *,
    insights_path: Path | None = None,
    local_node: str | None = None,
    mesh_dir: Path | None = None,
) -> str | None:
    """CONSUMER. Append a received insight frame to local insights.jsonl.

    Writes DIRECTLY (never via the tapped persist_insight) so this can never
    re-broadcast — the core loop-prevention. Returns the new insight id, or None
    if skipped (wrong kind / own echo / duplicate fingerprint / malformed).
    """
    if not isinstance(frame, dict) or frame.get("kind") != INSIGHT_KIND:
        return None
    node = local_node or _local_node(mesh_dir)
    try:
        body = json.loads(frame.get("body") or "{}")
    except Exception:
        return None
    origin = body.get("origin_machine")
    if not origin or origin == node:
        return None  # our own insight echoed back over the bus — ignore
    text = (body.get("text") or "").strip()
    if not text:
        return None
    fp = body.get("fingerprint") or _fallback_fingerprint(text)
    path = Path(insights_path) if insights_path else INSIGHTS_JSONL
    if fp in _existing_fingerprints(path):
        return None  # already have it — dedup (loop layer 2)
    record = {
        "id": hashlib.sha256(f"{text}{origin}{time.time()}".encode("utf-8")).hexdigest()[:12],
        "text": text,
        "context": body.get("context", ""),
        "tags": list(body.get("tags") or []) + ["mesh-peer", f"origin:{origin}"],
        "fingerprint": fp,
        "seen_count": 1,
        "related_to": [],
        "created_at": body.get("created_at") or _now_iso(),
        "ingested": False,  # the existing KONO/learn pipeline ingests un-ingested entries
        "origin_machine": origin,
    }
    return record["id"] if _append_jsonl(path, record) else None
