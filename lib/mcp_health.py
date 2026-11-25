"""Live MCP handshake shim (syscall-doctrine) over `claude mcp list`.

The verify FLOOR for GRIP activation: a real per-server connect probe, because
the #3896 failure class is "node_modules present but the server won't boot" —
exactly what a filesystem check passes and a handshake catches. `claude mcp
list` performs a fresh connect per configured server and reports its status.

DIP: the command runner is injectable so tests exercise the parser + classifier
with zero network and zero `claude` binary.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable

CONNECTED = "connected"
FAILED = "failed"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServerHealth:
    """Normalised health of one MCP server from a handshake probe."""

    name: str
    status: str  # CONNECTED | FAILED | UNKNOWN
    detail: str = ""  # verbatim status text, e.g. "Connected" / "Failed to connect"

    @property
    def ok(self) -> bool:
        return self.status == CONNECTED


def _classify(status_text: str) -> str:
    """Map a verbatim status tail to a normalised status. Order matters:
    'failed to connect' contains 'connect', so test FAIL before CONNECT."""
    low = status_text.lower()
    if "fail" in low:
        return FAILED
    if "connected" in low:
        return CONNECTED
    return UNKNOWN


def parse_mcp_list(output: str) -> dict[str, ServerHealth]:
    """Pure parser: `claude mcp list` text -> {name: ServerHealth}.

    Line shape: ``<name>: <command> - <status>``. The command may itself contain
    ' - ' and ':' (URLs), so the name is split on the FIRST ': ' and the status
    on the LAST ' - '.
    """
    out: dict[str, ServerHealth] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if ": " not in line or " - " not in line:
            continue
        name, rest = line.split(": ", 1)
        status_text = rest.rsplit(" - ", 1)[-1].strip()
        out[name] = ServerHealth(name, _classify(status_text), status_text)
    return out


def _default_runner(timeout: float) -> str:
    # Resolve the launcher rather than trusting CreateProcess to find it.
    #
    # npm installs `claude` on Windows as claude.cmd / claude.ps1 with NO
    # extensionless executable. subprocess.run does not go through a shell, and
    # CreateProcess does not apply PATHEXT, so subprocess.run(["claude", ...])
    # raises FileNotFoundError [WinError 2] on every Windows machine. check_health
    # then fails open to UNKNOWN — correct, and never a false green — but the
    # consequence is that EVERY MCP server reads OFFLINE forever, so the
    # ACTIVATION banner could never be cleared and `grip-doctor --fix` looped
    # against a probe that had not run at all.
    #
    # shutil.which does apply PATHEXT, returning the runnable claude.CMD. On POSIX
    # it resolves to exactly what the bare name would have, so that platform's
    # behaviour is unchanged. The `or "claude"` keeps the original argv when the
    # launcher is genuinely absent, preserving the OSError the caller expects.
    exe = shutil.which("claude") or "claude"
    # run_text, not subprocess.run(text=True): `claude mcp list` marks each
    # server with a status glyph, and parse_mcp_list reads those markers. A
    # cp1252 read would garble them into non-matches and report every server
    # OFFLINE on a machine where they are all connected -- the same false-red
    # the timeout bug above already caused once.
    from _compat import run_text
    proc = run_text(
        [exe, "mcp", "list"],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return proc.stdout


def check_health(
    servers: Iterable[str] | None = None,
    *,
    runner: Callable[[float], str] = _default_runner,
    # 20s was too short and failed SILENTLY. `claude mcp list` opens a fresh
    # connection to EVERY configured server, so its cost scales with how many
    # there are - not with how many the caller asked about. Measured 2026-08-05:
    # 44.1s for 15 servers, most of them remote HTTP (Slack, Gmail, Drive, Zoom,
    # Notion). The old default expired, TimeoutExpired was caught below, and the
    # whole probe fail-opened to {} - rendering as "every server is OFFLINE" on a
    # machine where all five GRIP servers were connected.
    # This value is a headroom figure, not a measurement: it must exceed the
    # slowest realistic probe, and being generous costs nothing because the call
    # returns as soon as the command does.
    timeout: float = 120.0,
) -> dict[str, ServerHealth]:
    """Return {name: ServerHealth} for the configured MCP servers.

    Fail-open toward UNKNOWN, never toward a false green: if the probe cannot run
    (no `claude` on PATH, timeout, subprocess error, undecodable output) this
    returns ``{}`` and the caller treats every server as unverified. ``servers``,
    if given, filters the result to that allowlist.

    ``runner`` is annotated as returning ``str`` because that is what a correct
    runner returns, and the annotation is the contract an implementer should
    write to. The non-``str`` guard below is defence against that contract being
    broken by the platform rather than by the caller -- subprocess hands back
    ``None`` for a stream whose reader thread died -- so it is deliberately NOT
    reflected as ``Optional[str]`` in the signature, which would read as
    permission to return ``None``.
    """
    try:
        output = runner(timeout)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        # UnicodeDecodeError is NEITHER an OSError nor a SubprocessError, so it
        # escaped this handler entirely and crashed grip-doctor outright,
        # contradicting the contract three lines up. #4674 pinned the default
        # runner's decoder, so that runner can no longer raise it; `runner` is an
        # injectable seam this module's docstring advertises, and one passing
        # errors="strict" raises it still.
        #
        # Named exactly, not as its ValueError base: a bare ValueError would also
        # swallow an ordinary logic bug inside an injected runner -- a bad int(),
        # a mis-built argv -- and silently downgrade it to "unverified". Only a
        # decode fault means "the probe could not be read"; everything else is a
        # defect the caller needs to see. (AGORA #4691, Speaker C.)
        return {}
    if not isinstance(output, str):
        # A probe that returned nothing is a probe that did not run. subprocess
        # yields None for a stream whose reader thread died, and the old code fed
        # that straight into str.splitlines() -- surfacing as
        # "AttributeError: 'NoneType' object has no attribute 'splitlines'"
        # three frames from the decode fault that caused it (A>>, 2026-08-11).
        # Honour the documented contract and fail open to UNKNOWN instead.
        return {}
    parsed = parse_mcp_list(output)
    if servers is None:
        return parsed
    allow = set(servers)
    return {k: v for k, v in parsed.items() if k in allow}
