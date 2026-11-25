#!/usr/bin/env python3
"""Session-scoped violation memory for quality gate feedback escalation.

Tracks which principles have been violated during a session and provides
escalating correction hints. Solves the "model doesn't learn from feedback
within a session" problem (H-RS02: 25% recurrence for structural violations).

Design principle (H016 finding, Gen 471): escalation increases SPECIFICITY
while decreasing VERBOSITY. Verbose feedback ("PERSISTENT VIOLATION... You
have failed...") caused 44.4% recurrence vs 22.2% for simple feedback on 8B.
Each tier is shorter and more imperative than the last.

The memory is keyed by the canonical session id from ``mesh_identity`` so
each session has its own violation history.

The previous key read ``CC_SESSION_ID`` / ``PPID`` from ``os.environ``. Neither
is ever present: nothing in the repo sets ``CC_SESSION_ID`` (the real names are
``CLAUDE_CODE_SESSION_ID`` / ``CLAUDE_SESSION_ID``), and ``PPID`` is a shell
variable bash does not export. Every session therefore fell through to the
literal ``"unknown"`` and shared one file. ``mesh_identity`` already exists to
solve exactly this; this module was simply never migrated to it.

Cleaned up on session end by ``hooks/session-end.sh``, which calls
``cleanup_session()`` in its pair-mode cleanup block. Before that call existed
this function had no production caller anywhere in lib/, bin/, hooks/ or
scripts/, which is why the shared file reached 1,416 entries over 89 days
before anyone looked. The call is anchored by
``tests/test_session_end_module_paths.py`` so it cannot be removed silently:
the cleanup fails non-fatally by design, so its absence has no runtime symptom
beyond cache/ slowly filling.

Usage (from hooks):
    from violation_memory import record_violation, get_escalated_feedback

    # On gate fire:
    record_violation("BIG-O", "/path/to/file.py", "Nested loop detected")

    # When building deny message:
    feedback = get_escalated_feedback("BIG-O")
    # Returns escalating hints based on repeat count
"""
import json
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from mesh_identity import resolve_session_id  # noqa: E402

_CACHE_DIR = _LIB.parent / "cache"
_SESSION_ID = resolve_session_id()

# Fix patterns per principle: each tier is MORE SPECIFIC but SHORTER.
# H016 evidence: verbose escalation (44.4% recurrence) worse than concise (22.2%).
_FIX_PATTERNS = {
    "BIG-O": [
        "Replace nested loops with a hash-based lookup (dict/set).",
        "Use: seen = set(b); [x for x in a if x in seen]",
        "set() or sorted() + bisect. No nested loops.",
    ],
    "KISS": [
        "Split into coordinator + focused helpers (<30 lines each).",
        "Extract: _validate(), _process(), _format(). Coordinator calls them.",
        "Dispatch dict for branches. Max 30 lines per function.",
    ],
    "DRY": [
        "Extract duplicated block into a shared helper function.",
        "def _shared(param): [repeated lines]. Call from each site.",
        "Parameterise differences: _shared(x, variant=True).",
    ],
    "YAGNI": [
        "Remove the unused import line.",
        "Delete import. Only keep what the body uses.",
        "Delete the import line now.",
    ],
    "SOLID-ISP": [
        "Split class into focused interfaces (<7 public methods each).",
        "Group methods by responsibility. Each group = own class.",
        "class Reader(Protocol): read()... class Writer(Protocol): write()...",
    ],
    "SOLID-DIP": [
        "Accept dependency as parameter, not direct instantiation.",
        "def process(repo: Repository) not def process(): repo = Concrete()",
        "Inject via parameter or factory.",
    ],
    "GRASP": [
        "Move method to the class whose data it accesses most.",
        "obj.a, obj.b, obj.c used? Method belongs on obj's class.",
        "Move logic to the data owner.",
    ],
}


def _get_session_file() -> Path:
    """Return path to session-scoped violation memory file."""
    return _CACHE_DIR / f"violation-memory-{_SESSION_ID}.jsonl"


def record_violation(principle: str, file_path: str, message: str) -> int:
    """Record a violation and return the cumulative count for this principle."""
    session_file = _get_session_file()
    session_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {"principle": principle, "file": file_path,
             "message": message, "ts": time.time()}
    with open(session_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return get_repeat_count(principle)


def get_repeat_count(principle: str) -> int:
    """Count how many times this principle has been violated this session."""
    session_file = _get_session_file()
    if not session_file.exists():
        return 0
    count = 0
    for line in session_file.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("principle") == principle:
                count += 1
        except json.JSONDecodeError:
            continue
    return count


def get_escalated_feedback(principle: str) -> str:
    """Return escalating feedback based on repeat count.

    1st: guidance (what to do)
    2nd: pattern (how to do it) — concise, no shaming
    3rd+: imperative (do this exact thing) — shortest form

    H016: verbose preambles ("You have failed...") increase recurrence.
    Each tier is shorter and more direct than the last.
    """
    count = get_repeat_count(principle)
    patterns = _FIX_PATTERNS.get(principle, ["Fix the violation."])

    if count <= 1:
        return patterns[0]
    if count == 2:
        idx = min(1, len(patterns) - 1)
        return f"Repeat ({count}x). {patterns[idx]}"

    idx = min(2, len(patterns) - 1)
    return f"({count}x) {patterns[idx]}"


def cleanup_session():
    """Remove session violation file. Call on session end."""
    session_file = _get_session_file()
    if session_file.exists():
        session_file.unlink()
