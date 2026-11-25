#!/usr/bin/env python3
"""SessionStart hook: hand the main session the main-session tier's rules in full.

    main-tier-inject.py --chunk K

config/rules-scope.json ``main_tier`` lists rules only the main session needs: their
readers are talks-to-operator or orchestrates. settings.claudeMdExcludes keeps them out
of native loading, so no subagent loads them, and this hook delivers them to the main
session whole. Hook output over 10,000 characters reaches the model as a 2KB preview
(GRIP#6446), so lib/main_tier.py packs the rules into chunks of at most 9,500
characters, and this hook prints chunk K. Settings bind it six times, ``--chunk 0`` to
``--chunk 5``, under a matcher that selects every kind of start, so the rules arrive
again after /clear and after compaction (KERN v2, ruling 5, 2026-10-18).

Contract:
- Prints {"hookSpecificOutput": {"hookEventName": "SessionStart",
  "additionalContext": <chunk K>}}, and nothing at all when the tier has no chunk K.
- Delivers the same chunk on every kind of start, so it does not read its input.
- Fail-open: any error, a missing or malformed --chunk included, prints nothing and
  exits 0. It never breaks session start.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

_CHUNK_ARG = re.compile(r"--chunk[ =]([0-9]+)")


def chunk_index(argv: list[str]) -> int:
    """K from ``--chunk K`` or ``--chunk=K``; ValueError for anything else."""
    match = _CHUNK_ARG.fullmatch(" ".join(argv))
    if not match:
        raise ValueError(f"expected --chunk K, got {argv!r}")
    return int(match.group(1))


def _emit(additional_context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }))


def main(argv: list[str]) -> int:
    try:
        index = chunk_index(argv)
        from claude_dir import get_claude_dir
        import main_tier
        import rules_scope

        root = get_claude_dir()
        cfg = rules_scope.load_config(root / "config" / "rules-scope.json")
        text = main_tier.chunk(cfg, root / "rules", index)
        if text:
            _emit(text)
    except Exception:
        pass  # Fail-open: never block session start over rule delivery.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
