#!/usr/bin/env python3
"""Rich TUI Session Monitor for GRIP pair-mode.

Provides a professional terminal UI with panels, syntax highlighting,
and live-updating event stream for cross-terminal pair programming.

Requires: rich, websockets
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

CLAUDE_DIR = Path.home() / ".claude"

# Max events to keep in scrollback
MAX_EVENTS = 200


class EventLog:
    """Scrollable event log with rich formatting."""

    def __init__(self):
        self.events: list[dict] = []
        self.show_reads = False

    def add(self, event: dict):
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            self.events.pop(0)

    def render(self, height: int = 30) -> Text:
        text = Text()
        visible = self.events[-(height - 2) :] if self.events else []

        for ev in visible:
            ev_type = ev.get("type", "")
            if ev_type == "event":
                self._render_tool_event(text, ev)
            elif ev_type == "heartbeat":
                pass  # Silent

        if not visible:
            text.append("  Waiting for events...\n", style="dim")

        return text

    def _render_tool_event(self, text: Text, ev: dict):
        tool = ev.get("event_type", "?")
        data = ev.get("data", {})
        output = ev.get("output_summary", "")
        ts = self._format_ts(ev.get("timestamp", ""))

        # Skip Read/Glob/Grep unless toggled on
        if tool in ("Read", "Glob", "Grep") and not self.show_reads:
            return

        # Timestamp
        text.append(f"  {ts} ", style="dim")

        # Tool name with color
        tool_styles = {
            "Bash": "bold green",
            "Write": "bold blue",
            "Edit": "bold cyan",
            "Skill": "bold magenta",
            "TeamCreate": "bold yellow",
            "SendMessage": "bold yellow",
            "Read": "dim",
            "Glob": "dim",
            "Grep": "dim",
        }
        style = tool_styles.get(tool, "bold")
        text.append(f"{tool:12s}", style=style)

        # Summary based on tool type
        if tool == "Bash":
            cmd = data.get("command", "")
            if len(cmd) > 100:
                cmd = cmd[:97] + "..."
            text.append(f" {cmd}", style="white")
        elif tool in ("Write", "Edit"):
            path = data.get("file_path", "")
            if path:
                fname = os.path.basename(path)
                text.append(f" {fname}", style="white")
                # Show diff info if available
                old = data.get("old_string", "")
                new = data.get("new_string", "")
                if old or new:
                    old_lines = old.count("\n") + 1 if old else 0
                    new_lines = new.count("\n") + 1 if new else 0
                    text.append(f"  ", style="dim")
                    if old_lines:
                        text.append(f"-{old_lines}", style="red")
                    if old_lines and new_lines:
                        text.append("/", style="dim")
                    if new_lines:
                        text.append(f"+{new_lines}", style="green")
        elif tool == "Skill":
            text.append(f" {data.get('skill', '')}", style="white")
        elif tool == "Read":
            path = data.get("file_path", "")
            if path:
                text.append(f" {os.path.basename(path)}", style="dim")
        else:
            summary = json.dumps(data)[:80] if data else ""
            text.append(f" {summary}", style="dim")

        text.append("\n")

        # Output preview (first 2 lines)
        if output and tool == "Bash":
            lines = output.strip().split("\n")
            for line in lines[:2]:
                preview = line[:120]
                text.append(f"               -> {preview}\n", style="dim")
            if len(lines) > 2:
                text.append(f"               ... ({len(lines)} lines total)\n", style="dim")

    @staticmethod
    def _format_ts(ts: str) -> str:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            return "??:??:??"


class PairMonitorTUI:
    """Rich TUI for pair-mode monitoring."""

    def __init__(self, session_id: str, steer: bool = False):
        self.session_id = session_id
        self.steer = steer
        self.console = Console()
        self.event_log = EventLog()
        self.connected = False
        self.steering_held = False
        self.monitor_count = 0
        self.session_info: dict = {}
        self._ws = None

    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="log", ratio=3),
            Layout(name="sidebar", ratio=1, minimum_size=25),
        )
        return layout

    def render_header(self) -> Panel:
        sid = self.session_id[:8]
        branch = self.session_info.get("branch", "?")
        uptime = self.session_info.get("uptime_display", "?")
        gen = self.session_info.get("generation", "?")
        project = self.session_info.get("project_name", "?")

        status = "[green]CONNECTED[/]" if self.connected else "[red]DISCONNECTED[/]"
        steer_status = "[yellow]STEERING[/]" if self.steering_held else "[dim]observe[/]"

        header_text = (
            f"[bold]GRIP Pair Monitor[/] | "
            f"[cyan]{sid}[/] | "
            f"{project} ({branch}) | "
            f"Gen {gen} | "
            f"Up {uptime} | "
            f"{status} | "
            f"{steer_status}"
        )
        return Panel(header_text, style="bold", height=3)

    def render_sidebar(self) -> Panel:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("key", style="dim")
        table.add_column("val")

        table.add_row("Session", self.session_id[:8])
        table.add_row("Branch", self.session_info.get("branch", "?"))
        table.add_row("Events", str(len(self.event_log.events)))
        table.add_row("Monitors", str(self.monitor_count))
        table.add_row("Steering", "HELD" if self.steering_held else "released")
        table.add_row("Reads", "shown" if self.event_log.show_reads else "hidden")

        return Panel(table, title="Status", border_style="dim")

    def render_footer(self) -> Panel:
        keys = "[dim]Ctrl+C[/] Exit"
        if self.steer and self.steering_held:
            keys += " | Type [yellow]inject: <text>[/] to steer"
        elif self.steer:
            keys += " | [dim]Steering expired - reconnect with --steer[/]"
        return Panel(keys, height=3, style="dim")

    def render_log(self) -> Panel:
        content = self.event_log.render(height=40)
        return Panel(content, title="Event Log", border_style="green")

    def update_layout(self, layout: Layout):
        layout["header"].update(self.render_header())
        layout["log"].update(self.render_log())
        layout["sidebar"].update(self.render_sidebar())
        layout["footer"].update(self.render_footer())

    def _print_stream_event(self, ev: dict):
        """Print a single event in Rich-formatted streaming mode (no Live)."""
        tool = ev.get("event_type", "?")
        data = ev.get("data", {})
        output = ev.get("output_summary", "")
        ts = EventLog._format_ts(ev.get("timestamp", ""))

        tool_styles = {
            "Bash": "bold green", "Write": "bold blue", "Edit": "bold cyan",
            "Skill": "bold magenta", "TeamCreate": "bold yellow",
            "SendMessage": "bold yellow", "Error": "bold red",
        }
        style = tool_styles.get(tool, "bold")

        # Build summary
        summary = ""
        if tool == "Bash":
            cmd = data.get("command", "")
            if len(cmd) > 100:
                cmd = cmd[:97] + "..."
            summary = cmd
        elif tool in ("Write", "Edit"):
            path = data.get("file_path", "")
            fname = os.path.basename(path) if path else "?"
            old = data.get("old_string", "")
            new = data.get("new_string", "")
            diff = ""
            if old or new:
                old_n = old.count("\n") + 1 if old else 0
                new_n = new.count("\n") + 1 if new else 0
                diff = f"  [red]-{old_n}[/]/[green]+{new_n}[/]" if old_n or new_n else ""
            summary = f"{fname}{diff}"
        elif tool == "Skill":
            summary = data.get("skill", "")
        elif tool == "Error":
            summary = data.get("reason", "?")
        else:
            summary = json.dumps(data)[:80] if data else ""

        self.console.print(f"  [dim]{ts}[/] [{style}]{tool:12s}[/] {summary}")

        # Output preview for Bash
        if output and tool == "Bash":
            lines = output.strip().split("\n")
            for line in lines[:3]:
                self.console.print(f"               [dim]-> {line[:120]}[/]")
            if len(lines) > 3:
                self.console.print(f"               [dim]... ({len(lines)} lines total)[/]")

    async def run(self):
        """Main entry point — resolve session and connect."""
        try:
            import websockets
        except ImportError:
            self.console.print("[red]websockets not installed. Run: pip install websockets[/]")
            return

        # Resolve session
        session = await self._resolve_session()
        if not session:
            self.console.print(f"[red]Session {self.session_id} not found[/]")
            return

        self.session_info = session
        port = session.get("tcp_port", 19000)
        url = f"ws://127.0.0.1:{port}"

        monitor_id = f"rich-{os.getpid()}"

        try:
            async with websockets.connect(url) as ws:
                self._ws = ws
                self.connected = True

                # Subscribe
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "monitor_id": monitor_id,
                    "events": [],
                }))

                # Acquire steering if requested
                if self.steer:
                    await ws.send(json.dumps({
                        "type": "acquire_steering",
                        "monitor_id": monitor_id,
                    }))

                # Start input reader for steering
                if self.steer:
                    asyncio.create_task(self._steering_input(ws, monitor_id))

                # Steer mode: streaming output (stdin-friendly)
                # Observe mode: Live panel display
                if self.steer:
                    await self._run_streaming(ws, monitor_id)
                else:
                    await self._run_live_panel(ws, monitor_id)

        except ConnectionRefusedError:
            self.console.print("[red]Connection refused. Is the control server running?[/]")
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            self.console.print("\n[dim]Disconnected[/]")
        finally:
            self.connected = False

    async def _run_streaming(self, ws, monitor_id: str):
        """Streaming mode — Rich-formatted but no Live (preserves stdin)."""
        sid = self.session_id[:8]
        branch = self.session_info.get("branch", "?")
        project = self.session_info.get("project_name", "?")

        self.console.print(Panel(
            f"[bold]GRIP Pair Monitor[/] | [cyan]{sid}[/] | {project} ({branch}) | [yellow]STEERING[/]",
            style="bold",
        ))
        self.console.print("[dim]  Type 'inject: <text>' to steer | Ctrl+C to exit[/]")
        self.console.print("─" * 70)

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            if msg_type == "event":
                self._print_stream_event(msg)
            elif msg_type == "steering_acquired":
                self.steering_held = True
                self.console.print("  [green bold]Steering acquired[/]")
            elif msg_type == "steering_denied":
                self.steering_held = False
                holder = msg.get("holder", "?")
                self.console.print(f"  [red]Steering denied (held by {holder})[/]")
            elif msg_type == "steer_ack":
                self.console.print("  [green]Steer command acknowledged[/]")
            elif msg_type == "error":
                self.console.print(f"  [red]Error: {msg.get('reason', '?')}[/]")

    async def _run_live_panel(self, ws, monitor_id: str):
        """Live panel mode — full TUI with auto-refresh (observe only)."""
        layout = self.make_layout()

        with Live(layout, console=self.console, refresh_per_second=4):
            self.update_layout(layout)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")
                if msg_type == "event":
                    self.event_log.add(msg)
                elif msg_type == "heartbeat":
                    self.monitor_count = msg.get("monitors", 0)
                    steering = msg.get("steering")
                    self.steering_held = (steering == monitor_id)
                elif msg_type == "steering_acquired":
                    self.steering_held = True
                elif msg_type == "steering_denied":
                    self.steering_held = False

                self.update_layout(layout)

    async def _resolve_session(self) -> dict | None:
        # The hyphenated session-lister.py was deleted in dcc92e559 (2026-03-06).
        lister = CLAUDE_DIR / "lib" / "session_lister.py"
        if not lister.exists():
            return None
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(lister),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        try:
            sessions = json.loads(stdout.decode())
        except json.JSONDecodeError:
            return None
        for s in sessions:
            if s["session_id"].startswith(self.session_id):
                return s
        return None

    async def _steering_input(self, ws, monitor_id: str):
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if line.startswith("inject:"):
                    text = line[7:].strip()
                    await ws.send(json.dumps({
                        "type": "steer_command",
                        "command_type": "inject_context",
                        "text": text,
                    }))
            except Exception:
                break


def main():
    parser = argparse.ArgumentParser(description="GRIP Rich Pair Monitor")
    sub = parser.add_subparsers(dest="command")
    conn = sub.add_parser("connect", help="Connect to a session")
    conn.add_argument("session_id", help="Session ID (or prefix)")
    conn.add_argument("--steer", action="store_true", help="Enable steering")

    args = parser.parse_args()
    if args.command == "connect":
        monitor = PairMonitorTUI(args.session_id, args.steer)
        asyncio.run(monitor.run())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
