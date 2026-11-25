#!/usr/bin/env python3
"""
Copilot CLI - Command-line interface for GRIP Copilot operations.

Commands:
    tick            Run ritual engine tick (execute due rituals)
    status          Print status report
    list            List all rituals and their state
    enable <id>     Enable a ritual
    disable <id>    Disable a ritual
    run <id>        Force-run a specific ritual
    dry-run <id>    Trace a ritual without side effects

Usage:
    python3 aide-cli.py tick
    python3 aide-cli.py status --format slack
    python3 aide-cli.py enable git-pr-status
    python3 aide-cli.py run morning-brief --dry-run

Version: 1.0.0
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

LOG_FILE = CLAUDE_DIR / "data" / "aide" / "rituals" / "cli.log"


def log_cli(message: str):
    """Append to CLI log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {message}\n")


def cmd_tick(args):
    """Run ritual engine tick - execute all due rituals."""
    from aide.ritual_engine import RitualEngine

    engine = RitualEngine()
    due = engine.get_due_rituals()

    if not due:
        print("No rituals due.")
        log_cli("tick: no rituals due")
        return

    print(f"Executing {len(due)} due ritual(s): {', '.join(due)}")
    results = engine.tick(dry_run=args.dry_run)

    for ritual_id, result in results:
        status_icon = "OK" if result.success else "FAIL"
        prefix = "[DRY] " if args.dry_run else ""
        print(f"  {prefix}{status_icon} {ritual_id}: {result.status} ({len(result.steps)} steps)")
        if result.was_escalated:
            print(f"       ESCALATED - human input required")

    log_cli(f"tick: executed {len(results)} rituals (dry_run={args.dry_run})")


def cmd_status(args):
    """Print status report."""
    # Import the whatsup generator
    sys.path.insert(0, str(CLAUDE_DIR / "scripts"))
    try:
        import importlib
        whatsup = importlib.import_module("aide-whatsup")
    except ImportError:
        # Fallback: direct import with path manipulation
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "aide_whatsup",
            str(CLAUDE_DIR / "scripts" / "aide-whatsup.py"),
        )
        whatsup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(whatsup)

    report = whatsup.generate_report()

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    elif args.format == "slack":
        print(whatsup.format_slack(report))
    else:
        print(whatsup.format_text(report))


def cmd_list(args):
    """List all rituals and their state."""
    from aide.ritual_engine import RitualEngine

    engine = RitualEngine()
    rituals = engine.list_rituals()

    if args.format == "json":
        print(json.dumps(rituals, indent=2, default=str))
        return

    # Table format
    print(f"{'ID':<25} {'Enabled':<9} {'Status':<10} {'Fires':<7} {'Next Fire':<25} {'Due'}")
    print("-" * 95)
    for r in sorted(rituals, key=lambda x: (not x.get("enabled", False), x.get("id", ""))):
        enabled = "YES" if r.get("enabled") else "no"
        due = "DUE" if r.get("is_due") else ""
        nf = r.get("next_fire", "")
        next_fire = nf[:19] if nf else "-"
        print(f"{r.get('id', '?'):<25} {enabled:<9} {r.get('last_status', '?'):<10} {r.get('fire_count', 0):<7} {next_fire:<25} {due}")


def cmd_enable(args):
    """Enable a ritual."""
    from aide.ritual_engine import RitualEngine

    engine = RitualEngine()
    if args.ritual_id not in engine.states:
        print(f"Unknown ritual: {args.ritual_id}")
        sys.exit(1)

    engine.enable_ritual(args.ritual_id)
    state = engine.states[args.ritual_id]
    print(f"Enabled {args.ritual_id}. Next fire: {state.next_fire}")
    log_cli(f"enable: {args.ritual_id}")


def cmd_disable(args):
    """Disable a ritual."""
    from aide.ritual_engine import RitualEngine

    engine = RitualEngine()
    if args.ritual_id not in engine.states:
        print(f"Unknown ritual: {args.ritual_id}")
        sys.exit(1)

    engine.disable_ritual(args.ritual_id)
    print(f"Disabled {args.ritual_id}")
    log_cli(f"disable: {args.ritual_id}")


def cmd_run(args):
    """Force-run a specific ritual."""
    from aide.ritual_engine import RitualEngine

    engine = RitualEngine()
    if args.ritual_id not in engine.states:
        print(f"Unknown ritual: {args.ritual_id}")
        sys.exit(1)

    print(f"Running {args.ritual_id} (dry_run={args.dry_run})...")
    result = engine.execute_ritual(args.ritual_id, dry_run=args.dry_run)

    if not result:
        print("No result returned.")
        return

    prefix = "[DRY] " if args.dry_run else ""
    print(f"{prefix}Status: {result.status}")
    for step in result.steps:
        icon = "OK" if step.status == "success" else step.status.upper()
        print(f"  {icon} {step.step_id}")
        if step.error:
            print(f"       Error: {step.error}")
        if step.output and args.verbose:
            print(f"       Output: {json.dumps(step.output, default=str)[:200]}")

    if result.was_escalated:
        print("\nESCALATED - human input required for some steps.")

    log_cli(f"run: {args.ritual_id} -> {result.status} (dry_run={args.dry_run})")


def cmd_history(args):
    """Show ritual execution history."""
    history_file = CLAUDE_DIR / "data" / "aide" / "rituals" / "history.jsonl"
    if not history_file.exists():
        print("No execution history found.")
        return

    entries = []
    with open(history_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        print("No execution history found.")
        return

    # Filter by ritual ID if specified
    if args.ritual_id:
        entries = [e for e in entries if e.get("ritual_id") == args.ritual_id]
        if not entries:
            print(f"No history for ritual: {args.ritual_id}")
            return

    # Limit to last N entries
    entries = entries[-args.limit:]

    if args.format == "json":
        print(json.dumps(entries, indent=2))
        return

    # Table format
    print(f"{'Timestamp':<22} {'Ritual':<25} {'Status':<10} {'Steps':<7} {'Escalated'}")
    print("-" * 80)
    for e in entries:
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        ritual = e.get("ritual_id", "?")
        status = e.get("status", "?")
        steps = e.get("steps", 0)
        escalated = "YES" if e.get("escalated") else ""

        print(f"{ts:<22} {ritual:<25} {status:<10} {steps:<7} {escalated}")

        # Show step details in verbose mode
        if args.verbose:
            step_summary = e.get("step_summary", [])
            duration_steps = e.get("duration_steps", [])
            if step_summary:
                for s in step_summary:
                    icon = "OK" if s.get("status") == "success" else s.get("status", "?").upper()
                    err = f" - {s.get('error', '')}" if s.get("error") else ""
                    print(f"  {icon} {s.get('id', '?')}{err}")
            elif duration_steps:
                print(f"  Steps: {', '.join(duration_steps)}")

    # Summary
    success = sum(1 for e in entries if e.get("status") == "success")
    fail = len(entries) - success
    print(f"\n{len(entries)} executions: {success} success, {fail} failed")


def cmd_decisions(args):
    """Show recent decisions from the audit log."""
    from aide.decision_logger import DecisionLogger

    logger = DecisionLogger()
    recent = logger.get_recent_decisions(args.limit)

    if not recent:
        print("No decisions logged.")
        return

    if args.format == "json":
        print(json.dumps(recent, indent=2, default=str))
        return

    # Table format
    unresolved = logger.get_unresolved_decisions()
    unresolved_ids = {d.get("id", "") for d in unresolved}

    print(f"{'Timestamp':<22} {'Decision':<40} {'Confidence':<12} {'Resolved'}")
    print("-" * 85)

    for entry in recent:
        dec = entry.get("decision", {}) if isinstance(entry, dict) else {}
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        what = dec.get("what", "?")[:38]
        confidence = dec.get("confidence", 0)
        entry_id = entry.get("id", "")
        resolved = "no" if entry_id in unresolved_ids else "yes"

        print(f"{ts:<22} {what:<40} {confidence:<12.0%} {resolved}")

    stats = logger.stats()
    print(f"\n{stats.get('decisions', 0)} total | {stats.get('outcomes', 0)} resolved "
          f"({stats.get('resolution_rate', 0):.0%} resolution rate)")


def cmd_health(args):
    """Check integration health."""
    from aide.integrations import IntegrationHub, MobilePushNotifier

    hub = IntegrationHub()
    # Use detailed_status() for rich info needed by CLI display
    status = hub.detailed_status()

    print("Integration Health Check")
    print("=" * 40)

    for name, info in status.items():
        available = info.get("available", False)
        icon = "OK" if available else "FAIL"
        details = ""

        if name == "slack" and available:
            details = f" (token: ...{info.get('token_prefix', '???')})"
        elif name == "github":
            details = " (gh CLI)" if available else " (gh CLI not found)"

        print(f"  [{icon:>4}] {name:<15}{details}")

    # Mobile push
    mobile = MobilePushNotifier()
    mobile_ok = mobile.available()
    print(f"  [{'OK' if mobile_ok else 'FAIL':>4}] mobile_push     (ntfy.sh)")

    # Data files
    data_dir = CLAUDE_DIR / "data" / "aide"
    files = {
        "company.json": data_dir / "company.json",
        "decision-log.jsonl": data_dir / "decision-log.jsonl",
        "preferences.json": data_dir / "preferences.json",
        "active.json": data_dir / "rituals" / "active.json",
    }

    print(f"\nData Files")
    print("-" * 40)
    for name, path in files.items():
        try:
            size = f"{path.stat().st_size:,} bytes"
            exists = True
        except OSError:
            size = "missing"
            exists = False
        print(f"  [{'OK' if exists else 'MISS':>4}] {name:<25} {size}")

    if args.format == "json":
        result = {"integrations": status, "mobile_push": mobile_ok}
        result["data_files"] = {n: {"exists": p.exists()} for n, p in files.items()}
        print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="GRIP Copilot CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # tick
    tick_parser = subparsers.add_parser("tick", help="Execute due rituals")
    tick_parser.add_argument("--dry-run", action="store_true", help="Trace without side effects")

    # status
    status_parser = subparsers.add_parser("status", help="Print status report")
    status_parser.add_argument("--format", choices=["text", "slack", "json"], default="text")

    # list
    list_parser = subparsers.add_parser("list", help="List all rituals")
    list_parser.add_argument("--format", choices=["table", "json"], default="table")

    # enable
    enable_parser = subparsers.add_parser("enable", help="Enable a ritual")
    enable_parser.add_argument("ritual_id", help="Ritual ID to enable")

    # disable
    disable_parser = subparsers.add_parser("disable", help="Disable a ritual")
    disable_parser.add_argument("ritual_id", help="Ritual ID to disable")

    # run
    run_parser = subparsers.add_parser("run", help="Force-run a ritual")
    run_parser.add_argument("ritual_id", help="Ritual ID to run")
    run_parser.add_argument("--dry-run", action="store_true", help="Trace without side effects")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Show step outputs")

    # history
    hist_parser = subparsers.add_parser("history", help="Show execution history")
    hist_parser.add_argument("ritual_id", nargs="?", help="Filter by ritual ID")
    hist_parser.add_argument("--limit", "-n", type=int, default=20, help="Max entries to show")
    hist_parser.add_argument("--format", choices=["table", "json"], default="table")
    hist_parser.add_argument("--verbose", "-v", action="store_true", help="Show step details")

    # decisions
    dec_parser = subparsers.add_parser("decisions", help="Show recent decisions")
    dec_parser.add_argument("--limit", "-n", type=int, default=20, help="Max entries to show")
    dec_parser.add_argument("--format", choices=["table", "json"], default="table")

    # health
    health_parser = subparsers.add_parser("health", help="Check integration health")
    health_parser.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "tick": cmd_tick,
        "status": cmd_status,
        "list": cmd_list,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "run": cmd_run,
        "history": cmd_history,
        "decisions": cmd_decisions,
        "health": cmd_health,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
