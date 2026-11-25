#!/usr/bin/env python3
"""GRIP-First Web Gate (W=1 DETECTED)

PreToolUse hook: when WebFetch or WebSearch is invoked, query KONO for
existing knowledge and suggest checking GRIP first before going external.

V2: Refactored to use lib/grip_first_common.py shared module (DRY, YSH).
Keeps only main(), extract_search_term(), and __main__ block.
All constants, type taxonomy, keyword regex, evaluation logic, timeout
guard, and embodiment recording live in the shared module.

Advisory only -- never blocks, always allows with an optional reason
when KONO has a match.
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
    _raw_kono_search as _kono_search,
    allow_response as allow,
    evaluate_results as _evaluate_results,
    keyword_check,
    kono_search as semantic_check,
    record_embodiment as _record_embodiment,
)


def extract_search_term(tool_name, tool_input):
    """Extract the URL or query from WebFetch/WebSearch tool input."""
    if tool_name == "WebFetch":
        return tool_input.get("url", "")
    if tool_name == "WebSearch":
        return tool_input.get("query", "")
    return ""


def main():
    event = json.load(sys.stdin)
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    if tool not in ("WebFetch", "WebSearch"):
        return allow()

    search_term = extract_search_term(tool, tool_input)
    if not search_term:
        return allow()

    is_grip, score, etype, snippet = semantic_check(search_term)

    if is_grip is True:
        _record_embodiment(True)
        from hook_response import emit, pretool_decision
        emit(pretool_decision("allow", (
            '[GRIP-FIRST WEB] KONO has a ' + etype + ' match '
            '(sim=' + f'{score:.3f}' + '): "' + snippet + '...". '
            'Consider /recall or /remind-yourself first '
            '-- avoids external dependency.'
        )))
        return

    if is_grip is False:
        return allow()

    # KONO unavailable or timed out -- keyword fallback
    is_keyword, matches = keyword_check(search_term)
    if is_keyword:
        _record_embodiment(True)
        from hook_response import emit, pretool_decision
        emit(pretool_decision("allow", (
            '[GRIP-FIRST WEB] KONO unavailable, keyword fallback '
            '(matches: ' + str(matches) + '). Consider /recall first.'
        )))
        return

    allow()


if __name__ == "__main__":
    main()
