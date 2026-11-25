"""Auto-mode detection — suggest mode switches based on tool usage patterns.

Analyzes recent tool call history and suggests a more appropriate operating mode
when the current activity pattern mismatches the active mode.

Usage:
    from mode_detector import detect_mode_mismatch
    suggestion = detect_mode_mismatch(tool_history, current_mode)
    if suggestion:
        print(f"Consider switching to {suggestion['suggested_mode']}")
"""

# Tool-to-mode affinity mapping
# Each mode has a set of tools that are its "signature" tools
MODE_TOOL_SIGNATURES = {
    "code": {"Edit", "Write", "NotebookEdit"},
    "research": {"WebSearch", "WebFetch"},
    "git-ops": {"Bash"},  # specifically gh/git commands, refined below
    "review": {"Read", "Grep"},
    "planning": {"EnterPlanMode", "ExitPlanMode"},
    "testing": {"Bash"},  # specifically pytest, refined below
    "architect": {"Glob", "Grep", "Read"},
}

# Command patterns within Bash that indicate specific modes
BASH_MODE_PATTERNS = {
    "git-ops": ["gh pr", "gh issue", "git push", "git pull", "git merge", "git rebase"],
    "testing": ["pytest", "python3 -m pytest", "npm test", "jest"],
    "code": ["python3 -c", "node -e"],
}

# Minimum consecutive matching tools before suggesting a switch
MISMATCH_THRESHOLD = 5


def _classify_tool(tool_name: str, command: str = "") -> set:
    """Return set of modes this tool call is associated with."""
    modes = set()

    if tool_name in ("Edit", "Write", "NotebookEdit"):
        modes.add("code")
    elif tool_name in ("WebSearch", "WebFetch"):
        modes.add("research")
    elif tool_name in ("EnterPlanMode", "ExitPlanMode"):
        modes.add("planning")
    elif tool_name == "Read":
        modes.add("review")
        modes.add("architect")
    elif tool_name in ("Glob", "Grep"):
        modes.add("architect")
        modes.add("review")
    elif tool_name == "Bash" and command:
        cmd_lower = command.lower()
        for mode, patterns in BASH_MODE_PATTERNS.items():
            if any(p in cmd_lower for p in patterns):
                modes.add(mode)

    return modes if modes else {"general"}


def detect_mode_mismatch(tool_history: list, current_mode: str) -> dict | None:
    """Detect when recent tool usage suggests a different mode.

    Args:
        tool_history: List of dicts with 'tool' key and optional 'command' key.
                     Most recent first.
        current_mode: Currently active mode name.

    Returns:
        Dict with 'suggested_mode' and 'reason' if mismatch detected, else None.
    """
    if not tool_history or len(tool_history) < MISMATCH_THRESHOLD:
        return None

    # Count mode associations for recent tools
    mode_counts: dict[str, int] = {}
    recent = tool_history[:MISMATCH_THRESHOLD * 2]  # Look at 2x window

    for entry in recent:
        tool = entry.get("tool", "")
        command = entry.get("command", "")
        modes = _classify_tool(tool, command)
        for mode in modes:
            mode_counts[mode] = mode_counts.get(mode, 0) + 1

    if not mode_counts:
        return None

    # Find dominant mode
    dominant = max(mode_counts, key=mode_counts.get)
    dominant_count = mode_counts[dominant]
    total = len(recent)

    # Only suggest if dominant mode is clearly dominant (>60%) and different from current
    if dominant == current_mode or dominant == "general":
        return None

    if dominant_count / total < 0.6:
        return None

    return {
        "suggested_mode": dominant,
        "reason": f"{dominant_count}/{total} recent tool calls match '{dominant}' mode (current: '{current_mode}')",
        "confidence": dominant_count / total,
    }
