"""Congruent Continuity — the push RECORDER: a falsifiable commitment at a
real decision boundary, carrying the context that makes it PAIRABLE.

The precision report showed the paired corpus is what is small, not the
label corpus: settled chain commitments carried no transcript reference, and
git proxies pair only when a commit's creating command survives in a
transcript on this machine. This module fixes that going forward at the one
boundary every session crosses — ``git push``. Before the push runs, it
pre-registers, through ``lib.continuity_chain.commit`` (signed, append
before act; anti-HARKing by ordering), the premise a push always makes:

    the head being pushed contains the origin/main tip this checkout knew at
    push time — the branch was born fresh and kept level
    (``rules/worktree-must-track-main.md``).

The falsifier is a deterministic, TIME-INVARIANT probe the settlement floor
already runs (``lib/continuity_settle.py``, git read subcommand):

    probe: git merge-base --is-ancestor <origin_main_sha_at_record> <head_sha>

Both SHAs are pinned at record time, so the verdict is the same whether the
worker settles it a minute or a month later; no maturity window is needed
and the settlement floor is untouched. INCONGRUENT means the session pushed a
branch that was already behind, the wrong-track moment a watcher could one
day steer with "fetch and rebase first".

``intended_action`` carries ``session_id``, ``transcript_path`` and
``recorded_at`` so ``lib/continuity_pairing.py`` pairs the record by its
declared transcript rather than by searching, and the vector is extracted
as-of ``recorded_at``.

RECORD-ONLY. Nothing here gates, steers or blocks; the hook that calls it
always allows. That keeps it inside build-order decision D3 (WARN-only until
precision is measured) and clears ``rules/gate-resolution-escape-hatch.md``
Invariant 1 by construction: a recorder cannot block its own resolution.

Refused, never recorded (a premise must be about the operator's own forward
motion): force pushes and force-with-lease, deletions, mirror and tag-only
pushes, dry runs, and pushes of ``main``/``master`` themselves. Git is called
read-only (``rev-parse``) with a short timeout; any failure yields ``None``,
never an exception into the hook.
"""
from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.continuity_chain import COMMIT_KIND, chain_path, commit, read_idr_chain  # noqa: E402

__all__ = [
    "GOAL_REF",
    "PROTECTED_BRANCHES",
    "PushIntent",
    "parse_push",
    "resolve_push",
    "record_push_premise",
]

GOAL_REF = "continuity-push-recorder"
PROTECTED_BRANCHES = frozenset({"main", "master"})
_REFUSED_FLAGS = {
    "-f": "force", "--force": "force", "--force-with-lease": "force", "--force-if-includes": "force",
    "-d": "delete", "--delete": "delete", "--mirror": "mirror", "--tags": "tags-only",
    "--all": "all-branches", "-n": "dry-run", "--dry-run": "dry-run", "--prune": "prune",
}
_SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_BASE_CHANGERS = frozenset({"merge", "pull", "rebase", "reset", "checkout", "switch"})
_GIT_TIMEOUT_S = 5


@dataclass(frozen=True)
class PushIntent:
    remote: str | None
    refspec: str | None
    refused: str | None = None


def _push_args(segment: str) -> list[str] | None:
    """Tokens after the first ``git push`` in one shell segment (``rtk git
    push`` counts), or ``None`` when the segment pushes nothing."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok == "git" and i + 1 < len(tokens) and tokens[i + 1] == "push":
            return tokens[i + 2:]
    return None


def _intent_from_args(args: list[str]) -> PushIntent:
    positional: list[str] = []
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in _REFUSED_FLAGS:
            return PushIntent(None, None, refused=_REFUSED_FLAGS[flag])
        if not arg.startswith("-"):
            positional.append(arg)
    remote = positional[0] if positional else None
    refspec = positional[1] if len(positional) > 1 else None
    return PushIntent(remote, refspec)


def _changes_base(segment: str) -> bool:
    """True when a segment runs a git verb that moves the branch tip's
    ancestry (merge, pull, rebase, reset, checkout, switch)."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    for i, tok in enumerate(tokens):
        if tok == "git" and i + 1 < len(tokens) and tokens[i + 1] in _BASE_CHANGERS:
            return True
    return False


def parse_push(command: str) -> PushIntent | None:
    """The first ``git push`` in ``command``, or ``None`` when the command
    pushes nothing. A refused push comes back with ``refused`` naming the
    reason, so the caller can log why it was skipped.

    A PreToolUse hook sees the checkout BEFORE the command runs. When an
    earlier segment of the same command merges, pulls, rebases or resets
    the branch, the head the push will send is not the head that exists at
    hook time, so the level-with-main premise would be judged on a state
    the command itself is about to change (observed in production on
    2026-09-10: a ``fetch && merge && push`` chain recorded the pre-merge
    tip and settled INCONGRUENT although the push was level). Such pushes
    are refused as ``base-changes-in-command`` rather than recorded wrong.
    A commit before the push is fine: a descendant keeps its ancestry."""
    for segment in _SEGMENT_SPLIT.split(command or ""):
        args = _push_args(segment)
        if args is not None:
            return _intent_from_args(args)
        if _changes_base(segment):
            return PushIntent(None, None, refused="base-changes-in-command")
    return None


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                             timeout=_GIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def resolve_push(cwd: Path | str, intent: PushIntent) -> dict[str, str] | None:
    """``{branch, head, origin_main}`` for the push, read-only, or ``None``
    when any of the three cannot be resolved in ``cwd``."""
    cwd = Path(cwd)
    src = (intent.refspec or "HEAD").split(":", 1)[0].lstrip("+") or "HEAD"
    branch = _git(cwd, "rev-parse", "--abbrev-ref", src)
    head = _git(cwd, "rev-parse", "--verify", f"{src}^{{commit}}")
    origin_main = _git(cwd, "rev-parse", "--verify", "origin/main^{commit}")
    if not branch or not head or not origin_main:
        return None
    return {"branch": branch, "head": head, "origin_main": origin_main}


def _already_recorded(claim: str, path: Path | None) -> bool:
    cpath = chain_path(path)
    if not cpath.is_file():
        return False
    return any(node.kind == COMMIT_KIND and node.decision.get("claim") == claim
               for node in read_idr_chain(path=cpath))


def _intended_action(command: str, cwd: Path, intent: PushIntent, resolved: dict[str, str],
                     session_id: str | None, transcript_path: str | None, stamp: datetime) -> dict:
    return {
        "tool": "Bash",
        "surface": "git-push",
        "command_sha256": hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:16],
        "cwd": str(cwd),
        "remote": intent.remote,
        "branch": resolved["branch"],
        "head": resolved["head"],
        "origin_main_at_record": resolved["origin_main"],
        "session_id": session_id,
        "transcript_path": transcript_path,
        "recorded_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def record_push_premise(
    *,
    command: str,
    cwd: Path | str,
    session_id: str | None = None,
    transcript_path: str | None = None,
    now: datetime | None = None,
    path: Path | None = None,
    head_pointer: Path | None = None,
) -> Any | None:
    """Pre-register the level-with-main premise for the push in ``command``.
    Returns the signed commitment, or ``None`` when nothing was recorded
    (not a push, a refused push shape, a protected branch, an unresolvable
    checkout, or the identical premise already on the chain)."""
    intent = parse_push(command)
    if intent is None or intent.refused:
        return None
    resolved = resolve_push(cwd, intent)
    if resolved is None or resolved["branch"] in PROTECTED_BRANCHES:
        return None
    head, origin_main = resolved["head"], resolved["origin_main"]
    claim = (f"push of {resolved['branch']} at {head[:12]} contains the origin/main tip known "
             f"locally at push time ({origin_main[:12]})")
    falsifier = f"probe: git merge-base --is-ancestor {origin_main} {head}"
    if _already_recorded(claim, path):
        return None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    action = _intended_action(command, Path(cwd), intent, resolved, session_id, transcript_path, stamp)
    return commit(claim, falsifier, intended_action=action, severity="reversible",
                  goal_ref=GOAL_REF, path=path, head_pointer=head_pointer)
