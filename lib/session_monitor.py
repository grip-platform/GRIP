#!/usr/bin/env python3
"""
Session Monitor CLI - Connect to and monitor active GRIP sessions.

Commands:
  list                                   List active sessions
  connect <session_id>                   Stream events from a session
  connect <session_id> --steer           Stream events + steering control
  connect <session_id> --remote <ip>     Connect via Tailscale
  follow [--steer]                       Auto-follow RSI session from lock file

Requires: websockets (pip install websockets) for connect command.

Version: 1.1.0
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"

# ANSI colors for terminal output
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_CYAN = "\033[36m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"

TOOL_COLORS = {
    "Bash": C_GREEN,
    "Write": C_BLUE,
    "Edit": C_CYAN,
    "Skill": C_MAGENTA,
    "TeamCreate": C_YELLOW,
    "SendMessage": C_YELLOW,
}


def format_event(event: dict) -> str:
    """Pretty-format a session event for terminal display."""
    event_type = event.get("event_type", "unknown")
    timestamp = event.get("timestamp", "")
    data = event.get("data", {})
    output = event.get("output_summary", "")

    color = TOOL_COLORS.get(event_type, C_DIM)

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        ts = dt.strftime("%H:%M:%S")
    except (ValueError, AttributeError):
        ts = "??:??:??"

    # Build summary based on tool type
    summary = ""
    if event_type == "Bash":
        cmd = data.get("command", "")
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        summary = cmd
    elif event_type in ("Write", "Edit"):
        path = data.get("file_path", "")
        if path:
            summary = os.path.basename(path)
    elif event_type == "Skill":
        summary = data.get("skill", "")
    else:
        summary = json.dumps(data)[:60] if data else ""

    line = f"{C_DIM}{ts}{C_RESET} {color}{C_BOLD}{event_type:12s}{C_RESET} {summary}"

    if output:
        first_line = output.split("\n")[0][:100]
        line += f"\n{C_DIM}  -> {first_line}{C_RESET}"

    return line


def _lister_path() -> Path:
    """lib/session_lister.py. The hyphenated name was deleted in dcc92e559 (2026-03-06)."""
    return CLAUDE_DIR / "lib" / "session_lister.py"


async def list_sessions():
    """List all active GRIP sessions."""
    lister = _lister_path()
    if not lister.exists():
        print(f"{C_RED}session_lister.py not found{C_RESET}")
        return

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(lister),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)

    try:
        sessions = json.loads(stdout.decode())
    except json.JSONDecodeError:
        print(f"{C_RED}Failed to parse session list{C_RESET}")
        return

    if not sessions:
        print(f"{C_DIM}No active sessions{C_RESET}")
        return

    print(f"\n{C_BOLD}Active GRIP Sessions{C_RESET}")
    header = f"{'ID':>10s}  {'PID':>6s}  {'Branch':12s}  {'Project':20s}  {'Uptime':8s}  {'Mon':3s}"
    print(f"{'─' * 70}")
    print(header)
    print(f"{'─' * 70}")

    for s in sessions:
        sid = s.get("session_id", "")[:8]
        pid = str(s.get("pid", ""))
        branch = s.get("branch", "?")[:12]
        project = s.get("project_name", "?")[:20]
        uptime = s.get("uptime_display", "?")
        mon = "Y" if s.get("monitoring_enabled") else "N"
        color = C_GREEN if s.get("monitoring_enabled") else C_DIM
        print(
            f"{color}{sid:>10s}  {pid:>6s}  {branch:12s}  "
            f"{project:20s}  {uptime:8s}  {mon:3s}{C_RESET}"
        )

    print(f"\n{C_DIM}Use: session_monitor.py connect <session_id>{C_RESET}\n")


async def resolve_session(session_id: str) -> dict | None:
    """Find a session by ID prefix using session_lister.py."""
    lister = _lister_path()
    if not lister.exists():
        return None

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(lister),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)

    try:
        sessions = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return None

    for s in sessions:
        if s["session_id"].startswith(session_id):
            return s
    return None


async def connect_session(
    session_id: str, steer: bool = False, remote: str = None
):
    """Connect to a session and stream events."""
    try:
        import websockets
    except ImportError:
        print(f"{C_RED}websockets not installed. Run: pip install websockets{C_RESET}")
        return

    # Determine connection URL
    if remote:
        import hashlib

        h = hashlib.sha256(session_id.encode()).hexdigest()
        port = 19000 + (int(h[:8], 16) % 1000)
        url = f"ws://{remote}:{port}"
    else:
        match = await resolve_session(session_id)
        if match:
            port = match.get("tcp_port", 19000)
            url = f"ws://127.0.0.1:{port}"
            session_id = match["session_id"]
        else:
            print(f"{C_RED}Session {session_id} not found{C_RESET}")
            return

    monitor_id = f"cli-{os.getpid()}"
    print(f"{C_BOLD}Connecting to session {session_id[:8]}...{C_RESET}")
    print(f"{C_DIM}URL: {url}{C_RESET}")

    try:
        async with websockets.connect(url) as ws:
            # Subscribe to all events
            await ws.send(
                json.dumps(
                    {"type": "subscribe", "monitor_id": monitor_id, "events": []}
                )
            )
            print(f"{C_GREEN}Connected as {monitor_id}{C_RESET}")

            # Acquire steering if requested
            if steer:
                await ws.send(
                    json.dumps(
                        {"type": "acquire_steering", "monitor_id": monitor_id}
                    )
                )
                print(f"{C_YELLOW}Steering requested...{C_RESET}")

            print(f"{'─' * 70}")
            print(f"{C_DIM}Press Ctrl+C to disconnect{C_RESET}\n")

            # Start steering input reader and auto-heartbeat if needed
            if steer:
                asyncio.create_task(_steering_input(ws, monitor_id))
                asyncio.create_task(_auto_heartbeat(ws))

            # Main event loop
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "event":
                    print(format_event(msg))
                elif msg_type == "heartbeat":
                    pass  # Silent heartbeats
                elif msg_type == "steering_acquired":
                    print(f"\n{C_GREEN}{C_BOLD}Steering acquired{C_RESET}")
                    print(
                        f"{C_DIM}Type 'inject: <text>' to inject context{C_RESET}\n"
                    )
                elif msg_type == "steering_denied":
                    holder = msg.get("holder", "?")
                    print(
                        f"\n{C_RED}Steering denied (held by {holder}){C_RESET}\n"
                    )
                elif msg_type == "steer_ack":
                    print(f"{C_GREEN}Steer command acknowledged{C_RESET}")
                elif msg_type == "steering_expiring":
                    remaining = msg.get("remaining_seconds", "?")
                    print(f"\n{C_YELLOW}{C_BOLD}Steering expires in {remaining}s — auto-heartbeat will renew{C_RESET}")
                elif msg_type == "steering_grace":
                    print(f"\n{C_YELLOW}Steering grace period — commands queued{C_RESET}")
                elif msg_type == "heartbeat_ack":
                    ttl = msg.get("ttl_remaining", 0)
                    if ttl > 0:
                        pass  # Silent ack, normal operation
                elif msg_type == "queued":
                    count = msg.get("queue_size", 0)
                    print(f"{C_DIM}Command queued ({count} in queue){C_RESET}")
                elif msg_type == "error":
                    print(f"{C_RED}Error: {msg.get('reason', '?')}{C_RESET}")

    except ConnectionRefusedError:
        print(
            f"{C_RED}Connection refused. Is the session server running?{C_RESET}"
        )
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        print(f"\n{C_DIM}Disconnected{C_RESET}")


async def _auto_heartbeat(ws, interval: int = 120):
    """Send periodic heartbeats to keep steering lock alive during extended thinking."""
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(json.dumps({"type": "heartbeat"}))
        except Exception:
            break


async def _steering_input(ws, monitor_id: str):
    """Read stdin for steering commands (background task)."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if line.startswith("inject:"):
                text = line[7:].strip()
                await ws.send(
                    json.dumps(
                        {
                            "type": "steer_command",
                            "command_type": "inject_context",
                            "text": text,
                        }
                    )
                )
        except Exception:
            break


def _read_rsi_lock() -> str | None:
    """Read session ID from RSI lock file. Returns None if not found."""
    lock_path = CLAUDE_DIR / "state" / "rsi" / "rsi-active.lock"
    if not lock_path.exists():
        return None
    try:
        data = lock_path.read_text().strip()
        parsed = json.loads(data)
        return parsed.get("session_id")
    except (json.JSONDecodeError, KeyError):
        return None


async def follow_session(steer: bool = False):
    """Auto-follow the active RSI session, reconnecting on session ID change."""
    print(f"{C_BOLD}Following RSI session from lock file...{C_RESET}")
    current_sid = None

    while True:
        new_sid = _read_rsi_lock()
        if not new_sid:
            print(f"{C_DIM}No RSI lock file or no session_id. Waiting...{C_RESET}")
            await asyncio.sleep(5)
            continue

        if new_sid != current_sid:
            if current_sid:
                print(f"\n{C_YELLOW}Session ID changed: {current_sid[:8]} -> {new_sid[:8]}{C_RESET}")
                print(f"{C_YELLOW}Reconnecting...{C_RESET}")
            current_sid = new_sid
            try:
                await connect_session(current_sid, steer=steer)
            except Exception as e:
                print(f"{C_RED}Connection lost: {e}{C_RESET}")
                print(f"{C_DIM}Will retry in 5s...{C_RESET}")
                await asyncio.sleep(5)
                current_sid = None  # Force reconnect on next loop
        else:
            await asyncio.sleep(5)


def main():
    parser = argparse.ArgumentParser(
        description="GRIP Session Monitor - connect to active Claude Code sessions"
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    sub.add_parser("list", help="List active sessions")

    conn = sub.add_parser("connect", help="Connect to a session")
    conn.add_argument("session_id", help="Session ID (or prefix)")
    conn.add_argument("--steer", action="store_true", help="Enable steering")
    conn.add_argument(
        "--remote", type=str, default=None, help="Remote IP (Tailscale)"
    )

    follow = sub.add_parser("follow", help="Auto-follow RSI session from lock file")
    follow.add_argument("--steer", action="store_true", help="Enable steering")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "list":
        asyncio.run(list_sessions())
    elif args.command == "connect":
        asyncio.run(connect_session(args.session_id, args.steer, args.remote))
    elif args.command == "follow":
        asyncio.run(follow_session(args.steer))


if __name__ == "__main__":
    main()
