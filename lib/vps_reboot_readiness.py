#!/usr/bin/env python3
"""VPS reboot readiness: prove every pm2 app and the hal-wire unit survive a
reboot, with a pre-reboot safety gate and a before/after proof pair.

Backs ``scripts/vps-reboot-readiness.sh``, which is a thin argv-forwarding
wrapper. The logic lives here so it is unit-testable (and readable) without
a second shell layer.

Three verbs:
  gate      -- is it safe to reboot RIGHT NOW?
  snapshot  -- capture a fact set worth comparing against later.
  verify    -- re-capture those facts and prove a genuine reboot happened.

GATE DEFINITION. A reboot is safe when BOTH hold:
  (a) hal-gateway (:4010) holds no established TCP connection. It is a
      genuine synchronous/streaming reverse proxy, so a connection there
      really does mean an upstream call is in flight.
  (b) no relay turn is in flight, read from whether
      ``state/relay/inflight/`` holds any marker file.

(b) deliberately does NOT read connection state on relay-dispatcher's own
port (:8787). An earlier design did, and two independent reviews falsified
it by reading ``lib/relay_dispatcher.py`` directly: ``Handler.do_POST``
enqueues the event and answers HTTP 202 within milliseconds: the actual
work -- including the up-to-300s-by-code-default (120s as actually deployed,
``config/relay-dispatcher.config.cjs``) subprocess spawn -- runs later, on a
detached per-channel worker thread, fully decoupled from the HTTP
connection's lifetime. So a TCP read on :8787 reads CLEAR almost
continuously, including while a turn is genuinely running, which is exactly
the "cannot tell done from not done" failure a reboot gate must never have.
``RelayDispatcher.process()`` now writes and clears a marker file for the
exact span of one turn (see ``lib/relay_dispatcher.py``'s own
``_mark_inflight``/``_clear_inflight``), which is the thing this gate reads.

A probe that cannot even run (``ss``/the marker directory unreadable) is
NEVER read as "0 found" / "clear": unknown must never look like fine, so
every BUSY-vs-CLEAR decision below fails toward BUSY on a broken probe.
Two corollaries added after the GRIP#7874 review, same doctrine:

  * An EMPTY marker listing is trusted only after the gate proves the
    marker MECHANISM works (a write-probe file in the directory) --
    ``_mark_inflight`` swallows write failures by design, so an unwritable
    directory would otherwise read CLEAR during a live turn (finding 1).
  * A marker whose recorded ``pid`` is provably dead is STALE, not live --
    a dispatcher killed mid-turn never clears its marker, and counting it
    would wedge the gate BUSY forever (finding 3). Unparseable or pidless
    markers still count as live: cannot-prove-dead fails toward BUSY.

The never-vacuous rule extends to the pm2 inventory (finding 2): a FAILED
``pm2 jlist`` probe reports ``None``, never ``[]``; ``snapshot`` refuses to
write an empty/None inventory, and ``verify`` refuses to pass one, so a
broken probe can never satisfy ``[] == []`` vacuously.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_HAL_GATEWAY_PORT = 4010
DEFAULT_BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
DEFAULT_PM2_TIMEOUT_S = 60.0
DEFAULT_PM2_POLL_S = 5.0
_PID_RE = re.compile(r"pid (\d+)")


def claude_dir() -> Path:
    """Resolve CLAUDE_DIR the way the rest of GRIP does (path-portability.md)."""
    return Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude").expanduser()


def inflight_dir(base: Optional[Path] = None) -> Path:
    """Where ``RelayDispatcher.process()`` marks a turn as running."""
    return (base or claude_dir()) / "state" / "relay" / "inflight"


def default_snapshot_dir(base: Optional[Path] = None) -> Path:
    """Where a ``snapshot`` lands when the caller names no path."""
    return (base or claude_dir()) / "state" / "reboot-readiness"


def run(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run one read-only probe command, tolerating its absence or a hang.

    Never raises: a missing binary or a timeout becomes a non-zero exit with
    the reason in stderr, so every caller has one failure shape to check.
    """
    try:
        return subprocess.run(
            list(argv), capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), 1, "", str(exc))


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """What the gate saw, and the one-line verdict.

    ``inflight`` is the LIVE markers (a turn is genuinely running);
    ``stale`` is ``(name, pid)`` pairs left behind by provably-dead
    dispatchers -- reported, but never counted as busy.
    """

    ok: bool
    established: int
    inflight: tuple
    stale: tuple
    detail: str


def established_connections(port: int, ss_bin: str = "ss") -> tuple:
    """Count established TCP connections whose LOCAL side is ``port``.

    Returns ``(count, probe_ok)``. Filters via ``ss``'s own ``sport =``
    expression rather than parsing output columns, so this never depends on
    a particular iproute2 version's header shape. ``-H`` suppresses the
    header line entirely, so every surviving line IS one connection.
    """
    result = run([ss_bin, "-Htn", "state", "established", f"( sport = :{port} )"])
    if result.returncode != 0:
        return 0, False
    count = len([line for line in result.stdout.splitlines() if line.strip()])
    return count, True


def _pid_alive(pid: int) -> bool:
    """True iff ``pid`` names a running process. ``os.kill(pid, 0)`` sends no
    signal: ``PermissionError`` means the process EXISTS (another user's);
    only ``ProcessLookupError`` proves it is gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _marker_pid(path: Path) -> Optional[int]:
    """The dispatcher pid a marker records, or None when the marker cannot be
    parsed or carries no usable pid -- in which case it must count as LIVE
    (a marker we cannot prove dead may guard a running turn)."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = record.get("pid") if isinstance(record, dict) else None
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        return pid
    return None


def inflight_markers(directory: Path) -> tuple:
    """Live relay-turn markers, plus markers provably left by dead dispatchers.

    Returns ``(live_names, stale, probe_ok)`` where ``stale`` is a tuple of
    ``(name, pid)`` pairs. A marker whose JSON records a pid that no longer
    exists was left by a dispatcher killed mid-turn -- its ``process()``
    finally-clause died with it -- and counting it as busy would wedge the
    gate BUSY forever (GRIP#7874 finding 3). Stale markers are therefore
    listed separately, never counted as live. Unparseable or pidless markers
    ARE counted as live: cannot-prove-dead fails toward BUSY, the module's
    design rule.

    A directory that does not exist yet is a genuine "no turn has ever run"
    state -- relay-dispatcher creates it lazily on its first turn -- not a
    probe failure. A directory that exists but cannot be listed
    (permissions) IS a probe failure.
    """
    if not directory.exists():
        return (), (), True
    try:
        paths = sorted(p for p in directory.iterdir() if p.is_file())
    except OSError:
        return (), (), False
    live = []
    stale = []
    for path in paths:
        pid = _marker_pid(path)
        if pid is not None and not _pid_alive(pid):
            stale.append((path.name, pid))
        else:
            live.append(path.name)
    return tuple(live), tuple(stale), True


def marker_mechanism_usable(directory: Path) -> tuple:
    """``(usable, reason)``: could the dispatcher write a marker here NOW?

    An empty (or absent) marker listing only proves "no turn in flight" if
    the marker mechanism itself works. ``_mark_inflight`` swallows OSError BY
    DESIGN (a full disk must not stop a relay turn), so with an unwritable
    directory no marker ever appears and the gate would read that empty
    listing as CLEAR *while a turn runs* (GRIP#7874 finding 1). Creating and
    removing ``.gate-probe-<pid>`` verifies the mechanism at gate time
    instead of trusting it. A directory that cannot be created is the same
    failure: the dispatcher's lazy ``mkdir`` would fail too.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create marker directory {directory}: {exc}"
    probe = directory / f".gate-probe-{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write a probe marker in {directory}: {exc}"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass  # a leftover probe file only ever fails the gate toward BUSY
    return True, ""


def gate_check(
    port: int = DEFAULT_HAL_GATEWAY_PORT,
    ss_bin: str = "ss",
    marker_dir: Optional[Path] = None,
) -> GateResult:
    """One point-in-time reboot-safety check. See the module docstring."""
    directory = marker_dir or inflight_dir()
    established, ss_ok = established_connections(port, ss_bin)
    inflight, stale, inflight_ok = inflight_markers(directory)
    mechanism_ok, mechanism_detail = True, ""
    if inflight_ok and not inflight:
        # Trust an empty listing only after proving a marker COULD be
        # written -- the gate verifies the mechanism, not just its output.
        mechanism_ok, mechanism_detail = marker_mechanism_usable(directory)
    probed_ok = ss_ok and inflight_ok and mechanism_ok
    ok = probed_ok and established == 0 and not inflight
    if not (ss_ok and inflight_ok):
        detail = "could not read connection or in-flight state -- treating as BUSY"
    elif not mechanism_ok:
        detail = (
            "in-flight marker mechanism unavailable "
            f"({mechanism_detail}) -- treating as BUSY"
        )
    elif ok:
        detail = f"0 established connections on :{port}, no relay turn in flight"
    else:
        detail = f"established:{port}={established} inflight={len(inflight)}"
    return GateResult(ok, established, inflight, stale, detail)


def cmd_gate(args: argparse.Namespace) -> int:
    """Print the gate verdict; exit 0 only when it is genuinely CLEAR."""
    result = gate_check(args.hal_gateway_port, args.ss, Path(args.inflight_dir))
    print(f"GATE: {'CLEAR' if result.ok else 'BUSY'} -- {result.detail}")
    for name in result.inflight:
        print(f"  in flight: {name}")
    for name, pid in result.stale:
        print(f"  stale marker {name} (pid {pid} dead)")
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def read_boot_id(path: Path = DEFAULT_BOOT_ID_FILE) -> str:
    """The kernel's own per-boot random id -- the one fact that can PROVE a
    reboot happened, as opposed to a service merely having restarted."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def pm2_processes(pm2_bin: str = "pm2") -> Optional[list]:
    """Sorted ``[{"name", "status"}]`` for every pm2-managed process.

    Returns ``None`` when the probe itself FAILED -- pm2 missing, a non-zero
    exit, unparseable output, or output that is not a list. ``[]`` means the
    probe ran and pm2 genuinely manages nothing. The old code coerced every
    failure to ``[]``, which let a failed probe pass verification vacuously
    (``[] == []``, GRIP#7874 finding 2): a broken probe must never look like
    an empty-but-fine inventory.
    """
    result = run([pm2_bin, "jlist"])
    if result.returncode != 0:
        return None
    try:
        procs = json.loads(result.stdout or "")
    except ValueError:
        return None
    if not isinstance(procs, list):
        return None
    rows = [
        {"name": p.get("name"), "status": (p.get("pm2_env") or {}).get("status")}
        for p in procs
        if isinstance(p, dict)
    ]
    rows.sort(key=lambda row: row["name"] or "")
    return rows


def wire_status(hal_services_bin: str = "grip-hal-services") -> dict:
    """``grip-hal-services status --service wire``'s verdict, plus the pid
    parsed out of "process alive: yes (pid N)" -- the one field ``verify``
    needs to prove a GENUINE restart rather than a stale read."""
    result = run([hal_services_bin, "status", "--service", "wire"])
    output = (result.stdout or "") + (result.stderr or "")
    match = _PID_RE.search(output)
    return {
        "exit_code": result.returncode,
        "output": output.strip().splitlines(),
        "pid": int(match.group(1)) if match else None,
    }


def build_snapshot(
    pm2_bin: str = "pm2",
    hal_services_bin: str = "grip-hal-services",
    boot_id_file: Path = DEFAULT_BOOT_ID_FILE,
) -> dict:
    """Everything a before/after comparison needs, captured once."""
    return {
        "captured_at": time.time(),
        "boot_id": read_boot_id(boot_id_file),
        "pm2": pm2_processes(pm2_bin),
        "wire": wire_status(hal_services_bin),
    }


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Write a snapshot JSON file and print its path (verify needs it).

    REFUSES to write when the pm2 inventory is None (probe failed) or empty
    (GRIP#7874 finding 2): a snapshot without an inventory makes the
    after-check compare ``[] == []`` and pass vacuously -- a fact set that
    cannot fail verification is worse than no fact set, because it looks
    like proof.
    """
    snapshot = build_snapshot(args.pm2, args.hal_services, Path(args.boot_id_file))
    inventory = snapshot["pm2"]
    if not inventory:
        reason = (
            "the pm2 probe failed (pm2 missing, non-zero exit, or unparseable "
            "output)" if inventory is None else "the pm2 inventory is empty"
        )
        print(
            f"SNAPSHOT REFUSED: {reason} -- a snapshot without a pm2 "
            "inventory would pass verify vacuously"
        )
        return 1
    if args.path:
        path = Path(args.path)
    else:
        out_dir = default_snapshot_dir()
        path = out_dir / f"snapshot-{int(snapshot['captured_at'])}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(str(path))
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    """One named PASS/FAIL result inside a ``verify`` run."""

    name: str
    ok: bool
    detail: str


def _retry_until(predicate, timeout_s: float, interval_s: float) -> CheckOutcome:
    """Poll ``predicate`` until it passes or ``timeout_s`` elapses, returning
    the LAST outcome either way.

    Startup lag across many pm2 processes is normal right after a reboot; a
    single immediate read would misread that lag as a resurrection failure.
    """
    deadline = time.monotonic() + timeout_s
    outcome = predicate()
    while not outcome.ok and time.monotonic() < deadline:
        time.sleep(interval_s)
        outcome = predicate()
    return outcome


def _check_boot_id_changed(before: dict, after_file: Path) -> CheckOutcome:
    """A changed boot_id is the mechanical proof a reboot actually happened."""
    before_id = before.get("boot_id", "")
    after_id = read_boot_id(after_file)
    changed = bool(before_id) and bool(after_id) and before_id != after_id
    return CheckOutcome(
        "boot_id changed", changed,
        f"{before_id or '(unreadable)'} -> {after_id or '(unreadable)'}",
    )


def _check_pm2_online(before: dict, pm2_bin: str) -> CheckOutcome:
    """Same set of pm2 app names, all reporting online. Restart counters are
    expected to change and are deliberately not compared here.

    Fails closed, and the detail names WHICH case failed (GRIP#7874 finding
    2): the after-probe returned None (pm2 itself broken -- the old code
    coerced that to ``[]``), the snapshot's inventory is empty (nothing to
    verify against -- the old ``[] == []`` vacuous pass), or the name sets
    differ / an app is not online.
    """
    before_rows = before.get("pm2") or []
    before_names = sorted(row["name"] for row in before_rows)
    after = pm2_processes(pm2_bin)
    if after is None:
        return CheckOutcome(
            "pm2 apps online", False,
            "pm2 probe failed (missing, non-zero exit, or unparseable output) "
            "-- cannot verify, treating as FAIL",
        )
    if not before_names:
        return CheckOutcome(
            "pm2 apps online", False,
            "snapshot pm2 inventory is empty -- nothing to verify against, "
            "refusing a vacuous pass",
        )
    after_names = sorted(row["name"] for row in after)
    not_online = [row["name"] for row in after if row.get("status") != "online"]
    ok = after_names == before_names and not not_online
    detail = (
        f"{len(after_names)} names present (expected {len(before_names)}), "
        f"{len(not_online)} not online"
    )
    return CheckOutcome("pm2 apps online", ok, detail)


def _check_wire_restarted(before: dict, hal_services_bin: str) -> CheckOutcome:
    """hal-wire answers healthy AND carries a NEW pid -- proof of a genuine
    restart, not a stale read of a process that never stopped."""
    before_pid = before.get("wire", {}).get("pid")
    after = wire_status(hal_services_bin)
    after_pid = after.get("pid")
    ok = after.get("exit_code") == 0 and after_pid is not None and after_pid != before_pid
    return CheckOutcome(
        "hal-wire restarted", ok,
        f"exit={after.get('exit_code')} pid {before_pid} -> {after_pid}",
    )


def verify_snapshot(
    before: dict,
    *,
    pm2_bin: str = "pm2",
    hal_services_bin: str = "grip-hal-services",
    boot_id_file: Path = DEFAULT_BOOT_ID_FILE,
    pm2_timeout_s: float = DEFAULT_PM2_TIMEOUT_S,
    pm2_poll_s: float = DEFAULT_PM2_POLL_S,
) -> list:
    """Run all three post-reboot checks against one captured ``before``."""
    return [
        _check_boot_id_changed(before, boot_id_file),
        _retry_until(lambda: _check_pm2_online(before, pm2_bin), pm2_timeout_s, pm2_poll_s),
        _check_wire_restarted(before, hal_services_bin),
    ]


def cmd_verify(args: argparse.Namespace) -> int:
    """Print PASS/FAIL per check plus one verdict line; exit 0 only if all pass."""
    before = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    outcomes = verify_snapshot(
        before,
        pm2_bin=args.pm2,
        hal_services_bin=args.hal_services,
        boot_id_file=Path(args.boot_id_file),
        pm2_timeout_s=args.pm2_timeout,
        pm2_poll_s=args.pm2_poll,
    )
    for outcome in outcomes:
        print(f"{'PASS' if outcome.ok else 'FAIL'}: {outcome.name} -- {outcome.detail}")
    passed = all(outcome.ok for outcome in outcomes)
    print(f"REBOOT-READINESS: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _common_probe_options(parser: argparse.ArgumentParser) -> None:
    """Options ``snapshot`` and ``verify`` share: the probe binaries' names,
    injectable so tests can point them at stub executables on a fake PATH."""
    parser.add_argument("--pm2", default="pm2")
    parser.add_argument("--hal-services", default="grip-hal-services")
    parser.add_argument("--boot-id-file", default=str(DEFAULT_BOOT_ID_FILE))


def build_parser() -> argparse.ArgumentParser:
    """The stable command-line interface: gate, snapshot, verify."""
    root = argparse.ArgumentParser(prog="vps-reboot-readiness")
    sub = root.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("gate", help="is it safe to reboot right now?")
    gate.add_argument("--ss", default="ss")
    gate.add_argument("--hal-gateway-port", type=int, default=DEFAULT_HAL_GATEWAY_PORT)
    gate.add_argument("--inflight-dir", default=str(inflight_dir()))

    snapshot = sub.add_parser("snapshot", help="capture a before fact set")
    snapshot.add_argument("path", nargs="?", default=None)
    _common_probe_options(snapshot)

    verify = sub.add_parser("verify", help="check a snapshot against reality now")
    verify.add_argument("snapshot")
    verify.add_argument("--pm2-timeout", type=float, default=DEFAULT_PM2_TIMEOUT_S,
                         help="seconds to tolerate pm2 apps still starting up")
    verify.add_argument("--pm2-poll", type=float, default=DEFAULT_PM2_POLL_S,
                         help="seconds between pm2 retries within --pm2-timeout")
    _common_probe_options(verify)

    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments and dispatch to one of the three verbs."""
    args = build_parser().parse_args(argv)
    commands = {"gate": cmd_gate, "snapshot": cmd_snapshot, "verify": cmd_verify}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
