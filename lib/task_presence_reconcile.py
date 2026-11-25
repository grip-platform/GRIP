#!/usr/bin/env python3
"""task_presence_reconcile.py — presence-aware, provenance-backed task reconcile.

Fuses THREE signals to reconcile a harness task's TRUE state instead of trusting
its decayed self-reported ``in_progress`` (``task-status-is-telemetry``):

  1. referent-truth      — ``task_reality_reconcile.classify_task`` probes the task's
                           referents (PRs / paths / URLs) against canonical reality.
  2. presence-liveness    — ``mesh_gc.live_session_predicate`` over the mesh presence
                           DB gives an HONEST live-vs-dead session count that is FUSED
                           into every non-DONE verdict (a task with zero live sessions
                           is likelier orphaned → ``surface-orphaned``) and SIGNED into
                           its receipt. READ-ONLY: reporting != deletion (mesh_gc owns
                           the patient 24h reap), so this pass NEVER removes a row.
  3. GRASP-signed receipt — one ``precog.idr`` leaf per verdict (HMAC-SHA256, content-
                           addressed, replayable) recording {verdict, evidence, the
                           presence context} — the cryptographic-causation moat applied
                           to GRIP's own task substrate.

Invariants (each a survived failure mode):

  * NEVER false-close a live task — the cardinal sin. Only ``PROVABLY-DONE`` is
    ``close``-eligible; every other verdict is keep/surface. Presence can only ADD
    orphan-suspicion, never upgrade a verdict toward close. Inherited from
    ``task_reality_reconcile``'s conservatism (an all-unreliable probe set is always
    ``UNVERIFIABLE``), so a probe error can never drive a close.
  * Presence is READ-only here — honest liveness reporting fixes the ``session_list``
    reporting gap without touching the mesh_gc deletion floor.
  * Every verdict carries a signed receipt — a claim GRIP cannot prove, GRIP does not
    act on. Receipt emission is fail-open: a signing failure yields an empty address,
    never a blocked or altered verdict.

Stdlib + existing GRIP libs only. DI-testable via ``db_path`` / ``probes`` / ``emit``
/ ``presence``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from task_pruner import REMINDER_LINE_RE  # noqa: E402  (shared snapshot grammar — DRY)
from task_reality_reconcile import (  # noqa: E402
    DEFAULT_PROBES,
    Probes,
    Verdict,
    classify_task,
    probe_task,
)

# Mesh-wide presence DB (source of truth) + the canonical, BUG-2-safe liveness window.
# Mesh presence DB, relative to the operator home. Resolved LAZILY at call time
# (not at import) so a home-less environment (CI `nobody`, sandbox) can still import
# this module — AGORA #3933 iter-2, concern 1.
_PRESENCE_DB_REL = (".grip-session-mesh", "presence.db")
_REPORT_WINDOW = "-1 hour"  # mesh_gc.REPORT_WINDOW — honest "live right now" reporting.


def _default_presence_db() -> Path:
    return Path.home().joinpath(*_PRESENCE_DB_REL)

# Verdict -> base operator recommendation. Only PROVABLY-DONE is close-eligible; every
# other verdict is keep/surface. This dispatch is the single place the close-safety
# policy lives (CC-flat, no branching in the caller). Presence can only refine a
# non-close recommendation into 'surface-orphaned' — never toward 'close'.
RECOMMEND: Mapping[str, str] = types.MappingProxyType({
    Verdict.DONE: "close",
    Verdict.OPEN: "keep",
    Verdict.UNVERIFIABLE: "surface",
})


@dataclass(frozen=True)
class PresenceCount:
    """Honest live-vs-dead session tally from the mesh presence DB."""

    live: int
    dead: int
    live_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ReconcileReceipt:
    """A reconciled task: the fused verdict, its evidence (incl. the presence note),
    a signed receipt, the operator recommendation, and the presence context recorded.
    ``receipt_addr`` is a ``sha256:<hex>`` content address or ``""`` when signing was
    unavailable (fail-open)."""

    task_id: str
    verdict: str
    evidence: str
    receipt_addr: str
    recommend: str
    presence_note: str


def _live_predicate(window: str) -> str:
    """The canonical liveness SQL fragment. Imported from mesh_gc when available so
    both share ONE source of truth; falls back to the identical literal if mesh_gc
    cannot be imported (keeps this module usable standalone). ``window`` is stripped
    so a stray space can never produce ``datetime('now', ' -1 hour')``."""
    win = window.strip()
    try:
        from mesh_gc import live_session_predicate  # noqa: E402

        return live_session_predicate(win)
    except ImportError:
        return f"datetime(last_heartbeat) > datetime('now', '{win}')"


def count_presence(
    db_path: Optional[Path] = None, window: str = _REPORT_WINDOW
) -> PresenceCount:
    """Honest live/dead session count via the canonical heartbeat predicate.

    READ-ONLY (opens the DB ``mode=ro``) — this reports liveness, it NEVER reaps
    (mesh_gc owns deletion). Fail-soft: a missing/locked/older-schema DB yields a
    zero tally rather than raising, so a reconcile run never dies on presence I/O.
    ``live_ids`` is de-duplicated (belt-and-braces; ``id`` is PRIMARY KEY so the
    invariant ``len(live_ids) == live`` already holds) preserving first-seen order.
    """
    db_path = db_path or _default_presence_db()
    if not db_path.exists():
        return PresenceCount(0, 0, ())
    try:
        predicate = _live_predicate(window)
    except Exception as exc:
        # A real mesh_gc bug (non-ImportError, which _live_predicate now lets surface)
        # must NOT crash the reconcile loop. Fail-soft to a zero tally, but surface it
        # via a WARNING (not silent) — this keeps BOTH the DEFER-narrowing intent AND
        # the count_presence "never raises" contract (AGORA #3935 concern 1, verified).
        logging.getLogger("task_presence_reconcile").warning(
            "presence predicate unavailable (%s); reporting zero tally", exc
        )
        return PresenceCount(0, 0, ())
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return PresenceCount(0, 0, ())
    try:
        rows = con.execute(f"SELECT id, ({predicate}) FROM sessions").fetchall()
    except sqlite3.Error:
        return PresenceCount(0, 0, ())
    finally:
        con.close()
    live_ids = tuple(dict.fromkeys(str(r[0]) for r in rows if r[1]))
    dead = len(rows) - sum(1 for r in rows if r[1])
    return PresenceCount(len(live_ids), dead, live_ids)


def _fuse_presence(
    verdict_str: str, presence: Optional[PresenceCount]
) -> Tuple[str, str]:
    """Fold the presence-liveness signal into the recommendation + a note.

    Returns ``(recommend, note)``. Presence NEVER upgrades a verdict toward close —
    a DONE verdict closes regardless of presence; a non-DONE verdict with NO live
    mesh session is more likely orphaned (no session could be working it) and is
    surfaced as ``surface-orphaned`` (still never closed). When presence is absent
    the base recommendation stands with an empty note."""
    base = RECOMMEND.get(verdict_str, "surface")
    if presence is None:
        return base, ""
    note = f"mesh {presence.live} live / {presence.dead} dead session(s) at reconcile"
    if base != "close" and presence.live == 0:
        return "surface-orphaned", note + " — no live owner candidate"
    return base, note


def _emit_receipt(task_id: str, verdict: str, evidence: str) -> str:
    """Sign ONE task-reconcile decision into the IDR chain and return its content
    address. Fail-open: any signing/append error returns ``""`` — the verdict stands,
    the receipt is simply absent (a receipt must never gate or mutate a verdict).
    The signed ``evidence`` is the SAME string stored on the ReconcileReceipt."""
    try:
        import precog.idr as idr  # noqa: E402

        fingerprint = hashlib.sha256(f"{task_id}|{verdict}".encode()).hexdigest()[:16]
        decision = {
            "action": "task-reconcile",
            "task_id": task_id,
            "verdict": verdict,
            "evidence": evidence,
        }
        leaf = idr.build_idr(
            prompt="",
            fingerprint=fingerprint,
            decision=decision,
            predecessor_idr=None,
            depth=0,
            kind="task-reconcile",
        )
        idr.append_idr(leaf)
        return idr.content_addr(asdict(leaf))
    except Exception as exc:
        import logging

        logging.getLogger("task_presence_reconcile").debug(
            "receipt emission failed for task %s (%s): %s", task_id, verdict, exc
        )
        return ""


def reconcile_task(
    task_id: str,
    text: str,
    probes: Probes = DEFAULT_PROBES,
    emit: bool = True,
    presence: Optional[PresenceCount] = None,
) -> ReconcileReceipt:
    """Reconcile ONE task: probe its referents, classify conservatively, FUSE the
    presence-liveness signal into the recommendation, and sign the verdict. Never
    closes a live task — presence only ever adds orphan-suspicion."""
    facts = probe_task(text, probes)
    verdict = classify_task(task_id, text, facts)
    recommend, note = _fuse_presence(verdict.verdict, presence)
    fused = f"{verdict.evidence} | {note}" if note else verdict.evidence
    # Sign the STABLE probe evidence (deterministic per task-state) — NOT the fused
    # string — so the receipt content address does not drift with the volatile
    # presence counter (AGORA #3933 iter-2, concern 3: replayable-per-fact). Presence
    # remains a recommendation INPUT + the operator-facing display note.
    receipt = _emit_receipt(task_id, verdict.verdict, verdict.evidence) if emit else ""
    return ReconcileReceipt(
        task_id=task_id,
        verdict=verdict.verdict,
        evidence=fused,
        receipt_addr=receipt,
        recommend=recommend,
        presence_note=note,
    )


def reconcile_tasks(
    tasks: List[Tuple[str, str]],
    probes: Probes = DEFAULT_PROBES,
    emit: bool = True,
    presence: Optional[PresenceCount] = None,
) -> List[ReconcileReceipt]:
    """Reconcile a list of ``(task_id, text)`` pairs (typically the in_progress set),
    fusing ONE honest presence tally (computed once) into every verdict."""
    if presence is None:
        presence = count_presence()
    return [reconcile_task(tid, text, probes, emit, presence) for tid, text in tasks]


# --------------------------------------------------------------------------- #
# CLI — reconcile an in_progress snapshot, print an honest presence header + a
# per-task verdict table, and (default) a JSON payload for the caller to act on.
# --------------------------------------------------------------------------- #


def parse_in_progress(text: str) -> List[Tuple[str, str]]:
    """Extract ``(id, subject)`` for the IN_PROGRESS rows of a reminder snapshot,
    reusing task_pruner's shared line grammar (``#123. [status] subject``)."""
    out: List[Tuple[str, str]] = []
    for line in text.splitlines():
        m = REMINDER_LINE_RE.match(line.strip())
        if m and m.group(2).lower() == "in_progress":
            out.append((m.group(1), m.group(3).strip()))
    return out


def _render(presence: PresenceCount, verdicts: List[ReconcileReceipt]) -> str:
    head = (
        f"presence: {presence.live} LIVE / {presence.dead} dead "
        f"(honest heartbeat window {_REPORT_WINDOW.strip()})"
    )
    lines = [head, ""]
    for v in verdicts:
        addr = (v.receipt_addr[:23] + "…") if v.receipt_addr else "(no receipt)"
        lines.append(f"#{v.task_id:<5} {v.recommend:<16} {v.verdict:<18} {addr}")
        lines.append(f"        └ {v.evidence[:120]}")
    return "\n".join(lines)


def _main(argv: List[str]) -> int:
    emit = "--no-receipt" not in argv
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    snapshot = Path(paths[0]).read_text(encoding="utf-8") if paths else sys.stdin.read()
    tasks = parse_in_progress(snapshot)
    presence = count_presence()
    verdicts = reconcile_tasks(tasks, emit=emit, presence=presence)
    if as_json:
        payload = {
            "presence": asdict(presence),
            "verdicts": [asdict(v) for v in verdicts],
            "auto_close": [v.task_id for v in verdicts if v.recommend == "close"],
            "surface": [v.task_id for v in verdicts if v.recommend != "close"],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_render(presence, verdicts))
    return 0


if __name__ == "__main__":  # pragma: no cover
    # This shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this the tool still runs and still records --
    # it just writes a forgeable demo-sealed record when a real seal was one
    # interpreter away (lib/pq_reexec.py).
    #
    # Imported as top-level `pq_reexec` off THIS checkout, never as
    # `lib.pq_reexec`: by now `lib` may already be bound to another tree's
    # package, and the import would miss the file or read another branch's copy.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    raise SystemExit(_main(sys.argv[1:]))
