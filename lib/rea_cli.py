"""Thin, read-only wrapper over the REA command line (npm ``rea-agents``).

Syscall-doctrine shape: every function takes an injectable runner so tests
need no REA on the machine, and the one live function (``tools``) speaks the
MCP wire directly to ``rea mcp`` because nothing in ``lib/`` already does.

Only surfaces verified on rea-agents 6.3.0 (2026-10-10) and 6.4.0 (2026-10-11)
are wrapped:

- ``rea doctor --json`` -- the readiness report. Measured: it exits 1 when any
  check is unhealthy (missing Hopper/Ghidra/IDA, registration drift) and STILL
  prints the full JSON report, so the exit code is not the error signal here;
  unparseable stdout is.
- ``npm ls -g rea-agents --json`` -- the installed version, with ``rea
  --version`` (prints the bare version, ``6.4.0``) as the fallback.
- ``rea mcp`` over stdio -- ``initialize`` / ``notifications/initialized`` /
  ``tools/list`` (protocol 2025-06-18); 139 tools on 6.3.0, 170 on 6.4.0.

No ``inspect()`` wrapper: the CLI has several inspection subcommands
(``inspect``, ``inspect-artifact``, ``analyze``, ``function``, ``decompile``)
with argument shapes this module has not verified, and guessing one would be
the placeholder ``rules/no-stubs.md`` forbids. Callers drive those through the
MCP tools, where the schema is advertised.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import grip_harness_picker as picker  # noqa: E402

PACKAGE = "rea-agents"
BINARY = "rea"
MCP_PROTOCOL_VERSION = "2025-06-18"


class ReaError(RuntimeError):
    """REA ran but did not produce the output this wrapper can read."""

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def _default_runner(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)


def rea_bin() -> str | None:
    """Path of the ``rea`` binary on PATH, or None when REA is not installed."""
    return shutil.which(BINARY)


def pinned_version() -> str:
    """The version GRIP verified against -- single-sourced in the harness registry."""
    return str(picker.install_spec(BINARY).get("version") or "")


def _parse_json(text: str, what: str, returncode: int | None) -> dict:
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise ReaError(f"{what} did not return JSON (exit {returncode}): {exc}", returncode) from exc
    if not isinstance(data, dict):
        raise ReaError(f"{what} returned {type(data).__name__}, expected an object", returncode)
    return data


def doctor(runner=_default_runner, timeout_s: float = 60.0) -> dict:
    """``rea doctor --json`` as a dict. Exit 1 with a JSON report is a healthy
    wrapper outcome (the report says what is unhealthy); non-JSON is the error,
    and so is a runner that cannot run at all (binary absent, timeout). Both
    surface as ``ReaError`` so one except clause means "no report" -- callers
    that caught ReaError alone died on FileNotFoundError on exactly the hosts
    the readiness check exists for (Codex review of GRIP#7967)."""
    try:
        proc = runner([BINARY, "doctor", "--json"], timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReaError(f"rea doctor --json could not run: {exc}", None) from exc
    return _parse_json(proc.stdout, "rea doctor --json", proc.returncode)


def installed_version(runner=_default_runner, timeout_s: float = 60.0) -> str | None:
    """Installed ``rea-agents`` version: npm's global ledger first, then
    ``rea --version``; None when neither answers."""
    try:
        proc = runner(["npm", "ls", "-g", PACKAGE, "--json"], timeout_s)
        version = (json.loads(proc.stdout or "{}").get("dependencies") or {}).get(PACKAGE, {}).get("version")
        if version:
            return str(version)
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, AttributeError):
        pass
    try:
        proc = runner([BINARY, "--version"], timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    token = (proc.stdout or "").strip().split()
    return token[0] if proc.returncode == 0 and token else None


def version_drift(runner=_default_runner) -> dict:
    """Installed vs pinned, as a small dict a status line can print."""
    installed = installed_version(runner)
    pinned = pinned_version()
    return {"installed": installed, "pinned": pinned,
            "drift": None if installed is None else installed != pinned}


def _send(proc, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _line_reader(proc) -> "queue.Queue[str | None]":
    """Feed stdout lines into a queue from a daemon thread so a reader can wait
    with a deadline; ``None`` marks end of stream. A blocking ``readline`` in
    the caller's thread would ignore every timeout (Codex P2 on GRIP#7964)."""
    lines: "queue.Queue[str | None]" = queue.Queue()

    def pump():
        try:
            for line in iter(proc.stdout.readline, ""):
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return lines


def _next_line(lines, deadline: float, want_id: int, proc) -> str:
    """One line from the queue before the deadline, or ``ReaError``."""
    remaining = deadline - time.monotonic()
    try:
        line = lines.get(timeout=max(remaining, 0.0))
    except queue.Empty:
        raise ReaError(f"rea mcp did not answer request {want_id} in time", proc.poll()) from None
    if line is None:
        raise ReaError("rea mcp closed its stdout before answering", proc.poll())
    return line


def _recv(lines, want_id: int, deadline: float, proc) -> dict:
    """Read frames until the response with ``want_id`` arrives; notifications
    and unparseable lines are skipped; a closed pipe or a passed deadline is an
    error."""
    while True:
        line = _next_line(lines, deadline, want_id, proc)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            if "error" in msg:
                raise ReaError(f"rea mcp error: {msg['error']}", None)
            return msg.get("result") or {}


def tools(popen=subprocess.Popen, timeout_s: float = 60.0) -> list[dict]:
    """The MCP ``tools/list`` of a fresh ``rea mcp`` -- name, description,
    inputSchema, annotations for every tool. ``popen`` is injectable so tests
    feed canned frames. ``timeout_s`` bounds the WHOLE exchange: a server that
    stays alive but never answers raises ``ReaError`` instead of hanging."""
    proc = popen([BINARY, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_s
    try:
        lines = _line_reader(proc)
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "grip-rea-cli", "version": "1"}}})
        _recv(lines, 1, deadline, proc)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = _recv(lines, 2, deadline, proc)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, AttributeError):
            pass
    listed = result.get("tools")
    if not isinstance(listed, list):
        raise ReaError("rea mcp tools/list carried no tools array", None)
    return listed


def tool_names(popen=subprocess.Popen) -> list[str]:
    """Just the names, sorted -- what the policy classification is checked against."""
    return sorted(str(t.get("name", "")) for t in tools(popen) if t.get("name"))
