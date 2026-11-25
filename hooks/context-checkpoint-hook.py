#!/usr/bin/env python3
"""Stop hook — capture ONE context-chain checkpoint per turn (B3, council wf_e537db61).

The WRITE-SIDE harness driver for the signed context memory-chain (P3 ``lib.context_chain``).
On turn end it appends at most ONE ``context-delta`` carrying the volatile ``next_step`` +
a kernel ``summary`` derived — WITHOUT an LLM (honours the P3 import-isolation lint) — from
the session's last assistant message (written by ``stop-capture.sh`` earlier in the same
Stop array to ``$CLAUDE_DIR/state/session/last-assistant-msg.txt``).

Three behaviours, in order:

1. **Dedup / milestone gate (the core).** Compute the candidate decision body EXACTLY as
   ``context_chain.checkpoint`` would, take its ``content_addr`` (the semantic body hash,
   excluding volatile id/ts/audit), and compare to the CURRENT HEAD node's ``content_addr``.
   If equal — the turn produced no new mental-model delta — **SKIP the append** (the chain
   stays bounded between snapshots; only genuine milestones extend it). Else append exactly
   one node via ``checkpoint(...)``.

2. **Debounced reaper.** Every Nth fire (``_REAP_EVERY``) run ``context_gc.reap()`` + log the
   ``gc_report`` report-split (``live`` vs ``archived_collapsed`` — never frames archived as
   current, the mesh_gc lesson). Debounced because reaping every turn is wasteful and the
   chain only grows on milestones.

3. **Fail-open always.** Any error (missing chain dir, malformed HEAD, import failure) is
   swallowed to stderr — a capture hook must NEVER block turn completion. This is telemetry,
   not a gate.

SHIPS DORMANT: this file is NOT wired into ``settings.json`` in the PR that introduces it
(that shared-config flip is V>>-gated). Enable later by adding it to the ``Stop`` array AFTER
``stop-capture.sh`` (which writes the ``last-assistant-msg.txt`` this hook reads).

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §4-§6.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Make the hook self-sufficient regardless of cwd/PYTHONPATH (the Stop array invokes it by
# absolute path, where cwd is NOT on sys.path). BOTH roots: CLAUDE_DIR resolves the
# ``from lib.context_chain import …`` style; CLAUDE_DIR/lib resolves the bare top-level
# imports those modules use transitively (e.g. ``import file_lock``) — the known dual-root
# lib/ resolution hazard (learned-insights f47b0f96). Idempotent + leaves cwd untouched.
for _root in (str(CLAUDE_DIR), str(CLAUDE_DIR / "lib")):
    if _root not in sys.path:
        sys.path.insert(0, _root)

_LAST_MSG = CLAUDE_DIR / "state" / "session" / "last-assistant-msg.txt"  # state_paths.SESSION_LAST_ASSISTANT_MSG
_FIRE_COUNTER = CLAUDE_DIR / "state" / "precog" / "checkpoint-fire-count.txt"
_GC_LOG = CLAUDE_DIR / "logs" / "context-checkpoint-gc.jsonl"

# Reaper debounce — reap every Nth fire. The chain only grows on milestones, so a
# per-turn reap is wasteful; N keeps GC amortised while bounding archive lag.
_REAP_EVERY = 20
_SUMMARY_CAP = 600  # mirrors checkpoint()'s own summary truncation (DRY with the body shape)


def _read_last_message() -> str:
    """The no-LLM summary source: the last assistant message captured by
    ``stop-capture.sh``. Empty string when absent (first turn / hook not yet run)."""
    try:
        return _LAST_MSG.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _derive_next_step(summary: str) -> dict:
    """Derive a structured ``next_step`` from the last message WITHOUT an LLM (P3).

    A purely mechanical extraction: GRIP's anti-drift block (Rule 11) ends turns with
    ``Next: <one concrete step>``. We lift that line by literal prefix-match when present,
    else leave it empty. Never calls a model — deterministic, byte-stable for a given input.
    """
    for raw in summary.splitlines():
        line = raw.strip().lstrip("*-# ").strip()
        low = line.lower()
        if low.startswith("next:"):
            return {"step": line.split(":", 1)[1].strip()}
    return {}


def _candidate_decision(next_step: dict, summary: str) -> dict:
    """Build the decision body BYTE-IDENTICALLY to ``context_chain.checkpoint`` so the
    candidate ``content_addr`` is comparable to a real HEAD node's. Any drift between this
    shape and ``checkpoint``'s would silently break the dedup gate — kept in lockstep, and
    the Goodhart test asserts an unchanged body actually skips."""
    return {
        "next_step": next_step if isinstance(next_step, dict) else {},
        "summary": str(summary or "")[:_SUMMARY_CAP],
        "title": "context checkpoint",
        "tier": "feedback",
        "paramount": False,
    }


def _dedup_addr(decision: dict) -> str:
    """THE single dedup coordinate — content address of a DECISION BODY (not a whole node).

    Computed in exactly ONE place (DRY) so the candidate and the HEAD node are hashed by an
    identical rule; any divergence would silently break the gate. We deliberately hash the
    decision body — NOT ``context_chain.context_addr``, which hashes the whole node envelope
    (incl. ``predecessor_idr``) and therefore differs node-to-node even for an unchanged
    mental model. The dedup question is "does this turn carry a new DECISION?", so the
    predecessor link is correctly excluded."""
    from lib.precog.idr import content_addr  # noqa: E402 (deferred; keeps import-clean)

    return content_addr({"kind": "context-delta", "decision": decision or {}})


def _head_addr() -> str | None:
    """The CURRENT HEAD node's dedup address, or ``None`` when the chain is empty. Routes
    through ``context_head.read_latest`` — the single reader shim (design §7) — then hashes
    its decision body with ``_dedup_addr`` (same rule as the candidate)."""
    from lib.context_head import read_latest  # noqa: E402

    head_node = read_latest()
    if head_node is None:
        return None
    return _dedup_addr(head_node.decision or {})


def _bump_fire_count() -> int:
    """Monotone per-session fire counter (debounce clock for the reaper). Best-effort:
    a counter read/write failure returns 0 so the reaper simply does not fire this turn."""
    try:
        _FIRE_COUNTER.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        if _FIRE_COUNTER.exists():
            n = int(_FIRE_COUNTER.read_text(encoding="utf-8").strip() or "0")
        n += 1
        _FIRE_COUNTER.write_text(str(n) + "\n", encoding="utf-8")
        return n
    except (OSError, ValueError):
        return 0


def _maybe_reap(fire_count: int) -> None:
    """Debounced GC: every ``_REAP_EVERY`` fires, reap the pre-snapshot prefix and log the
    report-split. Fail-open — GC failure never aborts the hook."""
    if fire_count <= 0 or fire_count % _REAP_EVERY != 0:
        return
    try:
        from lib.context_gc import gc_report, reap  # noqa: E402

        reap()
        report = gc_report()
        _GC_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _GC_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"fire": fire_count, "report": report}, separators=(",", ":")) + "\n")
    except Exception as exc:  # noqa: BLE001 — GC is best-effort telemetry
        print(f"[context-checkpoint] reap skipped: {exc}", file=sys.stderr)


def capture(summary: str) -> str:
    """Core capture decision (pure-ish; returns a one-word status for tests/telemetry).

    ``"skip"`` when the candidate body's content_addr equals HEAD's (dedup/milestone gate);
    ``"append"`` when it differs and a node was written. Raises nothing the caller relies on —
    the module entrypoint wraps this fail-open."""
    next_step = _derive_next_step(summary)
    decision = _candidate_decision(next_step, summary)
    candidate = _dedup_addr(decision)
    head = _head_addr()
    if head is not None and candidate == head:
        return "skip"
    from lib.context_chain import checkpoint  # noqa: E402
    from lib.context_chain_classify import classify_checkpoint  # noqa: E402

    # P1 source-population: stamp the same (tier, paramount) pin axis /save does,
    # via the shared classifier (DRY) — so a chain node written on the Stop path
    # is foldable on equal footing with one written by /save.
    tier, paramount = classify_checkpoint(summary, next_step)
    checkpoint(next_step or None, summary, tier=tier, paramount=paramount)
    return "append"


def main() -> int:
    # Drain stdin (Stop event JSON) — we read the message from the file stop-capture.sh
    # already wrote, not from this payload, so a parse failure here is harmless.
    try:
        sys.stdin.read()
    except Exception:  # noqa: BLE001
        pass

    summary = _read_last_message()
    status = "skip"
    try:
        status = capture(summary)
    except Exception as exc:  # noqa: BLE001 — a capture hook must never block turn end
        print(f"[context-checkpoint] capture skipped: {exc}", file=sys.stderr)

    fire_count = _bump_fire_count()
    _maybe_reap(fire_count)
    print(f"[context-checkpoint] {status} (fire {fire_count})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
