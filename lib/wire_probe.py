#!/usr/bin/env python3
"""wire_probe.py -- InfiniteMachine v2, Step 1: the wire probe harness.

A thin, auditable harness that asks ONE question at a time about how Claude Code
behaves when its traffic is re-routed. It arms the session four ways and runs a
named case under that arm, returning a structured verdict with redacted evidence.

Arms (``--arm``):
  wire       HTTPS_PROXY to HAL's real wire on 127.0.0.1:4011 with its per-host CA
             (GRIP_WIRE_URL / GRIP_WIRE_CA override the defaults).
  base-url   ANTHROPIC_BASE_URL=http://127.0.0.1:4010 (the HAL gateway). Today
             this REFUSES Remote Control -- Claude Code 2.1.288 logs "Remote
             Control is only available when using Claude via api.anthropic.com"
             and "--rc flag ignored". That refusal is the expected result, not a
             harness failure.
  direct     no proxy, no base URL -- the control arm.
  tunnel     HTTPS_PROXY pointing at a blind local CONNECT proxy; no decryption.
  intercept  HTTPS_PROXY + NODE_EXTRA_CA_CERTS carrying a throwaway, name-
             constrained CA that decrypts ONLY api.anthropic.com.

Cases (``--case``) each return ``outcome`` in
{pass, fail, refused, skipped, needs-operator} plus evidence lines:
  rc-connects         Remote Control reaches ``handleStateChange state=connected``.
  rc-archive-on-exit  on termination the session archives (``archive=200``), not
                      ``failed: timeout of 599ms exceeded`` (intercept failed this
                      on 2026-10-04).
  phone-message       waits for the operator to type on the phone; needs-operator
                      when non-interactive.
  stream-timing       a streamed ``claude -p`` turn, per-event timestamps, arm vs
                      direct.
  voice-menu          does ``/voice`` appear in the command menu under this arm.
  proxy-death         kill the proxy mid-stream; expect a recoverable interruption.
  hold-tolerance      a local fake upstream answers 200 and sends only SSE pings
                      for N seconds; measure how long Claude Code waits (H-HOLD-1).

Login isolation: rc-connects uses the operator's REAL ``CLAUDE_CONFIG_DIR``,
because it verifies Remote Control under the canonical login and device-trust
state. Every other case runs against a throwaway COPY of the login files, so a
probe run never mutates the operator's real config.

Safety: tokens, bearers, and session ids are redacted before any line is
surfaced (``redact``). The intercept CA's key and certificate live in a per-run
temporary directory deleted on exit; nothing is added to any system trust store.
The four analysers (``rc_state``, ``archive_outcome``, ``proxy_saw``,
``hold_tolerance``) are pure functions over log text so they are unit-testable
on recorded fixtures with no live network.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

GATEWAY_URL = "http://127.0.0.1:4010"
TARGET = "api.anthropic.com"
WAIT_S = 35
ARMS = ("base-url", "direct", "tunnel", "intercept", "wire")
# The wire arm uses HAL's real wire (lib/hal/wire_proxy.py, Step 2) instead of
# the throwaway proxy: the conventions fixed by the plan, overridable by env.
WIRE_URL = os.environ.get("GRIP_WIRE_URL", "http://127.0.0.1:4011")
WIRE_CA = os.environ.get("GRIP_WIRE_CA") or str(
    Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))
    / "state" / "wire-ca" / "hal-wire-ca.pem")
PROXY_ARMS = ("tunnel", "intercept")
REAL_LOGIN_CASES = ("rc-connects",)
LIVE_ENV = "GRIP_WIRE_PROBE_LIVE"

_KEEP = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "TMPDIR", "SHELL")
_ANSI = re.compile(
    rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]"
)
_REQ = re.compile(
    r"^\s*(?:->|<-)?\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)"
)
_ARCHIVE_FAIL = re.compile(r"Archive \S+ failed: timeout of (\d+)ms exceeded")
_ARCHIVE_OK = re.compile(r"Torn down \(archive=200\)|Archive \S+ status=200")
_TERMINAL = frozenset({"timeout", "retry", "abort", "error", "close", "exit"})

_REDACT = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"), "sk-ant-<redacted>"),
    (re.compile(r"(Bearer\s+)\S+"), r"\1<redacted>"),
    (re.compile(r"sessionId=\S+"), "sessionId=[REDACTED]"),
    (re.compile(r"cse_[A-Za-z0-9]+"), "cse_<id>"),
    (re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    ), "<uuid>"),
    (re.compile(r"[A-Za-z0-9_-]{40,}"), "<redacted>"),
)


# --------------------------------------------------------------------------- #
# Redaction + pure analysers (no I/O, no network -- unit-testable).
# --------------------------------------------------------------------------- #
def strip_ansi(raw: bytes) -> str:
    """Decode PTY bytes and remove ANSI escape sequences."""
    return _ANSI.sub(b"", raw).decode("utf-8", "replace")


def redact(text: str) -> str:
    """Mask tokens, bearers, session ids and uuids before any line is shown."""
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def rc_state(log: str) -> str:
    """Remote Control's state from a session's log: connected / refused / absent.

    'refused' is checked first: the explicit "--rc flag ignored" message means
    Remote Control was never attempted, so it is definitive over everything else.
    """
    if "--rc flag ignored" in log or "only available when using Claude" in log:
        return "refused"
    if "handleStateChange state=connected" in log:
        return "connected"
    return "absent"


def archive_outcome(log: str) -> str:
    """How the session archived on exit: 'ok', 'timeout:<N>ms', or 'absent'.

    The failure is matched first because it is definitive; the two never co-occur
    in a real run. Removing the matched line flips the verdict to 'absent'.
    """
    failed = _ARCHIVE_FAIL.search(log)
    if failed:
        return f"timeout:{failed.group(1)}ms"
    if _ARCHIVE_OK.search(log):
        return "ok"
    return "absent"


def proxy_saw(paths_out: str) -> set[str]:
    """The HTTP request lines ('<METHOD> <path>') a proxy log recorded.

    Response status lines ('HTTP/1.1 200 OK') never match -- HTTP is not a
    method -- so only real requests are returned.
    """
    seen: set[str] = set()
    for line in paths_out.splitlines():
        match = _REQ.match(line)
        if match:
            seen.add(f"{match.group(1)} {match.group(2)}")
    return seen


def _per_connection(events: list[dict]) -> dict[str, dict]:
    """Group events by connection; a legacy list without ``conn`` is one connection."""
    groups: dict[str, dict] = {}
    for event in events:
        g = groups.setdefault(str(event.get("conn", "0")),
                              {"pings": 0, "held_s": 0.0, "terminal": None,
                               "path": event.get("path", "")})
        if event.get("kind") == "ping":
            g["pings"] += 1
        g["held_s"] = max(g["held_s"], float(event["t"]))
        if event.get("kind") == "deadline":
            g["still_waiting"] = True
        elif event.get("kind") in _TERMINAL and g["terminal"] is None \
                and not g.get("still_waiting"):
            g["terminal"] = event["kind"]
            g["held_s"] = float(event["t"])
    return groups


def hold_tolerance(events: list[dict]) -> dict:
    """How long the client held a ping-only stream before giving up.

    ``events`` is an ordered list of ``{"t": seconds, "kind": str}``, each
    optionally tagged with ``conn`` (a connection id) and ``path`` (its request
    line). A client opens several connections (quota check, retries, the turn
    itself), so the tolerance is measured PER CONNECTION and the longest-held one
    is reported, with the number of connections seen. Measured 2026-10-05: the
    pooled version read 1.0 s from a quota check's close while the turn's
    connection was still being held; it was the instrument, not the client.
    """
    groups = _per_connection(events)
    if not groups:
        return {"held_s": 0.0, "terminal": None, "pings": 0, "connections": 0}
    longest = max(groups.values(), key=lambda g: g["held_s"])
    return {"held_s": longest["held_s"], "terminal": longest["terminal"],
            "pings": longest["pings"], "connections": len(groups),
            "path": longest["path"]}


# --------------------------------------------------------------------------- #
# Result type + environment / arm wiring.
# --------------------------------------------------------------------------- #
@dataclass
class CaseResult:
    case: str
    arm: str
    outcome: str
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"case": self.case, "arm": self.arm, "outcome": self.outcome,
                "evidence": self.evidence}


def claude_bin() -> str | None:
    """The claude executable, or None if it is not installed.

    ``CLAUDE_CODE_EXECPATH`` wins only while it still exists: Claude Code exports
    it into every session it runs, and a cask upgrade deletes that build's
    directory, so a long-lived session carries a path to a binary that is gone
    (measured 2026-10-05: 2.1.288 in the environment, 2.1.289 on disk).
    """
    exported = os.environ.get("CLAUDE_CODE_EXECPATH")
    if exported and Path(exported).is_file():
        return exported
    return shutil.which("claude")


def arm_env(extra: dict[str, str]) -> dict[str, str]:
    """A minimal environment for the probed child process.

    Preserves ONLY ``_KEEP`` -- HOME, USER, LOGNAME, PATH, LANG, TMPDIR, SHELL
    -- from the caller's own environment; nothing else inherited, so a stray
    secret sitting in the operator's shell never reaches the probe. Always
    SETS ``TERM=xterm-256color`` and ``ENABLE_CLAUDEAI_MCP_SERVERS=false``,
    then layers ``extra`` on top of those two. ``ANTHROPIC_API_KEY`` is then
    stripped unconditionally, even if ``extra`` itself carried one -- a probe
    must never present the paid API key, so this fails closed rather than
    trusting every caller to omit it.
    """
    env = {k: os.environ[k] for k in _KEEP if k in os.environ}
    env.update({"TERM": "xterm-256color", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
    env.update(extra)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def proxy_env(url: str, ca_path: str | None = None) -> dict[str, str]:
    """HTTPS_PROXY (all four spellings) and, for intercept, the extra CA."""
    env = {k: url for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy",
                            "http_proxy")}
    if ca_path:
        env["NODE_EXTRA_CA_CERTS"] = ca_path
    return env


def trusted_cwd() -> str:
    """A directory Claude Code already trusts, so no trust dialog blocks a probe."""
    for name in (".grip", ".claude"):
        path = Path.home() / name
        if path.is_dir():
            return str(path)
    return os.getcwd()


def _first_existing(paths: list[Path]) -> Path | None:
    """The first path in the list that is a regular file, else None."""
    for path in paths:
        if path.is_file():
            return path
    return None


def login_dir(case: str, tmp: Path) -> str:
    """The CLAUDE_CONFIG_DIR for a case.

    rc-connects gets the operator's real config (canonical login + device
    trust). Every other case gets a throwaway copy of just the login files, so a
    probe run never writes to the operator's real config.
    """
    real = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    if case in REAL_LOGIN_CASES:
        return str(real)
    found = {name: _first_existing([real / name, Path.home() / name])
             for name in (".claude.json", ".credentials.json")}
    if not found[".credentials.json"]:
        # No file-based login to copy: on macOS the login lives in the Keychain,
        # and a throwaway copy without it starts signed out. Measured 2026-10-05:
        # the copy read Remote Control as "refused" and a -p turn produced no
        # stream, while the real directory connected and streamed.
        return str(real)
    copy = tmp / "config"
    copy.mkdir(parents=True, exist_ok=True)
    for name, src in found.items():
        if src:
            shutil.copy2(src, copy / name)
    return str(copy)


# --------------------------------------------------------------------------- #
# Throwaway name-constrained CA + decrypting CONNECT proxy (intercept / tunnel).
# --------------------------------------------------------------------------- #
def make_certs(out: Path) -> tuple[str, str, str]:
    """A CA that may only ever vouch for api.anthropic.com, plus one leaf.

    cryptography is imported lazily so the module itself imports on a bare
    runner (pytest + pyyaml) that has no cryptography; only the live intercept
    arm needs it.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def _name(cn: str):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def _base(subject: str, issuer, key):
        return (x509.CertificateBuilder().subject_name(_name(subject))
                .issuer_name(issuer).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(hours=2)))

    ca_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    usage = x509.KeyUsage(digital_signature=True, key_cert_sign=True,
                          crl_sign=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, encipher_only=False,
                          decipher_only=False)
    ca = (_base("GRIP probe CA (throwaway)", _name("GRIP probe CA (throwaway)"),
                ca_key)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.NameConstraints([x509.DNSName(TARGET)], None), True)
          .add_extension(usage, True).sign(ca_key, hashes.SHA256()))
    leaf = (_base(TARGET, ca.subject, leaf_key)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(TARGET)]),
                           False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    blobs = {
        "ca.pem": ca.public_bytes(pem),
        "leaf.pem": leaf.public_bytes(pem),
        "leaf.key": leaf_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    }
    for filename, blob in blobs.items():
        (out / filename).write_bytes(blob)
    return str(out / "ca.pem"), str(out / "leaf.pem"), str(out / "leaf.key")


_CRLF = b"\r\n"
_PROXY_REQ = re.compile(rb"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+) HTTP")


def _close(*socks) -> None:
    for sock in socks:
        try:
            sock.close()
        except OSError:
            pass


def _read_head(conn: socket.socket) -> bytes:
    head = b""
    while _CRLF + _CRLF not in head:
        chunk = conn.recv(4096)
        if not chunk:
            break
        head += chunk
    return head


class Proxy:
    """A local CONNECT proxy. With certs it decrypts ONLY api.anthropic.com."""

    def __init__(self, certs: tuple[str, str, str] | None):
        self.log: list[str] = []
        self._server = self._client = None
        if certs:
            self._server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._server.load_cert_chain(certs[1], certs[2])
            self._server.set_alpn_protocols(["http/1.1"])
            self._client = ssl.create_default_context()
            self._client.set_alpn_protocols(["http/1.1"])

    def start(self) -> int:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        threading.Thread(target=self._serve, daemon=True).start()
        return self.sock.getsockname()[1]

    def stop(self) -> None:
        _close(self.sock)

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            self._dispatch(conn)
        except Exception as exc:  # report, never crash the probe
            self.log.append(f"error {type(exc).__name__}: {str(exc)[:80]}")
            _close(conn)

    def _dispatch(self, conn: socket.socket) -> None:
        parts = _read_head(conn).split(_CRLF, 1)[0].split(b" ")
        if len(parts) < 2 or parts[0] != b"CONNECT":
            conn.sendall(b"HTTP/1.1 405 Method Not Allowed" + _CRLF + _CRLF)
            return _close(conn)
        host, port = parts[1].decode().rsplit(":", 1)
        self.log.append(f"CONNECT {host}:{port}")
        conn.sendall(b"HTTP/1.1 200 Connection Established" + _CRLF + _CRLF)
        if self._server and host == TARGET:
            return self._mitm(conn, host, int(port))
        upstream = socket.create_connection((host, int(port)), timeout=15)
        upstream.settimeout(None)
        self._relay(conn, upstream)

    def _mitm(self, conn: socket.socket, host: str, port: int) -> None:
        client = self._server.wrap_socket(conn, server_side=True)
        raw = socket.create_connection((host, port), timeout=15)
        raw.settimeout(None)
        upstream = self._client.wrap_socket(raw, server_hostname=host)
        self.log.append(f"intercepted {host}")
        self._relay(client, upstream, self._tap)

    def _relay(self, left, right, tap=None) -> None:
        threading.Thread(target=self._pump, args=(right, left, tap, "<-"),
                         daemon=True).start()
        self._pump(left, right, tap, "->")

    def _pump(self, src, dst, tap, label: str) -> None:
        try:
            data = src.recv(65536)
            while data:
                if tap:
                    tap(label, data)
                dst.sendall(data)
                data = src.recv(65536)
        except (OSError, ssl.SSLError):
            pass
        finally:
            _close(src, dst)

    def _tap(self, label: str, data: bytes) -> None:
        match = _PROXY_REQ.match(data)
        if match:
            path = match.group(2).split(b"?")[0].decode("ascii", "replace")[:70]
            self.log.append(f"  {label} {match.group(1).decode()} {path}")
        elif data.startswith(b"HTTP/1.1 "):
            head = data.split(_CRLF, 1)[0].decode("ascii", "replace")[:40]
            self.log.append(f"  {label} {head}")


# --------------------------------------------------------------------------- #
# Fake upstream for hold-tolerance: answers 200, then SSE pings only.
# --------------------------------------------------------------------------- #
class HoldUpstream:
    """Answers 200 text/event-stream and sends ``event: ping`` for N seconds.

    It records a 'ping' event per ping and a 'close' event (with its elapsed
    time) the moment the client disconnects -- which IS the client's tolerance,
    observed at the source rather than parsed from a log.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.events: list[dict] = []
        self._stop = False

    def start(self) -> int:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()
        return self.sock.getsockname()[1]

    def stop(self) -> None:
        self._stop = True
        _close(self.sock)

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._hold, args=(conn,),
                             daemon=True).start()

    def _hold(self, conn: socket.socket) -> None:
        start = time.monotonic()
        cid, path = str(id(conn)), ""
        try:
            path = _read_head(conn).split(b"\r\n", 1)[0].decode("ascii", "replace")[:80]
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n")
            deadline = start + self.seconds
            while time.monotonic() < deadline and not self._stop:
                conn.sendall(b"event: ping\ndata: {}\n\n")
                self.events.append({"t": time.monotonic() - start,
                                    "kind": "ping", "conn": cid, "path": path})
                time.sleep(1.0)
            # The fake reached its own deadline with the client still waiting:
            # record that, so a close at the deadline is not read as the client
            # giving up.
            self.events.append({"t": time.monotonic() - start, "kind": "deadline",
                                "conn": cid, "path": path})
        except OSError:
            pass
        finally:
            self.events.append({"t": time.monotonic() - start, "kind": "close",
                                "conn": cid, "path": path})
            _close(conn)


# --------------------------------------------------------------------------- #
# Launching claude (PTY for interactive, pipe for `-p`) + lifecycle.
# --------------------------------------------------------------------------- #
def _winsize(slave: int) -> None:
    import fcntl
    import struct
    import termios
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))


def launch_pty(cmd: list[str], cwd: str, env: dict[str, str]):
    """Start ``cmd`` attached to a pseudo-terminal. Returns (proc, master_fd)."""
    import pty
    master, slave = pty.openpty()
    _winsize(slave)
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave, stdout=slave,
                            stderr=slave, start_new_session=True, close_fds=True)
    os.close(slave)
    return proc, master


def _read_once(master: int, buf: bytearray, timeout: float) -> bool:
    import select
    if not select.select([master], [], [], timeout)[0]:
        return True
    try:
        chunk = os.read(master, 65536)
    except OSError:
        return False
    buf += chunk
    return bool(chunk)


def drain_until(proc, master: int, buf: bytearray, secs: float) -> None:
    """Append PTY output until ``secs`` elapse or the process exits."""
    deadline = time.time() + secs
    while time.time() < deadline and proc.poll() is None:
        if not _read_once(master, buf, 0.3):
            return


def terminate(proc, master: int, buf: bytearray) -> None:
    """Stop the child with send_signal (os.killpg raised EPERM in the probe)."""
    import signal
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            return
        drain_until(proc, master, buf, 6)


def read_log(path: str) -> str:
    """The debug-file's text, redacted, or '' if it was never written."""
    try:
        return redact(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# Arm context: starts/stops a proxy for tunnel and intercept.
# --------------------------------------------------------------------------- #
def wire_ca_path() -> str:
    """The HAL wire's CA certificate, or a clear error if it is not there."""
    if not Path(WIRE_CA).is_file():
        raise FileNotFoundError(
            f"HAL wire CA not found at {WIRE_CA}; run python -m lib.hal.wire_ca init "
            "in the HAL checkout, or set GRIP_WIRE_CA")
    return WIRE_CA


@contextlib.contextmanager
def arm_context(arm: str, tmp: Path, proxy_out: bool = False):
    """Yield the arm's extra environment (and, with proxy_out, the live Proxy).

    base-url and direct yield just an env dict; tunnel and intercept also start a
    local proxy and tear it down on exit. intercept's CA lives under ``tmp`` and
    is removed with it.
    """
    proxy = None
    try:
        if arm == "base-url":
            extra = {"ANTHROPIC_BASE_URL": GATEWAY_URL}
        elif arm == "direct":
            extra = {}
        elif arm == "tunnel":
            proxy = Proxy(None)
            extra = proxy_env(f"http://127.0.0.1:{proxy.start()}")
        elif arm == "intercept":
            certs = make_certs(tmp)
            proxy = Proxy(certs)
            extra = proxy_env(f"http://127.0.0.1:{proxy.start()}", certs[0])
        elif arm == "wire":
            extra = proxy_env(WIRE_URL, wire_ca_path())
        else:
            raise ValueError(f"unknown arm: {arm}")
        yield (extra, proxy) if proxy_out else extra
    finally:
        if proxy:
            proxy.stop()


# --------------------------------------------------------------------------- #
# Case runners. Each returns a CaseResult. Live cases degrade to 'skipped'
# when claude is absent rather than faking a verdict.
# --------------------------------------------------------------------------- #
def _rc_cmd(name: str, dbg: str) -> list[str]:
    return [claude_bin(), "--remote-control", name,
            "--settings", '{"disableAllHooks": true}',
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--model", "default", "--debug-file", dbg]


def _run_rc(case: str, arm: str, wait: float, tmp: Path) -> tuple[str, str]:
    """Launch a Remote Control session under an arm; return (tty, debug) text."""
    dbg = str(tmp / f"{case}-{arm}.debug.log")
    with arm_context(arm, tmp) as extra:
        env = arm_env({**extra, "CLAUDE_CONFIG_DIR": login_dir(case, tmp)})
        proc, master = launch_pty(_rc_cmd(f"grip-wire-{case}-{arm}", dbg),
                                  trusted_cwd(), env)
        buf = bytearray()
        drain_until(proc, master, buf, wait)
        terminate(proc, master, buf)
        os.close(master)
    return redact(strip_ansi(bytes(buf))), read_log(dbg)


def _no_claude(case: str, arm: str) -> CaseResult:
    return CaseResult(case, arm, "skipped", ["claude binary not found on PATH"])


def case_rc_connects(arm: str, wait: float, tmp: Path) -> CaseResult:
    if not claude_bin():
        return _no_claude("rc-connects", arm)
    tty, dbg = _run_rc("rc-connects", arm, wait, tmp)
    state = rc_state(dbg + "\n" + tty)
    outcome = {"connected": "pass", "refused": "refused"}.get(state, "fail")
    return CaseResult("rc-connects", arm, outcome, [f"rc_state={state}"])


def case_rc_archive(arm: str, wait: float, tmp: Path) -> CaseResult:
    if not claude_bin():
        return _no_claude("rc-archive-on-exit", arm)
    _tty, dbg = _run_rc("rc-archive-on-exit", arm, wait, tmp)
    archive = archive_outcome(dbg)
    outcome = "pass" if archive == "ok" else ("skipped" if archive == "absent"
                                              else "fail")
    return CaseResult("rc-archive-on-exit", arm, outcome,
                      [f"archive_outcome={archive}", f"rc_state={rc_state(dbg)}"])


def case_phone_message(arm: str, wait: float, tmp: Path) -> CaseResult:
    interactive = sys.stdin.isatty() or os.environ.get(LIVE_ENV) == "1"
    if not interactive:
        return CaseResult("phone-message", arm, "needs-operator",
                          ["run interactively (or set GRIP_WIRE_PROBE_LIVE=1), "
                           "then send a message from the phone within the wait"])
    if not claude_bin():
        return _no_claude("phone-message", arm)
    print(f"[phone-message] send a message from the phone within {wait:.0f}s ...",
          file=sys.stderr)
    _tty, dbg = _run_rc("phone-message", arm, wait, tmp)
    arrived = "worker/events" in dbg and rc_state(dbg) == "connected"
    return CaseResult("phone-message", arm, "pass" if arrived else "needs-operator",
                      [f"rc_state={rc_state(dbg)}", f"inbound_event={arrived}"])


def case_voice_menu(arm: str, wait: float, tmp: Path) -> CaseResult:
    if not claude_bin():
        return _no_claude("voice-menu", arm)
    dbg = str(tmp / f"voice-menu-{arm}.debug.log")
    with arm_context(arm, tmp) as extra:
        env = arm_env({**extra, "CLAUDE_CONFIG_DIR": login_dir("voice-menu", tmp)})
        proc, master = launch_pty(
            [claude_bin(), "--model", "default", "--debug-file", dbg],
            trusted_cwd(), env)
        buf = bytearray()
        drain_until(proc, master, buf, min(wait, 12))
        os.write(master, b"/voice")
        drain_until(proc, master, buf, 4)
        os.write(master, b"\x15")  # Ctrl-U clears the line; nothing is submitted
        drain_until(proc, master, buf, 1)
        terminate(proc, master, buf)
        os.close(master)
    offered = "Toggle voice mode" in strip_ansi(bytes(buf))
    return CaseResult("voice-menu", arm, "pass" if offered else "fail",
                      [f"voice_in_menu={offered}"])


def _stream_latency(arm: str, extra: dict, tmp: Path) -> float | None:
    """First-event latency of a streamed `claude -p` turn, or None on failure."""
    env = arm_env({**extra, "CLAUDE_CONFIG_DIR": login_dir("stream-timing", tmp)})
    cmd = [claude_bin(), "-p", "reply with the single word ok",
           "--output-format", "stream-json", "--model", "default"]
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, cwd=trusted_cwd(), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True)
    except OSError:
        return None
    try:
        for line in proc.stdout:
            if line.strip():
                return time.monotonic() - start
    finally:
        proc.kill()
    return None


def case_stream_timing(arm: str, wait: float, tmp: Path) -> CaseResult:
    if not claude_bin():
        return _no_claude("stream-timing", arm)
    with arm_context(arm, tmp) as extra:
        armed = _stream_latency(arm, extra, tmp)
    control = _stream_latency("direct", {}, tmp)
    if armed is None or control is None:
        return CaseResult("stream-timing", arm, "skipped",
                          ["no stream produced (offline or no login)"])
    return CaseResult("stream-timing", arm, "pass",
                      [f"first_event_{arm}={armed:.3f}s",
                       f"first_event_direct={control:.3f}s"])


def case_proxy_death(arm: str, wait: float, tmp: Path) -> CaseResult:
    if arm not in PROXY_ARMS:
        return CaseResult("proxy-death", arm, "skipped",
                          ["proxy-death applies only to tunnel/intercept; the wire arm "
                           "cannot kill HAL's wire from here"])
    if not claude_bin():
        return _no_claude("proxy-death", arm)
    dbg = str(tmp / f"proxy-death-{arm}.debug.log")
    with arm_context(arm, tmp, proxy_out=True) as (extra, proxy):
        env = arm_env({**extra, "CLAUDE_CONFIG_DIR": login_dir("proxy-death", tmp)})
        cmd = [claude_bin(), "-p", "count slowly to twenty", "--model", "default",
               "--debug-file", dbg]
        proc = subprocess.Popen(cmd, cwd=trusted_cwd(), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        time.sleep(min(wait, 4))
        proxy.stop()  # kill the proxy mid-stream
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            proc.kill()
    recoverable = bool(re.search(r"retry|reconnect|aborted|ECONNREFUSED|error",
                                 read_log(dbg), re.I))
    return CaseResult("proxy-death", arm, "pass" if recoverable else "fail",
                      [f"interruption_visible={recoverable}"])


def case_hold_tolerance(arm: str, wait: float, tmp: Path) -> CaseResult:
    if not claude_bin():
        return _no_claude("hold-tolerance", arm)
    upstream = HoldUpstream(seconds=max(wait, 30))
    port = upstream.start()
    env = arm_env({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
                   "CLAUDE_CONFIG_DIR": login_dir("hold-tolerance", tmp)})
    proc = None
    try:
        proc = subprocess.Popen(
            [claude_bin(), "-p", "hello", "--model", "default"],
            cwd=trusted_cwd(), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        proc.wait(timeout=max(wait, 30) + 20)
    except (OSError, subprocess.TimeoutExpired):
        if proc:
            proc.kill()
    finally:
        upstream.stop()
    tol = hold_tolerance(upstream.events)
    outcome = "pass" if upstream.events else "skipped"
    return CaseResult("hold-tolerance", arm, outcome,
                      [f"held_s={tol['held_s']:.1f}", f"pings={tol['pings']}",
                       f"terminal={tol['terminal']}",
                       f"connections={tol['connections']}",
                       f"path={tol.get('path', '')}"])


CASES = {
    "rc-connects": case_rc_connects,
    "rc-archive-on-exit": case_rc_archive,
    "phone-message": case_phone_message,
    "stream-timing": case_stream_timing,
    "voice-menu": case_voice_menu,
    "proxy-death": case_proxy_death,
    "hold-tolerance": case_hold_tolerance,
}


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def run_case(arm: str, case: str, wait: float) -> CaseResult:
    """Run one case under one arm in a per-run temp dir (certs/config cleaned up)."""
    import tempfile
    runner = CASES[case]
    with tempfile.TemporaryDirectory(prefix="grip-wire-probe-") as raw:
        return runner(arm, wait, Path(raw))


def _render(result: CaseResult) -> str:
    lines = [f"case={result.case} arm={result.arm} -> {result.outcome.upper()}"]
    lines += [f"  {redact(line)}" for line in result.evidence]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip-wire-probe", description="InfiniteMachine v2 Step-1 wire probe")
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--case", required=True, choices=sorted(CASES))
    parser.add_argument("--wait", type=float, default=WAIT_S,
                        help=f"seconds to observe (default {WAIT_S})")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = run_case(args.arm, args.case, args.wait)
    if args.as_json:
        print(json.dumps(result.as_dict()))
    else:
        print(_render(result))
    return 0 if result.outcome in ("pass", "refused", "needs-operator",
                                   "skipped") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
