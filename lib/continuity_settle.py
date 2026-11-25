"""Congruent Continuity — the post-hoc settlement worker's DETERMINISTIC FLOOR.

Walks the unsettled worklist and settles every commitment whose falsifier is
machine-settleable under the ``probe:`` convention:

    falsifier = "probe: <command>"   ->  run <command> WITHOUT a shell;
                                         exit 0  -> CONGRUENT  (claim held)
                                         exit !0 -> INCONGRUENT (claim refuted)

Safety posture (untrusted-content-capability-lock): probe text is DATA from the
chain, never trusted, and the worker runs it AUTONOMOUSLY over ``unsettled()``,
so the capability surface — not anyone's judgement — is the boundary. Four
layers, each pinned by ``tests/test_continuity_settle_hardening.py``:

1. **Per-tool command policy, allowlisted.** A bare first token is NOT
   authorisation: ``git`` and ``rg`` can spawn arbitrary programs through
   documented flags (a bang-alias injected with git's inline-config option;
   ripgrep's preprocessor option). So for ``git`` the token right after
   ``git`` MUST be a read subcommand (``GIT_READ_SUBCOMMANDS``) — every global
   option is refused by construction, not by enumeration — and flags that
   write a file or spawn a program are refused per tool as defence-in-depth.
2. **Allowlisted environment** (``probe_env``): the probe inherits nothing that
   could re-enable an escape. Git's system and global configs are disabled,
   the pager is ``cat``, terminal prompts are off, optional index locks are
   off (a read subcommand does not even refresh the index), and ripgrep's
   config file is pointed at the null device. Built FROM an allowlist, never
   by deleting names from ``os.environ``.
3. **No shell, stdin closed, timeout** — ``subprocess.run(argv, shell=False,
   stdin=DEVNULL, timeout=...)``. The metacharacter screen refuses-and-records
   before exec; it is defence-in-depth, not the boundary.
4. **Output is digested, never stored** — a ``cat`` of a sensitive file yields
   a 16-hex sha256 prefix in the evidence, not the bytes.

A rejected or timed-out probe settles UNVERIFIABLE with the reason as evidence
— never CONGRUENT by default. ``dry_run`` classifies and screens WITHOUT
executing anything. A falsifier WITHOUT the ``probe:`` prefix is left
unsettled: that is the L2 exogenous council's worklist, not the floor's.
"Read-only" here means no change to tracked content, refs, config, or files.

Stated residual (a boundary, not a gap): the probe's working directory is
chosen by the worker's caller, never by chain content, so repo-local config in
that directory (``.git/config`` drivers such as an external diff) is the
operator's own and is trusted. A hostile probe cwd is out of scope.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.continuity_chain import (
    commit_addr_of,
    settle,
    unsettled,
)

__all__ = [
    "PROBE_PREFIX",
    "ALLOWED_COMMANDS",
    "GIT_READ_SUBCOMMANDS",
    "PROBE_ENV_ALLOWLIST",
    "PROBE_ENV_FIXED",
    "classify_falsifier",
    "probe_env",
    "run_probe",
    "settle_floor",
]

PROBE_PREFIX = "probe:"

# First tokens the floor may execute. Deliberately narrow; widen only with a
# test per addition. ``git`` and ``rg`` additionally pass a per-tool policy.
ALLOWED_COMMANDS = (
    "git", "rg", "ls", "test", "wc", "cat", "head", "tail",
    "bin/grip-tree-check",
)

# Read-only git subcommands. The token after ``git`` must be one of these, so
# inline config, change-directory, exec-path, git-dir and forced-pager options
# never reach git at all.
GIT_READ_SUBCOMMANDS = frozenset({
    "rev-parse", "rev-list", "show", "cat-file", "merge-base", "log", "diff",
    "diff-tree", "diff-index", "ls-files", "ls-tree", "describe", "name-rev",
    "for-each-ref", "show-ref", "check-ignore", "blame", "count-objects",
})

# Flags that turn a read subcommand into a file write or a program spawn.
# Prefix-matched so the ``--flag=value`` spelling is caught too.
_GIT_REFUSED_FLAG_PREFIXES = ("--output", "--ext-diff", "--open-files-in-pager", "-O")
_RG_REFUSED_FLAG_PREFIXES = ("--pre", "--hostname-bin", "--search-zip")

# Environment the probe runs under: these names are inherited when present …
PROBE_ENV_ALLOWLIST = ("PATH", "TMPDIR", "LANG", "LC_ALL")
# … and these are pinned, so no inherited setting can re-enable an escape.
PROBE_ENV_FIXED = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "RIPGREP_CONFIG_PATH": os.devnull,
}

_FORBIDDEN_CHARS = set(";|&<>`$(){}\n")
_TIMEOUT_S = 30


@dataclass
class ProbeResult:
    verdict: str            # CONGRUENT | INCONGRUENT | UNVERIFIABLE
    evidence: dict


def classify_falsifier(falsifier: str) -> str:
    """'probe' when machine-settleable, 'council' otherwise."""
    return "probe" if falsifier.strip().startswith(PROBE_PREFIX) else "council"


def probe_env() -> dict[str, str]:
    """The allowlisted environment a probe executes under (see module doc, layer 2)."""
    env = {name: os.environ[name] for name in PROBE_ENV_ALLOWLIST if name in os.environ}
    env.update(PROBE_ENV_FIXED)
    return env


def _refused_flag(tokens: list[str], prefixes: tuple[str, ...]) -> str | None:
    for tok in tokens:
        if any(tok.startswith(p) for p in prefixes):
            return tok
    return None


def _screen_git(tokens: list[str]) -> str | None:
    if len(tokens) < 2:
        return "rejected: git needs a read subcommand"
    sub = tokens[1]
    if sub not in GIT_READ_SUBCOMMANDS:
        return (f"rejected: git {sub!r} is not a read subcommand "
                "(global options before the subcommand are refused by construction)")
    bad = _refused_flag(tokens[2:], _GIT_REFUSED_FLAG_PREFIXES)
    if bad is not None:
        return f"rejected: git flag {bad!r} writes a file or spawns a program"
    return None


def _screen_rg(tokens: list[str]) -> str | None:
    bad = _refused_flag(tokens[1:], _RG_REFUSED_FLAG_PREFIXES)
    if bad is not None:
        return f"rejected: rg flag {bad!r} spawns a program"
    for tok in tokens[1:]:  # -z, also clustered as -lz, is --search-zip (spawns decompressors)
        if tok.startswith("-") and not tok.startswith("--") and "z" in tok:
            return f"rejected: rg flag {tok!r} spawns a decompressor"
    return None


_TOOL_POLICY = {"git": _screen_git, "rg": _screen_rg}


def _screen(cmd: str) -> str | None:
    """Reason the probe must be refused, or None when runnable."""
    if any(ch in _FORBIDDEN_CHARS for ch in cmd):
        return "rejected: shell metacharacters in probe"
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return f"rejected: unparseable probe ({exc})"
    if not tokens:
        return "rejected: empty probe"
    if tokens[0] not in ALLOWED_COMMANDS:
        return f"rejected: command {tokens[0]!r} not on the read-only allowlist"
    policy = _TOOL_POLICY.get(tokens[0])
    return policy(tokens) if policy else None


def _probe_cmd(falsifier: str) -> str:
    return falsifier.strip()[len(PROBE_PREFIX):].strip()


def run_probe(falsifier: str, *, cwd: Path | None = None) -> ProbeResult:
    """Execute one ``probe:`` falsifier under the safety posture above."""
    cmd = _probe_cmd(falsifier)
    reason = _screen(cmd)
    if reason is not None:
        return ProbeResult("UNVERIFIABLE", {"cmd": cmd, "reason": reason})
    try:
        proc = subprocess.run(
            shlex.split(cmd), shell=False, capture_output=True, stdin=subprocess.DEVNULL,
            timeout=_TIMEOUT_S, cwd=str(cwd) if cwd else None, env=probe_env(),
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("UNVERIFIABLE", {"cmd": cmd, "reason": f"timeout after {_TIMEOUT_S}s"})
    except OSError as exc:
        return ProbeResult("UNVERIFIABLE", {"cmd": cmd, "reason": f"exec failed: {exc}"})
    digest = hashlib.sha256(proc.stdout + proc.stderr).hexdigest()[:16]
    verdict = "CONGRUENT" if proc.returncode == 0 else "INCONGRUENT"
    return ProbeResult(verdict, {"cmd": cmd, "exit": proc.returncode, "output_digest": digest})


def _dry_row(addr: str, falsifier: str) -> dict:
    """What a dry run reports: the screen's verdict, with nothing executed."""
    cmd = _probe_cmd(falsifier)
    reason = _screen(cmd)
    return {"commit_addr": addr, "action": "dry-run", "cmd": cmd,
            "would_run": reason is None, "reason": reason}


def settle_floor(
    *,
    path: Path | None = None,
    head_pointer: Path | None = None,
    cwd: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Settle every probe-settleable unsettled commitment. Returns a report
    row per commitment considered (settled, dry-run, or left for the council).

    ``axis`` is hardcoded ``"reality"`` BY DESIGN, not omission: a shell probe
    can only test a premise against canonical reality (files, exits, repo
    state). Intent-axis verdicts require the operator-goal context and arrive
    only with the L2 exogenous council — this floor must never emit one, so
    the parameter is deliberately not exposed here."""
    rows: list[dict] = []
    work = unsettled(path=path)
    if limit is not None:
        work = work[:limit]
    for node in work:
        addr = commit_addr_of(node)
        falsifier = str(node.decision.get("falsifier", ""))
        if classify_falsifier(falsifier) != "probe":
            rows.append({"commit_addr": addr, "action": "left-for-council"})
            continue
        if dry_run:
            rows.append(_dry_row(addr, falsifier))
            continue
        result = run_probe(falsifier, cwd=cwd)
        settle(
            addr, result.verdict, axis="reality", evidence=result.evidence,
            verifier="deterministic", action_taken="none",
            path=path, head_pointer=head_pointer,
        )
        rows.append({"commit_addr": addr, "action": "settled", "verdict": result.verdict,
                     "evidence": result.evidence})
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="continuity_settle")
    p.add_argument("--chain", help="chain file override (tests)")
    p.add_argument("--head", help="HEAD pointer override (tests)")
    p.add_argument("--cwd", help="probe working directory")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")
    ns = p.parse_args(argv)
    rows = settle_floor(
        path=Path(ns.chain) if ns.chain else None,
        head_pointer=Path(ns.head) if ns.head else None,
        cwd=Path(ns.cwd) if ns.cwd else None,
        limit=ns.limit,
        dry_run=ns.dry_run,
    )
    for row in rows:
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
