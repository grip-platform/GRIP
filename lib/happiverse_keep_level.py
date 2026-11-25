"""happiverse_keep_level — keep every HAPPIverse machine level with main.

V>> chose on 2026-09-24 that Blackbook PUSHES main to the other machines rather
than each pulling for itself. Two of them could not pull at all: the second Mac's
GitHub sign-in had been failing silently since July (its checkout sat 1,651
commits behind while its self-deploy exited 0), and ol ancient never had one.
The VPS can fetch, but by design its self-deploy never advances HEAD, so it
drifted too. One job on the machine that holds the SSH key fixes all three with
no new credential anywhere.

For each machine in config/happiverse-fleet.txt that has both ``--ssh`` and
``--repo``: push main's tip into the machine's ``refs/remotes/origin/main``
(never forced), then run scripts/happiverse-advance.sh ON the machine to move
its checkout forward. Blackbook itself is measured locally and has no ``--ssh``;
its shared tree is kept level by grip-tree-sync, not here.

The script is sent over SSH on stdin, so a machine always runs the current copy
rather than whatever version its own checkout holds — which may be the very
thing that is out of date.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib.happiverse_node_check import canonical_main  # noqa: E402

ADVANCE_SCRIPT = REPO / "scripts" / "happiverse-advance.sh"
LEVEL_OUTCOMES = ("ALREADY", "UPDATED")

# (destination, shell command, stdin) -> (exit code or None when unreachable, output)
Execute = Callable[[str, str, Optional[str]], tuple]


@dataclass(frozen=True)
class Machine:
    nickname: str
    ssh: str
    repo: str


def parse_fleet(text: str) -> list:
    """The machines this job can reach: those given both --ssh and --repo.

    The args half of a fleet line is split with shlex and never evaluated, so a
    hostile line can at worst name the wrong machine; it cannot run anything."""
    machines = []
    for line in text.splitlines():
        if "|" not in line or line.lstrip().startswith("#"):
            continue
        nickname, args = line.split("|", 1)
        opts = _options(args)
        if opts.get("--ssh") and opts.get("--repo"):
            machines.append(Machine(nickname.strip(), opts["--ssh"], opts["--repo"]))
    return machines


def _options(args: str) -> dict:
    try:
        words = shlex.split(args)
    except ValueError:
        return {}
    return {w: words[i + 1] for i, w in enumerate(words[:-1]) if w in ("--ssh", "--repo")}


def ssh_execute(dest: str, command: str, stdin: Optional[str] = None) -> tuple:
    """Run ``command`` on ``dest`` over SSH. None as the exit code means the
    command never ran — ssh's own transport failure, not the command's."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", dest, command]
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    output = (p.stdout or "") + (p.stderr or "")
    return (None if p.returncode == 255 else p.returncode), output


def resolve_repo(machine: Machine, execute: Execute) -> Optional[str]:
    """The machine's repository as an absolute path, symlinks resolved.
    ``--repo .claude`` is relative to the SSH user's home, and a push needs the
    real path: on ol ancient ~/.claude is a link to ~/.grip."""
    rc, out = execute(machine.ssh, f"cd {shlex.quote(machine.repo)} && pwd -P", None)
    paths = [line.strip() for line in (out or "").splitlines() if line.strip().startswith("/")]
    return paths[-1] if rc == 0 and paths else None


def push_main(local_repo: str, url: str, tip: str,
              run: Callable = subprocess.run) -> tuple:
    """Push main's tip into the machine's refs/remotes/origin/main, never forced.
    A machine already holding a NEWER main (the VPS fetches for itself) keeps it:
    that rejection is success, and the advance step reads what the machine has."""
    p = run(["git", "-C", local_repo, "push", "-q", url, f"{tip}:refs/remotes/origin/main"],
            capture_output=True, text=True, timeout=900)
    detail = ((p.stderr or "") + (p.stdout or "")).strip()
    if p.returncode == 0:
        return True, "pushed"
    if "(non-fast-forward)" in detail or "(fetch first)" in detail:
        return True, "the machine already holds a newer main"
    return False, detail[-200:]


def advance(dest: str, repo_path: str, tip: str, execute: Execute) -> str:
    """Run scripts/happiverse-advance.sh on the machine; return its RESULT."""
    command = f"bash -s -- {shlex.quote(repo_path)} {shlex.quote(tip)}"
    rc, out = execute(dest, command, ADVANCE_SCRIPT.read_text())
    if rc is None:
        return f"UNREACHABLE {out.strip()[:160]}"
    result = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    return result[len("RESULT "):] if result else f"NO-RESULT rc={rc} {out.strip()[-160:]!r}"


def keep_machine_level(machine: Machine, local_repo: str, tip: str, execute: Execute,
                       push: Callable = push_main,
                       url_for: Callable = lambda m, path: f"ssh://{m.ssh}{path}") -> str:
    """Push, then advance. One outcome line, never an exception."""
    path = resolve_repo(machine, execute)
    if path is None:
        return "UNREACHABLE could not reach the machine or its repository"
    ok, detail = push(local_repo, url_for(machine, path), tip)
    if not ok:
        return f"PUSH-FAILED {detail}"
    return advance(machine.ssh, path, tip, execute)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="grip-happiverse-keep-level",
                                 description="Push main to every HAPPIverse machine and move it forward.")
    ap.add_argument("--fleet", default=str(REPO / "config" / "happiverse-fleet.txt"))
    ap.add_argument("--main-repo", default=str(REPO),
                    help="repository whose freshly fetched origin/main is pushed")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen; push nothing")
    ns = ap.parse_args(argv)

    canon = canonical_main(ns.main_repo)
    if canon is None:
        print("main could not be read on this machine; nothing was pushed")
        return 2
    worst = 0
    for machine in parse_fleet(Path(ns.fleet).read_text()):
        if ns.dry_run:
            print(f"{machine.nickname}: would push {canon.tip[:9]} to {machine.ssh}:{machine.repo}")
            continue
        outcome = keep_machine_level(machine, ns.main_repo, canon.tip, ssh_execute)
        print(f"{machine.nickname}: {outcome}")
        if not outcome.startswith(LEVEL_OUTCOMES):
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
