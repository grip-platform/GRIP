#!/usr/bin/env python3
"""
KONO Auto-Capture: diversify memory dimensions from session insights.

Reads learning/insights.jsonl, classifies each insight into the appropriate
KONO dimension (factual/procedural/episodic/emergent), and stores via
MemorySubstrate.store_session_memory(). Uses a fingerprint cache to avoid
duplicate stores.

Usage:
    python3 lib/kono_auto_capture.py [--dry-run]
"""

import json
import sys
from pathlib import Path

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
INSIGHTS_FILE = CLAUDE_DIR / "learning" / "insights.jsonl"
CAPTURE_CACHE = CLAUDE_DIR / "state" / "integrations" / "kono-auto-capture-cache.json"
PROJECT_PATH = str(CLAUDE_DIR)


# ---------------------------------------------------------------------------
# Dimension classifier (keyword heuristic — no ML)
# ---------------------------------------------------------------------------

# Context tags and text keywords that signal each dimension.
# First match wins; emergent is the default for high-score insights.
_PROCEDURAL_SIGNALS = {
    "how-to", "workflow", "procedure", "plan", "step", "process",
    "batch", "commit", "push", "pr", "sequence", "pattern",
    "usage_pattern", "insights_report_usage_pattern",
}
_FACTUAL_SIGNALS = {
    "definition", "fact", "reference", "rule", "always", "never",
    "must", "should", "insights_report_friction", "insights_report_code_safety",
    "code_safety",
}
_EMERGENT_SIGNALS = {
    "cross-session", "pattern", "emergent", "learning", "insight",
    "drift", "anti-drift", "paramount", "analysis",
}
_EPISODIC_SIGNALS = {
    "gate_interaction", "error_recovery", "checkpoint", "session", "event",
    "blocked", "error",
}


def _tokens(insight: dict) -> set:
    """Extract searchable token set from an insight record."""
    tokens = set()
    text = (insight.get("text") or "").lower()
    context = (insight.get("context") or "").lower()
    tags = [t.lower() for t in (insight.get("tags") or [])]
    tokens.update(text.split())
    tokens.update(context.split("_"))
    tokens.update(context.split())
    tokens.update(tags)
    return tokens


def classify(insight: dict) -> str:
    """
    Classify an insight into a KONO memory_type.

    Returns one of: 'factual', 'procedural', 'episodic', 'emergent'
    """
    tokens = _tokens(insight)
    context = (insight.get("context") or "").lower()

    # Episodic: gate interactions and session events
    if context in {"gate_interaction", "error_recovery"} or tokens & _EPISODIC_SIGNALS:
        # Gate-blocked insights are episodic unless text is generalisable
        if "gate blocked" in (insight.get("text") or "").lower():
            return "episodic"

    # Score >= 0.9 with drift/pattern context → emergent
    score = insight.get("learning_score", 0.8)
    if score >= 0.9 and (tokens & _EMERGENT_SIGNALS or context in {"drift_analysis"}):
        return "emergent"

    # Procedural: how-to, workflows, usage patterns
    if context in {"insights_report_usage_pattern"} or tokens & _PROCEDURAL_SIGNALS:
        return "procedural"

    # Factual: rules, friction points, code safety
    if context in {
        "insights_report_friction",
        "insights_report_code_safety",
    } or tokens & _FACTUAL_SIGNALS:
        return "factual"

    # Episodic: session events / gate interactions
    if context in {"gate_interaction", "error_recovery"} or tokens & _EPISODIC_SIGNALS:
        return "episodic"

    # Default high-score → emergent, others → factual
    return "emergent" if score >= 0.85 else "factual"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cache() -> set:
    """Return set of already-captured fingerprints/ids."""
    if CAPTURE_CACHE.exists():
        try:
            data = json.loads(CAPTURE_CACHE.read_text())
            return set(data.get("captured", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_cache(captured: set) -> None:
    tmp = CAPTURE_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"captured": sorted(captured)}, indent=2))
    # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8778)
    tmp.replace(CAPTURE_CACHE)


# ---------------------------------------------------------------------------
# Main capture logic
# ---------------------------------------------------------------------------

def _load_insights() -> list:
    """Parse INSIGHTS_FILE's JSONL into insight dicts, skipping bad lines."""
    insights = []
    for line in INSIGHTS_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                insights.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return insights


def _connect_kono(dry_run: bool):
    """Try to import and construct the KONO substrate.

    Returns (substrate_or_None, dry_run) -- dry_run flips to True when the
    substrate cannot be reached, so the caller always knows which mode it
    is actually operating in.
    """
    if dry_run:
        return None, dry_run
    venv_lib = CLAUDE_DIR / "venv"
    if venv_lib.exists():
        import sys as _sys
        lib_dir = str(CLAUDE_DIR / "lib")
        if lib_dir not in _sys.path:
            _sys.path.insert(0, lib_dir)
    try:
        from kono_substrate import MemorySubstrate
        return MemorySubstrate(), dry_run
    except Exception as exc:
        print(f"KONO unavailable ({exc}) — dry-run only")
        return None, True


def _store_new_insights(new_insights: list, substrate, dry_run: bool, captured: set) -> tuple:
    """Classify and store each new insight; returns (stored, skipped) counts."""
    stored, skipped = 0, 0
    for insight in new_insights:
        key = insight.get("fingerprint") or insight.get("id")
        mem_type = classify(insight)
        text = insight.get("text", "").strip()
        if not text:
            skipped += 1
            continue

        print(f"[{mem_type}] {text[:80]}{'...' if len(text) > 80 else ''}")

        if not dry_run and substrate:
            try:
                memory_id = substrate.store_session_memory(
                    content=text,
                    memory_type=mem_type,
                    project_path=PROJECT_PATH,
                )
                captured.add(key)
                if memory_id is not None:
                    stored += 1
                else:
                    skipped += 1
            except Exception as exc:
                print(f"  store failed: {exc}")
                skipped += 1
        else:
            stored += 1
    return stored, skipped


def run(dry_run: bool = False) -> None:
    if not INSIGHTS_FILE.exists():
        print(f"No insights file at {INSIGHTS_FILE}")
        return

    captured = load_cache()
    insights = _load_insights()
    new_insights = [
        i for i in insights
        if (i.get("fingerprint") or i.get("id")) not in captured
    ]

    if not new_insights:
        print(f"No new insights to capture (cache: {len(captured)} entries)")
        return

    substrate, dry_run = _connect_kono(dry_run)
    stored, skipped = _store_new_insights(new_insights, substrate, dry_run, captured)

    if not dry_run and substrate:
        substrate.close()
        save_cache(captured)

    label = "dry-run candidates before deduplication (not stored)" if dry_run else "stored"
    print(f"\n{label}: {stored} | skipped: {skipped} | total cache: {len(captured)}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
