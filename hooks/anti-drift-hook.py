#!/usr/bin/env python3
"""
Anti-Drift PostToolUse Hook - Context-aware drift detector for GRIP.

Detects context switches and reminds the agent to output "What's Up Next"
before pivoting to new work. Two detection triggers:

1. Git commit detection: When Bash output contains a commit hash pattern,
   immediately emit "What's Up Next" reminder via systemMessage.

2. Periodic check (every 15 operations): Checks 4 state sources for
   unfinished work:
   - Topic keyword familiarity (overlap coefficient < 0.3 = drift)
   - Instance JSON next_up (remaining items)
   - KONO memory (recent unfinished mentions)
   - Git status (uncommitted changes)

State: ~/.claude/.drift-state.json
Registration: PostToolUse hook, matcher Bash|Edit|Write|Skill

Version: 1.0.0
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hook_utils import parse_hook_input, respond, extract_tool_info

CLAUDE_DIR = Path.home() / ".claude"
DRIFT_STATE = CLAUDE_DIR / "state" / "audit" / "drift-state.json"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Oracle Gradient: budget tier determines hook verbosity
_LIB_DIR = str(CLAUDE_DIR / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

# Commit hash pattern (shared with rsi-posttool-hook.py)
COMMIT_PATTERN = re.compile(r"\[[\w./-]+ ([0-9a-f]{7,})\]")

# Periodic check interval
PERIODIC_INTERVAL = 15

# Familiarity below this fraction counts as a topic switch (see topic_familiarity)
DRIFT_THRESHOLD = 0.3

# Max recent path entries to track (also the comparison window)
MAX_RECENT_PATHS = 10

# Operations of history required before any drift verdict is meaningful.
MIN_HISTORY_FOR_DRIFT = 5


def log_hook(level: str, message: str):
    """Append to GRIP hook log."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [ANTI-DRIFT][{level}] {message}\n")
    except Exception:
        pass


def load_state() -> dict:
    """Load drift detection state."""
    default = {
        "op_count": 0,
        "recent_paths": [],
        "last_commit_at": None,
        "commit_reminder_pending": False,
    }
    if DRIFT_STATE.exists():
        try:
            state = json.loads(DRIFT_STATE.read_text())
            for k, v in default.items():
                if k not in state:
                    state[k] = v
            return state
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_state(state: dict):
    """Persist drift state."""
    try:
        DRIFT_STATE.write_text(json.dumps(state, indent=2))
        DRIFT_STATE.chmod(0o600)
    except OSError:
        pass


# Split on every run of non-alphanumeric characters.
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _tokens(text: str) -> set:
    """Lowercase alphanumeric tokens longer than two characters.

    The previous version split only on `/`, `\\`, `.` and whitespace and then kept
    a token only if `str.isalnum()`. Hyphens and underscores survive that split but
    fail that test, so every hyphenated or underscored identifier was silently
    DISCARDED -- which is most filenames here. `cat -n hooks/anti-drift-hook.py`
    reduced to {cat, hooks} and `sed -n lib/gates/context_gate.py` to
    {gates, lib, sed}: the tokens that actually carry the topic were exactly the
    ones dropped, leaving one or two generic words to compare.

    Splitting on any non-alphanumeric run keeps them as parts, so the same command
    now yields {cat, hooks, anti, drift, hook} and two commands about the same
    subsystem visibly share vocabulary.
    """
    return {t.lower() for t in _TOKEN_SPLIT.split(text) if len(t) > 2}


def extract_path_keywords(tool_input: dict) -> set:
    """Extract topic keywords from tool input paths."""
    keywords = set()
    for key in ("file_path", "path", "command", "pattern"):
        val = tool_input.get(key, "")
        if isinstance(val, str):
            keywords |= _tokens(val)
    return keywords


def topic_familiarity(recent_keywords: set, current_keywords: set) -> float:
    """How much of the smaller keyword set is shared -- the overlap coefficient.

    |A INTERSECT B| / min(|A|, |B|), also called Szymkiewicz-Simpson.

    This deliberately is NOT Jaccard. Jaccard divides by the UNION, which makes it
    a similarity measure between two comparable sets. Here the two sets are not
    comparable by construction: `recent_keywords` is the UNION of five commands'
    keywords, while `current_keywords` comes from one command, so recent is
    routinely four to ten times larger. Dividing by the union then penalises that
    size gap rather than the topic, and the score collapses toward
    |current| / |recent| no matter how familiar the work is.

    That made the check unsatisfiable, not merely strict. With a 0.3 threshold, any
    window where |recent| > |current| / 0.3 scores below the threshold even when
    EVERY current keyword already appears in recent -- so "no drift" was unreachable
    and the reminder fired on ordinary within-task movement. Measured on the real
    hook: 3 fires in 60 operations of a single continuous task, at 0.22 / 0.29 /
    0.22, against a 0.30 threshold.

    Dividing by the smaller set asks the question actually meant -- "is what I am
    doing now familiar?" -- and is invariant to how many commands happen to be in
    the window. Perfect containment scores 1.0; genuinely unrelated work still
    scores 0.0, so real drift is still caught. The 0.3 threshold is unchanged: it
    was never the defect.
    """
    if not recent_keywords and not current_keywords:
        return 1.0
    if not recent_keywords or not current_keywords:
        return 0.0
    return len(recent_keywords & current_keywords) / min(len(recent_keywords), len(current_keywords))


def detect_topic_drift(prior_paths: list, current_keywords: set) -> tuple:
    """Check if current work is drifting from recent topics.

    `prior_paths` MUST exclude the operation being judged. The caller appends the
    current operation to the tracking window, and comparing the current command
    against a window that already contains it makes the intersection the entire
    current keyword set by construction -- so the score stops depending on the
    topic at all. Under Jaccard that collapsed the whole check to
    |current| / |window UNION current|, a pure ratio of set sizes: it fired on
    continuous work (small current set, large window) and stayed SILENT on a real
    context switch, which scored 0.78 against a 0.30 threshold because the switch's
    own keywords had just been added to the window it was measured against.

    Returns (drifted: bool, reason: str).
    """
    if len(prior_paths) < MIN_HISTORY_FOR_DRIFT:
        return False, ""

    # Compare against the WHOLE retained window, not an arbitrary sub-slice of it.
    # The window is already capped at MAX_RECENT_PATHS; storing ten operations and
    # then judging against only the last five was an unexplained asymmetry that
    # threw away evidence. Vocabulary rotates within a single task -- editing a
    # module, then running its tests, then diffing -- and the narrower the window,
    # the more of that ordinary rotation reads as a topic change.
    recent_keywords = set()
    for path_str in prior_paths:
        recent_keywords |= _tokens(path_str)

    sim = topic_familiarity(recent_keywords, current_keywords)
    if sim < DRIFT_THRESHOLD:
        return True, f"Topic familiarity {sim:.2f} < {DRIFT_THRESHOLD}"
    return False, ""


def get_instance_next_up() -> list:
    """Read remaining items from most recent instance JSON."""
    try:
        project_encoded = os.getcwd().replace("/", "-").replace(".", "-")
        grip_dir = CLAUDE_DIR / "projects" / project_encoded / "grip"
        index_file = grip_dir / "index.json"
        if not index_file.exists():
            return []

        with open(index_file) as f:
            index = json.load(f)

        instances = index.get("instances", [])
        if not instances:
            return []

        latest_id = instances[-1].get("instance_id", "")
        if not latest_id:
            return []

        inst_file = grip_dir / f"{latest_id}.json"
        if not inst_file.exists():
            return []

        with open(inst_file) as f:
            inst = json.load(f)

        next_up = inst.get("next_up", {})
        if not next_up or next_up == "null":
            return []

        remaining = next_up.get("remaining", [])
        return remaining if isinstance(remaining, list) else []
    except Exception:
        return []


def get_kono_unfinished() -> str:
    """Check KONO for recent unfinished work mentions."""
    try:
        kono = CLAUDE_DIR / "lib" / "kono_substrate.py"
        if not kono.exists():
            return ""
        # run_text, not subprocess.run(text=True): the first 200 chars of this
        # are surfaced to the operator as the unfinished-work reminder.
        # _LIB_DIR is already on sys.path from module import above.
        from _compat import run_text
        result = run_text(
            [sys.executable, str(kono), "recent", "1"],
            capture_output=True, timeout=3,
        )
        output = result.stdout.strip()
        unfinished_keywords = ("unfinished", "remaining", "todo", "next", "blocked")
        if output and any(kw in output.lower() for kw in unfinished_keywords):
            return output[:200]
        return ""
    except Exception:
        return ""


def get_uncommitted_summary() -> str:
    """Summarize uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        if lines:
            return f"{len(lines)} uncommitted file(s)"
        return ""
    except Exception:
        return ""


def build_reminder(trigger_reason: str, items: list) -> str:
    """Build the systemMessage drift reminder."""
    parts = [f"[ANTI-DRIFT] {trigger_reason}. Unfinished work detected:"]
    for item in items:
        if item:
            parts.append(f"  - {item}")
    parts.append('Output "What\'s Up Next" or acknowledge before proceeding.')
    return "\n".join(parts)


def gather_unfinished_items() -> list:
    """Collect unfinished work from all state sources."""
    items = []

    # Instance next_up
    for item in get_instance_next_up():
        items.append(item)

    # KONO memory
    kono = get_kono_unfinished()
    if kono:
        items.append(f"KONO: {kono}")

    # Uncommitted changes
    uncommitted = get_uncommitted_summary()
    if uncommitted:
        items.append(uncommitted)

    return items


def _detect_budget_mode() -> str:
    """Detect Oracle Gradient budget tier. Returns mode string."""
    try:
        from token_budget import TokenBudget
        return TokenBudget.detect().mode
    except (ImportError, Exception):
        return "abundant"


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, tool_output = extract_tool_info(hook_input)

    # Oracle Gradient: budget tier determines hook behaviour
    budget_mode = _detect_budget_mode()

    # Oracle mode: minimal 1-line reminder, skip file reads
    if budget_mode == "oracle":
        if tool_name == "Bash" and COMMIT_PATTERN.search(tool_output):
            respond("[DRIFT] Check MEMORY.md")
        else:
            respond()
        return

    state = load_state()
    state["op_count"] = state.get("op_count", 0) + 1

    # Snapshot the window BEFORE folding this operation into it. detect_topic_drift
    # compares against this snapshot; measuring the current command against a window
    # that already contains it removes the topic signal entirely (see that function).
    prior_paths = list(state.get("recent_paths", []))

    # Track path keywords for drift detection
    current_keywords = extract_path_keywords(tool_input)
    if current_keywords:
        entry = " ".join(sorted(current_keywords))
        recent = state.get("recent_paths", [])
        recent.append(entry)
        if len(recent) > MAX_RECENT_PATHS:
            recent = recent[-MAX_RECENT_PATHS:]
        state["recent_paths"] = recent

    trigger_reason = None
    reminder_items = []

    # ── TRIGGER 1: Git commit detected ───────────────────────────────
    if tool_name == "Bash" and COMMIT_PATTERN.search(tool_output):
        trigger_reason = "Git commit detected"
        state["last_commit_at"] = datetime.now(timezone.utc).isoformat()
        log_hook("INFO", "Commit detected, triggering reminder")
        # Scarce mode: skip file reads, cached items only
        if budget_mode == "scarce":
            reminder_items = get_instance_next_up()
        else:
            reminder_items = gather_unfinished_items()

    # ── TRIGGER 2: Periodic check every N operations ─────────────────
    elif state["op_count"] % PERIODIC_INTERVAL == 0:
        drifted, drift_reason = detect_topic_drift(prior_paths, current_keywords)

        if drifted:
            trigger_reason = f"Context switch detected ({drift_reason})"
            log_hook("INFO", f"Topic drift: {drift_reason}")
            if budget_mode == "scarce":
                reminder_items = get_instance_next_up()
            else:
                reminder_items = gather_unfinished_items()
        else:
            # Even without drift, check for unfinished work at periodic interval
            next_up = get_instance_next_up()
            if next_up:
                trigger_reason = f"Periodic check (op #{state['op_count']})"
                if budget_mode == "scarce":
                    reminder_items = next_up
                else:
                    reminder_items = gather_unfinished_items()

    save_state(state)

    if trigger_reason and reminder_items:
        message = build_reminder(trigger_reason, reminder_items)
        log_hook("REMIND", f"Emitting: {trigger_reason} ({len(reminder_items)} items)")
        respond(message)
    else:
        respond()


if __name__ == "__main__":
    main()
