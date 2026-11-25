"""Shared classifier for context-chain checkpoints (memory-chain P1).

Both /save (``grip_save._save_context_chain``) and the Stop-path checkpoint hook
(``hooks/context-checkpoint-hook.capture``) write signed ``context-delta`` nodes
and must stamp the SAME ``(tier, paramount)`` pin axis so the eventual P2
projector folds ``(pinned ∪ recent-N)`` consistently regardless of which path
wrote the node (plans/blackbook-memory-chain-canonical.md, P1).

Extracted from grip_save (YSH — proven at 2 callers) into this lightweight module
so the frequently-fired hook need not import grip_save's heavy surface (argparse,
subprocess, tui_links, …). Only stdlib ``re`` at import; the holistic synthesiser
classifiers are imported lazily so a missing/!importable synthesiser degrades to
the inert default, never raising into a best-effort chain write.
"""
from __future__ import annotations

import re

# A cited memory file: feedback_/project_/reference_/user_ prefix + .md.
_MEM_FILE_RE = re.compile(r"\b((?:feedback|project|reference|user)_[\w./-]+?\.md)\b")

# Inert default when nothing classifies — mirrors checkpoint()'s own defaults.
_DEFAULT_TIER = "feedback"


def classify_checkpoint(summary: str, next_up: dict | None) -> tuple[str, bool]:
    """Return ``(tier, paramount)`` for a context-chain checkpoint.

    - ``paramount`` — the literal ``PARAMOUNT`` token appears in the achievement
      summary or the next_step text (the same mechanical precheck the holistic
      synthesiser uses to flag never-drop memories).
    - ``tier`` — the highest-priority tier (feedback > project > user > reference)
      among any memory files cited in the text, else the ``"feedback"`` default.
      A checkpoint that references a PARAMOUNT/feedback memory inherits that
      importance for the fold.

    Fail-soft: any classifier import/parse failure degrades to the inert default
    ``("feedback", False)`` — never raises into the best-effort chain write.
    """
    try:
        from lib.holistic_synthesiser import (  # noqa: PLC0415 — lazy: keep module light
            _TIER_PRIORITY,
            _is_paramount,
            _tier_of,
        )
    except Exception:  # noqa: BLE001 — default IS the inert floor; never abort the chain
        return _DEFAULT_TIER, False

    nu = next_up if isinstance(next_up, dict) else {}
    next_text = " ".join(
        str(v) for v in nu.values()
        if isinstance(v, (str, int, float))
    )
    blob = f"{summary or ''}\n{next_text}"
    paramount = _is_paramount(summary or "", next_text)
    cited = _MEM_FILE_RE.findall(blob)
    if cited:
        tier = min(
            (_tier_of(f) for f in cited),
            key=lambda t: _TIER_PRIORITY.get(t, 6),
        )
    else:
        tier = _DEFAULT_TIER
    return tier, paramount
