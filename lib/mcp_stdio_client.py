"""A minimal MCP stdio client: start a server, ``initialize``, list its tools.

Extracted at N=2 (``lib/rea_cli.tools`` was the first copy; grip-browser is the
second) so a tool server's advertised catalogue can be read the same way
whichever server it is. The wire is JSON-RPC 2.0 over newline-delimited stdio,
protocol ``2025-06-18``: ``initialize`` -> ``notifications/initialized`` ->
``tools/list``. Nothing else is spoken.

``popen`` is injectable so tests feed canned frames; ``timeout_s`` bounds the
WHOLE exchange, because a server that stays alive and never answers would
otherwise hang the caller (the lesson ``rea_cli`` learnt from a Codex P2). A
blocking ``readline`` in the caller's thread would ignore every deadline, so
stdout is pumped through a queue from a daemon thread.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

MCP_PROTOCOL_VERSION = "2025-06-18"


class McpStdioError(RuntimeError):
    """The server ran but did not complete the exchange this client speaks."""

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def _send(proc, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _line_reader(proc) -> "queue.Queue[str | None]":
    lines: "queue.Queue[str | None]" = queue.Queue()

    def pump():
        try:
            for line in iter(proc.stdout.readline, ""):
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return lines


def _next_line(lines, deadline: float, want_id: int, proc, what: str) -> str:
    remaining = deadline - time.monotonic()
    try:
        line = lines.get(timeout=max(remaining, 0.0))
    except queue.Empty:
        raise McpStdioError(f"{what} did not answer request {want_id} in time",
                            proc.poll()) from None
    if line is None:
        raise McpStdioError(f"{what} closed its stdout before answering", proc.poll())
    return line


def _recv(lines, want_id: int, deadline: float, proc, what: str) -> dict:
    """Frames until the response carrying ``want_id``; notifications and
    unparseable lines are skipped; a closed pipe or a passed deadline raises."""
    while True:
        line = _next_line(lines, deadline, want_id, proc, what)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            if "error" in msg:
                raise McpStdioError(f"{what} error: {msg['error']}", None)
            return msg.get("result") or {}


def list_tools(argv: list[str], *, env: dict | None = None, cwd: str | None = None,
               popen=subprocess.Popen, timeout_s: float = 60.0,
               client_name: str = "grip-mcp-stdio-client") -> list[dict]:
    """``tools/list`` of a fresh server started with ``argv``: name, description,
    inputSchema and annotations for every tool. Terminates the server afterwards."""
    what = argv[0] if argv else "mcp server"
    proc = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
    deadline = time.monotonic() + timeout_s
    try:
        lines = _line_reader(proc)
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1"}}})
        _recv(lines, 1, deadline, proc, what)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = _recv(lines, 2, deadline, proc, what)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, AttributeError):
            pass
    listed = result.get("tools")
    if not isinstance(listed, list):
        raise McpStdioError(f"{what} tools/list carried no tools array", None)
    return listed
