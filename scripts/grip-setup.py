#!/usr/bin/env python3
"""
GRIP Setup - Cross-platform hook path resolver.

Fixes the Windows ~/$ HOME expansion issue where Claude Code runs hook commands
directly (not through a shell), so ~ and $HOME remain literal strings.

Usage:
    python3 grip-setup.py --check       # Verify current settings
    python3 grip-setup.py --fix         # Auto-fix paths for current platform
    python3 grip-setup.py --fix --platform windows  # Force Windows mode

The fix for Windows: wrap commands with bash -c "..." so Git Bash handles ~ expansion.
macOS/Linux: no changes needed (shell expands ~ natively).
"""

import argparse
import copy
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Pattern: command starting with ~ or containing ~/
TILDE_PATH_RE = re.compile(r"(^|(?<=\s))~/")

# Pattern: command using $HOME
HOME_VAR_RE = re.compile(r"\$HOME/")

# Already wrapped in bash -c (either single or double quoted).
# Why wrapping is needed: On Windows, Claude Code invokes hook commands without a
# shell, so ~ and $HOME remain literal strings. Wrapping in bash -c forces Git Bash
# to expand paths. macOS/Linux shells expand these natively.
BASH_WRAP_RE = re.compile(r'^bash\s+-c\s+["\']')


def detect_platform(override=None):
    """Return normalized platform: 'windows', 'macos', or 'linux'."""
    if override:
        return override.lower()
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def needs_wrapping(command, plat):
    """Check if a command needs bash -c wrapping for the given platform."""
    if plat != "windows":
        return False
    if BASH_WRAP_RE.match(command):
        return False
    if TILDE_PATH_RE.search(command) or HOME_VAR_RE.search(command):
        return True
    return False


def wrap_command(command):
    """Wrap a command with bash -c for Windows Git Bash execution.

    Uses double-quote wrapping: bash -c "command".
    Tilde (~) and $HOME both expand correctly inside bash -c on all platforms.

    Note: settings.json may use single-quote wrapping (bash -c '...') from manual
    edits. Both styles are valid and handled by unwrap_command().
    """
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return f'bash -c "{escaped}"'


def _unwrap_bash_c(command, quote_char):
    """Extract inner command from a bash -c wrapper with the given quote character.

    Returns the unwrapped command, or None if the pattern doesn't match.
    Handles escape sequences: backslash-quote -> quote, double-backslash -> backslash.
    """
    pattern = rf"^bash\s+-c\s+{re.escape(quote_char)}(.*){re.escape(quote_char)}$"
    match = re.match(pattern, command, re.DOTALL)
    if match:
        inner = match.group(1)
        escaped_quote = f"\\{quote_char}"
        return inner.replace(escaped_quote, quote_char).replace("\\\\", "\\")
    return None


def unwrap_command(command):
    """Remove bash -c wrapper if present. Returns original command.

    Handles both quoting styles produced by different tools:
      - bash -c "..."  (wrap_command output / --fix mode)
      - bash -c '...'  (manual edits, e.g. settings.json with $HOME)
    """
    for quote_char in ('"', "'"):
        result = _unwrap_bash_c(command, quote_char)
        if result is not None:
            return result
    return command


def transform_command(command, plat):
    """Transform a hook command for the target platform."""
    if plat == "windows":
        if needs_wrapping(command, plat):
            return wrap_command(command)
        return command
    else:
        # macOS/Linux: unwrap any bash -c wrappers (restore native form)
        unwrapped = unwrap_command(command)
        return unwrapped


def find_settings_files():
    """Find all settings.json files that may contain hooks."""
    home = Path.home()
    paths = []

    # User-level settings
    user_settings = home / ".claude" / "settings.json"
    if user_settings.exists():
        paths.append(user_settings)

    # Project-level settings in current directory
    project_settings = Path.cwd() / ".claude" / "settings.json"
    if project_settings.exists() and project_settings != user_settings:
        paths.append(project_settings)

    return paths


def find_all_hook_commands(settings):
    """Extract all hook command strings from a settings dict. Returns (path, command) tuples."""
    commands = []

    hooks = settings.get("hooks", {})
    for event_name, event_groups in hooks.items():
        if not isinstance(event_groups, list):
            continue
        for group in event_groups:
            hook_list = group.get("hooks", [])
            for hook in hook_list:
                if hook.get("type") == "command" and "command" in hook:
                    commands.append(("hooks", event_name, hook))

    # statusLine command
    status_line = settings.get("statusLine", {})
    if isinstance(status_line, dict) and status_line.get("type") == "command":
        commands.append(("statusLine", None, status_line))

    return commands


def check_settings(settings_path, plat):
    """Check a settings file for path issues. Returns (issues, total_hooks)."""
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    commands = find_all_hook_commands(settings)
    issues = []

    for location, event_name, hook in commands:
        cmd = hook["command"]
        if plat == "windows" and needs_wrapping(cmd, plat):
            ctx = f"{location}.{event_name}" if event_name else location
            issues.append({
                "context": ctx,
                "command": cmd,
                "fix": wrap_command(cmd),
                "reason": "~ path not expanded on Windows without shell",
            })
        elif plat != "windows" and BASH_WRAP_RE.match(cmd):
            inner = unwrap_command(cmd)
            if TILDE_PATH_RE.search(inner) or HOME_VAR_RE.search(inner):
                ctx = f"{location}.{event_name}" if event_name else location
                issues.append({
                    "context": ctx,
                    "command": cmd,
                    "fix": inner,
                    "reason": "unnecessary bash -c wrapper on Unix",
                })

    return issues, len(commands)


def fix_settings(settings_path, plat, dry_run=False):
    """Fix hook paths in a settings file. Returns (changes_made, backup_path)."""
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = json.load(f)

    original = json.dumps(settings)
    commands = find_all_hook_commands(settings)
    changes = []

    for location, event_name, hook in commands:
        old_cmd = hook["command"]
        new_cmd = transform_command(old_cmd, plat)
        if new_cmd != old_cmd:
            ctx = f"{location}.{event_name}" if event_name else location
            changes.append({
                "context": ctx,
                "old": old_cmd,
                "new": new_cmd,
            })
            hook["command"] = new_cmd

    if not changes:
        return changes, None

    if dry_run:
        return changes, None

    # Backup before writing
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = settings_path.parent / f"settings.{timestamp}.backup.json"
    shutil.copy2(settings_path, backup_path)

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return changes, backup_path


def print_check_report(settings_path, issues, total_hooks, plat):
    """Print a check report for a settings file."""
    print(f"\n--- {settings_path} ---")
    print(f"Platform: {plat}")
    print(f"Hooks scanned: {total_hooks}")

    if not issues:
        print("Status: OK - all hook paths are compatible")
        return

    print(f"Status: {len(issues)} issue(s) found\n")
    for i, issue in enumerate(issues, 1):
        print(f"  [{i}] {issue['context']}")
        print(f"      Current: {issue['command']}")
        print(f"      Fix:     {issue['fix']}")
        print(f"      Reason:  {issue['reason']}")
        print()


def print_fix_report(settings_path, changes, backup_path):
    """Print a fix report for a settings file."""
    print(f"\n--- {settings_path} ---")

    if not changes:
        print("No changes needed.")
        return

    print(f"Backup: {backup_path}")
    print(f"Changes: {len(changes)}\n")
    for i, change in enumerate(changes, 1):
        print(f"  [{i}] {change['context']}")
        print(f"      Was: {change['old']}")
        print(f"      Now: {change['new']}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="GRIP cross-platform hook path resolver"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Verify current settings")
    group.add_argument("--fix", action="store_true", help="Auto-fix paths")
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "linux"],
        default=None,
        help="Override platform detection",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    parser.add_argument(
        "--file",
        type=str,
        action="append",
        default=None,
        help="Specific settings file(s) to process",
    )

    args = parser.parse_args()
    plat = detect_platform(args.platform)

    if args.file:
        settings_files = [Path(f) for f in args.file]
        missing = [f for f in settings_files if not f.exists()]
        if missing:
            for f in missing:
                print(f"Error: {f} not found", file=sys.stderr)
            sys.exit(1)
    else:
        settings_files = find_settings_files()

    if not settings_files:
        print("No settings.json files found.")
        print(f"Expected: {Path.home() / '.claude' / 'settings.json'}")
        sys.exit(1)

    exit_code = 0

    if args.check:
        for sf in settings_files:
            try:
                issues, total = check_settings(sf, plat)
                print_check_report(sf, issues, total, plat)
                if issues:
                    exit_code = 1
            except json.JSONDecodeError as e:
                print(f"\n--- {sf} ---")
                print(f"Error: malformed JSON - {e}", file=sys.stderr)
                exit_code = 2
            except Exception as e:
                print(f"\n--- {sf} ---")
                print(f"Error: {e}", file=sys.stderr)
                exit_code = 2

    elif args.fix:
        for sf in settings_files:
            try:
                changes, backup = fix_settings(sf, plat, dry_run=args.dry_run)
                if args.dry_run:
                    print(f"\n--- {sf} (dry run) ---")
                    if not changes:
                        print("No changes needed.")
                    else:
                        print(f"Would make {len(changes)} change(s):")
                        for i, c in enumerate(changes, 1):
                            print(f"  [{i}] {c['context']}")
                            print(f"      Was: {c['old']}")
                            print(f"      Now: {c['new']}")
                            print()
                else:
                    print_fix_report(sf, changes, backup)
            except json.JSONDecodeError as e:
                print(f"\n--- {sf} ---")
                print(f"Error: malformed JSON - {e}", file=sys.stderr)
                exit_code = 2
            except Exception as e:
                print(f"\n--- {sf} ---")
                print(f"Error: {e}", file=sys.stderr)
                exit_code = 2

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
