#!/usr/bin/env python3
"""pre-check — PARAMOUNT prompt pre-dispatch gate.

Scans outgoing subagent / task / continuation / web-fetch prompts for
linguistic patterns that commonly trigger false-positive content refusals
from upstream model providers. Defensive security reviews, code audits,
and meta-reasoning tasks often get blocked when phrased with first-person
investigative framing ("if I could X", "how would I Y") even when the
intent is defensive.

The gate fires on PreToolUse for Agent, Task, SendMessage, TaskCreate,
TaskUpdate, and WebFetch. On a pattern hit it returns
permissionDecision=deny with specific rewrite hints, letting the main
agent rephrase and retry.

This is a PARAMOUNT gate: mechanical enforcement, not advisory. Every
hit is logged to state/pre-check.jsonl for audit and false-positive
tuning.

CLI modes (for V>> tuning workflow):
- (stdin JSON)              — default hook dispatch mode
- --simulate "prompt text"  — scan a prompt without dispatching
- --stats                   — summarise audit log by category + tool

Gen 509 origin: a broly subagent review of a domain-wide delegation
refactor was refused on the drafting step because the prompt asked
"if I could compromise ONE thing". The defensive intent was legitimate
but the phrasing tripped the filter on the provider side. This gate
prevents recurrence.
"""

import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
LOG_PATH = CLAUDE_DIR / "state" / "pre-check.jsonl"
BYPASS_LOG_PATH = CLAUDE_DIR / "state" / "pre-check-bypass.jsonl"
RULES_PATH = CLAUDE_DIR / "hooks" / ".pre-check-patterns.json"
BYPASS_ENV_VAR = "PRECHECK_BYPASS"

# Tool names that carry free-form prompt text worth scanning.
GATED_TOOLS = (
    "Agent",
    "Task",
    "SendMessage",
    "TaskCreate",
    "TaskUpdate",
    "WebFetch",
)

# Field names that commonly hold the prompt / description text.
PROMPT_FIELDS = ("prompt", "description", "message", "subject", "activeForm")

# Pattern rules are loaded from a sibling JSON file at runtime so the
# source code doesn't carry the rule text in plaintext within the repo.
# See hooks/.pre-check-patterns.json (git-crypt encrypted at rest).
#
# Each rule entry: {"p": <regex>, "c": <category>, "h": <hint>}.
_RULES_CACHE = None


def _load_rules():
    """Lazy-load pattern rules from the sibling JSON file.

    Returns a list of (compiled_regex, category, hint) tuples. Missing or
    malformed files produce an empty rule set (the gate degrades to
    permit-all rather than block-all, on the principle that a broken gate
    must not obstruct work).
    """
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    if not RULES_PATH.exists():
        _RULES_CACHE = []
        return _RULES_CACHE
    try:
        # UTF-8 explicitly: the platform default (cp1252 on Windows) garbles the
        # file's non-ASCII text. ValueError covers JSONDecodeError AND the
        # UnicodeDecodeError raised by a git-crypt-locked clone's ciphertext.
        raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        _RULES_CACHE = []
        return _RULES_CACHE
    rules = []
    for entry in raw if isinstance(raw, list) else []:
        pattern = entry.get("p", "") if isinstance(entry, dict) else ""
        category = entry.get("c", "uncategorised") if isinstance(entry, dict) else "uncategorised"
        hint = entry.get("h", "") if isinstance(entry, dict) else ""
        if not pattern:
            continue
        try:
            rules.append((re.compile(pattern, re.IGNORECASE), category, hint))
        except re.error:
            continue
    _RULES_CACHE = rules
    return _RULES_CACHE


def _extract_prompt(tool_input):
    """Collect scannable text from a tool input dict across known fields."""
    if not isinstance(tool_input, dict):
        return ""
    chunks = []
    for key in PROMPT_FIELDS:
        val = tool_input.get(key)
        if isinstance(val, str):
            chunks.append(val)
    return "\n".join(chunks)


def _scan(text):
    """Return list of (category, match, hint) tuples for pattern hits.

    Rules are loaded lazily from the sibling JSON file on first call.
    """
    hits = []
    for regex, category, hint in _load_rules():
        m = regex.search(text)
        if m:
            hits.append((category, m.group(0), hint))
    return hits


def _prompt_digest(text):
    """Return a short hash of the scanned text for audit without content leak."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _log(tool_name, hits, prompt_text):
    """Append an audit entry to state/pre-check.jsonl.

    Stores only category labels + prompt hash (not the prompt text) so the
    log can be safely rotated/committed without leaking the content of
    gated dispatches. Tuning workflow uses --stats (aggregates) and
    --simulate (local test), neither of which requires retained prompts.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tool": tool_name,
        "categories": [c for c, _m, _h in hits],
        "digest": _prompt_digest(prompt_text),
        "length": len(prompt_text),
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _log_bypass(tool_name, prompt_text):
    """Append a bypass audit entry when PRECHECK_BYPASS env var is honoured."""
    BYPASS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tool": tool_name,
        "digest": _prompt_digest(prompt_text),
        "length": len(prompt_text),
    }
    with BYPASS_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _allow():
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _format_reason(hits):
    """Format a deny reason WITHOUT leaking the matched substring.

    Only category labels and rewrite hints are surfaced to callers. The
    literal match is visible only via local --simulate mode (V>>'s tuning
    path), never in the deny JSON returned to Claude Code.
    """
    lines = [
        "[pre-check] Prompt would likely be refused upstream. Rewrite before dispatch.",
        "",
    ]
    for category, _match, hint in hits:
        lines.append(f"- [{category}]  {hint}")
    lines.append("")
    lines.append(
        "Tuning: `python3 ~/.claude/hooks/pre-check-hook.py --simulate \"<prompt>\"` "
        "for a local dry-run with full match detail. "
        f"Override: set {BYPASS_ENV_VAR}=1 in the shell to force dispatch (logged separately)."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI helper modes — --simulate and --stats
# ---------------------------------------------------------------------------


def _simulate(prompt_text):
    """Scan a prompt locally and print human-readable results. No log write."""
    if not prompt_text:
        print("pre-check simulate: empty prompt, nothing to scan")
        return 0
    hits = _scan(prompt_text)
    if not hits:
        print("pre-check simulate: PASS — no patterns matched")
        return 0
    print(f"pre-check simulate: BLOCKED — {len(hits)} pattern(s) matched")
    print()
    for category, match, hint in hits:
        print(f"  [{category}]  matched: '{match}'")
        print(f"    rewrite: {hint}")
    print()
    print(
        "Principle: defensive security questions should use third-person observer framing "
        "('what assumptions fail', 'what is the blast radius', 'where is the trust boundary')."
    )
    return 1


def _load_log_entries():
    """Yield each JSON entry in the audit log, skipping malformed lines."""
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _flatten_keys(entries, key_fn):
    """Collect keys from all entries into a flat list (single-pass scan)."""
    all_keys = []
    for entry in entries:
        all_keys.extend(key_fn(entry))
    return all_keys


def _count_by(entries, key_fn):
    """Return a dict of {key: count} from an iterable of entries.

    Flattens via helper first so the counting loop is O(n) single-depth.
    """
    counts = {}
    for key in _flatten_keys(entries, key_fn):
        counts[key] = counts.get(key, 0) + 1
    return counts


def _format_stats_table(title, counts, total):
    """Format a count dict as a plain-text table sorted by frequency."""
    lines = [f"{title}:"]
    if not counts:
        lines.append("  (no hits)")
        return lines
    width = max(len(k) for k in counts)
    for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = (count / total * 100) if total else 0
        lines.append(f"  {key.ljust(width)}  {count:4d}  ({pct:5.1f}%)")
    return lines


def _entry_categories(entry):
    """Extract category list from an audit entry, handling legacy + new formats.

    New format: {"categories": ["first_person_compromise", ...]}
    Legacy format: {"hits": [{"category": "first_person_compromise", ...}]}
    """
    cats = entry.get("categories")
    if cats:
        return list(cats)
    return [h.get("category", "?") for h in entry.get("hits") or []]


def _stats():
    """Read the audit log and print frequency stats for tuning.

    Never prints the matched substring — even legacy entries are reduced
    to category labels only. Use --simulate for full match detail.
    """
    entries = _load_log_entries()
    total_hits = sum(len(_entry_categories(e)) for e in entries)
    print(f"pre-check audit stats  ({len(entries)} events, {total_hits} pattern hit(s))")
    print(f"log: {LOG_PATH}")
    print()
    if not entries:
        print("(empty log — no dispatches have been gated yet)")
        return 0
    categories = _count_by(entries, _entry_categories)
    tools = _count_by(entries, lambda e: [e.get("tool", "?")])
    for line in _format_stats_table("by category", categories, total_hits):
        print(line)
    print()
    for line in _format_stats_table("by tool", tools, len(entries)):
        print(line)
    print()
    print("recent events (last 5):")
    for entry in entries[-5:]:
        ts = entry.get("ts", "")[:19].replace("T", " ")
        tool = entry.get("tool", "?")
        cats = _entry_categories(entry)
        cat_str = ",".join(cats) if cats else "(none)"
        print(f"  {ts} | {tool:12s} | {cat_str}")
    return 0


# ---------------------------------------------------------------------------
# Main — stdin hook dispatch (default) or CLI helper modes
# ---------------------------------------------------------------------------


def _bypass_active():
    """True if the operator has opted into a pre-check bypass.

    Two independent paths:

    1. ``PRECHECK_BYPASS=1`` — the existing per-hook escape hatch.
    2. **yolo marker** — /ultrado and /rsi-optimal set the marker and the
       gate honours ``pre-check-dispatch`` in the yolo bypass set. This is
       separately audit-logged by the yolo module itself, so a single
       invocation produces one line in the pre-check bypass log AND one
       line in ``logs/yolo-mode.log``.
    """
    if os.environ.get(BYPASS_ENV_VAR, "").strip():
        return True
    # Late import so pre-check-hook stays importable in environments that
    # don't ship the gates package (e.g. minimal CI).
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        from gates.yolo_mode import is_yolo_bypass
    except ImportError:
        return False
    return bool(is_yolo_bypass("pre-check-dispatch"))


def _hook_dispatch():
    """Default mode: read a PreToolUse JSON payload from stdin and decide."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(json.dumps(_allow()))
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in GATED_TOOLS:
        print(json.dumps(_allow()))
        return 0

    prompt = _extract_prompt(payload.get("tool_input", {}))
    if not prompt:
        print(json.dumps(_allow()))
        return 0

    # Operator escape hatch: PRECHECK_BYPASS=1 force-allows but logs the
    # bypass event to a separate audit file for transparency.
    if _bypass_active():
        _log_bypass(tool_name, prompt)
        print(json.dumps(_allow()))
        return 0

    hits = _scan(prompt)
    if not hits:
        print(json.dumps(_allow()))
        return 0

    _log(tool_name, hits, prompt)
    print(json.dumps(_deny(_format_reason(hits))))
    return 0


def main():
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "--simulate":
            return _simulate(" ".join(sys.argv[2:]))
        if mode == "--stats":
            return _stats()
        if mode in ("-h", "--help"):
            print(
                "Usage:\n"
                "  (stdin JSON)              hook dispatch mode (PreToolUse)\n"
                "  --simulate <prompt...>    scan a prompt and print results without logging\n"
                "  --stats                   summarise the audit log by category + tool\n"
                "  --help                    show this message"
            )
            return 0
    return _hook_dispatch()


if __name__ == "__main__":
    sys.exit(main())
