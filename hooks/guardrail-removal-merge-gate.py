#!/usr/bin/env python3
"""PreToolUse gate: DENY `gh pr merge` when the PR REMOVES guardrail enforcement.

Origin: 2026-08-10, GRIP #4513. A dispatch brief said "do not merge this one, it
changes a security check — bring it back to me". The worker that received the
brief obeyed. A second worker, running concurrently, saw an AGORA MERGE verdict,
applied the standing PARAMOUNT rule "AGORA-green = merge, don't ask", and merged
it. Neither did anything wrong: a brief is a message to ONE worker, so any safety
constraint living only in a brief is advisory the moment a second worker exists.

Two things bind every concurrent worker — canon, which they all load, and a
mechanism in the path itself. This file is the second one.

IT INVENTS NO POLICY. CLAUDE.md already lists **guardrail removal** among the
"real, present, clear danger" conditions that HALT a merge even on an AGORA pass.
Until now that rule depended on each session noticing, unaided, that the diff in
front of it weakened a control. This makes the same rule checkable.

WHY DENY AND NOT WARN
---------------------
`merged-branch-guard.py` deliberately only warns, because it gates `git commit` —
a frequent, cheap, reversible act — on a network call, and blocking every commit
during a GitHub hiccup would be worse than the bug. The calculus here is the
opposite on both axes: merging a guardrail-removal is rare and effectively
irreversible on a shared main, and the condition is already a documented HALT.
Blocking is the behaviour canon already asks for.

Every *uncertainty*, though, resolves to allow. If `gh` is missing, slow,
rate-limited, unauthenticated, or returns something unparseable, the merge
proceeds. The gate can only ever act on a diff it positively read and positively
found to remove teeth. A miss is a return to today's behaviour; a false block
would earn the gate a bypass, which is the one outcome that would make things
worse (rules/no-guardrail-evasion.md).

DEADLOCK CHECK (rules/gate-resolution-escape-hatch.md, Invariant 1)
-------------------------------------------------------------------
Blocked condition: an agent merging a PR that removes guardrail enforcement.
Its resolutions are (a) a human merges it — from the GitHub web UI or any shell
outside a Claude Code session, neither of which this hook can see — or (b) the
PR stops removing the guardrail, which is an Edit and is not gated here. Both
lie OUTSIDE the denied set, so this gate cannot trap the session that trips it.

There is deliberately NO environment-variable acknowledgement escape. One was
considered and rejected for now: an autonomous loop reading "a human must decide"
and then setting an ack variable itself would satisfy the letter of the gate
while defeating its entire purpose, and the friction it saves is small because
guardrail-removing PRs are rare. If the human-merge step proves too costly in
practice, add the ack with measured evidence rather than by assumption.

Detector: lib/guardrail_removal_detector.py (pure, network-free)
Tests:    tests/test_guardrail_removal_merge_gate.py
Audit:    ~/.claude/logs/guardrail-removal-merge-gate.jsonl
Rule:     rules/agent-dispatch-protocol.md ("A brief binds only its recipient")
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
sys.path.insert(0, str(CLAUDE_DIR / "lib"))

AUDIT_PATH = CLAUDE_DIR / "logs" / "guardrail-removal-merge-gate.jsonl"
GH_TIMEOUT_SECONDS = int(os.environ.get("GUARDRAIL_REMOVAL_GATE_TIMEOUT", "15"))

# A cheap, permissive pre-filter — NOT the decision. Every Bash call in the
# session pays this regex, so it has to be fast; `_merge_argv` then decides
# properly on the small number of candidates it lets through.
_GH_PR_MERGE = re.compile(r"\bgh\b[^\n;&|]*?\spr\s+merge(?![-\w])")


def _bail() -> None:
    """Every uncertain path ends here: silent, non-blocking."""
    sys.exit(0)


def _audit(record: dict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with AUDIT_PATH.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 — auditing must never block a merge
        pass


def _is_gh(token: str) -> bool:
    """True for the gh binary however it is spelled: `gh`, `grip-gh`, `/bin/gh`."""
    return token == "gh" or token.endswith(("-gh", "/gh"))


def _repo_from(rest: list[str]) -> str | None:
    for index, token in enumerate(rest):
        if token in ("--repo", "-R") and index + 1 < len(rest):
            return rest[index + 1]
        if token.startswith("--repo="):
            return token.split("=", 1)[1]
    return None


def _diff_argv(rest: list[str]) -> list[str]:
    """Build the `gh pr diff` call for whatever the merge was aimed at.

    With no PR number, `gh pr diff` resolves the current branch's PR — which is
    also what `gh pr merge` with no number would have merged.
    """
    argv = ["gh", "pr", "diff"]
    number = next((token for token in rest if token.isdigit()), None)
    if number:
        argv.append(number)
    repo = _repo_from(rest)
    if repo:
        argv += ["--repo", repo]
    return argv


def _merge_argv(command: str) -> list[str] | None:
    """The `gh pr diff` argv for a real `gh pr merge`, or None if this isn't one.

    Two stages, because shell quoting is not a regular language: a regex
    genuinely cannot tell `gh pr merge` the command from "gh pr merge" the string
    inside an `echo`. AGORA raised exactly that on #4564 and it reproduced.

    Its suggested remedy — anchoring the regex with `^` — was refuted by this
    file's own corpus: it breaks `rtk gh pr merge` and `cd /tmp && gh pr merge`,
    both of which GRIP really issues. Tokenising does work, because the quoted
    form collapses into ONE token (`['echo', 'gh pr merge']`) while every real
    invocation keeps `gh`, `pr`, `merge` as three ADJACENT tokens.

    Unbalanced quotes make shlex raise. That is an uncertainty, so it allows.
    """
    if not _GH_PR_MERGE.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for index, token in enumerate(tokens[:-2]):
        if _is_gh(token) and tokens[index + 1] == "pr" and tokens[index + 2] == "merge":
            return _diff_argv(tokens[index + 3:])
    return None


def _fetch_diff(argv: list[str]) -> str | None:
    """Ask gh for the PR's diff. Returns None on any doubt whatsoever."""
    if not shutil.which("gh"):
        return None
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # noqa: BLE001 — timeout, missing binary, anything
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        _bail()

    if payload.get("tool_name") != "Bash":
        _bail()
    argv = _merge_argv(str(payload.get("tool_input", {}).get("command", "")))
    if argv is None:
        _bail()

    diff_text = _fetch_diff(argv)
    if not diff_text:
        _bail()

    try:
        from guardrail_removal_detector import evaluate_diff
        from hook_response import emit, pretool_decision
    except Exception:  # noqa: BLE001
        _bail()

    verdict = evaluate_diff(diff_text)
    if not verdict.removes_guardrail:
        _bail()

    _audit({
        "decision": "deny",
        "signal": verdict.signal,
        "evidence": list(verdict.evidence),
        "argv": argv,
    })
    # Emitting is the last thing that can fail, and an exception here would exit
    # non-zero — the one path that could turn this deliberately fail-open gate
    # into a hard error. Raised by AGORA on #4564; cheap to close, so closed.
    try:
        emit(pretool_decision("deny", verdict.reason))
    except Exception as exc:  # noqa: BLE001
        # Bailing here is right — a broken emitter must not block every merge.
        # Bailing SILENTLY is not: the caller then sees exactly what it sees
        # when the gate never fired at all, so a deny that was correctly
        # decided and merely could not be spoken leaves no trace anywhere.
        # The record costs nothing (_audit already swallows its own errors)
        # and is the only thing that makes the two cases distinguishable
        # afterwards.
        _audit({"decision": "deny-emit-failed", "error": repr(exc)[:400]})
        _bail()
    sys.exit(0)


if __name__ == "__main__":
    main()
