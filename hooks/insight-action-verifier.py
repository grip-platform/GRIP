#!/usr/bin/env python3
"""PostToolUse hook: Verify that detected insights lead to actions.

Watches for RSI pipeline outputs (heatmap, pattern scan) and reminds
the agent to act if too many tool calls pass without follow-up.

Prevents the "note-but-don't-act" anti-pattern where the agent identifies
issues but moves on without fixing them.

Part of the GRIP automatic improvement MVP (Gen 276).
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from hook_utils import parse_hook_input, respond, extract_tool_info
from state_paths import AUDIT_INSIGHT_VERIFIER, state_file

# Under state/audit/ since April audit task D5; it was a dotfile at the install root.
STATE_FILE = state_file(AUDIT_INSIGHT_VERIFIER)
KILL_SWITCH = Path.home() / ".claude" / ".no-auto-improve"

# Tool calls after insight detection before reminder fires
REMINDER_THRESHOLD = 5

# Minimum seconds between reminders
REMINDER_COOLDOWN = 300  # 5 minutes

# Markers specific enough to mean "an analysis emitted this" wherever they
# appear. A document is vanishingly unlikely to contain these by accident.
STRUCTURED_MARKERS = [
    "[IMPROVEMENT HEATMAP]",
    "heatmap signal",
]

# Ordinary English that only indicates a finding when an analysis produced it.
# On its own each of these is just a phrase: rules, council reviews and commit
# messages discuss dead code and stale references constantly, and matching them
# anywhere made the reminder fire on reading ABOUT the concept rather than on
# any finding existing. Observed firing 15 times in one session with no
# analysis having run at all.
PROSE_PATTERNS = [
    "design smell",
    "improvement opportunities",
    "count drift",
    "dead code",
    "stale reference",
]

# Retained for callers that want the whole vocabulary.
FINDING_PATTERNS = STRUCTURED_MARKERS + PROSE_PATTERNS

# Patterns in Bash commands that indicate RSI analysis ran
ANALYSIS_COMMANDS = [
    "improvement-analyzer.py",
    "learning_detector.py detect",
    "learning_detector.py scan",
    "learning-detector.py",
    "auto_skill_generator.py detect",
]

# Tool names that count as "taking improvement action"
ACTION_TOOLS = {"Edit", "Write"}

# Bash commands that count as "taking improvement action"
ACTION_BASH_PATTERNS = [
    "grip-stats.sh --fix",
    "improvement_queue.py",
    "auto_fix_classifier.py",
    "learning_detector.py generate-rules",
]


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "findings_pending": False,
        "calls_since_finding": 0,
        "finding_description": "",
        "last_reminder_at": 0,
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def is_action_tool(tool_name: str, tool_input: dict) -> bool:
    """Check if this tool call counts as taking action on findings."""
    if tool_name in ACTION_TOOLS:
        return True
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        return any(pat in command for pat in ACTION_BASH_PATTERNS)
    return False


def has_findings(tool_output: str, from_analysis: bool = False) -> str:
    """Return the matched finding description, or "" if there is none.

    Structured markers count wherever they appear. Prose patterns count only
    when an analysis command produced the output, because otherwise the match
    means the text discussed the concept rather than that a finding exists.
    """
    output_lower = tool_output.lower()
    vocabulary = STRUCTURED_MARKERS + PROSE_PATTERNS if from_analysis else STRUCTURED_MARKERS
    for pattern in vocabulary:
        if pattern.lower() in output_lower:
            return pattern
    return ""


def is_analysis_command(tool_input: dict) -> bool:
    """Check if the Bash command is an RSI analysis command."""
    command = tool_input.get("command", "")
    return any(pat in command for pat in ANALYSIS_COMMANDS)


def main():
    if KILL_SWITCH.exists():
        respond()
        return

    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, tool_output = extract_tool_info(hook_input)

    state = load_state()

    # If this is an action tool and findings are pending, clear the flag
    if is_action_tool(tool_name, tool_input) and state["findings_pending"]:
        state["findings_pending"] = False
        state["calls_since_finding"] = 0
        state["finding_description"] = ""
        save_state(state)
        respond()
        return

    # One check, carrying whether an analysis produced this output. Previously
    # this was two branches: an unconditional one, then an analysis-gated one
    # that could never fire, since it re-tested output the first branch had
    # already rejected.
    from_analysis = tool_name == "Bash" and is_analysis_command(tool_input)
    finding_desc = has_findings(tool_output, from_analysis=from_analysis)
    if finding_desc:
        state["findings_pending"] = True
        state["calls_since_finding"] = 0
        state["finding_description"] = finding_desc
        save_state(state)
        respond()
        return
    if from_analysis:
        save_state(state)
        respond()
        return

    # Increment counter if findings are pending
    if state["findings_pending"]:
        state["calls_since_finding"] += 1

        if state["calls_since_finding"] >= REMINDER_THRESHOLD:
            now = time.time()
            if now - state.get("last_reminder_at", 0) > REMINDER_COOLDOWN:
                state["last_reminder_at"] = now
                state["calls_since_finding"] = 0
                save_state(state)

                reminder = (
                    f"[AUTO-IMPROVE REMINDER] Improvement findings "
                    f"({state['finding_description']}) detected but no action "
                    f"taken in {REMINDER_THRESHOLD} tool calls. Consider: "
                    f"(1) Fix the issues now, "
                    f"(2) Queue them via improvement_queue.py, or "
                    f"(3) Explicitly skip with reasoning."
                )
                respond(reminder)
                return

    save_state(state)
    respond()


if __name__ == "__main__":
    main()
