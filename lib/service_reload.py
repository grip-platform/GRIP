"""service_reload -- reload a per-user service without ever leaving it down.

Origin. At 05:19:42 UTC on 2026-10-05 a session ran ``launchctl bootout ...;
launchctl bootstrap ...`` on one line to switch the HAL gateway's interpreter.
``bootout`` finishes asynchronously, so ``bootstrap`` ran while launchd still
held the old job and failed (``Bootstrap failed: 5: Input/output error``). The
gateway stayed unloaded for 13 hours, and the session that ran the command sent
its own model traffic through that gateway, so it could never issue the retry.

One reload has the same shape on every platform (launchd on macOS, systemd
``--user`` on Linux, Task Scheduler on Windows):

  1. validate the new definition; nothing has changed yet;
  2. if it equals the installed one and the service runs and answers
     ``/health`` with the expected identity, change nothing;
  3. refuse, even with ``--force``, when another process holds the
     service's port while the manager runs no instance of it (a copy started
     by hand): a managed copy would either kill it or fail to bind, and HAL's
     port reclaim kills even a healthy holder. Refuse, unless ``--force``,
     when the calling process or one of its ancestors routes through the
     service (``ANTHROPIC_BASE_URL``, ``HTTPS_PROXY`` and their kin naming its
     loopback port);
  4. back up the installed definition;
  5. stop the service and WAIT, bounded, until the service manager reports
     it gone (``launchctl print``, ``systemctl --user show``,
     ``schtasks /Query``);
  6. install the new definition and start it, retrying while launchd still
     holds the old job (``bootstrap`` exits 5, EIO);
  7. verify: running, the same process id across a settle interval, and
     ``/health`` answering the expected identity;
  8. on any failure from step 5 on: stop what was started, restore the
     backup, start it, verify it, exit non-zero, and say which state the
     service is in.

Detached by default. The front end writes the request to a job directory
and hands it to a worker in its own session (a double fork on POSIX, a
detached process on Windows), so the reload finishes even if the caller dies
mid-way, which is exactly what a caller routed through the service would do.
The front end then waits for the result; ``--no-wait`` returns at once and
``status <job>`` reads it later. ``--foreground`` runs in this process.

The routes-through-me check reads this process's environment and, best
effort, each ancestor's (``/proc/<pid>/environ`` on Linux, ``ps -E`` on
macOS), because a caller can strip ``HTTPS_PROXY`` from its own command while
the harness above it still routes through the wire. A harness that routes by
its own configuration file (Codex's ``-p hal``) is invisible to it; the
detached run is what protects that case.

Exit codes: 0 reloaded or unchanged; 1 failed and rolled back; 2 bad input,
nothing touched; 3 refused (another process holds the port, or the caller
routes through it), nothing touched;
4 failed and the rollback failed too, or an unexpected error (the service may
be down); 5 no result within ``--wait-timeout`` (the job carries on);
6 another reload of the same service held its lock for too long.

It never prints or logs a definition's contents, only names, paths and
digests, and it keeps backups at mode 0600 under its state directory,
outside every repository: ``$GRIP_SERVICE_RELOAD_HOME``, else
``$XDG_STATE_HOME/grip/service-reload`` (``~/.local/state/...``), or
``%LOCALAPPDATA%\\grip\\service-reload`` on Windows.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import dataclasses
import hashlib
import http.client
import io
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

PLATFORMS = ("launchd", "systemd", "schtasks")
(EXIT_OK, EXIT_ROLLED_BACK, EXIT_USAGE, EXIT_REFUSED, EXIT_BROKEN,
 EXIT_WAIT_TIMEOUT, EXIT_BUSY) = range(7)
#: Variables that send a process's traffic through a loopback port.
ROUTING_VARS = ("ANTHROPIC_BASE_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                "http_proxy", "ALL_PROXY", "all_proxy", "OPENAI_BASE_URL")
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_LAUNCHD_RETRY = frozenset({5, 37})  # EIO: launchd still holds the old job; EALREADY
_MAX_ANCESTORS = 16
_BACKUPS_KEPT = 10
_BOM = (b"\xff\xfe", b"\xfe\xff")
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ReloadError(RuntimeError):
    """A step of a reload failed. The message says which, never a secret."""


class UsageError(ValueError):
    """The request cannot be acted on; nothing was touched."""


@dataclass(frozen=True)
class Preset:
    """A known service: its loopback port, /health identity and names."""

    port: int
    health: str
    names: Mapping[str, str]
    legacy: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)


#: Mirrors HAL's ``lib/hal/deploy_render.py`` (names, ports, /health identities).
#: GRIP's installer passes HAL's own values; these serve a caller that names the
#: service ("reload gateway"). ``com.grip.hal-gateway`` is what GRIP's older
#: installers called the gateway's launchd agent.
PRESETS: Dict[str, Preset] = {
    "gateway": Preset(4010, "hal-gateway",
                      {"launchd": "com.hal.gateway", "systemd": "hal-gateway.service",
                       "schtasks": "GRIP HAL Gateway"},
                      {"launchd": ("com.grip.hal-gateway",)}),
    "wire": Preset(4011, "hal-wire",
                   {"launchd": "com.hal.wire", "systemd": "hal-wire.service",
                    "schtasks": "GRIP HAL Wire"}),
}


@dataclass(frozen=True)
class Target:
    """What to reload: its name on ``platform``, and how to tell it is healthy."""

    platform: str
    name: str
    port: Optional[int] = None
    health: Optional[str] = None
    legacy: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Timing:
    """Every wait is bounded. Seconds."""

    gone: float = 60.0
    start: float = 30.0
    health: float = 30.0
    settle: float = 2.0
    poll: float = 0.25


@dataclass(frozen=True)
class State:
    """What the service manager reports. ``running`` is None when it cannot tell."""

    loaded: bool
    running: Optional[bool] = False
    pid: Optional[int] = None


@dataclass
class Outcome:
    status: str
    detail: str
    code: int
    steps: List[str] = field(default_factory=list)
    backup: Optional[str] = None


# --------------------------------------------------------------------------- #
# Small helpers.
# --------------------------------------------------------------------------- #
def _run(argv: Sequence[str], timeout: float = 60.0) -> Tuple[int, bytes]:
    """(exit code, stdout) of a command; 127 when it cannot run. stderr is dropped."""
    try:
        done = subprocess.run(list(argv), capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return 127, b""
    return done.returncode, done.stdout


def _poll(check: Callable[[], Tuple[bool, Any]], timeout: float,
          interval: float) -> Tuple[bool, Any]:
    """Call ``check`` until it reports done or ``timeout`` passes; its last answer."""
    deadline = time.monotonic() + timeout
    done, value = check()
    while not done and time.monotonic() < deadline:
        time.sleep(max(0.0, min(interval, deadline - time.monotonic())))
        done, value = check()
    return done, value


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def _make_parents(paths: Sequence[str]) -> None:
    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "service"


def _digest(data: Optional[bytes]) -> str:
    return hashlib.sha256(data).hexdigest()[:16] if data is not None else "none"


def _check_program(program: str) -> None:
    path = Path(program)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ReloadError(f"the program {program!r} is not an absolute path to an "
                          "executable file")


def native_platform() -> str:
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        return "systemd"
    if os.name == "nt" or sys.platform in ("win32", "cygwin", "msys"):
        return "schtasks"
    raise UsageError(f"no supported service manager on {sys.platform}")


def state_home() -> Path:
    """Where jobs, backups and locks live: outside every repository."""
    override = os.environ.get("GRIP_SERVICE_RELOAD_HOME", "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "grip" / "service-reload"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "grip" / "service-reload"


# --------------------------------------------------------------------------- #
# /health, read past any proxy the caller set.
# --------------------------------------------------------------------------- #
def probe(port: int, service: str, timeout: float = 1.5) -> Tuple[bool, str]:
    """One GET of 127.0.0.1:<port>/health. True only for ``ok`` and the identity."""
    try:
        with _NO_PROXY.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as answer:
            body = json.loads(answer.read(65536).decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return False, f"no answer on 127.0.0.1:{port} ({type(exc).__name__})"
    if not isinstance(body, dict) or body.get("ok") is not True:
        return False, f"127.0.0.1:{port} answered without ok: true"
    if body.get("service") != service:
        return False, (f"127.0.0.1:{port} answered as {str(body.get('service'))[:40]!r}, "
                       f"not {service!r}")
    return True, f"answers as {service} on 127.0.0.1:{port}"


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _occupant(target: Target, backend: Backend) -> Optional[str]:
    """Who else holds the service's port while its manager runs no instance of it.

    Starting a managed copy then would either kill the holder (HAL's port
    reclaim kills even a healthy one) or fail to bind, so a reload refuses,
    even with --force. A manager that cannot tell whether its instance runs
    (a localised schtasks status) is given the benefit of the doubt.
    """
    if target.port is None or not _port_open(target.port):
        return None
    states = [backend.control.state(name) for name in (target.name, *target.legacy)]
    if any(state.loaded and state.running is not False for state in states):
        return None
    same = probe(target.port, target.health)[0] if target.health else False
    holder = (f"a {target.health} that {target.platform} does not manage (started by "
              "hand?)" if same else "another program")
    return (f"127.0.0.1:{target.port} is already held by {holder}. Stop it, then rerun: a "
            "managed copy would kill it or fail to bind, so this refuses even with --force.")


# --------------------------------------------------------------------------- #
# Does the caller route through the service?
# --------------------------------------------------------------------------- #
def loopback_port(value: str) -> Optional[int]:
    """The port of a loopback URL or host:port, else None."""
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urllib.parse.urlsplit(text)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if port is not None and (host in ("localhost", "::1") or host.startswith("127.")):
        return port
    return None


def routing_vars(port: int, env: Mapping[str, str]) -> List[str]:
    """The names in ``env`` that send traffic to 127.0.0.1:<port>."""
    return [name for name in ROUTING_VARS if loopback_port(env.get(name, "")) == port]


def caller_routes(port: int, env: Optional[Mapping[str, str]] = None,
                  pid: Optional[int] = None) -> List[str]:
    """Where this process or an ancestor routes through ``port``: names, never values."""
    own = os.environ if env is None else env
    found = [f"{name} (this process)" for name in routing_vars(port, own)]
    found += [f"{name} (ancestor {ancestor} {comm})"
              for ancestor, comm, ancestor_env in _ancestors(os.getpid() if pid is None else pid)
              for name in routing_vars(port, ancestor_env)]
    return found


def _ancestors(pid: int) -> Iterator[Tuple[int, str, Dict[str, str]]]:
    reader = _PROC if Path("/proc/self/environ").exists() else _PS
    seen: set = set()
    current = reader.parent(pid)
    while current and current > 1 and current not in seen and len(seen) < _MAX_ANCESTORS:
        seen.add(current)
        yield current, reader.comm(current), reader.environ(current)
        current = reader.parent(current)


class _ProcReader:
    """Linux: /proc. Unreadable entries (another user's process) read as empty."""

    @staticmethod
    def parent(pid: int) -> Optional[int]:
        stat = _read(Path(f"/proc/{pid}/stat")) or b""
        fields = stat.rsplit(b")", 1)[-1].split()
        return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None

    @staticmethod
    def comm(pid: int) -> str:
        return (_read(Path(f"/proc/{pid}/comm")) or b"?").decode("utf-8", "replace").strip()

    @staticmethod
    def environ(pid: int) -> Dict[str, str]:
        raw = _read(Path(f"/proc/{pid}/environ")) or b""
        pairs = (item.decode("utf-8", "replace").partition("=") for item in raw.split(b"\0"))
        return {name: value for name, _, value in pairs if name in ROUTING_VARS}


class _PsReader:
    """macOS and the BSDs: ``ps``. Only the routing variables are kept."""

    _VARS = re.compile(r"(?:^|\s)(" + "|".join(ROUTING_VARS) + r")=(\S*)")

    @staticmethod
    def _ps(*args: str) -> str:
        rc, out = _run(["ps", *args], timeout=5)
        return out.decode("utf-8", "replace") if rc == 0 else ""

    @classmethod
    def parent(cls, pid: int) -> Optional[int]:
        text = cls._ps("-o", "ppid=", "-p", str(pid)).strip()
        return int(text) if text.isdigit() else None

    @classmethod
    def comm(cls, pid: int) -> str:
        return cls._ps("-o", "comm=", "-p", str(pid)).strip().rsplit("/", 1)[-1] or "?"

    @classmethod
    def environ(cls, pid: int) -> Dict[str, str]:
        return dict(cls._VARS.findall(cls._ps("-E", "-ww", "-o", "command=", "-p", str(pid))))


_PROC, _PS = _ProcReader(), _PsReader()


# --------------------------------------------------------------------------- #
# Service managers. A store keeps definitions (where, read, check, write,
# remove); a control drives the running service (state, stop, gone, start).
# --------------------------------------------------------------------------- #
def _load_plist(data: bytes) -> dict:
    try:
        plist = plistlib.loads(data)
    except Exception as exc:  # noqa: BLE001 - plistlib raises several unrelated types
        raise ReloadError(f"the definition is not a property list ({type(exc).__name__})") from exc
    if not isinstance(plist, dict):
        raise ReloadError("the definition is not a property-list dictionary")
    return plist


def _unit_lines(data: bytes) -> List[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReloadError("the unit is not UTF-8") from exc
    stripped = (line.strip() for line in text.splitlines())
    return [line for line in stripped if line and not line.startswith(("#", ";"))]


def _unit_log_paths(lines: Sequence[str]) -> List[str]:
    pairs = (line.partition("=") for line in lines)
    return [value.split(":", 1)[1] for key, _, value in pairs
            if key in ("StandardOutput", "StandardError")
            and value.startswith(("append:", "file:"))]


def _first_word(command: str) -> str:
    text = command.strip()
    if text.startswith('"'):
        return text[1:].split('"', 1)[0]
    return text.split(None, 1)[0] if text else ""


def _systemctl(*args: str) -> None:
    rc, _ = _run(["systemctl", "--user", *args])
    if rc != 0:
        raise ReloadError(f"systemctl --user {' '.join(args)} failed (exit {rc})")


def _windows_text(data: bytes) -> str:
    """Text from schtasks or a task file: UTF-16 with or without a byte-order mark,
    else UTF-8. Undecodable bytes are replaced, never raised."""
    if data.startswith(_BOM):
        return data.decode("utf-16", "replace")
    if b"\x00" in data[:64]:  # UTF-16 with no mark: ASCII text has a NUL every other byte
        return data.decode("utf-16-le", "replace")
    return data.decode("utf-8-sig", "replace")


def _utf16(data: bytes) -> bytes:
    """Task XML as UTF-16 with a byte-order mark, the encoding schtasks reads."""
    return data if data.startswith(_BOM) else _windows_text(data).encode("utf-16")


def _parse_task(data: bytes) -> ET.Element:
    try:
        root = ET.fromstring(_windows_text(data))
    except ET.ParseError as exc:
        raise ReloadError(f"the task is not readable XML ({type(exc).__name__})") from exc
    if root.tag != "{%s}Task" % TASK_NS["t"]:
        raise ReloadError("the definition is not a Task Scheduler task")
    return root


def _task_registered(name: str) -> bool:
    return _run(["schtasks", "/Query", "/TN", name])[0] == 0


class _FileStore:
    """A definition kept as one file in one directory (launchd, systemd)."""

    def __init__(self, directory: Path, extension: str):
        self.directory = directory
        self.extension = extension

    def _path(self, name: str) -> Path:
        return self.directory / f"{name}{self.extension}"

    def where(self, name: str) -> str:
        return str(self._path(name))

    def canonical(self, data: bytes) -> bytes:
        return data

    def installed(self, name: str) -> Optional[bytes]:
        return _read(self._path(name))

    def export(self, name: str) -> Optional[bytes]:
        return self.installed(name)

    def uninstall(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(name).unlink()


class LaunchdStore(_FileStore):
    def __init__(self, home: Optional[Path] = None):
        root = Path.home() if home is None else Path(home)
        super().__init__(root / "Library" / "LaunchAgents", ".plist")

    def validate(self, name: str, data: bytes) -> None:
        plist = _load_plist(data)
        if plist.get("Label") != name:
            raise ReloadError(f"the definition's Label is {plist.get('Label')!r}, not {name!r}")
        args = plist.get("ProgramArguments")
        if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
            raise ReloadError("ProgramArguments must be a non-empty list of strings")
        _check_program(args[0])
        workdir = plist.get("WorkingDirectory")
        if workdir is not None and not Path(str(workdir)).is_dir():
            raise ReloadError(f"WorkingDirectory {workdir!r} is not a directory")
        _plutil_lint(data)

    def install(self, name: str, data: bytes) -> None:
        plist = _load_plist(data)
        _make_parents([plist[key] for key in ("StandardOutPath", "StandardErrorPath")
                       if isinstance(plist.get(key), str)])
        _write_atomic(self._path(name), data, 0o644)


def _plutil_lint(data: bytes) -> None:
    if shutil.which("plutil") is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "definition.plist"
        staged.write_bytes(data)
        rc, _ = _run(["plutil", "-lint", str(staged)])
    if rc != 0:
        raise ReloadError("plutil -lint rejected the definition")


class SystemdStore(_FileStore):
    def __init__(self, home: Optional[Path] = None):
        root = Path.home() if home is None else Path(home)
        config = os.environ.get("XDG_CONFIG_HOME") or str(root / ".config")
        super().__init__(Path(config) / "systemd" / "user", "")

    def validate(self, name: str, data: bytes) -> None:
        if not name.endswith(".service"):
            raise ReloadError(f"{name!r} is not a .service unit")
        lines = _unit_lines(data)
        if "[Service]" not in lines:
            raise ReloadError("the unit has no [Service] section")
        execs = [line[len("ExecStart="):] for line in lines if line.startswith("ExecStart=")]
        if len(execs) != 1:
            raise ReloadError("the unit must have exactly one ExecStart=")
        _check_program(_first_word(execs[0]))

    def install(self, name: str, data: bytes) -> None:
        _make_parents(_unit_log_paths(_unit_lines(data)))
        _write_atomic(self._path(name), data, 0o644)
        _systemctl("daemon-reload")

    def uninstall(self, name: str) -> None:
        _run(["systemctl", "--user", "disable", name])
        super().uninstall(name)
        _systemctl("daemon-reload")


class SchtasksStore:
    """Task Scheduler rewrites a registered task (dates, author), so what this
    module last installed is kept beside it for the comparison."""

    def __init__(self, state_dir: Optional[Path] = None):
        base = state_home() if state_dir is None else Path(state_dir)
        self.sidecars = base / "installed" / "schtasks"

    def _sidecar(self, name: str) -> Path:
        return self.sidecars / (hashlib.sha256(name.encode("utf-8")).hexdigest()[:16] + ".xml")

    def where(self, name: str) -> str:
        return f"Task Scheduler task {name!r}"

    def canonical(self, data: bytes) -> bytes:
        return _utf16(data)

    def installed(self, name: str) -> Optional[bytes]:
        if not _task_registered(name):
            return None
        return _read(self._sidecar(name)) or self.export(name)

    def export(self, name: str) -> Optional[bytes]:
        rc, out = _run(["schtasks", "/Query", "/TN", name, "/XML"])
        return _utf16(out) if rc == 0 and out else None

    def validate(self, name: str, data: bytes) -> None:
        if _parse_task(data).find("t:Actions/t:Exec/t:Command", TASK_NS) is None:
            raise ReloadError("the task has no Actions/Exec/Command")

    def install(self, name: str, data: bytes) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "task.xml"
            staged.write_bytes(_utf16(data))
            rc, _ = _run(["schtasks", "/Create", "/TN", name, "/XML", str(staged), "/F"])
        if rc != 0:
            raise ReloadError(f"schtasks /Create {name} failed (exit {rc})")
        _write_atomic(self._sidecar(name), _utf16(data), 0o600)

    def uninstall(self, name: str) -> None:
        _run(["schtasks", "/Delete", "/TN", name, "/F"])
        with contextlib.suppress(FileNotFoundError):
            self._sidecar(name).unlink()


class LaunchdControl:
    def __init__(self, store: LaunchdStore):
        self.store = store
        self.domain = f"gui/{os.getuid()}"

    def state(self, name: str) -> State:
        rc, out = _run(["launchctl", "print", f"{self.domain}/{name}"])
        if rc != 0:
            return State(loaded=False)
        text = out.decode("utf-8", "replace")
        status = re.search(r"^\s*state = (\S+)", text, re.M)
        pid = re.search(r"^\s*pid = (\d+)", text, re.M)
        return State(True, bool(status) and status.group(1) == "running",
                     int(pid.group(1)) if pid else None)

    def stop(self, name: str) -> None:
        _run(["launchctl", "bootout", f"{self.domain}/{name}"])  # the wait decides, not the code

    def gone(self, name: str) -> bool:
        return not self.state(name).loaded

    def start(self, name: str, timeout: float, poll: float) -> None:
        """bootstrap, retrying while launchd still holds the old job (exit 5)."""
        argv = ["launchctl", "bootstrap", self.domain, self.store.where(name)]

        def attempt() -> Tuple[bool, int]:
            rc = _run(argv)[0]
            return rc not in _LAUNCHD_RETRY, rc

        _, rc = _poll(attempt, timeout, poll)
        if rc != 0 and not self.state(name).loaded:
            raise ReloadError(f"launchctl bootstrap {name} failed (exit {rc})")


class SystemdControl:
    _ACTIVE = frozenset({"active", "activating", "deactivating", "reloading"})

    @staticmethod
    def _show(name: str) -> Dict[str, str]:
        rc, out = _run(["systemctl", "--user", "show", name,
                        "--property=LoadState,ActiveState,SubState,MainPID"])
        if rc != 0:
            return {}
        pairs = (line.partition("=") for line in out.decode("utf-8", "replace").splitlines())
        return {key: value for key, _, value in pairs if key}

    def state(self, name: str) -> State:
        props = self._show(name)
        pid = int(props["MainPID"]) if props.get("MainPID", "").isdigit() else 0
        running = props.get("ActiveState") == "active" and props.get("SubState") == "running"
        return State(props.get("LoadState") == "loaded", running, pid or None)

    def stop(self, name: str) -> None:
        _run(["systemctl", "--user", "stop", name])

    def gone(self, name: str) -> bool:
        return self._show(name).get("ActiveState", "inactive") not in self._ACTIVE

    def start(self, name: str, timeout: float, poll: float) -> None:
        """systemctl waits for the start job itself, so there is nothing to retry."""
        _systemctl("enable", name)
        _systemctl("start", name)


class SchtasksControl:
    #: Status words schtasks prints in English; any other (a localised one) reads as unknown.
    _RUNNING = {"Running": True, "Ready": False, "Disabled": False, "Queued": False}

    def state(self, name: str) -> State:
        rc, out = _run(["schtasks", "/Query", "/TN", name, "/FO", "CSV", "/NH"])
        if rc != 0:
            return State(loaded=False)
        rows = list(csv.reader(io.StringIO(_windows_text(out))))
        status = rows[0][-1].strip() if rows and rows[0] else ""
        return State(True, self._RUNNING.get(status), None)

    def stop(self, name: str) -> None:
        _run(["schtasks", "/End", "/TN", name])

    def gone(self, name: str) -> bool:
        return self.state(name).running is not True

    def start(self, name: str, timeout: float, poll: float) -> None:
        """/Run queues the task once; verification then waits for it to answer."""
        rc, _ = _run(["schtasks", "/Run", "/TN", name])
        if rc != 0:
            raise ReloadError(f"schtasks /Run {name} failed (exit {rc})")


@dataclass(frozen=True)
class Backend:
    platform: str
    suffix: str
    store: Any
    control: Any


def backend_for(platform: str) -> Backend:
    if platform == "launchd":
        store = LaunchdStore()
        return Backend(platform, ".plist", store, LaunchdControl(store))
    if platform == "systemd":
        return Backend(platform, ".service", SystemdStore(), SystemdControl())
    if platform == "schtasks":
        return Backend(platform, ".xml", SchtasksStore(), SchtasksControl())
    raise UsageError(f"unknown platform {platform!r}")


# --------------------------------------------------------------------------- #
# The reload itself.
# --------------------------------------------------------------------------- #
class Journal:
    """Each step, timestamped, kept for the result and written to ``stream``."""

    def __init__(self, stream=None):
        self.stream = stream
        self.steps: List[str] = []
        self.backup: Optional[str] = None

    def say(self, message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        self.steps.append(line)
        if self.stream is not None:
            print(line, file=self.stream, flush=True)

    def end(self, status: str, detail: str, code: int) -> Outcome:
        self.say(f"{status}: {detail}")
        return Outcome(status, detail, code, list(self.steps), self.backup)


def _observe(target: Target, state: State) -> Tuple[bool, str]:
    if not state.loaded:
        return False, "not loaded"
    if state.running is False:
        return False, "loaded but not running"
    if target.port is None or target.health is None:
        return state.running is True, "running" if state.running else "state unknown"
    return probe(target.port, target.health)


def _serving(target: Target, backend: Backend, seconds: float, poll: float) -> bool:
    """Running and answering; an instance its manager does not even hold never is."""
    if not backend.control.state(target.name).loaded:
        return False
    ok, _ = _poll(lambda: _observe(target, backend.control.state(target.name)), seconds, poll)
    return ok


def _wait_gone(name: str, backend: Backend, timing: Timing, journal: Journal) -> None:
    gone, _ = _poll(lambda: (backend.control.gone(name), None), timing.gone, timing.poll)
    if not gone:
        raise ReloadError(f"{name} was still held by the service manager "
                          f"{timing.gone:g} s after the stop")
    journal.say(f"{name} is gone")


def _stop_all(names: Sequence[str], backend: Backend, timing: Timing, journal: Journal) -> None:
    for name in names:
        if backend.control.state(name).loaded:
            journal.say(f"stopping {name}")
            backend.control.stop(name)
    for name in names:
        _wait_gone(name, backend, timing, journal)


def _start(name: str, backend: Backend, timing: Timing, journal: Journal) -> None:
    journal.say(f"starting {name}")
    backend.control.start(name, timing.start, timing.poll)


def _settled(target: Target, backend: Backend, settle: float) -> Tuple[bool, str]:
    """Healthy now, and still healthy as the same process ``settle`` seconds later."""
    first = backend.control.state(target.name)
    ok, detail = _observe(target, first)
    if not ok:
        return False, detail
    time.sleep(settle)
    second = backend.control.state(target.name)
    ok, detail = _observe(target, second)
    if ok and None not in (first.pid, second.pid) and first.pid != second.pid:
        return False, f"it restarted while settling (pid {first.pid} then {second.pid})"
    return ok, detail


def _verify(target: Target, backend: Backend, timing: Timing, journal: Journal) -> None:
    ok, detail = _poll(lambda: _settled(target, backend, timing.settle),
                       timing.health, timing.poll)
    if not ok:
        holder = _occupant(target, backend)  # the usual reason a new instance cannot bind
        raise ReloadError(f"{target.name} was not healthy within {timing.health:g} s: {detail}"
                          + (f"; {holder}" if holder else ""))
    journal.say(f"{target.name}: {detail}")


def save_backup(platform: str, name: str, data: bytes, suffix: str) -> Path:
    """Copy a definition to the backups directory (0600) and keep the newest few."""
    directory = state_home() / "backups" / platform / _safe(name)
    path = directory / f"{_stamp()}{suffix}"
    _write_atomic(path, data, 0o600)
    with contextlib.suppress(OSError):
        os.chmod(directory, 0o700)
    for old in sorted(directory.glob(f"*{suffix}"))[:-_BACKUPS_KEPT]:
        with contextlib.suppress(OSError):
            old.unlink()
    return path


def _save_backups(target: Target, previous: Optional[bytes], legacy: Mapping[str, bytes],
                  backend: Backend, journal: Journal) -> None:
    pending = ([(target.name, previous)] if previous is not None else []) + list(legacy.items())
    saved = [save_backup(target.platform, name, data, backend.suffix) for name, data in pending]
    journal.backup = str(saved[0]) if saved else None
    journal.say(f"backed up {len(saved)} definition(s) to {state_home() / 'backups'}")


def _swap(target: Target, desired: bytes, legacy: Mapping[str, bytes], backend: Backend,
          timing: Timing, journal: Journal) -> None:
    _stop_all([target.name, *legacy], backend, timing, journal)
    journal.say(f"installing {target.name} (sha256 {_digest(desired)})")
    backend.store.install(target.name, desired)
    for name in legacy:
        journal.say(f"retiring {name}, an older name of the same service")
        backend.store.uninstall(name)
    _start(target.name, backend, timing, journal)
    _verify(target, backend, timing, journal)


def _reinstate(target: Target, data: bytes, backend: Backend, timing: Timing,
               journal: Journal) -> None:
    journal.say(f"restoring the previous definition of {target.name}")
    backend.store.install(target.name, data)
    _start(target.name, backend, timing, journal)
    _verify(target, backend, timing, journal)


def _reinstate_legacy(target: Target, legacy: Mapping[str, bytes], backend: Backend,
                      timing: Timing, journal: Journal) -> None:
    backend.store.uninstall(target.name)
    for name, data in legacy.items():
        _reinstate(dataclasses.replace(target, name=name, legacy=()), data,
                   backend, timing, journal)


def _describe(target: Target, backend: Backend) -> str:
    ok, detail = _observe(target, backend.control.state(target.name))
    return f"{target.name} is {'serving' if ok else 'NOT serving'} ({detail})"


def _restore(target: Target, previous: Optional[bytes], legacy: Mapping[str, bytes],
             backend: Backend, timing: Timing, journal: Journal, reason: str) -> Outcome:
    """Put back what was installed before, and say which state the service is in."""
    journal.say(f"rolling back: {reason}")
    try:
        _stop_all([target.name], backend, timing, journal)
        if previous is not None:
            _reinstate(target, previous, backend, timing, journal)
        elif legacy:
            _reinstate_legacy(target, legacy, backend, timing, journal)
        else:
            backend.store.uninstall(target.name)
            return journal.end("rolled-back", f"{reason}; nothing was installed before, "
                               "so nothing is installed now", EXIT_ROLLED_BACK)
    except ReloadError as exc:
        return journal.end("broken", f"{reason}; the rollback failed too: {exc}; "
                           f"{_describe(target, backend)}", EXIT_BROKEN)
    return journal.end("rolled-back", f"{reason}; the previous definition is back and "
                       "answers", EXIT_ROLLED_BACK)


def _legacy_installed(target: Target, backend: Backend) -> Dict[str, bytes]:
    found = {name: backend.store.installed(name) for name in target.legacy}
    return {name: data for name, data in found.items() if data is not None}


def _effective(target: Target, definition: Optional[bytes], backend: Backend) -> Target:
    """With no new definition, an older name stands in when it is the one installed."""
    if definition is not None or backend.store.installed(target.name) is not None:
        return target
    older = [name for name in target.legacy if backend.store.installed(name) is not None]
    return dataclasses.replace(target, name=older[0], legacy=()) if older else target


def _refusal(target: Target, routes: Sequence[str]) -> str:
    return (f"the caller routes through 127.0.0.1:{target.port} ({'; '.join(routes)}), so "
            f"stopping {target.name} would cut it off mid-reload. Run this from a shell that "
            "does not route through it, or pass --force: the reload runs detached and "
            "finishes even if the caller dies.")


def reload(target: Target, definition: Optional[bytes], backend: Backend, *, timing: Timing,
           force: bool, routes: Sequence[str], journal: Journal) -> Outcome:
    """One reload: the eight steps in the module docstring."""
    target = _effective(target, definition, backend)
    installed = backend.store.installed(target.name)
    desired = installed if definition is None else backend.store.canonical(definition)
    if desired is None:
        return journal.end("invalid", f"no definition was given and none of {target.name} "
                           "is installed", EXIT_USAGE)
    try:
        backend.store.validate(target.name, desired)
    except ReloadError as exc:
        return journal.end("invalid", f"the definition was refused: {exc}", EXIT_USAGE)
    legacy = _legacy_installed(target, backend)
    if desired == installed and not legacy and _serving(
            target, backend, min(5.0, timing.health), timing.poll):
        return journal.end("unchanged", f"{target.name} already runs this definition "
                           "and answers", EXIT_OK)
    holder = _occupant(target, backend)
    if holder:
        return journal.end("occupied", holder, EXIT_REFUSED)
    if routes and not force:
        return journal.end("refused", _refusal(target, routes), EXIT_REFUSED)
    previous = backend.store.export(target.name) if installed is not None else None
    _save_backups(target, previous, legacy, backend, journal)
    try:
        _swap(target, desired, legacy, backend, timing, journal)
    except ReloadError as exc:
        return _restore(target, previous, legacy, backend, timing, journal, str(exc))
    first = previous is None and not legacy
    return journal.end("installed" if first else "reloaded",
                       f"{target.name} is running the new definition and answers", EXIT_OK)


def plan(target: Target, definition: Optional[bytes], backend: Backend,
         routes: Sequence[str]) -> dict:
    """What a reload would do. Reads only: no stop, no write, no start."""
    target = _effective(target, definition, backend)
    installed = backend.store.installed(target.name)
    desired = installed if definition is None else backend.store.canonical(definition)
    report = {"platform": target.platform, "name": target.name,
              "where": backend.store.where(target.name), "installed": installed is not None,
              "changes": desired != installed,
              "legacy_installed": sorted(_legacy_installed(target, backend)),
              "routes_through_caller": list(routes)}
    try:
        if desired is None:
            raise ReloadError("no definition was given and none is installed")
        backend.store.validate(target.name, desired)
        report["valid"] = True
    except ReloadError as exc:
        report.update(valid=False, invalid_because=str(exc))
    report["serving"], report["observed"] = _observe(
        target, backend.control.state(target.name))
    report["occupied_by"] = _occupant(target, backend)
    report["would"] = _would(report)
    return report


def _would(report: dict) -> str:
    if not report["valid"]:
        return "nothing: the definition is refused"
    if not report["changes"] and not report["legacy_installed"] and report["serving"]:
        return "nothing: unchanged and serving"
    if report["occupied_by"]:
        return "refuse, even with --force: another process holds the port"
    if report["routes_through_caller"]:
        return "refuse unless --force: the caller routes through this service"
    return "reload" if report["installed"] else "install"


# --------------------------------------------------------------------------- #
# Jobs: the detached worker, its lock, and waiting for its result.
# --------------------------------------------------------------------------- #
def new_job(request: dict, definition: Optional[bytes]) -> Path:
    job = state_home() / "jobs" / (f"{_stamp()}-{_safe(request['target']['name'])}-"
                                   f"{secrets.token_hex(3)}")
    job.mkdir(parents=True, mode=0o700)
    _write_atomic(job / "request.json", json.dumps(request, indent=2).encode("utf-8"), 0o600)
    if definition is not None:
        _write_atomic(job / "definition", definition, 0o600)
    return job


def _try_lock(fd: int) -> bool:
    """Take the service lock without waiting. True when ``fd`` now holds it.

    The lock comes from _compat: POSIX fcntl, or the LockFileEx lock on Windows. The
    old test for an importable fcntl chose msvcrt on Windows, and a venv with a
    stand-in fcntl passed that test, so Windows then got no lock (GRIP#8757).
    """
    from _compat import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _service_lock(target: Target, timeout: float):
    """One reload per service at a time. Yields whether the lock was taken."""
    path = state_home() / "locks" / f"{target.platform}-{_safe(target.name)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        held, _ = _poll(lambda: (_try_lock(fd), None), timeout, 0.2)
        yield held
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _request_parts(request: dict) -> Tuple[Target, Timing]:
    raw = dict(request["target"])
    raw["legacy"] = tuple(raw.get("legacy", ()))
    return Target(**raw), Timing(**request["timing"])


def _locked_reload(job: Path, request: dict, journal: Journal) -> Outcome:
    target, timing = _request_parts(request)
    with _service_lock(target, float(request.get("lock_timeout", 600))) as held:
        if not held:
            return journal.end("busy", f"another reload of {target.name} held its lock",
                               EXIT_BUSY)
        try:
            return reload(target, _read(job / "definition"), backend_for(target.platform),
                          timing=timing, force=bool(request.get("force")),
                          routes=request.get("routes", []), journal=journal)
        except Exception as exc:  # noqa: BLE001 - the caller must always get a result
            return journal.end("error", f"the reload stopped on an unexpected "
                               f"{type(exc).__name__}: {exc}", EXIT_BROKEN)


def run_job(job: Path) -> int:
    """The worker: one reload under the service's lock; result.json, always."""
    request = json.loads((job / "request.json").read_text(encoding="utf-8"))
    with open(job / "log", "a", encoding="utf-8") as stream:
        outcome = _locked_reload(job, request, Journal(stream))
    _write_atomic(job / "result.json",
                  json.dumps(dataclasses.asdict(outcome), indent=2).encode("utf-8"), 0o600)
    return outcome.code


def detach(job: Path) -> None:
    """Start ``run_job(job)`` in a process that outlives this one."""
    argv = [sys.executable, str(Path(__file__).resolve()), "run-job", str(job)]
    if os.name == "nt":
        _spawn_windows(argv, job)
    else:
        _double_fork(argv, job)


def _double_fork(argv: List[str], job: Path) -> None:
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)  # the first child exits at once; the grandchild is orphaned
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        null = os.open(os.devnull, os.O_RDONLY)
        log = os.open(job / "worker.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(null, 0)
        os.dup2(log, 1)
        os.dup2(log, 2)
        os.execv(argv[0], argv)
    finally:
        os._exit(127)  # reached only when the exec failed


def _popen_detached(argv: List[str], log, flags: int) -> bool:
    try:
        subprocess.Popen(argv, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    except OSError:
        return False
    return True


def _spawn_windows(argv: List[str], job: Path) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    with open(job / "worker.log", "ab") as log:
        # A job object may forbid breaking away, so try without it second.
        started = any(_popen_detached(argv, log, flags | extra) for extra in (breakaway, 0))
    if not started:
        raise ReloadError("the detached worker could not be started")


def wait_for(job: Path, timeout: float) -> Optional[dict]:
    result = job / "result.json"
    done, _ = _poll(lambda: (result.exists(), None), timeout, 0.2)
    return json.loads(result.read_text(encoding="utf-8")) if done else None


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-service-reload",
        description="Reload a per-user service without leaving it down: wait for the old "
                    "one to go, start the new one, check /health, roll back on failure.")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("reload", help="reload a service (detached; waits for the result)")
    cmd.add_argument("service", help="gateway, wire, or the service's own name on this "
                                     "platform (a launchd label, a systemd unit, a task name)")
    cmd.add_argument("--definition", help="the new definition; without it the installed one "
                                          "is reloaded")
    cmd.add_argument("--platform", choices=PLATFORMS, help="default: this machine's")
    cmd.add_argument("--port", type=int, help="its loopback port (default: the preset's)")
    cmd.add_argument("--health-service", help="the identity /health must answer "
                                              "(default: the preset's)")
    cmd.add_argument("--legacy", action="append", default=[],
                     help="an older name of the same service, retired by the reload")
    cmd.add_argument("--force", action="store_true",
                     help="reload even though this caller routes through the service")
    mode = cmd.add_mutually_exclusive_group()
    mode.add_argument("--foreground", action="store_true", help="run in this process")
    mode.add_argument("--no-wait", action="store_true", help="start the job and return")
    mode.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    cmd.add_argument("--wait-timeout", type=float, default=600.0)
    for flag, default in (("--gone-timeout", Timing.gone), ("--start-timeout", Timing.start),
                          ("--health-timeout", Timing.health), ("--settle", Timing.settle),
                          ("--poll", Timing.poll)):
        cmd.add_argument(flag, type=float, default=default, help=f"seconds (default {default:g})")
    status = sub.add_parser("status", help="a job's result, or that it is still running")
    status.add_argument("job")
    worker = sub.add_parser("run-job", help=argparse.SUPPRESS)
    worker.add_argument("job")
    return parser


def target_from(args: argparse.Namespace, platform: str) -> Target:
    preset = PRESETS.get(args.service)
    port = args.port if args.port is not None else (preset.port if preset else None)
    health = args.health_service or (preset.health if preset else None)
    if health and port is None:
        raise UsageError("--health-service needs --port")
    legacy = tuple(args.legacy) or (tuple(preset.legacy.get(platform, ())) if preset else ())
    name = preset.names[platform] if preset else args.service
    return Target(platform, name, port, health, legacy)


def _definition(path: Optional[str]) -> Optional[bytes]:
    if path is None:
        return None
    data = _read(Path(path))
    if data is None:
        raise UsageError(f"cannot read the definition {path}")
    return data


def _emit(result: dict, job: Optional[Path] = None) -> int:
    payload = dict(result, job=str(job)) if job else dict(result)
    print(json.dumps(payload, indent=2))
    if "status" in result and "detail" in result:
        print(f"grip-service-reload: {result['status']}: {result['detail']}", file=sys.stderr)
    return int(result.get("code", EXIT_OK))


def _reload_command(args: argparse.Namespace) -> int:
    try:
        platform = args.platform or native_platform()
        target = target_from(args, platform)
        definition = _definition(args.definition)
    except UsageError as exc:
        print(f"grip-service-reload: {exc}", file=sys.stderr)
        return EXIT_USAGE
    routes = caller_routes(target.port) if target.port is not None else []
    if args.dry_run:
        return _emit(plan(target, definition, backend_for(platform), routes))
    timing = Timing(args.gone_timeout, args.start_timeout, args.health_timeout, args.settle,
                    args.poll)
    job = new_job({"target": dataclasses.asdict(target), "timing": dataclasses.asdict(timing),
                   "force": args.force, "routes": routes}, definition)
    if args.foreground:
        run_job(job)
    else:
        detach(job)
    if args.no_wait:
        return _emit({"status": "started", "detail": f"job {job.name}", "code": EXIT_OK}, job)
    result = wait_for(job, 0.0 if args.foreground else args.wait_timeout)
    if result is None:
        return _emit({"status": "running", "code": EXIT_WAIT_TIMEOUT,
                      "detail": f"no result after {args.wait_timeout:g} s; the job carries on"},
                     job)
    return _emit(result, job)


def _status_command(job: Path) -> int:
    result = wait_for(job, 0.0)
    if result is None:
        return _emit({"status": "running", "detail": "no result yet",
                      "code": EXIT_WAIT_TIMEOUT}, job)
    return _emit(result, job)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run-job":
        return run_job(Path(args.job))
    if args.command == "status":
        return _status_command(Path(args.job))
    return _reload_command(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
