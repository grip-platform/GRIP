"""Machine-local integration hooks, detection-gated for the shared settings.json.

The problem class: GUI tools (Otty's Settings -> Agents -> Install Hooks) and
machine-local packages (prompt-cache-keepalive's CC plugin) write hook commands
with ABSOLUTE machine paths (/Applications/Otty.app/..., /Users/<me>/.grip/venv/...)
into settings.json. GRIP's settings.json is committed and shared by the whole
team — on a machine without the tool (the Windows teammates today) every hook
event then spawns a failing command: noise on every prompt, every Stop.

The fix encoded here: each integration's hook entry becomes a thin,
$HOME-portable wrapper (hooks/otty-state-hook.sh, hooks/cache-keepalive-hook.sh)
that exits 0 instantly when the integration is absent. Full power where the
tool lives (V>>'s Mac and a former client contact's), provably inert everywhere else.

`ensure()` is a pure, idempotent transform: raw Otty entries are replaced by
their gated equivalents (state/ctx preserved), gated entries are injected when
missing, and NOTHING else in the file is touched. The CLI applies it atomically
with a JSON round-trip check (a malformed settings.json disables ALL hooks —
learned 2026-06-27).

Doctor integration: `bin/grip-otty doctor` calls `check()` to flag drift (e.g.
Otty's installer re-adding raw entries after a reinstall).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

__all__ = ["OTTY_RAW_MARK", "SPECS", "check", "ensure"]

# Any command still referencing the app bundle directly is a raw (ungated) entry.
OTTY_RAW_MARK = "/Applications/Otty.app"

_OTTY_GATED = '/bin/sh "$HOME/.claude/hooks/otty-state-hook.sh"'
_KEEPALIVE_GATED = '/bin/sh "$HOME/.claude/hooks/cache-keepalive-hook.sh"'

# The desired end-state: event -> gated command. Otty rows mirror exactly what
# Otty 1.1.0's installer wires (states verified live 2026-07-02); keepalive rows
# mirror prompt-cache-keepalive 0.3.0's .claude-plugin/hooks.json.
SPECS: tuple[tuple[str, str], ...] = (
    ("PermissionRequest", f'{_OTTY_GATED} awaiting "$PPID" ctx'),
    ("PreToolUse", f'{_OTTY_GATED} processing "$PPID"'),
    ("PostToolUse", f'{_OTTY_GATED} processing "$PPID"'),
    ("UserPromptSubmit", f'{_OTTY_GATED} processing "$PPID"'),
    ("Stop", f'{_OTTY_GATED} idle "$PPID"'),
    ("SessionStart", f'{_OTTY_GATED} idle "$PPID"'),
    ("SessionStart", f"{_KEEPALIVE_GATED} session_start"),
    ("Stop", f"{_KEEPALIVE_GATED} stop"),
    ("PreCompact", f"{_KEEPALIVE_GATED} pre_compact"),
)

_RAW_STATE_RE = re.compile(r"otty-hook\.sh'\s+(\w+)\s+\"\$PPID\"(\s+ctx)?")


def _iter_commands(event_matchers: list) -> list[str]:
    commands = []
    for matcher in event_matchers:
        for hook in matcher.get("hooks", []):
            commands.append(str(hook.get("command", "")))
    return commands


def _strip_raw_otty(hooks: dict, changes: list[str]) -> None:
    for event, matchers in list(hooks.items()):
        kept_matchers = []
        for matcher in matchers:
            kept = [h for h in matcher.get("hooks", [])
                    if OTTY_RAW_MARK not in str(h.get("command", ""))]
            removed = len(matcher.get("hooks", [])) - len(kept)
            if removed:
                changes.append(f"{event}: removed {removed} raw Otty entr{'y' if removed == 1 else 'ies'}")
            if kept or not matcher.get("hooks"):
                matcher = dict(matcher)
                matcher["hooks"] = kept
                if kept or matcher.get("matcher"):
                    kept_matchers.append(matcher)
            # matcher blocks emptied by the strip are dropped entirely
        hooks[event] = kept_matchers
        if not kept_matchers:
            del hooks[event]


def _inject_gated(hooks: dict, changes: list[str]) -> None:
    for event, command in SPECS:
        existing = _iter_commands(hooks.get(event, []))
        if command in existing:
            continue
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": command}]})
        changes.append(f"{event}: added gated hook ({command.split()[-1]})")


def ensure(settings: dict) -> tuple[dict, list[str]]:
    """Return (new_settings, changes). Pure; input is not mutated; idempotent."""
    result = copy.deepcopy(settings)
    hooks = result.setdefault("hooks", {})
    changes: list[str] = []
    _strip_raw_otty(hooks, changes)
    _inject_gated(hooks, changes)
    return result, changes


def check(settings: dict) -> list[str]:
    """Drift report: raw entries present / gated entries missing. Empty = clean."""
    problems: list[str] = []
    hooks = settings.get("hooks", {})
    for event, matchers in hooks.items():
        for command in _iter_commands(matchers):
            if OTTY_RAW_MARK in command:
                state = _RAW_STATE_RE.search(command)
                problems.append(
                    f"{event}: raw ungated Otty hook ({state.group(1) if state else '?'}) — "
                    "fails loudly on machines without Otty"
                )
    for event, command in SPECS:
        if command not in _iter_commands(hooks.get(event, [])):
            problems.append(f"{event}: gated hook missing ({command.split()[-1]})")
    return problems


def _apply_file(path: Path, *, dry_run: bool) -> int:
    settings = json.loads(path.read_text(encoding="utf-8"))
    new_settings, changes = ensure(settings)
    if not changes:
        print(f"{path}: already gated — no changes")
        return 0
    for change in changes:
        print(f"  {change}")
    if dry_run:
        print("(dry run — nothing written)")
        return 1
    rendered = json.dumps(new_settings, indent=2, ensure_ascii=False) + "\n"
    json.loads(rendered)  # round-trip guard: malformed settings.json kills ALL hooks
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".gated-hooks-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(f"{path}: {len(changes)} change(s) applied")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate machine-local hooks in settings.json")
    parser.add_argument("--file", default=str(Path.home() / ".claude" / "settings.json"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift, exit 1 if any")
    mode.add_argument("--apply", action="store_true", help="rewrite the file (atomic)")
    args = parser.parse_args(argv)
    path = Path(args.file).expanduser()
    if args.check:
        problems = check(json.loads(path.read_text(encoding="utf-8")))
        for problem in problems:
            print(f"  ✗ {problem}")
        print(f"{path}: {'clean' if not problems else str(len(problems)) + ' problem(s)'}")
        return 1 if problems else 0
    return _apply_file(path, dry_run=not args.apply)


if __name__ == "__main__":
    sys.exit(main())
