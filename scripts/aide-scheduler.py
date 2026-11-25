#!/usr/bin/env python3
"""
Copilot Scheduler - launchd dispatcher for ritual execution.

Runs every 15 minutes via launchd. Checks which rituals are due
and executes them through the CopilotCore.

Usage:
  python3 aide-scheduler.py                    # Normal tick
  python3 aide-scheduler.py --dry-run           # Trace without side effects
  python3 aide-scheduler.py --force morning-brief  # Force-execute a specific ritual
  python3 aide-scheduler.py --status            # Show ritual status
  python3 aide-scheduler.py --install           # Install launchd agent
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
LOG_DIR = CLAUDE_DIR / "logs"

# Add lib to path (lib/ for direct imports, CLAUDE_DIR for "from lib.X" imports)
sys.path.insert(0, str(CLAUDE_DIR / "lib"))
sys.path.insert(0, str(CLAUDE_DIR))


def log(msg: str):
    """Log to copilot scheduler log file and stderr (not stdout).

    JSON output goes to stdout for machine parsing.
    Log lines go to stderr and log file for human debugging.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "copilot-scheduler.log"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)
    with open(log_file, "a") as f:
        f.write(line + "\n")


def cmd_tick(dry_run: bool = False):
    """Execute scheduler tick - run all due rituals."""
    from aide.core import CopilotCore

    log(f"--- Copilot scheduler tick {'(DRY RUN)' if dry_run else ''} ---")

    try:
        copilot = CopilotCore()
        result = copilot.handle("tick", {"dry_run": dry_run})

        executed = result.get("executed", 0)
        log(f"Executed {executed} ritual(s)")

        for r in result.get("results", []):
            rid = r.get("ritual_id", "unknown")
            status = r.get("status", "unknown")
            log(f"  [{rid}] {status}")

        if executed == 0:
            due = copilot.ritual_engine.get_due_rituals()
            if due:
                log(f"  Due but not executed: {due}")
            else:
                log("  No rituals due")

    except Exception as e:
        log(f"ERROR: {e}")
        raise


def cmd_force(ritual_id: str, dry_run: bool = False):
    """Force-execute a specific ritual."""
    from aide.core import CopilotCore

    log(f"--- Force executing: {ritual_id} {'(DRY RUN)' if dry_run else ''} ---")

    try:
        copilot = CopilotCore()
        result = copilot.handle("ritual_management", {
            "action": "dry_run" if dry_run else "force",
            "ritual_id": ritual_id,
        })

        print(json.dumps(result, indent=2, default=str))

        status = result.get("status", "error")
        log(f"  Result: {status}")

        if result.get("error"):
            log(f"  Error: {result['error']}")

    except Exception as e:
        log(f"ERROR: {e}")
        raise


def cmd_status():
    """Show ritual status."""
    from aide.core import CopilotCore

    try:
        copilot = CopilotCore()
        result = copilot.handle("status")
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        log(f"ERROR: {e}")
        raise


def cmd_install():
    """Install launchd agent for 15-minute scheduling."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.grip.copilot-scheduler.plist"
    scheduler_path = CLAUDE_DIR / "scripts" / "aide-scheduler.py"
    python_path = sys.executable

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.grip.copilot-scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{scheduler_path}</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/copilot-scheduler-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/copilot-scheduler-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLAUDE_DIR</key>
        <string>{CLAUDE_DIR}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)
    print(f"Installed: {plist_path}")
    print(f"Load with: launchctl load {plist_path}")
    print(f"Unload with: launchctl unload {plist_path}")


def main():
    parser = argparse.ArgumentParser(description="Copilot Scheduler")
    parser.add_argument("--dry-run", action="store_true", help="Trace without side effects")
    parser.add_argument("--force", type=str, metavar="RITUAL_ID", help="Force-execute a ritual")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--install", action="store_true", help="Install launchd agent")
    args = parser.parse_args()

    if args.install:
        cmd_install()
    elif args.status:
        cmd_status()
    elif args.force:
        cmd_force(args.force, dry_run=args.dry_run)
    else:
        cmd_tick(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
