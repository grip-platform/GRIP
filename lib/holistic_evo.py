"""P2 W3 — `/holistic evo` (the backgrounded fractal-evolution chain).

plans/unified-holistic-command.md W3 (CAREFUL): ONE ``run_in_background``
Agent chaining ``insights -> evo -> learn``, a plain-language ledger, NO
auto-broadcast, NEVER the word "deterministic" for the composite.

evo-dispatch-protocol (PARAMOUNT): ``/evo`` emits 80+ lines of friction
tables / verdicts / yolo tiles. Run inline it pollutes the main session
context; run in an isolated Agent only the agent's tiny return reaches
the main thread. H-HOL-4: ``/holistic evo`` must never block or pollute
the main session.

This lib is PURE COMPOSITION — identical shape to ``holistic_save`` /
``holistic_advisory``. It does NOT run evo/learn, does NOT spawn a
subprocess, and does NOT call the Agent tool (a harness primitive, not a
Python API). It returns a *dispatch spec* the router/skill hands to ONE
``Agent(run_in_background=True, subagent_type="general-purpose", ...)``,
plus a plain-language ledger, and returns IMMEDIATELY (non-blocking).
Unlike ``/holistic update`` it does NOT broadcast a digest.

Rule 21: the spec composes the REAL canonical primitives —
``lib/insight_collector.py flush``, ``lib/evo.py run``, and the ``/learn``
skill — it reimplements none of them.
"""
from __future__ import annotations

# The three canonical primitives the background chain composes. Referenced
# as the real GRIP entrypoints (Rule 21) — never reimplemented here.
# GRIP dir is resolved by the dispatched agent's SHELL, never hardcoded
# to a user home (rules/path-portability.md PARAMOUNT; sibling
# holistic_advisory.py uses the same CLAUDE_DIR contract). These command
# strings are executed by the background agent via shell, so
# ``${CLAUDE_DIR:-$HOME/.claude}`` expands correctly there and honours an
# operator CLAUDE_DIR override. Plain concatenation (NOT an f-string and
# NEVER str.format-substituted) so the shell ``${...}`` braces can never
# collide with Python field parsing — this structurally removes the whole
# KeyError class, not just one instance.
_GRIP = "${CLAUDE_DIR:-$HOME/.claude}"
_INSIGHTS_FLUSH = "python3 " + _GRIP + "/lib/insight_collector.py flush ''"
_LEARN_STEP = "Invoke the /learn skill (pattern detection -> playbook proposals)"


def _evo_cmd(evo_n: int) -> str:
    """The evo step, built by concatenation (no .format) so the shell
    ``${...}`` in _GRIP is never seen by Python's format parser."""
    return "python3 " + _GRIP + "/lib/evo.py run " + str(int(evo_n))

_AGENT_PROMPT = (
    "Run these three GRIP maintenance steps in order, each exactly once, "
    "then STOP:\n"
    "1. {flush}\n"
    "2. {evo}\n"
    "3. {learn}\n"
    "Return ONLY a 3-line plain-language summary: insights flushed "
    "(count), evo facet count + calibrated FD, learn proposal count. Do "
    "NOT paste the evo friction tables, verdict blocks, yolo tiles, or "
    "any learn internals — summary lines only."
)


def build_dispatch(*, evo_n: int = 7) -> dict:
    """The spec the router hands to ONE background Agent. Pure data: no
    side effect, no subprocess, no Agent call here. evo-dispatch-protocol:
    ``subagent_type`` general-purpose, ``run_in_background`` True, isolated
    context so the 80+ line evo output never reaches the main session."""
    return {
        "subagent_type": "general-purpose",
        "run_in_background": True,
        "description": "/holistic evo - insights->evo->learn (isolated)",
        # _AGENT_PROMPT has ONLY {flush}{evo}{learn} fields (no shell
        # ${...}); str.format does not re-scan substituted values, so the
        # ${CLAUDE_DIR:-$HOME/.claude} inside flush/evo is inert here.
        "prompt": _AGENT_PROMPT.format(
            flush=_INSIGHTS_FLUSH,
            evo=_evo_cmd(evo_n),
            learn=_LEARN_STEP,
        ),
    }


def holistic_evo(*, evo_n: int = 7) -> dict:
    """Compose the ``/holistic evo`` dispatch spec + a plain-language
    ledger. NON-BLOCKING: returns the spec for the router to background;
    runs NO evo/learn here, broadcasts NOTHING (H-HOL-4 — evo must never
    block or pollute the main session; the digest-broadcast path is
    ``/holistic update``, deliberately separate). The ledger NEVER calls
    the composite "deterministic" — evo/learn are best-effort, not
    deterministic.

    Returns ``{"dispatch": <Agent spec>, "ledger": [plain lines],
    "blocking": False, "broadcast": False}``.
    """
    dispatch = build_dispatch(evo_n=evo_n)
    ledger = [
        "queued (background, isolated): flush newly-accumulated insights",
        f"queued (background, isolated): fractal evolution (evo run {evo_n})",
        "queued (background, isolated): learn - patterns into playbook "
        "proposals",
        "dispatched to ONE background agent; main session not blocked and "
        "no digest broadcast (use /holistic update for the broadcast path)",
    ]
    return {
        "dispatch": dispatch,
        "ledger": ledger,
        "blocking": False,
        "broadcast": False,
    }
