"""Decision-chain HEAD pointer — the primitive the IDR chain never had.

``state/precog/idr-HEAD.txt`` holds the id of the latest decision record, so a
fresh process can link the record it is about to write to the one before it.

Why this module exists. The belief chain has had ``lib/context_head.py`` all
along, and ``context_chain.append_context`` reads the head, links to it, and
swaps it atomically. The decision chain had no equivalent: ``append_idr`` wrote a
line and maintained no pointer, so every caller had to supply ``predecessor_idr``
itself and a fresh process had nothing to read. Measured 2026-09-13, that is
exactly what the data shows — **895 of 910 decision records carry
``predecessor_idr: null``** against **20,231 of 20,283 (99.7%)** belief records
that link. Same repo, same record type, one had a head pointer and one did not.

**This module cannot repair history, and nothing should imply it can.** A
predecessor lives inside the signed body, so linking the existing 895 would mean
re-signing them — manufacturing a chain that never existed, which is precisely
the forgery the seal exists to detect. The gap in the record is permanent and is
disclosed in ``rules/cryptographic-causation.md``. This fixes the next record,
not the last one.

Session-namespaced, deliberately, reusing the belief chain's own rule rather than
restating it. ``idr.jsonl`` is a SINGLE shared file, so a single shared head would
let two concurrent sessions each link onto whatever the other wrote last, and the
chain would assert a causal edge between unrelated decisions — a false link is
worse than the absent one it replaces. Per-session heads give each session an
honest lineage inside the shared file.

No signing, no crypto, no HAL imports: this module decides chain POSITION only.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["head_path", "read_head", "write_head", "advance"]


def _base_head_path() -> Path:
    """Un-namespaced pointer path, mirroring ``precog.idr._default_idr_path``.

    Reads the environment at CALL time. A module-level constant would freeze
    ``CLAUDE_DIR`` at import and ignore a later override — which under pytest,
    where a conftest autouse fixture redirects it, means writing the pointer
    into the operator's REAL state instead of the tmp path.
    """
    return (
        Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
        / "state" / "precog" / "idr-HEAD.txt"
    )


def head_path(path: Path | None = None) -> Path:
    """Resolve the head pointer file, session-namespaced.

    Imports ``context_head._session_path`` rather than reimplementing the
    namespacing rule: two copies of that rule would be two independent opinions
    about which file a session owns, and they would silently disagree the first
    time either changed.
    """
    if path is not None:
        return path
    from lib.context_head import _session_path
    return _session_path(_base_head_path())


def read_head(path: Path | None = None) -> str | None:
    """The latest decision-record id, or ``None`` when this chain has no head.

    ``None`` means "no predecessor is known", which is the correct input for a
    genesis record. It is never an error: an unstarted chain is the ordinary
    state of a fresh session.
    """
    p = head_path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def write_head(node_id: str, path: Path | None = None) -> None:
    """Atomically point HEAD at ``node_id`` (temp sibling + ``os.replace``).

    Atomic so a concurrent reader sees the old id or the new one, never a
    half-written pointer that would be read as a predecessor that does not exist.
    """
    p = head_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(node_id.strip() + "\n", encoding="utf-8")
    os.replace(tmp, p)


def advance(node_id: str, path: Path | None = None) -> bool:
    """Move HEAD to ``node_id`` after a record has been durably written.

    Fail-open by contract, and the contract is the point: the record is already
    on disk when this runs, so a failure here must degrade the NEXT record's link
    rather than raise over a write that already succeeded. Returns whether the
    pointer moved, so a caller that wants to notice can, and the ordinary caller
    is not forced to.
    """
    try:
        write_head(node_id, path)
        return True
    except OSError:
        return False
