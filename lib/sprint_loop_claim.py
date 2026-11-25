"""Sprint-loop issue claim gate -- wires lib/task_claims.py into issue pickup (#4489, #4501).

The mechanism existed and nothing called it. ``lib/task_claims.py`` shipped a
complete atomic lease (#436/#2795) and ``rules/task-lease-claim-before-work.md``
made claim-before-work PARAMOUNT, but ``agents/sprint-loop-agent.md`` contained
zero references to either -- so every autonomous worker read the same backlog,
picked the same top item, and worked it independently. Measured 2026-08-10:
GRIP #4411 got three PRs in ~20 minutes, #4443 got four in 16.

This module is the missing selection step. It is deliberately thin -- no daemon,
no new storage format, no lock server. It reuses the existing
``cache/task-claims.json`` registry and its ``FileLock``, and adds only the
pickup policy the agent needs:

    pick_claimable(issues, session_id) -> the first issue this worker may work,
                                          already claimed for it, or None.

Two independent gates, cheapest first:

1. **Local lease (authoritative, same machine).** ``TaskClaims.claim`` is an
   atomic read-modify-write under a ``FileLock``, so of N same-host workers
   racing one issue exactly one wins. Both reported incidents were same-host --
   #4489 names an interactive session and ``com.grip.overnight-loop-opencode``
   on one machine -- so this closes the observed failure.

2. **Open-PR probe (advisory, cross-machine).** ``cache/task-claims.json`` is a
   local file and coordinates ONLY workers on the same machine. GitHub is the
   sole shared surface, so an open PR already referencing the issue is the one
   cross-machine duplication signal available cheaply. It is a heuristic, not a
   lock: two machines can still both see "no PR" and both start. Treat it as
   defence in depth, never as cross-machine mutual exclusion.

Claim ids are namespaced ``issue/<n>`` so an issue lease can never collide with
a harness task lease of the same number in the shared registry.

Failure directions are deliberately opposite, because the costs are:

* An **indeterminate liveness** probe keeps a claim held (``task_claims.py``) --
  freeing a live worker's issue re-creates the collision.
* An **errored PR probe** is treated as "no PR" (fail-open). Failing closed on a
  ``gh`` outage would stall every worker on every issue; the local lease still
  protects the case that actually occurred.

CLI:
    gh issue list --label sprint-loop --state open --json number,title,labels \\
      | python3 lib/sprint_loop_claim.py pick "$CLAUDE_CODE_SESSION_ID"
    python3 lib/sprint_loop_claim.py release <issue_number> <session_id>
    python3 lib/sprint_loop_claim.py claimed
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from task_claims import TaskClaims  # noqa: E402

CLAIM_PREFIX = "issue/"

# A sprint-loop issue is worked for far longer than a harness task edit, so the
# default lease outlasts a typical implement->PR cycle (~30-40 min per #4501).
# Still bounded: a crashed worker's issue frees itself, per the escape-hatch
# requirement in rules/gate-resolution-escape-hatch.md.
ISSUE_TTL_HOURS = 4


def claim_id(number) -> str:
    """Registry key for an issue lease, namespaced away from harness task ids."""
    return f"{CLAIM_PREFIX}{number}"


def issue_number(issue) -> Optional[int]:
    """The issue number from a ``gh issue list --json`` record, or None."""
    raw = issue.get("number") if isinstance(issue, dict) else issue
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def has_open_pr(number: int) -> bool:
    """True if an open PR already references this issue (cross-machine probe).

    Fail-open: a ``gh`` failure, timeout, or unparseable payload returns False
    ("no PR known"), because failing closed would stall every worker whenever
    GitHub is unreachable. The local lease remains the authoritative gate.
    """
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--search", str(number),
             "--json", "number"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if out.returncode != 0:
            return False
        return bool(json.loads(out.stdout or "[]"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def pick_claimable(
    issues: Iterable,
    session_id: str,
    claims: Optional[TaskClaims] = None,
    pr_probe: Optional[Callable[[int], bool]] = None,
    ttl_hours: int = ISSUE_TTL_HOURS,
) -> Optional[dict]:
    """Return the first issue this session may work, having claimed it.

    Walks ``issues`` in the caller's priority order and returns the first one
    that is not already being worked -- claiming it atomically before returning,
    so a concurrent worker calling this with the same list gets a DIFFERENT
    issue (or None when every candidate is taken).

    Returns the issue record as given, with ``claim_id`` added, or None.
    """
    claims = claims or TaskClaims()
    probe = pr_probe if pr_probe is not None else has_open_pr

    for issue in issues:
        number = issue_number(issue)
        if number is None:
            continue
        if probe(number):
            continue  # another machine is already on it (advisory)
        cid = claim_id(number)
        if not claims.claim(cid, session_id, ttl_hours):
            continue  # a live worker on THIS machine holds it (authoritative)
        record = dict(issue) if isinstance(issue, dict) else {"number": number}
        record["claim_id"] = cid
        return record
    return None


def release_issue(number, session_id: str, claims: Optional[TaskClaims] = None) -> bool:
    """Release an issue lease on completion or abandonment."""
    claims = claims or TaskClaims()
    return claims.release(claim_id(number), session_id)


def active_issue_claims(claims: Optional[TaskClaims] = None) -> dict:
    """Active issue leases only (harness task leases excluded)."""
    claims = claims or TaskClaims()
    return {k: v for k, v in claims.list_claims().items() if k.startswith(CLAIM_PREFIX)}


def _cmd_pick(argv) -> int:
    if len(argv) < 3:
        print("Usage: sprint_loop_claim.py pick <session_id>", file=sys.stderr)
        return 2
    try:
        issues = json.loads(sys.stdin.read() or "[]")
    except ValueError as exc:
        print(f"Unparseable issue list on stdin: {exc}", file=sys.stderr)
        return 2
    picked = pick_claimable(issues, argv[2])
    if picked is None:
        print(json.dumps({"picked": None,
                          "reason": "every candidate issue is claimed or has an open PR"}))
        return 1
    print(json.dumps(picked))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    cmd = argv[1] if len(argv) > 1 else ""

    if cmd == "pick":
        return _cmd_pick(argv)

    if cmd == "release" and len(argv) >= 4:
        ok = release_issue(argv[2], argv[3])
        print(json.dumps({"released": ok, "issue": argv[2]}))
        return 0 if ok else 1

    if cmd == "claimed":
        print(json.dumps(active_issue_claims(), indent=2))
        return 0

    print("Usage: sprint_loop_claim.py pick <session_id> | "
          "release <issue> <session_id> | claimed", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
