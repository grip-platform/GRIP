#!/usr/bin/env python3
"""
RSI PostToolUse Hook - Detect improvement events after every tool call.

Detects:
- Git commits (Bash output with commit hash pattern)
- GRIP file edits (Edit/Write under the install directory: $CLAUDE_DIR,
  else ~/.claude, compared as real paths so a symlinked ~/.claude matches)
- Skill invocations (Skill tool usage)
- Gate blocks (hook output with DENY/BLOCK)
- Insight blocks (★ Insight pattern in tool output)
- Pattern detection signals

Calls lib/rsi_auto_collector.py in background for each detected event.
Bridges events to learning pipeline via learn-event mode. Gate-block output is
scrubbed whole by the redaction shield before it is cut; without the shield it
is not sent.
Uses debounce (5s same-type) to avoid noisy logging.

Registered in settings.json as PostToolUse hook with matcher ".*".

Version: 3.0.0 — budget tracking removed (Anthropic generous usage model)
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _directory in (_HERE, _HERE.parent / "lib"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))
from hook_utils import parse_hook_input, respond, extract_tool_info
from claude_dir import get_claude_dir
from insight_text import gate_block_summary

CLAUDE_DIR = get_claude_dir()
DEBOUNCE_FILE = CLAUDE_DIR / "state" / "rsi" / "rsi-last-collect.json"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Minimum seconds between same-type events
DEBOUNCE_SECONDS = 5

# Tool output longer than this is not captured as an insight. It is scrubbed
# whole before it is cut, and scrubbing took 1.3 s for a million characters and
# 6.2 s for five million (measured 2026-10-06) against this hook's 3 s timeout.
SCRUB_LIMIT = 100_000

# Commit hash pattern: [main abc1234] or [branch-name abc1234]
COMMIT_PATTERN = re.compile(r"\[[\w./-]+ ([0-9a-f]{7,})\]")

# Insight block pattern: ★ Insight followed by content before closing ─
INSIGHT_PATTERN = re.compile(
    r"★ Insight[─\s]+\n(.*?)\n[─]+",
    re.DOTALL,
)


def log_hook(level: str, message: str):
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [RSI-POSTTOOL][{level}] {message}\n")
    except Exception:
        pass


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def load_debounce() -> dict:
    """Load debounce state."""
    if DEBOUNCE_FILE.exists():
        try:
            return json.loads(DEBOUNCE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_debounce(state: dict):
    """Save debounce state."""
    try:
        DEBOUNCE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def is_debounced(event_type: str) -> bool:
    """Check if event type is within debounce window."""
    state = load_debounce()
    last = state.get(event_type, 0)
    now = now_ts()
    if now - last < DEBOUNCE_SECONDS:
        return True
    state[event_type] = now
    save_debounce(state)
    return False


def run_collector(args: list):
    """Run lib/rsi_auto_collector.py in background with timeout.

    args is the collector's argv after the program name: a mode and its
    arguments, as its parse_args() accepts them. Uses a wrapper to enforce a
    120-second timeout on the child process, preventing indefinite resource
    consumption.
    """
    collector = collector_path()
    if not collector.exists():
        return
    try:
        # Launch with timeout enforcement via shell timeout command (POSIX)
        # The child runs in its own session so it won't block the hook
        subprocess.Popen(
            ["timeout", "120", sys.executable, str(collector)] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log_hook("ERROR", f"Collector launch failed: {e}")


def detect_commit(tool_output: str) -> str:
    """Detect commit hash from Bash output. Returns hash or empty string."""
    if not tool_output:
        return ""
    match = COMMIT_PATTERN.search(tool_output)
    if match:
        return match.group(1)
    return ""


def install_dir() -> Path:
    """The GRIP install directory as a real path, resolved on every call.

    get_claude_dir() honours $CLAUDE_DIR and falls back to $HOME/.claude.
    Resolving it means a symlinked ~/.claude and its target are one place,
    which is how detect_grip_edit already treats the edited path. Resolved
    per call, not at import, so a $CLAUDE_DIR set after import is honoured;
    in the hook's own process the two moments are the same.
    """
    return get_claude_dir().resolve()


def collector_path() -> Path:
    """lib/rsi_auto_collector.py under the install directory, resolved per call.

    The file name has underscores. The hyphenated name this hook used to build
    matched no file, so run_collector returned before launching anything and
    no detected event ever reached the collector (GRIP#7616).
    """
    return install_dir() / "lib" / "rsi_auto_collector.py"


def detect_grip_edit(tool_name: str, tool_input: dict) -> str:
    """Detect if Edit/Write targets a GRIP infrastructure file. Returns path or empty.

    Both sides are compared as real paths: a file reached through a
    symlinked ~/.claude and the same file reached through its target match.
    """
    if tool_name not in ("Edit", "Write"):
        return ""
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return ""
    # Normalize path
    try:
        resolved = Path(file_path).resolve()
    except Exception:
        resolved = Path(file_path)
    if install_dir() in resolved.parents:
        return str(resolved)
    return ""


def gate_block_text(tool_name: str, tool_output: str) -> str:
    """The learn-event text for a gate block, or "" when none may be sent.

    The output is scrubbed whole by lib/review_redaction.py first. Cut first, a key
    the cut splits leaves a fragment the shield no longer recognises: measured
    2026-10-06, up to 15 characters after "sk-" pass unredacted. The shielded text
    then goes through lib/insight_text.gate_block_summary, which drops dict dumps
    and paths, cuts at a word, and returns "" when nothing but a payload is left:
    a tool-input dump was reaching rules/learned-insights.md as an "insight"
    (GRIP#8068). "" when the shield cannot run or the output is over SCRUB_LIMIT,
    so nothing unshielded leaves this hook.
    """
    if len(tool_output) > SCRUB_LIMIT:
        return ""
    try:
        from review_redaction import shield_text
        return gate_block_summary(tool_name, shield_text(tool_output))
    except Exception:  # noqa: BLE001 -- without a shield, nothing is sent
        log_hook("WARN", "redaction shield unavailable: gate-block capture dropped")
        return ""


def handle_tool_events(tool_name: str, tool_input: dict, tool_output: str):
    """Steps 1-3: events named by the tool call itself."""
    # 1. Detect git commits (Bash tool)
    if tool_name == "Bash":
        commit_hash = detect_commit(tool_output)
        if commit_hash and not is_debounced("commit"):
            log_hook("INFO", f"Detected commit: {commit_hash}")
            run_collector(["on-commit", commit_hash])

            # Meta-inference: DISABLED (Gen 499)
            # Was spawning Llama 3.2 11B (5.6GB model) per commit with
            # start_new_session=True — processes detach, survive kills,
            # and pile up to 500%+ CPU / 7GB+ RAM. No debounce prevented
            # concurrent spawns. Re-enable only with proper process cap.
            # if not is_debounced("meta_inference"):
            #     ...

    # 2. Detect GRIP file edits (Edit/Write tools)
    if tool_name in ("Edit", "Write"):
        grip_path = detect_grip_edit(tool_name, tool_input)
        if grip_path and not is_debounced(f"edit:{grip_path}"):
            log_hook("INFO", f"Detected GRIP edit: {grip_path}")
            run_collector(["on-edit", grip_path])

    # 3. Detect skill invocations (Skill tool)
    if tool_name == "Skill":
        skill_name = tool_input.get("skill", "unknown")
        if not is_debounced(f"skill:{skill_name}"):
            log_hook("INFO", f"Detected skill invocation: {skill_name}")
            run_collector(["on-event", "skills_triggered", f"Skill invoked: {skill_name}"])


def handle_output_signals(tool_name: str, tool_output: str):
    """Steps 4-6: signals found in the tool's output, whatever the tool."""
    # 4. Detect gate blocks → bridge to learning pipeline
    if "DENY" in tool_output or "BLOCK" in tool_output:
        if not is_debounced("gate_block"):
            log_hook("INFO", "Detected gate block in tool output")
            run_collector(["on-event", "gate_effectiveness", f"Gate blocked {tool_name}"])
            # Bridge: gate blocks are learning opportunities, sent scrubbed or not at all
            insight = gate_block_text(tool_name, tool_output)
            if insight:
                run_collector(["learn-event", insight, "gate_interaction"])

    # 5. Detect pattern detection signals
    if "[PATTERN DETECTED" in tool_output or "[PERIODIC: PATTERN_SCAN_DUE]" in tool_output:
        if not is_debounced("pattern_detected"):
            log_hook("INFO", "Detected pattern signal in tool output")
            run_collector(["on-event", "patterns_detected", f"Pattern signal from {tool_name}"])

    # 6. Detect Insight blocks in tool output → persist as learning insights
    insight_matches = INSIGHT_PATTERN.findall(tool_output)
    for insight_text in insight_matches:
        clean = insight_text.strip()
        if clean and len(clean) > 20 and not is_debounced(f"insight:{clean[:30]}"):
            log_hook("INFO", f"Detected Insight block: {clean[:60]}")
            run_collector(["learn-event", clean, "agent_insight"])


def main():
    hook_input = parse_hook_input()
    if not hook_input:
        respond()
        return

    tool_name, tool_input, tool_output = extract_tool_info(hook_input)
    handle_tool_events(tool_name, tool_input, tool_output)
    handle_output_signals(tool_name, tool_output)
    respond()


if __name__ == "__main__":
    main()
