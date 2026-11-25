#!/usr/bin/env python3
"""
verify_hooks.py — Worker hook verification for GRIP safety.

Checks that critical safety hooks are configured and active.
Called at worker startup to detect hook bypass (Issue #215).

Usage:
    python3 lib/verify_hooks.py          # Exit 0 if hooks present, 1 if missing
    python3 lib/verify_hooks.py --json   # Output JSON status

Returns exit code 0 if all required hooks are found, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Accepted hook configurations — verify passes if ANY configuration matches.
# System settings use Stop/ConfigChange; worker settings use PreToolUse/PostToolUse.
HOOK_CONFIGS = [
    {
        "Stop": ["stop-capture"],
        "ConfigChange": ["config-change-log"],
    },
    {
        "PreToolUse": ["context-gate-hook"],
        "PostToolUse": ["secrets-output-hook"],
    },
]

# Legacy alias for backward compatibility
REQUIRED_HOOKS = HOOK_CONFIGS[0]


def _is_hook_settings(path: Path) -> bool:
    """True unless a readable file declares no event from any accepted hook config.

    The repo's own .claude/settings.json only registers the cloud-session
    bootstrap (SessionStart), so it is not the settings file GRIP's hooks live
    in. An unreadable or malformed file still counts, so verify() reports it.
    """
    try:
        hooks = json.loads(path.read_text()).get("hooks", {})
    except (OSError, ValueError, AttributeError):
        return True
    return any(event in hooks for config in HOOK_CONFIGS for event in config)


def find_settings() -> Path | None:
    """Locate the active settings file, checking project then user level.

    A project-level file is used only when it declares GRIP's required hooks."""
    project = Path.cwd() / ".claude" / "settings.json"
    candidates = [
        Path(os.environ.get("CLAUDE_DIR", "")) / "settings.json",
        Path.home() / ".claude" / "settings.json",       # user-level
        Path.home() / ".claude" / "settings.worker.json", # worker fallback
    ]
    if project.exists() and _is_hook_settings(project):
        return project
    for path in candidates:
        if path.exists():
            return path
    return None


def verify(settings_path: Path | None = None) -> dict:
    """Verify hooks are present and return status dict.

    Returns:
        {
            "ok": bool,
            "settings_file": str | None,
            "missing": {"PreToolUse": [...], "PostToolUse": [...]},
            "found": {"PreToolUse": [...], "PostToolUse": [...]},
        }
    """
    if settings_path is None:
        settings_path = find_settings()

    result = {
        "ok": True,
        "settings_file": str(settings_path) if settings_path else None,
        "missing": {},
        "found": {},
    }

    if settings_path is None or not settings_path.exists():
        result["ok"] = False
        result["missing"] = REQUIRED_HOOKS.copy()
        return result

    try:
        settings = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        result["ok"] = False
        result["missing"] = REQUIRED_HOOKS.copy()
        return result

    hooks_config = settings.get("hooks", {})

    # Try each accepted hook configuration; pass if any matches fully
    best_result = None
    for config in HOOK_CONFIGS:
        attempt = {"ok": True, "missing": {}, "found": {}}
        for hook_type, required_names in config.items():
            entries = hooks_config.get(hook_type, [])
            commands = []
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if cmd:
                        commands.append(cmd)

            found = []
            missing = []
            for name in required_names:
                if any(name in cmd for cmd in commands):
                    found.append(name)
                else:
                    missing.append(name)

            if found:
                attempt["found"][hook_type] = found
            if missing:
                attempt["missing"][hook_type] = missing
                attempt["ok"] = False

        if attempt["ok"]:
            result["found"] = attempt["found"]
            return result

        # Track the best (fewest missing) attempt
        if best_result is None or len(attempt["missing"]) < len(best_result["missing"]):
            best_result = attempt

    # No config matched fully — report best attempt
    result["ok"] = False
    result["missing"] = best_result["missing"] if best_result else {}
    result["found"] = best_result["found"] if best_result else {}
    return result


def main() -> None:
    json_mode = "--json" in sys.argv

    status = verify()

    if json_mode:
        print(json.dumps(status, indent=2))
    else:
        if status["ok"]:
            print(f"[GRIP] Hooks verified. Settings: {status['settings_file']}")
        else:
            print(f"[GRIP] WARNING: Safety hooks missing!")
            if status["settings_file"]:
                print(f"  Settings file: {status['settings_file']}")
            else:
                print("  No settings file found!")
            for hook_type, names in status.get("missing", {}).items():
                for name in names:
                    print(f"  Missing {hook_type}: {name}")
            print("  Workers without hooks bypass ALL GRIP safety gates.")
            print("  Fix: Ensure worker runs in GRIP project dir or use --settings")

    sys.exit(0 if status["ok"] else 1)


if __name__ == "__main__":
    main()
