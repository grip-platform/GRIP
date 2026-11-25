"""Pool-A offload advisory: the first reader of `route_heavy_to_hal`.

rules/pool-a-offload-routing.md: on a Pro plan, every Claude Code subagent
(``Agent``/``Task``) and every ``claude -p`` / ``claude --print`` child spends
the one Claude account's weekly allowance -- Pool A. ``lib/grip_tier.py``'s
per-tier envelope has carried ``route_heavy_to_hal`` since it was introduced
(GRIP#3276, 2026-05-28) -- ``True`` for ``pro``, ``False`` for ``max``/
``api`` -- but nothing in production read it before this module. It is that
first reader, and it is advisory only: it never denies a spawn and takes no
permission decision (rules/gate-resolution-escape-hatch.md, Invariant 1 --
there is no deny path to escape).

``advise`` is the pure half. It has no side effects: no filesystem write, no
subprocess, no network. Reading ``os.environ`` for the operator override is
the same notion of "pure resolver" ``lib/grip_tier.py`` itself uses -- a
deterministic read within a process, not an I/O effect a caller needs to
mock. ``hooks/pool-a-offload-advisory.py`` is the impure half: it reads the
PreToolUse event, resolves the tier envelope, keeps the per-session
already-advised state, and emits the response.
"""
from __future__ import annotations

import os

__all__ = ["KINDS", "ENV_OFF", "advise"]

#: The two spawn shapes this advisory recognises.
KINDS = ("agent", "claude-p")

#: Set in the environment to silence the advisory for a session or machine.
ENV_OFF = "GRIP_POOL_A_ADVISORY"

#: Pool-B entry points named in rules/pool-a-offload-routing.md -- a council,
#: a cost-tiered reasoning pass, the Codex subscription ladder, and the HAL
#: gateway itself.
_ENTRY_POINTS = (
    "grip-sovereign-council, /ultrareason, bin/grip-codex-ladder, or the HAL "
    "gateway"
)

_RULE = "rules/pool-a-offload-routing.md"

_TEXT = {
    "agent": (
        "[Pool-A offload] This session is on a Pro plan, so every subagent "
        "(Agent/Task) call spends the one Claude account's weekly "
        "allowance. Heavy or parallel work can run on HAL's Pool B "
        f"instead, at no Claude cost: try {_ENTRY_POINTS}. See {_RULE}."
    ),
    "claude-p": (
        "[Pool-A offload] This session is on a Pro plan, so a `claude -p` "
        "/ `claude --print` child spends the one Claude account's weekly "
        "allowance too. Heavy or batch work can run on HAL's Pool B "
        f"instead, at no Claude cost: try {_ENTRY_POINTS}. See {_RULE}."
    ),
}


def _classify(tool_name: object, command: object) -> str | None:
    """The spawn shape this call is, or ``None`` when it is neither."""
    if tool_name in ("Agent", "Task"):
        return "agent"
    if (
        tool_name == "Bash"
        and isinstance(command, str)
        and ("claude -p" in command or "claude --print" in command)
    ):
        return "claude-p"
    return None


def advise(
    tool_name: object,
    command: object,
    envelope: dict,
    already_advised=None,
) -> tuple[str, str] | None:
    """The advisory for one tool call, or ``None`` when there is nothing to say.

    ``envelope`` is a ``lib/grip_tier.py`` tier envelope (or anything carrying
    its ``route_heavy_to_hal`` key): silent whenever that is falsy, because
    only a tier that actually benefits from offloading (today: ``pro``) gets
    a nudge. ``already_advised`` is the set of kinds already shown this
    session; each kind fires at most once per session.
    """
    if not isinstance(envelope, dict) or not envelope.get("route_heavy_to_hal"):
        return None
    if os.environ.get(ENV_OFF, "").strip().lower() == "off":
        return None
    kind = _classify(tool_name, command)
    if kind is None:
        return None
    if already_advised and kind in already_advised:
        return None
    return kind, _TEXT[kind]
