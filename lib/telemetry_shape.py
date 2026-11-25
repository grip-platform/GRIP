#!/usr/bin/env python3
"""
telemetry_shape.py — shared predicate: is this text /evo friction
telemetry rather than a generalisable skill/insight?

/evo emits friction-frequency aggregates like:

    "/evo: 'tool_limitation' ff=23% (21/27). Ev: grip_workflow:broly, ..."

These are MEASUREMENTS, not engineering lessons. They must never enter
the skill -> approved -> KONO promotion path (they pollute semantic
recall and duplicate across source hashes). This module is the single
source of truth for that distinction, imported by both the creation
gate (learning_detector) and the promotion floor (insight_promoter).

Pure, deterministic, stdlib-only — no I/O, no imports beyond `re`.
"""
from __future__ import annotations

import re

# Two independent signatures, EITHER one disqualifies:
#   1. content begins with the "/evo:" facet-calibration prefix
#   2. the friction-frequency aggregate shape: 'name' ff=NN% (n/m)
_EVO_PREFIX_RE = re.compile(r"^\s*/evo:\s", re.IGNORECASE)
_FRICTION_AGG_RE = re.compile(r"'[a-z_]+'\s+ff=\d+%\s+\(\d+/\d+\)", re.IGNORECASE)


def is_evo_telemetry(content: str) -> bool:
    """
    True if `content` is /evo friction-frequency telemetry rather than a
    generalisable skill/insight.

    A genuine engineering lesson ("search for all callers before deleting
    a function") matches neither signature, so it is never mis-flagged.
    A casual prose mention of the evo command ("I ran /evo and ...") also
    does not match — the prefix requires a leading "/evo:" and the
    aggregate requires the full ff=NN% (n/m) shape.
    """
    if not content:
        return False
    return bool(_EVO_PREFIX_RE.search(content) or _FRICTION_AGG_RE.search(content))


# Execution-friction records: a guardrail/gate BLOCKED a tool call, captured
# verbatim by auto-memory as an "insight" (e.g. "Bash execution error
# [guardrail_generic]: A GRIP PreToolUse guardrail blocked the Bash call ...").
# These are FRICTION TELEMETRY, not engineering lessons — the buried "fix the
# root cause" advice is already canonical in rules/. Promoting them pollutes the
# always-loaded learned-insights.md, and the naive Do->Don't inverse can render a
# safety "never evade the gate" as "always evade the gate".
_FRICTION_BLOCK_RE = re.compile(
    r"^\s*(?:bash|tool)\s+execution\s+error\b", re.IGNORECASE)


def is_friction_telemetry(content: str) -> bool:
    """True if `content` is an auto-captured execution-friction record (a
    guardrail/gate blocked a tool call) rather than a generalisable lesson.

    Sibling to is_evo_telemetry: same purpose (keep friction telemetry out of
    the skill/rule promotion path), different shape. Anchored on the verbatim
    auto-capture PREFIX ("Bash execution error", "Tool execution error"), so a
    genuine lesson that merely DISCUSSES guardrails in prose ("fix the root
    cause, never evade a gate") is never mis-flagged — only the block record,
    which always leads with the execution-error prefix, matches.
    """
    if not content:
        return False
    return bool(_FRICTION_BLOCK_RE.search(content))
