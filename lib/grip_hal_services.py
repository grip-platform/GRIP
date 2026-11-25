"""Manage HAL services through the host's per-user service manager."""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

PLATFORMS = ("auto", "launchd", "systemd", "schtasks")
SERVICES = ("wire", "gateway", "brain")
CA_STORES = ("auto", "file")
NO_PROXY = "127.0.0.1,localhost,.ts.net"
REPO = Path(__file__).resolve().parent.parent
RELOAD = REPO / "bin" / "grip-service-reload"
# The --bind value on a systemd unit's ExecStart line (HAL's deploy/hal-brain.service).
# A comment that mentions --bind is not that line, so it is never read.
_EXEC_BIND = re.compile(r'^ExecStart=.*?\s--bind[ =]"?([^\s"]+)', re.MULTILINE)


class CommandError(RuntimeError):
    """An operation could not be completed; ``code`` is the CLI exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Service:
    """The renderer's authoritative details for one HAL service."""

    key: str
    name: str
    port: Optional[int]
    health: Optional[str]


def run(argv: list[str], *, env: Optional[dict] = None, input_text: Optional[str] = None,
        cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run one command and turn a failed command into a readable error."""
    try:
        done = subprocess.run(argv, env=env, input=input_text, cwd=cwd, text=True,
                              capture_output=True, check=False)
    except OSError as exc:
        raise CommandError(f"could not run {argv[0]}: {exc}") from exc
    if check and done.returncode:
        detail = (done.stderr or done.stdout or "no explanation").strip()
        raise CommandError(f"{' '.join(argv)} failed: {detail}")
    return done


def run_hal_module(python: str, home: Path, env: dict[str, str], module: str,
                   arguments: Sequence[str]) -> subprocess.CompletedProcess:
    """Run a HAL module from HAL home with its package root importable."""
    module_env = dict(env)
    existing = module_env.get("PYTHONPATH")
    module_env["PYTHONPATH"] = str(home) + (os.pathsep + existing if existing else "")
    return run([python, "-m", module, *arguments], env=module_env, cwd=home)


def native_platform() -> str:
    """Return the per-user service manager native to this host."""
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        return "systemd"
    if os.name == "nt" or sys.platform in ("win32", "cygwin", "msys"):
        return "schtasks"
    raise CommandError(f"unsupported platform {sys.platform!r}", 2)


def hal_home(value: Optional[str]) -> Path:
    """Resolve HAL_HOME at command execution time."""
    return Path(value or os.environ.get("HAL_HOME") or Path.home() / ".hal").expanduser().resolve()


def _venv_python(root: Path) -> Path:
    return root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def hal_python(home: Path, override: Optional[str]) -> str:
    """Choose the virtual-environment interpreter HAL's services run on.

    In order: ``--hal-python``; HAL's own venv under HAL_HOME (the VPS); GRIP's
    venv under ``$CLAUDE_DIR`` or ``~/.claude`` (what this Mac's live wire runs
    on, per its launchd plist). Never a system ``python3``: HAL's renderer
    refuses one, because Homebrew's python3 has no ``cryptography`` and the
    wire CA needs it, so a unit rendered on it would fail at first start.
    """
    # Never resolve() an interpreter path: a venv's bin/python is a symlink to
    # the base interpreter, and following it leaves the venv (and cryptography)
    # behind. Measured 2026-10-10: the resolved path was Homebrew's python3.14.
    if override:
        return str(Path(override).expanduser().absolute())
    grip_home = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude").expanduser()
    for candidate in (_venv_python(home), _venv_python(grip_home)):
        if candidate.is_file():
            return str(candidate.absolute())
    raise CommandError(
        "no virtual-environment interpreter for HAL: looked for "
        f"{_venv_python(home)} and {_venv_python(grip_home)}; pass --hal-python "
        "<venv>/bin/python (a venv that has the cryptography package)", 2)


def is_venv_python(python: str) -> bool:
    """Whether an interpreter has the virtual-environment marker beside it."""
    return (Path(python).parent.parent / "pyvenv.cfg").is_file()


def hal_env(home: Path, platform: str, store: str) -> dict[str, str]:
    """Environment shared by HAL's renderer and CA commands."""
    env = dict(os.environ, HAL_HOME=str(home))
    chosen = "file" if platform != "launchd" else store
    if chosen == "file":
        env["HAL_WIRE_CA_STORE"] = "file"
    return env


def catalogue(python: str, home: Path, env: dict[str, str]) -> dict:
    """Ask HAL for the service names, ports, and health identities."""
    done = run_hal_module(python, home, env, "lib.hal.deploy_render", ["list"])
    try:
        return json.loads(done.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CommandError("HAL's deploy renderer returned invalid JSON") from exc


def service_from(data: dict, key: str, platform: str, port: Optional[int]) -> Service:
    """Read one service from HAL's catalogue, refusing absent platform support."""
    details = data.get("services", {}).get(key)
    if not details:
        raise CommandError(f"HAL's deploy renderer does not support service {key!r}", 2)
    name = details.get("names", {}).get(platform)
    if not name:
        raise CommandError(f"service {key!r} is unsupported on platform {platform!r}", 2)
    chosen = port if port is not None else details.get("port")
    return Service(key, name, chosen, details.get("health"))


def brain_options(args: argparse.Namespace, require_paths: bool = True) -> dict[str, str]:
    """Check brain-only options before any render or service-manager action."""
    names = ("state_db", "pins_file", "bind", "tailscale_tag")
    given = {name: getattr(args, name, None) for name in names}
    if args.service != "brain":
        if any(value is not None for value in given.values()):
            raise CommandError("state DB, pins file, bind and Tailscale tag are brain only", 2)
        return {}
    if require_paths:
        for name in names[:2]:
            if given[name] is None:
                raise CommandError(f"the brain needs --{name.replace('_', '-')}", 2)
    for name in names[:2]:
        if given[name] is not None:
            given[name] = str(Path(given[name]).expanduser().absolute())
    return {name: value for name, value in given.items() if value is not None}


def brain_flags(options: dict[str, str]) -> list[str]:
    """Translate validated brain settings to HAL's renderer CLI."""
    return [item for name, value in options.items()
            for item in (f"--{name.replace('_', '-')}", value)]


def render(python: str, home: Path, platform: str, service: Service,
           store: str, env: dict[str, str], brain: Optional[dict[str, str]] = None) -> str:
    """Render one unit through HAL's documented command-line contract."""
    cmd = [python, "-m", "lib.hal.deploy_render", "render", "--platform", platform,
           "--service", service.key, "--python", python, "--hal-home", str(home),
           "--log-dir", str(home / "logs")]
    if service.port is not None:
        cmd += ["--port", str(service.port)]
    if service.key == "wire":
        cmd += ["--wire-ca-store", store]
    if service.key == "brain":
        cmd += brain_flags(brain or {})
    if platform == "schtasks":
        cmd += ["--user", os.environ.get("USERNAME") or os.environ.get("USER", "")]
    # HAL explicitly falls back to python3 where it has no venv; only the
    # renderer's on-disk venv check is skipped, never its escaping checks.
    if not is_venv_python(python):
        cmd.append("--no-check-paths")
    return run_hal_module(python, home, env, "lib.hal.deploy_render", cmd[3:]).stdout


def render_without_process(home: Path, python: str, platform: str, service: str,
                           port: Optional[int], store: str,
                           brain: Optional[dict[str, str]] = None) -> tuple[Service, str]:
    """Render a dry run in-process so it executes no external command."""
    path = home / "lib" / "hal" / "deploy_render.py"
    spec = importlib.util.spec_from_file_location("_hal_deploy_render_dry_run", path)
    if spec is None or spec.loader is None:
        raise CommandError(f"cannot load HAL's deploy renderer at {path}")
    module = importlib.util.module_from_spec(spec)
    old_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode
        sys.modules.pop(spec.name, None)
    svc = service_from(module.catalogue(), service, platform, port)
    try:
        text = module.render(platform, service, python=python, hal_home=str(home),
                             log_dir=str(home / "logs"), port=port,
                             wire_ca_store=store if service == "wire" else None,
                             user=(os.environ.get("USERNAME") or os.environ.get("USER"))
                             if platform == "schtasks" else None,
                             check_paths=is_venv_python(python),
                             **((brain or {}) if service == "brain" else {}))
    except Exception as exc:
        raise CommandError(f"HAL's deploy renderer refused the unit: {exc}", 2) from exc
    return svc, text


def unit_path(platform: str, service: Service) -> Path:
    """Return the persistent per-user definition path."""
    paths: dict[str, Callable[[], Path]] = {
        "launchd": lambda: Path.home() / "Library" / "LaunchAgents" / f"{service.name}.plist",
        "systemd": lambda: Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) /
        "systemd" / "user" / service.name,
        "schtasks": lambda: Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) /
        "GRIP" / "HAL" / f"{service.key}.task.xml",
    }
    return paths[platform]()


def manager_state(platform: str, service: Service) -> tuple[bool, Optional[int], str]:
    """Ask the native manager whether the unit is loaded and running."""
    checks = {"launchd": launchd_state, "systemd": systemd_state, "schtasks": schtasks_state}
    return checks[platform](service)


def launchd_state(service: Service) -> tuple[bool, Optional[int], str]:
    target = f"gui/{os.getuid()}/{service.name}"
    done = run(["launchctl", "print", target], check=False)
    pid = None
    for line in (done.stdout or "").splitlines():
        if line.strip().startswith("pid ="):
            with_value = line.split("=", 1)[1].strip()
            pid = int(with_value) if with_value.isdigit() else None
    return done.returncode == 0, pid, f"launchctl print {target}"


def systemd_state(service: Service) -> tuple[bool, Optional[int], str]:
    show = run(["systemctl", "--user", "show", service.name,
                "--property=LoadState,ActiveState,MainPID"], check=False)
    fields = dict(line.split("=", 1) for line in (show.stdout or "").splitlines() if "=" in line)
    loaded = show.returncode == 0 and fields.get("LoadState") == "loaded"
    raw_pid = fields.get("MainPID", "0")
    pid = int(raw_pid) if raw_pid.isdigit() and raw_pid != "0" else None
    return loaded, pid if fields.get("ActiveState") == "active" else None, \
        f"systemctl --user show {service.name}"


def schtasks_state(service: Service) -> tuple[bool, Optional[int], str]:
    done = run(["schtasks", "/Query", "/TN", service.name, "/FO", "LIST"], check=False)
    alive = done.returncode == 0 and "Running" in (done.stdout or "")
    return done.returncode == 0, 1 if alive else None, f"schtasks /Query /TN {service.name}"


# Directly to the service, whatever HTTP_PROXY or HTTPS_PROXY say: a proxy-free
# opener, as the reloader's own probe uses. urlopen() would send the check to a
# corporate proxy, or to the very wire being installed, when NO_PROXY does not
# name loopback (Codex on GRIP#7817).
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: Variables that route a shell's traffic through a loopback service
#: (the same list the reloader refuses to be run under without --force).
ROUTING_VARS = ("ANTHROPIC_BASE_URL", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                "http_proxy", "ALL_PROXY", "all_proxy", "OPENAI_BASE_URL")


def routed_through(port: Optional[int]) -> bool:
    """Whether this shell's own traffic goes through the service on ``port``."""
    if port is None:
        return False
    needles = (f"127.0.0.1:{port}", f"localhost:{port}")
    return any(needle in os.environ.get(var, "") for var in ROUTING_VARS for needle in needles)


def probe_health(port: Optional[int], identity: Optional[str], timeout: float = 1.5,
                 bind: str = "127.0.0.1") -> tuple[bool, str]:
    """Read /health without consulting proxy environment variables."""
    if port is None or not identity:
        return True, identity or "not applicable"
    try:
        with _NO_PROXY.open(f"http://{bind}:{port}/health", timeout=timeout) as answer:
            status = getattr(answer, "status", answer.getcode())
            body = json.loads(answer.read(65536).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return False, f"connection error: {type(exc).__name__}"
    found = body.get("service") if isinstance(body, dict) else None
    if status == 200 and body.get("ok") is True and found == identity:
        return True, str(found)
    return False, f"HTTP {status}, identity {found!r}"


def prove(service: Service, seconds: float = 30.0, bind: Optional[str] = None) -> bool:
    """Poll until the expected identity answers, or the deadline expires."""
    deadline, detail = time.monotonic() + seconds, "no attempt"
    while True:
        ok, detail = (probe_health(service.port, service.health, bind=bind or "127.0.0.1")
                      if service.key == "brain" else probe_health(service.port, service.health))
        if ok:
            print(f"PROVEN {detail}")
            return True
        if time.monotonic() >= deadline:
            print(f"FAILED {detail}")
            return False
        time.sleep(0.25)


def ca_report(python: str, home: Path, env: dict[str, str], command: str) -> dict:
    """Initialise or inspect the per-host wire CA."""
    done = run_hal_module(python, home, env, "lib.hal.wire_ca", [command])
    try:
        report = json.loads(done.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CommandError("HAL's wire CA command returned invalid JSON") from exc
    if not report.get("ok", True):
        raise CommandError(report.get("error", "HAL's wire CA command failed"))
    return report


def check_linger() -> None:
    """Refuse a systemd user unit that would be killed when its owner logs out."""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    done = run(["loginctl", "show-user", user, "-p", "Linger"], check=False)
    if done.returncode == 0 and done.stdout.strip() == "Linger=yes":
        return
    command = f"sudo loginctl enable-linger {user}"
    raise CommandError(f"systemd user services die at logout without linger. Run: {command}", 3)


def write_unit(path: Path, text: str) -> None:
    """Write a service definition with ordinary per-user permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def start_new(platform: str, service: Service, path: Path) -> None:
    """Load and start a definition that was not already loaded."""
    commands = {
        "launchd": ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        "systemd": ["systemctl", "--user", "enable", "--now", service.name],
        "schtasks": ["schtasks", "/Create", "/TN", service.name, "/XML", str(path), "/F"],
    }
    if platform == "systemd":
        run(["systemctl", "--user", "daemon-reload"])
    run(commands[platform])
    if platform == "schtasks":
        run(["schtasks", "/Run", "/TN", service.name])


def legacy_names(service: Service, platform: str) -> tuple[str, ...]:
    """Older names of the same service on this platform, from the reloader's
    presets (it owns them: ``com.grip.hal-gateway`` is what GRIP's earlier
    installers called the gateway's launchd agent)."""
    path = REPO / "lib" / "service_reload.py"
    spec = importlib.util.spec_from_file_location("_grip_service_reload_presets", path)
    if spec is None or spec.loader is None:
        return ()
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the reloader defines dataclasses, and on
    # Python 3.14 a dataclass resolves its annotations through
    # sys.modules[cls.__module__]; unregistered, that lookup is None.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    preset = module.PRESETS.get(service.key)
    return tuple(preset.legacy.get(platform, ())) if preset else ()


def loaded_legacy(platform: str, service: Service) -> list[str]:
    """Legacy units of this service that the manager still has loaded."""
    return [name for name in legacy_names(service, platform)
            if manager_state(platform, Service(service.key, name, service.port, service.health))[0]]


def reload_loaded(platform: str, service: Service, text: str, legacy: Sequence[str] = ()) -> None:
    """Use GRIP's rollback-capable reload for an installed definition."""
    suffix = {"launchd": ".plist", "systemd": ".service", "schtasks": ".xml"}[platform]
    with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8", delete=False) as tmp:
        tmp.write(text)
        definition = tmp.name
    try:
        cmd = [str(RELOAD), "reload", service.key, "--platform", platform,
               "--definition", definition]
        # A shell routed through this very service must not reload it in its
        # own process: the restart would cut the route mid-way. The reloader's
        # default is a detached job that is waited for, and --force says the
        # routing is deliberate. An unrouted shell keeps the simpler foreground.
        cmd += ["--force"] if routed_through(service.port) else ["--foreground"]
        for name in legacy:
            cmd += ["--legacy", name]
        if service.port is not None:
            cmd += ["--port", str(service.port)]
        if service.health:
            cmd += ["--health-service", service.health]
        run(cmd)
    finally:
        Path(definition).unlink(missing_ok=True)


def print_environment(port: int, ca_path: str) -> None:
    """Print only the three variables a wired shell session needs."""
    print(f"HTTPS_PROXY=http://127.0.0.1:{port}")
    print(f"NO_PROXY={NO_PROXY}")
    print(f"NODE_EXTRA_CA_CERTS={ca_path}")


def dry_run_install(home: Path, python: str, platform: str, args: argparse.Namespace,
                    store: str, brain: dict[str, str]) -> int:
    """Show the rendered definition without starting a manager."""
    service, text = render_without_process(home, python, platform, args.service,
                                           args.port, store, brain)
    if service.key == "wire":
        print(f"DRY-RUN initialise wire CA using {store} store")
    print(f"DRY-RUN write {unit_path(platform, service)}")
    print(text, end="" if text.endswith("\n") else "\n")
    print(f"DRY-RUN load and start {service.name}; prove {service.health}")
    return 0


def install(args: argparse.Namespace) -> int:
    """Render, install, start, prove, and explain one HAL service."""
    brain = brain_options(args)
    platform = native_platform() if args.platform == "auto" else args.platform
    home, python = hal_home(args.hal_home), hal_python(hal_home(args.hal_home), args.hal_python)
    store = "file" if platform != "launchd" else args.wire_ca_store
    if args.dry_run:
        return dry_run_install(home, python, platform, args, store, brain)
    env = hal_env(home, platform, store)
    service = service_from(catalogue(python, home, env), args.service, platform, args.port)
    text = render(python, home, platform, service, store, env, brain)
    if platform == "systemd":
        check_linger()
    # Only the wire terminates TLS. A brain or gateway install on a host that runs
    # no wire must not leave an unused private CA key there.
    ca = ca_report(python, home, env, "init") if service.key == "wire" else {}
    path = unit_path(platform, service)
    loaded, _, _ = manager_state(platform, service)
    legacy = loaded_legacy(platform, service)
    if loaded or legacy:
        # A legacy unit still owning the port would answer /health for a new
        # agent that never started; the reloader retires it in the same swap.
        reload_loaded(platform, service, text, legacy)
    else:
        write_unit(path, text)
        start_new(platform, service, path)
    proven = prove(service, bind=brain.get("bind")) if service.key == "brain" else prove(service)
    if not proven:
        return 1
    print_install_environment(service, ca, bind=brain.get("bind"))
    return 0


def print_install_environment(service: Service, ca: dict, bind: Optional[str] = None) -> None:
    """Print connection details appropriate to the installed service."""
    if service.key == "wire":
        print_environment(int(service.port), ca["ca_cert"])
    elif service.key == "gateway":
        print(f"HAL_GATEWAY_URL=http://127.0.0.1:{service.port}")
    elif service.key == "brain":
        print(f"HAL_BRAIN_URL=http://{bind or '127.0.0.1'}:{service.port}")


def context(args: argparse.Namespace) -> tuple[str, Path, str, dict, Service]:
    """Resolve common command context from HAL's authoritative catalogue."""
    chosen = getattr(args, "platform", "auto")
    platform = native_platform() if chosen == "auto" else chosen
    home = hal_home(getattr(args, "hal_home", None))
    python = hal_python(home, getattr(args, "hal_python", None))
    env = hal_env(home, platform, "auto")
    service = service_from(catalogue(python, home, env), args.service, platform,
                           getattr(args, "port", None))
    return platform, home, python, env, service


def installed_bind(platform: str, service: Service) -> Optional[str]:
    """The IPv4 address the installed brain unit listens on, read from the ``--bind``
    of its ``ExecStart`` line. None when there is no unit, no such line, or the value
    is not an IPv4 address: the caller then probes loopback, as it did before."""
    try:
        text = unit_path(platform, service).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = _EXEC_BIND.search(text)
    try:
        return str(ipaddress.IPv4Address(found.group(1))) if found else None
    except ValueError:
        return None


def status_bind(platform: str, service: Service, brain: dict[str, str]) -> str:
    """Where status probes the brain: ``--bind`` when given, else the address the
    installed unit starts it on, else loopback. After `install --bind 100.64.0.10` a
    plain `status` used to probe loopback and call a healthy brain unhealthy."""
    return brain.get("bind") or installed_bind(platform, service) or "127.0.0.1"


def status(args: argparse.Namespace) -> int:
    """Report manager, process, and health state as three stable lines."""
    brain = brain_options(args, require_paths=False)
    platform, _, _, _, service = context(args)
    loaded, pid, checked = manager_state(platform, service)
    healthy, identity = (probe_health(service.port, service.health,
                                      bind=status_bind(platform, service, brain))
                         if service.key == "brain" else probe_health(service.port, service.health))
    print(f"unit loaded: {'yes' if loaded else 'no'} ({checked})")
    print(f"process alive: {'yes' if pid else 'no'} (pid {pid if pid else '-'})")
    print(f"/health ok: {'yes' if healthy else 'no'} ({identity})")
    return 0 if loaded and pid is not None and healthy else 1


def stop_remove(platform: str, service: Service, path: Path, loaded: bool) -> None:
    """Stop, unload, and delete one per-user definition without touching HAL state."""
    commands = {
        "launchd": ["launchctl", "bootout", f"gui/{os.getuid()}/{service.name}"],
        "systemd": ["systemctl", "--user", "disable", "--now", service.name],
        "schtasks": ["schtasks", "/Delete", "/TN", service.name, "/F"],
    }
    if loaded:
        run(commands[platform])
    path.unlink(missing_ok=True)
    if platform == "systemd":
        run(["systemctl", "--user", "daemon-reload"])


def remove(args: argparse.Namespace) -> int:
    """Remove the selected unit and confirm the manager no longer has it."""
    platform, _, _, _, service = context(args)
    loaded, _, _ = manager_state(platform, service)
    stop_remove(platform, service, unit_path(platform, service), loaded)
    remaining, _, checked = manager_state(platform, service)
    print(f"unit loaded: {'yes' if remaining else 'no'} ({checked})")
    return 1 if remaining else 0


def environment(args: argparse.Namespace) -> int:
    """Print the wire variables and no other text."""
    home = hal_home(args.hal_home)
    python = hal_python(home, args.hal_python)
    platform = native_platform() if args.platform == "auto" else args.platform
    env = hal_env(home, platform, "auto")
    ca = ca_report(python, home, env, "show")
    print_environment(args.port, ca["ca_cert"])
    return 0


def _common_options(cmd: argparse.ArgumentParser) -> None:
    """The options every subcommand shares. The first VPS run of
    `status --platform systemd --hal-home ...` was refused as unrecognised while
    `install` took the same flags; one helper keeps the four in step."""
    cmd.add_argument("--platform", choices=PLATFORMS, default="auto")
    cmd.add_argument("--hal-home")
    cmd.add_argument("--hal-python")


def _brain_install_options(cmd: argparse.ArgumentParser) -> None:
    """Options that HAL's brain renderer accepts."""
    cmd.add_argument("--state-db", help="brain only: path of the brain's state database")
    cmd.add_argument("--pins-file", help="brain only: path of the brain's pins file")
    cmd.add_argument("--bind", help="brain only: IPv4 loopback or tailnet address to listen on")
    cmd.add_argument("--tailscale-tag", help="brain only: tag:name, required for a tailnet bind")


def parser() -> argparse.ArgumentParser:
    """Build the stable command-line interface."""
    root = argparse.ArgumentParser(prog="grip-hal-services")
    sub = root.add_subparsers(dest="command", required=True)
    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--service", choices=SERVICES, default="wire")
    install_cmd.add_argument("--port", type=int)
    install_cmd.add_argument("--wire-ca-store", choices=CA_STORES, default="auto")
    install_cmd.add_argument("--dry-run", action="store_true")
    _brain_install_options(install_cmd)
    _common_options(install_cmd)
    for command in ("status", "remove"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--service", choices=SERVICES, default="wire")
        if command == "status":
            cmd.add_argument("--port", type=int)
            cmd.add_argument("--bind", help="brain only: address to probe; the default is "
                                            "the --bind of the installed unit, else 127.0.0.1")
        _common_options(cmd)
    env_cmd = sub.add_parser("env")
    env_cmd.add_argument("--port", type=int, default=4011)
    _common_options(env_cmd)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments, dispatch one command, and print actionable failures."""
    args = parser().parse_args(argv)
    commands = {"install": install, "status": status, "remove": remove, "env": environment}
    try:
        return commands[args.command](args)
    except CommandError as exc:
        print(f"grip-hal-services: {exc}", file=sys.stderr)
        return exc.code
