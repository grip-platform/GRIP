"""Thin, read-only wrapper over the grip-browser install (stealth-browser-mcp).

The sibling of ``lib/rea_cli.py`` for the second tool server GRIP attaches.
Syscall-doctrine shape: every function takes an injectable runner or ``popen``
so tests need no install on the machine, and the install's own facts come from
the filesystem the installer wrote (``lib/grip_tools_dir.py``), never from a
recollection of them.

Only surfaces verified at the pinned sha (ba95cca9, 2026-10-18) are wrapped:

- ``src/server.py --list-sections`` prints ``  <section>: <text> (<N> tools)``
  per section, 11 lines summing to 101, and exits 0. Importing the server
  constructs upstream's ``ProcessCleanup``, which reads
  ``~/.stealth_browser_pids.json`` and kills the PIDs it finds tracked, so every
  diagnostic run here uses a THROWAWAY ``HOME``.
- the MCP wire over stdio through the GRIP launcher
  (``mcp-servers/grip-browser/start.sh``), so what is listed is what a session
  would attach under the same environment.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import grip_harness_picker as picker  # noqa: E402
import grip_tools_dir  # noqa: E402
import grip_browser_surface as surface  # noqa: E402
import mcp_stdio_client  # noqa: E402

NAME = surface.SERVER_KEY
TOOL = surface.TOOL
REPO_ROOT = _LIB.parent
LAUNCHER = REPO_ROOT / "mcp-servers" / "grip-browser" / "start.sh"
SECTION_LINE = re.compile(r"^\s+([a-z-]+):\s.*\((\d+) tools\)\s*$")


class GripBrowserError(RuntimeError):
    """The install ran but did not produce the output this wrapper can read."""

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def _default_runner(argv, timeout_s: float, env: dict | None = None,
                    cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                          env=env, cwd=cwd)


def pinned_sha() -> str:
    """The sha GRIP verified against -- single-sourced in the harness registry."""
    return str(picker.install_spec(NAME).get("sha") or "")


def pinned_version() -> str:
    return str(picker.install_spec(NAME).get("version") or "")


def install_dir(env: dict | None = None) -> Path:
    return surface.install_dir(dict(os.environ if env is None else env))


def installed_sha(env: dict | None = None) -> str | None:
    """The sha the installer recorded at the active install, or None."""
    return grip_tools_dir.read_pin(install_dir(env))


def sha_drift(env: dict | None = None) -> dict:
    """Installed vs pinned, as a small dict a status line can print."""
    installed = installed_sha(env)
    pinned = pinned_sha()
    return {"installed": installed, "pinned": pinned,
            "drift": None if installed is None else installed != pinned}


def parse_sections(text: str) -> dict[str, int]:
    """``--list-sections`` stdout as ``{section: tool count}``; raises on a
    text that carries no section line."""
    found = {}
    for line in (text or "").splitlines():
        m = SECTION_LINE.match(line)
        if m:
            found[m.group(1)] = int(m.group(2))
    if not found:
        raise GripBrowserError("--list-sections printed no section line", None)
    return found


def list_sections(runner=_default_runner, env: dict | None = None,
                  timeout_s: float = 60.0) -> dict[str, int]:
    """Run the installed server's ``--list-sections`` under a throwaway HOME."""
    environ = dict(os.environ if env is None else env)
    where = install_dir(environ)
    python = grip_tools_dir.venv_python(where)
    server = where / "repo" / "src" / "server.py"
    if python is None or not server.is_file():
        raise GripBrowserError(f"{NAME} is not installed at {where}; {surface.INSTALL_HINT}", None)
    with tempfile.TemporaryDirectory(prefix="grip-browser-home-") as home:
        child = {k: v for k, v in environ.items() if k not in surface.UNSET_IN_CHILD}
        child["HOME"] = home
        try:
            proc = runner([str(python), str(server), "--list-sections"], timeout_s,
                          env=child, cwd=home)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GripBrowserError(f"--list-sections could not run: {exc}", None) from exc
    if proc.returncode != 0:
        raise GripBrowserError(f"--list-sections exited {proc.returncode}: "
                               f"{(proc.stderr or '').strip()[-300:]}", proc.returncode)
    return parse_sections(proc.stdout)


def tools(popen=subprocess.Popen, env: dict | None = None,
          timeout_s: float = 90.0) -> list[dict]:
    """The MCP ``tools/list`` of a fresh server started through the GRIP
    launcher under ``env`` (so the surface variables apply)."""
    environ = dict(os.environ if env is None else env)
    try:
        return mcp_stdio_client.list_tools(
            ["bash", str(LAUNCHER)], env=environ, cwd=str(REPO_ROOT), popen=popen,
            timeout_s=timeout_s, client_name="grip-browser-cli")
    except mcp_stdio_client.McpStdioError as exc:
        raise GripBrowserError(str(exc), exc.returncode) from exc


def tool_names(popen=subprocess.Popen, env: dict | None = None) -> list[str]:
    """Just the names, sorted -- what the policy classification is checked against."""
    return sorted(str(t.get("name", "")) for t in tools(popen, env) if t.get("name"))


def schema_chars(listed: list[dict]) -> int:
    """A Claude-Code-independent measure of what a surface costs: the characters
    of every tool's name, description and input schema, serialised compactly."""
    import json
    return sum(len(json.dumps({"name": t.get("name"), "description": t.get("description"),
                               "inputSchema": t.get("inputSchema")}, separators=(",", ":")))
               for t in listed)


def main(argv: list[str]) -> int:
    """``status`` (pin, install, drift), ``sections``, ``tools`` (names), ``cost``."""
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "status":
            drift = sha_drift()
            print(f"pinned:    {drift['pinned']} ({pinned_version()})")
            print(f"installed: {drift['installed'] or 'absent'} at {install_dir()}")
            print(f"drift:     {drift['drift']}")
            return 0 if drift["drift"] is False else 1
        if cmd == "sections":
            found = list_sections()
            for section, count in found.items():
                print(f"{section:22} {count}")
            print(f"{'total':22} {sum(found.values())}")
            return 0
        if cmd == "tools":
            print("\n".join(tool_names()))
            return 0
        if cmd == "cost":
            listed = tools()
            print(f"tools: {len(listed)}  schema_chars: {schema_chars(listed)}")
            return 0
    except GripBrowserError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("usage: grip_browser_cli.py status|sections|tools|cost", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
