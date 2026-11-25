#!/usr/bin/env python3
"""Measure what GRIP's hooks cost before their own logic runs (April audit task E3).

    measure_hook_startup.py counts            # hooks one call of each tool starts
    measure_hook_startup.py measure [--runs N]

``counts`` reads settings.json and works on any platform. ``measure`` times one
Bash call's Python PreToolUse hooks in three arms, interleaved round by round so
every arm sees the same machine load. The arm order cycles through every
permutation, so over each six rounds every arm runs first equally often and
follows each other arm equally often. It stops at the first round in which any
hook fails to load or any client exits non-zero, and names the command, because
a failed command finishes early and would make its arm look fast:

  separate_processes  each hook started as settings.json starts it, all at once
  one_process         all of them loaded in a single Python process
  daemon_round_trips  one round trip per hook to a warm Unix-socket server, with
                      a compiled client (nc), its own work not included. The
                      server answers one connection at a time, so the clients
                      queue: this arm is slower than a concurrent server doing
                      the same trivial work. A server doing each hook's real
                      work could be slower or faster.

Each hook is loaded under a name other than ``__main__``, so its ``main()`` never
runs and nothing it would write is written. ``measure`` needs ``os.wait4`` and
``nc -U``, so it runs on macOS and Linux only. The results and the kill criteria
this script measures are in docs/measurements/2026-11-24-hook-daemon-go-no-go.md.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "Agent", "WebFetch", "Skill")
_HOOK_FILE = re.compile(r"hooks/([A-Za-z0-9_.-]+\.py)")
# Loads each path given as an argument without running it as __main__, prints
# each one that fails to load to stderr, and exits 1 if any did, so a broken
# hook cannot make an arm look fast and the report can name it.
_LOADER = (
    "import importlib.util as u,sys\n"
    "bad=0\n"
    "for i,p in enumerate(sys.argv[1:]):\n"
    "    s=u.spec_from_file_location(f'h{i}',p);m=u.module_from_spec(s)\n"
    "    try:\n"
    "        s.loader.exec_module(m)\n"
    "    except BaseException as e:\n"
    "        bad+=1;sys.stderr.write(f'{p}: {type(e).__name__}\\n')\n"
    "sys.exit(1 if bad else 0)\n"
)


def matches(matcher: str | None, tool: str) -> bool:
    """The matcher rule GRIP's settings rely on: missing, empty or "*" matches
    every tool; anything else is a regex over the whole tool name."""
    return matcher in (None, "", "*") or re.fullmatch(matcher, tool) is not None


def _group_commands(group: dict) -> list[str]:
    return [h.get("command", "") for h in group.get("hooks", []) if h.get("type") == "command"]


def hook_commands(settings: dict, event: str, tool: str) -> list[str]:
    """The command hooks one call of ``tool`` starts for ``event``."""
    groups = [g for g in settings.get("hooks", {}).get(event, []) if matches(g.get("matcher"), tool)]
    return list(itertools.chain.from_iterable(map(_group_commands, groups)))


def python_hooks(settings: dict, event: str, tool: str) -> list[str]:
    """File names of the Python hooks in ``hook_commands``, in order."""
    found = (_HOOK_FILE.search(c) for c in hook_commands(settings, event, tool))
    return [m.group(1) for m in found if m]


def _tool_counts(settings: dict, tool: str) -> dict[str, int]:
    pre = hook_commands(settings, "PreToolUse", tool)
    post = hook_commands(settings, "PostToolUse", tool)
    return {"pre": len(pre), "post": len(post),
            "python": sum(1 for c in pre + post if _HOOK_FILE.search(c))}


def counts(settings: dict) -> dict[str, dict[str, int]]:
    return {tool: _tool_counts(settings, tool) for tool in TOOLS}


def _wait(proc: subprocess.Popen) -> float:
    _, status, usage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return usage.ru_utime + usage.ru_stime


def _failure(proc: subprocess.Popen, err) -> str:
    """One failed command: its exit code and the first line it wrote to stderr."""
    err.seek(0)
    first = err.read().decode("utf-8", "replace").strip().splitlines()[:1]
    return f"exit {proc.returncode}: {first[0] if first else 'no stderr'}"


def run_together(argvs: list[list[str]]) -> tuple[float, float, list[str]]:
    """Start every command at once, as Claude Code starts one event's hooks.
    Returns (wall ms until the last exits, summed child CPU ms, one line per
    command that exited non-zero). A failed command finishes early, so its
    timing is not comparable. Each stderr goes to a temporary file rather than
    a pipe, so a child that writes a lot cannot block before it exits."""
    errs = [tempfile.TemporaryFile() for _ in argvs]
    start = time.perf_counter()
    procs = [subprocess.Popen(a, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=e)
             for a, e in zip(argvs, errs)]
    cpu = sum(map(_wait, procs))
    wall = (time.perf_counter() - start) * 1000
    failures = [_failure(p, e) for p, e in zip(procs, errs) if p.returncode != 0]
    for err in errs:
        err.close()
    return wall, cpu * 1000, failures


def _serve(server: socket.socket) -> None:
    """Answer every connection until the socket is closed, then stop quietly."""
    try:
        while True:
            conn, _ = server.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(b'{"continue": true}')
    except OSError:
        return


def _start_server(path: str) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(64)
    threading.Thread(target=_serve, args=(server,), daemon=True).start()
    return server


def _arms(claude_dir: Path, names: list[str], sock: str) -> dict[str, list[list[str]]]:
    runner = str(claude_dir / "lib" / "grip-python.sh")
    paths = [str(claude_dir / "hooks" / n) for n in names]
    return {
        "separate_processes": [["bash", runner, "-c", _LOADER, p] for p in paths],
        "one_process": [["bash", runner, "-c", _LOADER, *paths]],
        "daemon_round_trips": [["bash", "-c", 'printf x | nc -U "$0"', sock] for _ in paths],
    }


def _one_round(arms: dict[str, list[list[str]]], index: int) -> dict[str, tuple[float, float, list[str]]]:
    """Run every arm once, in the order of the index-th permutation of the arms.

    A cyclic shift varied only which arm ran first, so each arm always followed
    the same other arm and inherited what it left in the caches (AGORA on
    GRIP#9554). Every permutation in turn balances both."""
    orders = list(itertools.permutations(arms))
    return {arm: run_together(arms[arm]) for arm in orders[index % len(orders)]}


def _first_failure(result: dict[str, tuple[float, float, list[str]]]) -> str | None:
    """The first failed command of a round, named with its arm, or None."""
    return next((f"{arm}: {timing[2][0]}" for arm, timing in result.items() if timing[2]), None)


def _timed_rounds(arms: dict[str, list[list[str]]], runs: int) -> list[dict]:
    """Run the rounds, stopping at the first one in which any command failed."""
    rounds = []
    for index in range(runs):
        result = _one_round(arms, index)
        failure = _first_failure(result)
        if failure:
            raise SystemExit(f"round {index + 1} failed, so the timings are not comparable: {failure}")
        rounds.append(result)
    return rounds


def _medians(samples: list[tuple[float, float, list[str]]]) -> dict[str, float]:
    return {"wall_ms": round(statistics.median(s[0] for s in samples), 1),
            "cpu_ms": round(statistics.median(s[1] for s in samples), 1)}


def measure(claude_dir: Path, settings: dict, runs: int) -> dict:
    """Interleaved timing of one Bash call's Python PreToolUse hooks."""
    if not hasattr(os, "wait4"):
        raise SystemExit("measure needs os.wait4 and nc -U: run it on macOS or Linux")
    names = python_hooks(settings, "PreToolUse", "Bash")
    with tempfile.TemporaryDirectory() as tmp:
        sock = os.path.join(tmp, "hooks.sock")
        server = _start_server(sock)
        try:
            arms = _arms(claude_dir, names, sock)
            load_before = os.getloadavg()
            rounds = _timed_rounds(arms, runs)
            load_after = os.getloadavg()
        finally:
            server.close()
    return {"hooks": len(names), "runs": runs,
            "load_before": [round(x, 1) for x in load_before],
            "load_after": [round(x, 1) for x in load_after],
            "arms": {arm: _medians([r[arm] for r in rounds]) for arm in arms}}


def _positive_int(text: str) -> int:
    """argparse type for --runs: zero rounds would leave nothing to take a median of."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure GRIP hook start-up (April audit task E3).")
    parser.add_argument("mode", choices=("counts", "measure"))
    parser.add_argument("--runs", type=_positive_int, default=18,
                        help="interleaved rounds for measure; a multiple of 6 balances the arm order")
    args = parser.parse_args(argv)
    claude_dir = get_claude_dir()
    settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
    result = counts(settings) if args.mode == "counts" else measure(claude_dir, settings, args.runs)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
