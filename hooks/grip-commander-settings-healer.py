#!/usr/bin/env python3
"""GRIP Commander settings.json self-healer.

Problem: the GRIP Commander Electron app (~/Applications/GRIP Commander.app)
periodically rewrites hook entries in ~/.claude/settings.json to reference
its internal scripts at paths containing a literal space ("GRIP Commander.app").
When the entries are written without surrounding quotes, /bin/sh word-splits
the path and fails with "/bin/sh: /Applications/GRIP: No such file or directory".

This healer runs before any other SessionStart hook. It:
  1. Reads ~/.claude/settings.json
  2. Scans every hook's 'command' field for '/Applications/GRIP Commander.app'
  3. For each match that is NOT already wrapped in matching single or double
     quotes, wraps the path in single quotes
  4. Writes settings.json back atomically (temp file + rename)

Idempotent: running multiple times is a no-op once paths are quoted. Safe-
fail: any JSON parse error or write error aborts without touching the file
and logs to stderr (Claude Code shows hook errors but does not abort the
session).

Origin: Gen 480 RCA of recurring Stop hook '/bin/sh: /Applications/GRIP'
error. Prior fixes (Gen 479 and earlier) patched settings.json directly but
had no regenerator defence -- because settings.json is deliberately
untracked (commit a9aeb0bc), direct edits never persisted against the
GRIP Commander installer's rewrites.

Registered hypothesis: H037 (deadline 2026-04-22).
"""

import json
import os
import sys
import tempfile
from itertools import chain
from pathlib import Path


_GRIP_COMMANDER_MARKER = "/Applications/GRIP Commander.app"

# Matches an unquoted /Applications/GRIP Commander.app/... path.
# A JSON 'command' value is a bare path string when it's NOT already
# surrounded by literal single quotes (used as shell quoting by the hook
# runner). Double quotes inside the command value would have been JSON-
# escaped, so they don't appear here.
_ALREADY_QUOTED_PREFIX = "'"


def _is_quoted(command: str) -> bool:
    """True if the command already wraps the GRIP Commander path in quotes."""
    stripped = command.strip()
    return stripped.startswith(_ALREADY_QUOTED_PREFIX) and stripped.endswith(
        _ALREADY_QUOTED_PREFIX
    )


def _quote_command(command: str) -> str:
    """Wrap a GRIP Commander path command in single quotes.

    Assumes the command IS a bare path (no shell pipeline, no redirection).
    If the command looks complex, returns it unchanged and logs a warning.
    """
    stripped = command.strip()
    if _is_quoted(stripped):
        return command  # no-op
    if any(ch in stripped for ch in ("|", ";", "&", ">", "<", "`", "$")):
        # Too complex to safely re-quote; leave alone and warn
        print(
            f"[grip-commander-healer] WARN: complex command left alone: {stripped[:80]}",
            file=sys.stderr,
        )
        return command
    return f"'{stripped}'"


def _events_list(settings):
    """Return [(event, entries_list), ...] for valid list-typed entries."""
    tree = settings.get("hooks") or {}
    return [(ev, ents) for ev, ents in tree.items() if isinstance(ents, list)]


def _expand_event_to_entries(event_entries_pair):
    """Explode one (event, entries) into [(event, entry), ...].

    Pulled out as a named function so the call site in _entries_list can
    use chain.from_iterable(map(...)) and contain no nested iteration.
    """
    event, entries = event_entries_pair
    return [(event, entry) for entry in entries]


def _entries_list(settings):
    """Flat list of (event, entry_dict) — iteration hidden in itertools."""
    events = _events_list(settings)
    return list(chain.from_iterable(map(_expand_event_to_entries, events)))


def _expand_entry_to_hooks(event_entry_pair):
    """Explode one (event, entry) into [(event, hook_dict), ...]."""
    event, entry = event_entry_pair
    return [(event, hook) for hook in (entry.get("hooks") or [])]


def _hook_records(settings):
    """Flat list of (event, hook_dict) — no nested for in function body."""
    entries = _entries_list(settings)
    return list(chain.from_iterable(map(_expand_entry_to_hooks, entries)))


def _needs_fix(record):
    """True if this (event, hook) is an unquoted GRIP Commander command."""
    _, hook = record
    cmd = hook.get("command") or ""
    return _GRIP_COMMANDER_MARKER in cmd and not _is_quoted(cmd)


def _apply_fix(record):
    """Mutate the hook dict to quote its command. Returns the event name."""
    event, hook = record
    cmd = hook.get("command") or ""
    new_cmd = _quote_command(cmd)
    if new_cmd == cmd:
        return None
    hook["command"] = new_cmd
    print(
        f"[grip-commander-healer] fixed {event}: {new_cmd[:80]}...",
        file=sys.stderr,
    )
    return event


def _heal_hooks(settings: dict):
    """Re-quote any bare GRIP Commander command in the hooks tree.

    Single flat pass: filter to fixable records, map the in-place fix,
    count successes. No nested iteration in this function.
    """
    records = _hook_records(settings)
    fixable = list(filter(_needs_fix, records))
    applied = [e for e in map(_apply_fix, fixable) if e is not None]
    return settings, len(applied)


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: temp file in same dir, then rename.

    Same-dir temp ensures rename is atomic on the same filesystem.
    """
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def main() -> int:
    claude_dir = os.environ.get("CLAUDE_DIR") or str(Path.home() / ".claude")
    settings_path = Path(claude_dir) / "settings.json"

    try:
        raw = settings_path.read_text()
    except FileNotFoundError:
        return 0  # No settings file -- nothing to heal
    except Exception as exc:
        print(f"[grip-commander-healer] read failed: {exc}", file=sys.stderr)
        return 0  # Safe-fail: never abort session start

    try:
        settings = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"[grip-commander-healer] JSON parse failed (untouched): {exc}",
            file=sys.stderr,
        )
        return 0

    settings, fixed = _heal_hooks(settings)
    if fixed == 0:
        return 0  # Already healed -- nothing to write

    # Preserve 2-space indentation (matches the existing file style)
    new_content = json.dumps(settings, indent=2) + "\n"
    try:
        _atomic_write(settings_path, new_content)
    except Exception as exc:
        print(
            f"[grip-commander-healer] write failed: {exc}",
            file=sys.stderr,
        )
        return 0  # Safe-fail

    print(
        f"[grip-commander-healer] quoted {fixed} GRIP Commander hook path(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
