"""Session-start token counter (D6 from TF>> liberation matrix follow-on, 2026-04-20).

Measures the token cost of context loaded at session start:
    CLAUDE.md + always-loaded rules + the active mode's skill set
    (D4-aware via :class:`SkillLoader`)

This is the measurement instrument that D4's falsifier requires:

    > "If lazy-load shaves <100 tokens off session start, complexity isn't
    > justified."  -- docs/NOVEL-FEATURES.md, TF>> liberation matrix

Without this counter, D4's hypothesis can't be confirmed or refuted: marking
a skill ``lazy`` in ``grip-features.json`` would shrink the auto-load list,
but with no meter on either side of the change, the saving stays invisible.
This module is the "before" / "after" needle.

Why bytes/4 instead of a real tokenizer? Because the hypothesis cares about
deltas in the ~100-token range. The 4-chars-per-token approximation is the
industry rule-of-thumb, stable across snapshots, and avoids importing
``tiktoken`` (heavy dep) for a measurement that doesn't need exactness.
A real tokenizer would shift every count by ~10-20%, but the *delta* between
two snapshots taken with the same approximation is all D4 needs.

Usage::

    from session_start_tokens import count_session_start_tokens, record_session_start_tokens

    breakdown = count_session_start_tokens(mode="code")
    # {"total": 12500, "claude_md": 1800, "rules": 4200, "skills": 6500, ...}

    record_session_start_tokens(mode="code")  # also writes to metrics.jsonl
"""
import os
import sys
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Industry rule-of-thumb. tiktoken would be more accurate but the hypothesis
# only needs delta resolution at ~100 tokens — the constant is adequate for
# that and stable across snapshots.
_CHARS_PER_TOKEN = 4


def _file_token_count(path: Path) -> int:
    """Return rough token count for a single file. Missing or unreadable = 0."""
    if not path.is_file():
        return 0
    try:
        return path.stat().st_size // _CHARS_PER_TOKEN
    except OSError:
        return 0


def _rules_token_count(rules_dir: Path) -> int:
    """Sum token cost of every always-loaded ``rules/*.md``."""
    if not rules_dir.is_dir():
        return 0
    return sum(_file_token_count(md) for md in sorted(rules_dir.glob("*.md")))


def _skills_token_count(mode: Optional[str],
                        claude_dir: Path) -> tuple[int, int]:
    """Token cost of skills auto-loaded for the given mode.

    Returns ``(total_tokens, skill_count)``. Honours D4 ``disabled_skills``
    / ``lazy_skills`` via :class:`SkillLoader` — that's the whole point: a
    lazy-load change must visibly reduce this number.

    SkillLoader defaults its ``skills_dir`` to ``HAL_HOME/skills``; for D6 we
    point it at GRIP's tree (``CLAUDE_DIR/skills``) since that's what loads
    into the user's session, not HAL's substrate skills.
    """
    if not mode:
        return (0, 0)
    # Two paths needed: lib/ for ``from hal.skill_loader import SkillLoader``
    # and the repo root for ``from lib.hal.home import HAL_HOME`` that the
    # loader does internally. Without the root entry, the inner import fails
    # silently and the ImportError trap below misreports as zero skills.
    for p in (str(claude_dir / "lib"), str(claude_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from hal.skill_loader import SkillLoader  # type: ignore
    except ImportError:
        return (0, 0)
    loader = SkillLoader(
        skills_dir=str(claude_dir / "skills"),
        modes_dir=str(claude_dir / "modes" / "definitions"),
        features_path=str(claude_dir / "grip-features.json"),
    )
    names = loader.resolve_for_mode(mode)
    total = sum(len(loader.get_skill_content(name)) // _CHARS_PER_TOKEN
                for name in names)
    return (total, len(names))


def count_session_start_tokens(mode: Optional[str] = None,
                                claude_dir: Optional[Path] = None) -> dict:
    """Snapshot the token cost of session-start context.

    Args:
        mode: GRIP mode to resolve skills for (None = no skills counted).
        claude_dir: Override CLAUDE_DIR (testing). Defaults to env var or ~/.claude.

    Returns:
        ``{"total": int, "claude_md": int, "rules": int, "skills": int,
           "skill_count": int, "mode": Optional[str]}``

        ``total`` is the sum of ``claude_md + rules + skills``. Snapshots
        taken before and after a ``grip-features.json`` change measure D4's
        100-token falsifier directly: ``before["total"] - after["total"]``.
    """
    base = Path(claude_dir) if claude_dir else CLAUDE_DIR
    claude_md = _file_token_count(base / "CLAUDE.md")
    rules = _rules_token_count(base / "rules")
    skills_total, skill_count = _skills_token_count(mode, base)
    return {
        "total": claude_md + rules + skills_total,
        "claude_md": claude_md,
        "rules": rules,
        "skills": skills_total,
        "skill_count": skill_count,
        "mode": mode,
    }


def record_session_start_tokens(mode: Optional[str] = None,
                                 claude_dir: Optional[Path] = None) -> dict:
    """Snapshot + persist via the central :mod:`metrics_tracker` facade.

    The breakdown is returned even when persistence fails (no metrics_tracker
    on path) so callers can use the result directly.
    """
    breakdown = count_session_start_tokens(mode=mode, claude_dir=claude_dir)
    base = Path(claude_dir) if claude_dir else CLAUDE_DIR
    lib_path = str(base / "lib")
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    try:
        from metrics_tracker import track_event  # type: ignore
        track_event("session_start_tokens", **breakdown)
    except ImportError:
        pass
    return breakdown


def delta_tokens(before: dict, after: dict) -> dict:
    """Compute the per-component delta between two snapshots.

    Positive numbers mean ``after`` shrank (tokens saved); negative means
    ``after`` grew. Designed for D4 evaluation::

        baseline = count_session_start_tokens(mode="code")
        # ... edit grip-features.json to lazy-load some skills ...
        after = count_session_start_tokens(mode="code")
        savings = delta_tokens(baseline, after)
        if savings["total"] >= 100:
            print("D4 confirmed for this mode")
    """
    keys = ("total", "claude_md", "rules", "skills", "skill_count")
    return {k: before.get(k, 0) - after.get(k, 0) for k in keys}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Snapshot session-start token cost (D6).",
    )
    parser.add_argument("--mode", default=None,
                        help="GRIP mode to resolve skills for (e.g. code, analysis)")
    parser.add_argument("--record", action="store_true",
                        help="Persist the snapshot to learning/metrics.jsonl")
    args = parser.parse_args()

    fn = record_session_start_tokens if args.record else count_session_start_tokens
    print(json.dumps(fn(mode=args.mode), indent=2))
