"""Windows process tables for GRIP's session counters and lock checks (GRIP#8280, GRIP#8835).

Git Bash on Windows has no ``pgrep``, so a counter that asks it never sees a second
session. psutil finds the processes instead, by program name as ``pgrep -x`` does,
and reports Windows PIDs. The Claude desktop app runs its own ``claude.exe``
processes, and they are Windows GUI programs, while a terminal session's
``claude.exe`` is a console program. The Subsystem field of the PE header tells
them apart, so a GUI program never counts as a session (measured 2026-10-29: ten
GUI ``claude.exe`` processes beside the CLI's one).

Every counter that needs this answer on Windows asks ``console_pids``: the session
wizard, the Gate O counter in ``lib/gates/session_isolation.py``, the heartbeat's
session check and, through ``python lib/windows_processes.py count``, the gg++
counter in ``lib/gg-concurrency.sh``. Each one passes the names its POSIX branch
gives ``pgrep -x``, so both platforms count the same thing.

A branch lock and a launch claim record a Git Bash PID, which psutil cannot see.
Git's ``ps -W`` maps each Git Bash PID to its Windows PID, so the session wizard
and ``lib/session_coordinator.py`` both judge a lock's owner from that listing
(``git_bash_pid_table``). The control server's PID file holds bash's ``$!``, also
a Git Bash PID, so ``hooks/session-broadcast.py`` judges it the same way
(``git_bash_pid_alive``, GRIP#8883).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from typing import Iterable

PE_GUI = 2  # the PE Subsystem value of a windowed program, which is never a terminal session


def pe_subsystem(path: str) -> int | None:
    """The Subsystem field of the PE header at ``path``, or None when it cannot be read."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0x3C)
            fh.seek(struct.unpack("<I", fh.read(4))[0])
            header = fh.read(4 + 20 + 70)
    except (OSError, struct.error):
        return None
    if len(header) < 4 + 20 + 70 or header[:4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", header, 4 + 20 + 68)[0]


def console_pids(names: Iterable[str]) -> set[int] | None:
    """Windows PIDs of live console processes named ``<name>.exe``, or None.

    A process counts when its name is ``<name>.exe`` for one of ``names``, compared
    without case, and its program is not a Windows GUI program. A program that
    cannot be read counts. Parent PIDs are never asked for, because psutil took
    2.7 s for one scan that asked for them. None means the table could not be read
    (no psutil, or psutil failed mid-scan), and each caller decides what that means.
    """
    try:
        import psutil
    except ImportError:
        return None
    wanted = {f"{name}.exe".lower() for name in names}
    gui: dict[str, bool] = {}
    pids: set[int] = set()
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            if (proc.info.get("name") or "").lower() not in wanted:
                continue
            exe = proc.info.get("exe")
            if exe and exe not in gui:
                gui[exe] = pe_subsystem(exe) == PE_GUI
            if not (exe and gui[exe]):
                pids.add(proc.pid)
    except psutil.Error:
        return None
    return pids


FOREIGN_PID_BASE = 4194304  # ps -W lists a process Git Bash did not start as this plus its Windows PID


def parse_ps_w(text: str) -> dict[int, int] | None:
    """``{PID: WINPID}`` from Git's ``ps -W`` listing, or None when ``text`` is not one.

    Cygwin's ps can start a row with a one-letter status, which moves every field
    one place (``lib/grip-paths.sh`` reads the same listing). A process Git Bash did
    not start is listed as ``FOREIGN_PID_BASE`` plus its Windows PID.
    """
    lines = text.splitlines()
    if not lines or lines[0].split()[:4] != ["PID", "PPID", "PGID", "WINPID"]:
        return None
    table: dict[int, int] = {}
    for line in lines[1:]:
        fields = line.split()
        if fields and len(fields[0]) == 1 and fields[0].isalpha():
            fields = fields[1:]
        if len(fields) > 3 and fields[0].isdigit() and fields[3].isdigit():
            table[int(fields[0])] = int(fields[3])
    return table


def git_bash_pid_table(runner=None) -> dict[int, int] | None:
    """Git's ``ps -W`` listing as ``{PID: WINPID}``, or None when it cannot be read.

    No owner is checked here. Another Cygwin-family ``ps`` lists a different set of
    processes, so a caller that judges a Git Bash PID first checks that the listing
    holds a process it knows belongs to the same Git Bash.
    """
    ps = shutil.which("ps")
    if ps is None:
        return None
    run = runner or subprocess.run
    try:
        res = run([ps, "-W"], capture_output=True, encoding="utf-8",
                  errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return parse_ps_w(res.stdout or "")


def started_by_git_bash(table: dict[int, int], winpid: int) -> bool:
    """True when the process with Windows PID ``winpid`` has a Git Bash PID in ``table``."""
    return any(win == winpid and pid < FOREIGN_PID_BASE for pid, win in table.items())


def pid_listed(table: dict[int, int], pid: int) -> bool:
    """True when ``pid`` is in ``table`` as a Git Bash PID or as a Windows PID.

    A lock can record its owner either way: ``bin/grip`` passes the launcher's Git
    Bash PID, and a direct run of the wizard falls back to a Windows PID.
    """
    return pid in table or pid in table.values()


def git_bash_pid_alive(pid: int, *, when_unknown: bool) -> bool:
    """True while Git's ``ps -W`` lists ``pid``, as a Git Bash PID or a Windows PID.

    The listing is read fresh for every call. It is trusted only when this process's
    parent has a Git Bash PID in it, which shows that it lists the Git Bash the PID
    came from. Otherwise the answer is ``when_unknown``: with no listing, with a
    listing that lacks the parent, and when the parent's PID cannot be read (on
    Windows ``os.getppid`` raises OSError when both of CPython's lookups fail). Each
    caller chooses that answer: a lock check keeps the lock (True), and a socket
    lookup skips the socket (False).
    """
    try:
        parent = os.getppid()
    except OSError:
        return when_unknown
    table = git_bash_pid_table()
    if table is None or not started_by_git_bash(table, parent):
        return when_unknown
    return pid_listed(table, pid)


def main(argv: list[str] | None = None) -> int:
    """``count NAME...`` prints how many live console processes have one of the names.

    For ``lib/gg-concurrency.sh``, which runs where Git Bash has no ``pgrep``. It
    prints nothing and exits 1 when the processes cannot be read, so the caller
    keeps its own fail-safe count.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2 or args[0] != "count":
        print("usage: windows_processes.py count NAME...", file=sys.stderr)
        return 2
    pids = console_pids(args[1:])
    if pids is None:
        return 1
    print(len(pids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
