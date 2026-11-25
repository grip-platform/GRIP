"""P2 W4 — the `/holistic` verb router (ONE dispatch table, no if/elif).

plans/unified-holistic-command.md W4 (CAREFUL, d3). A single
``{verb: handler}`` table is the entire control flow
(cc-enforcement.md Extract-Dispatch — never an if/elif ladder; adding a
verb is one row).

Rule 21: every handler LAZY-imports and calls the canonical W1/W2/W3
lib UNCHANGED. This module reimplements nothing and — because the
imports are in-function (the exact discipline ``holistic_save`` uses) —
stays importable even when a verb's lib is still in an unmerged PR
(advisory #2855, evo #2856). Importing this router never requires those
libs; only *dispatching that verb* does.

Bounded scope (the binding-DA invariant): the router exposes only
memory-local verbs. There is deliberately **no close-others verb** —
``advisory`` is write-only; the OWNING peer self-consumes its advisory
file and runs its OWN ``/closeout``. Under ``/holistic`` provenance the
W0 PreToolUse gate independently DENIES any git-mutation/closeout
(cross-wave backstop — defence in depth, not the primary).
"""
from __future__ import annotations

from typing import Callable, Dict, List


def _save(**kw):
    from lib.holistic_save import holistic_save
    return holistic_save(kw.get("achievement"), quiet=kw.get("quiet", False))


def _update(**kw):
    from lib.holistic_synthesiser import regenerate
    return regenerate()


def _advisory(**kw):
    from lib.holistic_advisory import emit_closeout_advisory
    return emit_closeout_advisory(kw.get("root"), self_pid=kw.get("self_pid"))


def _evo(**kw):
    from lib.holistic_evo import holistic_evo
    return holistic_evo(evo_n=kw.get("evo_n", 7))


# THE dispatch table — this dict IS the control flow (KISS / Extract-
# Dispatch). There is deliberately no if/elif anywhere in this module.
_HANDLERS: Dict[str, Callable[..., object]] = {
    "save": _save,
    "update": _update,
    "advisory": _advisory,
    "evo": _evo,
}


def known_verbs() -> List[str]:
    """The supported verbs, sorted. Bounded set — no close-others verb."""
    return sorted(_HANDLERS)


def dispatch(verb: str, **kw) -> object:
    """Route ``verb`` to its canonical handler. Unknown verb ⇒ ValueError
    naming the known verbs (never a silent no-op, never an opaque
    KeyError). CC is ~2 by construction: table lookup, one guard, call."""
    handler = _HANDLERS.get(verb)
    if handler is None:
        raise ValueError(
            f"unknown /holistic verb {verb!r}; "
            f"known: {', '.join(known_verbs())}"
        )
    return handler(**kw)


def _main(argv: List[str]) -> int:
    """Thin CLI surface for bin/holistic. ``holistic <verb>`` → dispatch.
    Unknown verb ⇒ stderr + exit 2 (the bin/holistic usage contract)."""
    if not argv:
        print(f"usage: holistic <{('|'.join(known_verbs()))}>", flush=True)
        return 2
    verb, rest = argv[0], argv[1:]
    # CLI-adapter boundary only (the dispatch table itself stays a pure
    # {verb: handler} dict — no per-verb branching leaks into dispatch()).
    # `save` is the one verb with a documented free-text positional
    # (`/holistic save [achievement]` per skills/holistic-memory/SKILL.md);
    # forward it so the documented surface actually works. A bare verb
    # with no args is fully valid — every handler defaults via kw.get().
    kw = {"achievement": " ".join(rest)} if verb == "save" and rest else {}
    try:
        result = dispatch(verb, **kw)
    except ValueError as e:
        print(str(e), file=__import__("sys").stderr)
        return 2
    ledger = result.get("ledger") if isinstance(result, dict) else None
    for line in (ledger or [f"{verb}: done"]):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
