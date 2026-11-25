#!/usr/bin/env python3
"""GRIP-First Retrieval Gate (W=4 EMBODIED)

PreToolUse hook: when Agent(subagent_type=Explore) is invoked for a query
that GRIP's own knowledge can answer, suggest /recall first.

V4: Refactored to use lib/grip_first_common.py shared module (DRY, YSH).
Keeps only dispatch logic. All constants, type taxonomy, keyword regex,
evaluation logic, and embodiment recording live in the shared module.

Falls back to keyword check if KONO is unavailable.
"""
import json
import os
import sys

# --- Shared module imports (aliased for backward compat with existing tests) ---
# Code is imported from the tree this hook lives in, never from $HOME/.claude:
# where $HOME/.claude is a stale copy rather than a link to the clone (a Windows
# teammate's layout, where Python reads the home folder from USERPROFILE),
# importing from it crashed this gate before it could check anything
# (fleet report 2026-10-02, B-2). Data paths are unchanged.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib"))
from grip_first_common import (  # noqa: E402
    GRIP_KEYWORDS,
    GRIP_PATHS,
    GRIP_TYPES_HIGH,
    GRIP_TYPES_MEDIUM,
    HIGH_SIMILARITY_FLOOR,
    KONO_TIMEOUT,
    SIMILARITY_FLOOR,
    allow_response as allow,
    evaluate_results as _evaluate_results,
    keyword_check,
    kono_search as semantic_check,
    record_embodiment as _record_embodiment,
)


def main():
    event = json.load(sys.stdin)
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    if tool != "Agent":
        return allow()
    subtype = tool_input.get("subagent_type", "")
    if subtype not in ("Explore", "general-purpose"):
        return allow()

    prompt = tool_input.get("prompt", "")

    is_grip, score, etype, snippet = semantic_check(prompt)

    if is_grip is True:
        _record_embodiment(True)
        from hook_response import emit, pretool_decision
        emit(pretool_decision("allow", (
            f"[GRIP-FIRST RETRIEVAL] KONO has a {etype} match "
            f"(sim={score:.3f}): \"{snippet}...\". "
            f"Consider /recall first — <2s vs ~88k tokens."
        )))
        return

    if is_grip is False:
        return allow()

    # KONO unavailable — keyword fallback
    is_keyword, matches = keyword_check(prompt)
    if is_keyword:
        _record_embodiment(True)
        from hook_response import emit, pretool_decision
        emit(pretool_decision("allow", (
            f"[GRIP-FIRST RETRIEVAL] KONO unavailable, keyword fallback "
            f"(matches: {matches}). Consider /recall first."
        )))
        return

    allow()


if __name__ == "__main__":
    main()
