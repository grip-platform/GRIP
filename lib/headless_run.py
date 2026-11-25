"""Headless GRIP, slice 1 -- run a task with no interactive session, and
write a verifiable receipt.

Implements task 4 of
``~/.grip/drafts/ULTRAPROMPT-2026-10-01-headless-grip-hal-runtime.md`` (slice 1
of the headless-GRIP plan): ``gg++ -p "<task>"`` assembles GRIP's context
exactly as an interactive launch would, runs ``ClaudeCodeHarness``
(``lib/harness/cli.py``) under a capability lock and a budget, and writes a
``receipt.json`` whose ``idr_leaf`` verifies through the IDR forest's own
verify path.

Design choices, each traceable to a measurement or an existing pattern rather
than invented here:

* ``--permission-mode dontAsk``, never ``--dangerously-skip-permissions`` and
  never ``--bare``. Slice 0 (``~/.grip/drafts/headless-grip/slice0/``,
  2026-10-01) measured that a GRIP PreToolUse deny HOLDS under ``dontAsk``,
  and that ``--bare`` cannot authenticate on the subscription path at all --
  it skips hooks by skipping the whole config-dir credential resolution.
* ``--tools`` is the capability lock, not ``--allowedTools`` (slice 0:
  ``--allowedTools`` is pre-approval -- the init line still lists every tool
  AND every MCP connector as available). ``--strict-mcp-config`` closes the
  other half of that gap: with no ``--mcp-config``, Claude Code starts with
  ZERO MCP servers, so a widened native-tool set can never reach a
  send-shaped MCP connector (Gmail, Slack, ...) that ``--tools`` alone does
  not see. Mirrors ``lib/relay_dispatcher.py``'s ``build_argv`` -- the one
  production headless path this slice generalises from
  (``rules/untrusted-content-capability-lock.md``).
* Outcome is derived from the stream-json transcript, never from Claude
  Code's own process exit code. Slice 0's R1 control run exited 1 even
  though its one requested tool call ran twice and succeeded -- the process
  hit ``--max-turns`` and reported ``error_max_turns``, which is a budget
  outcome, not a tool-call failure. Relying on ``rc == 0`` would have
  misreported a successful run as "failed".
* The outward-action barrier is a DETERMINISTIC floor over the task text
  (never an LLM judgement, per the slice-1 falsifier): a task naming an
  outward action never reaches the harness at all. A second, narrower pass
  over any tool-use events a run DID produce is defence in depth for a
  future wider tool allowlist, where the text-only check could miss an
  outward-shaped tool call the model made anyway.
* The IDR leaf is minimal/deterministic (``predecessor_idr=None, depth=0``):
  the majority existing convention (``lib/idr_head.py`` exists, but no
  production caller reads it before ``build_idr`` today -- chaining through
  it is a real gap, and not one this module should paper over by pretending
  to fix it). Content is hashed into the signed body, never embedded raw,
  matching ``lib/harness/base.py``'s own dispatch-log discipline; the raw
  task text stays local, in ``receipt.json`` only.

* Slice 2's gate-equivalence check (``lib/headless_gate.py``) runs before any
  worktree or harness exists: every hard PreToolUse policy the registry binds
  must be either unreachable with this run's tools, or bound in the settings
  the child will load. One uncovered policy refuses the run as ``denied``.

Out of scope here (see ``docs/headless-grip.md``): detached/durable jobs
(slice 3), a HAL-native executor (slice 4), and relay/room delivery (slice 5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
import throttle_flag  # noqa: E402  the tick's throttle flag (read-only): a headless run refuses while it is set

from lib.harness.base import HarnessResult, Task  # noqa: E402
from lib.harness.cli import (  # noqa: E402
    DEFAULT_CLAUDE_TOOLS,
    ClaudeCodeHarness,
    credentialed_env,
)
from lib.headless_gate import Admission, admit_from_disk  # noqa: E402

__all__ = [
    "OUTCOME_VALUES",
    "OUTWARD_ACTION_KEYWORDS",
    "DEFAULT_TOOLS",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_S",
    "Receipt",
    "RunSignals",
    "HeadlessClaudeHarness",
    "detect_outward_action",
    "detect_outward_tool_use",
    "parse_stream_json",
    "classify_outcome",
    "create_isolation_worktree",
    "remove_isolation_worktree",
    "run_headless_task",
    "write_receipt",
    "main",
]

# ---------------------------------------------------------------------------
# The outward-action barrier -- deterministic, never an LLM judgement.
# ---------------------------------------------------------------------------

# Exactly the brief's own falsifier list (ULTRAPROMPT-2026-10-01, Slice 1):
# "an outward action (send, post, publish, pay, submit) must end in
# awaiting-approval". Known, documented limitation: "post" and "submit" are
# common words and will over-block some benign tasks ("post-mortem the
# outage"); narrowing the list is review work for a later slice, not an
# LLM-judged exception to a floor that must stay deterministic.
OUTWARD_ACTION_KEYWORDS: tuple[str, ...] = (
    "send", "post", "publish", "pay", "payment", "submit",
    "email", "e-mail", "whatsapp", "slack", "tweet", "deploy",
)

_OUTWARD_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in OUTWARD_ACTION_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_GIT_PUSH_RE = re.compile(r"\bgit\s+push\b", re.IGNORECASE)


def detect_outward_action(text: str) -> Optional[str]:
    """The first outward-action keyword/phrase in ``text``, or ``None``.

    Pure, deterministic, case-insensitive, word-boundaried. ``git push`` is
    matched as a phrase because ``push`` alone is too common a word
    (data push, push notification) to gate on.
    """
    m = _GIT_PUSH_RE.search(text)
    if m:
        return m.group(0)
    m = _OUTWARD_WORD_RE.search(text)
    return m.group(0) if m else None


def detect_outward_tool_use(tool_uses: list[dict]) -> Optional[str]:
    """Defence-in-depth pass over tool-use events a run DID produce.

    With the default read-only allowlist (Read, Grep, Glob) this can never
    fire -- none of those tools can act outward. It exists for a future,
    wider allowlist: the same deterministic floor applied to what the model
    actually tried, not just what the operator typed.
    """
    for call in tool_uses:
        name = call.get("name", "") if isinstance(call, dict) else ""
        rendered = json.dumps(call.get("input", {}) if isinstance(call, dict) else {},
                              ensure_ascii=False)
        found = detect_outward_action(name) or detect_outward_action(rendered)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# The harness: ClaudeCodeHarness, capability-locked + budgeted + structured.
# ---------------------------------------------------------------------------


class HeadlessClaudeHarness(ClaudeCodeHarness):
    """``ClaudeCodeHarness`` for one headless run (see module docstring for
    why each flag is here and not in the shared base class)."""

    def __init__(self, tools: tuple[str, ...] = DEFAULT_CLAUDE_TOOLS,
                 max_turns: int = 20, model: Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(tools=tools, **kwargs)
        self._max_turns = max_turns
        self._model = model

    def as_argv(self, task: Task) -> list[str]:
        argv = [
            self.EXECUTABLE, "-p", task.prompt,
            "--tools", ",".join(self._tools),
            "--strict-mcp-config",
            "--permission-mode", "dontAsk",
            "--max-turns", str(self._max_turns),
            "--output-format", "stream-json",
            "--verbose",
        ]
        if self._model:
            argv += ["--model", self._model]
        return argv

    def _execute(self, task: Task) -> HarnessResult:
        """As ``CLIHarness._execute``, except ``ok`` means "produced a
        transcript", never "exit code 0" -- see the module docstring on why
        Claude Code's own ``-p`` exit code is not a reliable success signal.
        The nuanced completed/denied/failed split lives in
        ``classify_outcome``, applied to the transcript this returns.
        """
        rc, out, err = self._runner(self.as_argv(task), task.timeout_s)
        content = out.strip()
        if content:
            return HarnessResult(id=task.id, kind=self.kind,
                                 harness_id=self.harness_id, ok=True,
                                 content=content)
        return self.fail(task, err.strip() or f"empty output (exit {rc})")


def _cwd_runner(cwd: Path) -> Callable[[list, float], tuple]:
    """A ``RunnerFn`` that runs inside ``cwd`` -- the isolation worktree.

    ``CLIHarness``'s default runner has no ``cwd=`` at all (it inherits the
    caller's), so this is passed via the ``runner=`` injection point rather
    than patching the shared harness. Reuses ``credentialed_env`` rather than
    re-deriving which keychain entry authenticates ``claude``.
    """
    def run(argv: list, timeout_s: float) -> tuple:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, cwd=str(cwd),
            env=credentialed_env(argv[0] if argv else ""), timeout=timeout_s,
        )
        return proc.returncode, proc.stdout, proc.stderr
    return run


# ---------------------------------------------------------------------------
# Transcript parsing -- mirrors slice0/parse_slice0.py, the measured, trusted
# shape of this exact parsing problem.
# ---------------------------------------------------------------------------


def parse_stream_json(text: str) -> list[dict]:
    """Parse newline-delimited stream-json events, skipping bad lines."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _content_blocks(events: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    for ev in events:
        content = (ev.get("message") or {}).get("content") or []
        if isinstance(content, list):
            blocks.extend(b for b in content if isinstance(b, dict))
    return blocks


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


# A GRIP PreToolUse hook deny, exactly as Claude Code renders it in a
# tool_result (slice 0, R2-deny-dontask.jsonl): "PreToolUse:<Tool> hook
# error: <message>". Generalised from the one gate slice 0 exercised
# (Bash-lint) to the shape every PreToolUse hook denial shares.
_HOOK_DENY_RE = re.compile(r"^PreToolUse:\S+ hook error:", re.IGNORECASE)

OUTCOME_VALUES: tuple[str, ...] = (
    "completed", "awaiting-approval", "denied", "failed",
    "cancelled", "uncertain-external-outcome",
)


@dataclass
class RunSignals:
    """What the transcript (or its absence) told us -- extracted once and
    reused by every receipt field, never re-derived inconsistently."""

    outcome: str
    tools_used: tuple[str, ...] = ()
    model: Optional[str] = None
    cost: Optional[float] = None
    blocked_action: Optional[str] = None
    error: Optional[str] = None


def _split_tool_events(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
    tool_results = [b for b in blocks if b.get("type") == "tool_result"]
    return tool_uses, tool_results


def _tools_used(tool_uses: list[dict]) -> tuple[str, ...]:
    return tuple(sorted({u.get("name", "") for u in tool_uses if u.get("name")}))


def _final_result_event(events: list[dict]) -> Optional[dict]:
    finals = [ev for ev in events if ev.get("type") == "result"]
    return finals[-1] if finals else None


def _cost_and_model(final: Optional[dict]) -> tuple[Optional[float], Optional[str]]:
    if final is None:
        return None, None
    model_usage = final.get("modelUsage") or {}
    model = ",".join(sorted(model_usage)) if model_usage else None
    return final.get("total_cost_usd"), model


def _any_hook_denial(tool_results: list[dict]) -> bool:
    return any(r.get("is_error") and _HOOK_DENY_RE.match(_result_text(r))
              for r in tool_results)


def _decide_outcome(blocked: Optional[str], tool_results: list[dict],
                    final: Optional[dict]) -> str:
    """Precedence: an outward tool-use beats everything (the barrier
    matters more than the budget); a hook deny beats a bare ``is_error``
    read on the final event, because a denial IS the safety mechanism
    working, not a failure; the final ``result`` event's own ``is_error``
    decides completed vs failed; no ``result`` event at all is honestly
    uncertain, never a guessed "completed"."""
    if blocked:
        return "awaiting-approval"
    if _any_hook_denial(tool_results):
        return "denied"
    if final is not None:
        return "failed" if final.get("is_error") else "completed"
    return "uncertain-external-outcome"


def classify_outcome(events: list[dict]) -> RunSignals:
    """The nuanced outcome, from a parsed stream-json transcript."""
    tool_uses, tool_results = _split_tool_events(_content_blocks(events))
    blocked = detect_outward_tool_use(tool_uses)
    final = _final_result_event(events)
    cost, model = _cost_and_model(final)
    outcome = _decide_outcome(blocked, tool_results, final)
    return RunSignals(outcome=outcome, tools_used=_tools_used(tool_uses),
                      model=model, cost=cost, blocked_action=blocked)


def _signals_from_result(result: HarnessResult) -> RunSignals:
    """``RunSignals`` from a real ``HarnessResult`` -- the ok/not-ok split
    the harness layer already made, refined by parsing its transcript."""
    if result.ok:
        events = parse_stream_json(result.content)
        if not events:
            return RunSignals(outcome="uncertain-external-outcome",
                              error="transcript present but unparseable")
        return classify_outcome(events)
    error = result.error or ""
    outcome = "cancelled" if "TimeoutExpired" in error else "failed"
    return RunSignals(outcome=outcome, error=error[:500] or None)


# ---------------------------------------------------------------------------
# Isolation worktree -- always via lib/grip-worktree.sh (git-crypt-safe).
# ---------------------------------------------------------------------------

_WORKTREE_SCRIPT = "lib/grip-worktree.sh"


def _fetch_origin_best_effort(repo_root: Path) -> None:
    """Best-effort ``git fetch origin``: capped, fail-open, never blocking.

    ``rules/worktree-must-track-main.md``: a worktree is born from a
    freshly fetched ``origin/main``; a launch that cannot fetch (offline,
    slow) must still proceed from the last-fetched ref, never stall.
    """
    try:
        subprocess.run(["git", "-C", str(repo_root), "fetch", "origin", "--quiet"],
                       timeout=20, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def create_isolation_worktree(repo_root: Path, worktrees_root: Path, run_id: str,
                              *, script: Optional[Path] = None) -> Path:
    """Create a disposable git-crypt-safe worktree for one headless run.

    Always via ``lib/grip-worktree.sh`` (never ``git worktree add``
    directly -- git-crypt's keys live outside a linked worktree's own
    ``.git`` dir and the helper symlinks them in). ``--repo`` is passed
    explicitly rather than relied on via the script's own location, so a
    caller testing against a scratch repo can point it anywhere while still
    exercising the real, trusted script.
    """
    _fetch_origin_best_effort(repo_root)
    script = script or (repo_root / _WORKTREE_SCRIPT)
    path = worktrees_root / f"headless-{run_id}"
    branch = _isolation_branch(run_id)
    proc = subprocess.run(
        ["bash", str(script), "add", str(path), branch,
         "--repo", str(repo_root), "--base", "origin/main", "--lock-mutex"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"isolation worktree creation failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return path


def _isolation_branch(run_id: str) -> str:
    """The branch a run's isolation worktree checks out."""
    return f"sess/headless-{run_id}"


def _branch_tip(repo_root: Path, branch: str) -> Optional[str]:
    """The commit ``branch`` points at, or ``None`` when git cannot say."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet",
             f"refs/heads/{branch}"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _delete_branch_if_unchanged(repo_root: Path, branch: str, base: Optional[str]) -> None:
    """Delete the run's branch only while it still points at ``base``, the commit
    it was created on, so a run that committed keeps its work.

    Without a delete, every real run left its ``sess/headless-*`` branch in the
    shared repository (first live run, 2026-10-26). ``git branch -d`` is not the
    test: with no upstream (``branch.autoSetupMerge`` false or ``simple``) it
    judges against the shared tree's HEAD, which can be behind ``origin/main``,
    and keeps an untouched branch (Codex on GRIP#8668). An unknown ``base``
    deletes nothing.
    """
    if not base or _branch_tip(repo_root, branch) != base:
        return
    try:
        subprocess.run(["git", "-C", str(repo_root), "branch", "-D", branch],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def remove_isolation_worktree(repo_root: Path, path: Path, *,
                              branch: Optional[str] = None,
                              base: Optional[str] = None,
                              script: Optional[Path] = None) -> None:
    """Best-effort teardown, then the run's branch when it is unchanged.

    The default tool set is read-only (Read/Grep/Glob), so by default this
    worktree carries no uncommitted work and ``--force`` discards nothing. A
    run given write tools loses its uncommitted edits here; its commits
    survive, because a branch that moved off ``base`` is kept.
    """
    script = script or (repo_root / _WORKTREE_SCRIPT)
    try:
        subprocess.run(
            ["bash", str(script), "remove", str(path), "--repo", str(repo_root),
             "--force"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    if branch:
        _delete_branch_if_unchanged(repo_root, branch, base)


# ---------------------------------------------------------------------------
# The receipt.
# ---------------------------------------------------------------------------

DEFAULT_TOOLS: tuple[str, ...] = DEFAULT_CLAUDE_TOOLS  # Read, Grep, Glob
DEFAULT_MAX_TURNS = 20
DEFAULT_TIMEOUT_S = 900.0


@dataclass
class Receipt:
    """What the receipt.json carries. Every field is a measured fact or an
    honest ``None`` -- never a guess (``rules/prove-it-claims-canonical.md``)."""

    origin: str
    cwd: str
    model: Optional[str]
    tools_allowed: list
    tools_used: list
    cost: Optional[float]
    outcome: str
    idr_leaf: Optional[str]
    started_at: str
    ended_at: str
    tldr: str
    task: str
    blocked_action: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "origin": self.origin, "cwd": self.cwd, "model": self.model,
            "tools_allowed": self.tools_allowed, "tools_used": self.tools_used,
            "cost": self.cost, "outcome": self.outcome,
            "idr_leaf": self.idr_leaf, "started_at": self.started_at,
            "ended_at": self.ended_at, "tldr": self.tldr, "task": self.task,
            "blocked_action": self.blocked_action, "error": self.error,
        }


def write_receipt(receipt: Receipt, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


_GATE_REFUSED_CWD = "(not entered -- the gate-equivalence check refused before launch)"
_THROTTLE_REFUSED_CWD = "(not entered -- the tick's throttle flag refused before launch)"


def _tldr(signals: RunSignals) -> str:
    if signals.outcome == "denied" and (signals.error or "").startswith("gate-equivalence"):
        return ("Refused before launch: at least one hard GRIP gate would not "
                "fire for this harness and tool set.")
    if signals.outcome == "denied" and (signals.error or "").startswith("throttle:"):
        return ("Refused before launch: the tick's throttle flag is set (the weekly "
                "Claude spend reached the operator's cap); headless GRIP runs refuse "
                "until it clears.")
    by_outcome = {
        "awaiting-approval": "Refused to run: the task named '{}', which "
                             "needs your approval first.".format(
                                 signals.blocked_action or "an outward action"),
        "completed": "Ran to completion with the read-only tool set; "
                    "nothing was sent or changed outside the run.",
        "denied": "A GRIP safety gate refused a tool call during the run.",
        "cancelled": "Stopped: the run exceeded its time budget.",
        "failed": "The run failed before producing a usable result.",
    }
    return by_outcome.get(
        signals.outcome,
        "The run finished, but its outcome could not be determined from "
        "the transcript.",
    )


def _append_receipt_idr(*, run_id: str, origin: str, task_text: str,
                        signals: RunSignals, tools_allowed: tuple) -> Optional[str]:
    """Append one IDR leaf recording this run's decision; return its id, or
    ``None`` if the chain could not be written (a receipt is still written
    either way -- a missing seal is disclosed, never silently substituted).
    """
    from lib.precog.idr import append_idr, build_idr

    decision = {
        "action": "headless-run",
        "outcome": signals.outcome,
        "tools_allowed": list(tools_allowed),
        "model": signals.model,
        "cost_usd": signals.cost,
        "blocked_action": signals.blocked_action,
    }
    inputs = {
        "run_id": run_id,
        "origin": origin,
        "task_sha256": hashlib.sha256(task_text.encode("utf-8")).hexdigest(),
        "task_len": len(task_text),
    }
    try:
        idr = build_idr(
            prompt="headless-run", fingerprint=f"headless-run:{run_id}",
            decision=decision, predecessor_idr=None, depth=0,
            kind="headless-run", inputs=inputs,
        )
        append_idr(idr)
    except Exception:  # noqa: BLE001 -- the receipt must still be written
        return None
    return idr.id


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


GateFn = Callable[[str, tuple], Admission]


def _default_gate(claude_dir: Path) -> GateFn:
    """The gate against this install's registry and the settings ``claude -p``
    will load. The isolation worktree is born from ``claude_dir``, so its
    project settings are ``claude_dir``'s own ``.claude/settings.json``."""
    def gate(harness_kind: str, tools: tuple) -> Admission:
        return admit_from_disk(harness_kind, tools, claude_dir=claude_dir,
                               project_dir=claude_dir)
    return gate


def _gate_refusal(admission: Admission) -> Optional[RunSignals]:
    """``RunSignals`` for a refused admission, or ``None`` when admitted."""
    if admission.ok:
        return None
    return RunSignals(outcome="denied", error=admission.reason()[:500])


_NO_WORKTREE_CWD = "(not entered -- the isolation worktree could not be created)"


def _isolation_worktrees_root() -> Path:
    """Where headless worktrees live: OUTSIDE the repository, which
    ``lib/grip-worktree.sh`` refuses otherwise. Same home and override as
    session worktrees (``lib/grip_session_wizard.py::_worktree_home``).

    Slice 1 placed them under ``state/headless-runs/``, inside the repo, so
    the first live run (2026-10-26) died at worktree creation with "Worktree
    path must be outside the repo" -- every real run failed before
    ``claude -p`` was spawned, and no receipt was written.
    """
    raw = os.environ.get("GRIP_WORKTREE_HOME", "").strip()
    return Path(raw) if raw else Path.home() / ".grip-worktrees"


def _run_in_isolation(repo_root: Path, run_id: str, task: Task, *, tools: tuple,
                      max_turns: int, model: Optional[str]) -> tuple:
    """Create the isolation worktree, run the harness in it, always remove it.

    Returns ``(cwd_str, signals)``. A worktree that cannot be created is a
    ``failed`` run that still gets its receipt, never an unhandled crash.
    """
    branch = _isolation_branch(run_id)
    try:
        path = create_isolation_worktree(repo_root, _isolation_worktrees_root(), run_id)
    except RuntimeError as exc:
        return _NO_WORKTREE_CWD, RunSignals(outcome="failed", error=str(exc)[:500])
    base = _branch_tip(repo_root, branch)
    try:
        harness = HeadlessClaudeHarness(tools=tools, max_turns=max_turns,
                                        model=model, runner=_cwd_runner(path))
        return str(path), _signals_from_result(harness.run(task))
    finally:
        remove_isolation_worktree(repo_root, path, branch=branch, base=base)


def run_headless_task(
    task_text: str,
    *,
    origin: str = "operator",
    tools: tuple = DEFAULT_TOOLS,
    max_turns: int = DEFAULT_MAX_TURNS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    model: Optional[str] = None,
    claude_dir: Optional[Path] = None,
    run_id: Optional[str] = None,
    receipts_dir: Optional[Path] = None,
    harness_maker: Optional[Callable[[Path], object]] = None,
    gate: Optional[GateFn] = None,
) -> tuple:
    """Run ``task_text`` headlessly and write a verifiable receipt.

    ``harness_maker``, when given, is called as ``harness_maker(repo_root)
    -> Harness`` INSTEAD of creating a real isolation worktree and a real
    ``HeadlessClaudeHarness`` -- the seam tests use to run the pipeline
    against a fake transcript with no subprocess, and to prove the
    outward-action barrier never constructs a harness at all.

    ``gate`` decides gate-equivalence as ``gate(harness_kind, tools) ->
    Admission``; omitted, it reads this install's registry and live settings
    (``lib/headless_gate.py``). It runs on BOTH paths before ``run`` is ever
    called, keyed on the kind of the harness that would actually run.

    The tick's throttle flag (``state/session/throttle.json`` under
    ``claude_dir``, ``lib/throttle_flag.py``) is read before either path makes
    a harness: while it is set, a headless GRIP-started run refuses to start
    (V>> 2026-10-29) and the receipt records ``denied`` with the flag's own
    line as the reason. Deleting the file is the reversal.

    Returns ``(Receipt, receipt_path)``.
    """
    started_at = _now()
    run_id = run_id or f"{int(time.time())}-{secrets.token_hex(4)}"
    claude_dir = claude_dir or get_claude_dir()
    receipts_dir = receipts_dir or (claude_dir / "state" / "headless-runs" / run_id)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    gate = gate or _default_gate(claude_dir)

    blocked = detect_outward_action(task_text)
    throttled = throttle_flag.describe(claude_dir / "state" / "session" / "throttle.json")
    repo_root = claude_dir
    task = Task(id=run_id, prompt=task_text, timeout_s=timeout_s)

    if blocked:
        signals = RunSignals(outcome="awaiting-approval",
                             blocked_action=blocked)
        cwd_str = "(not entered -- outward action refused before launch)"
    elif throttled:
        signals = RunSignals(outcome="denied", error=f"throttle: {throttled}"[:500])
        cwd_str = _THROTTLE_REFUSED_CWD
    elif harness_maker is not None:
        harness = harness_maker(repo_root)
        signals = _gate_refusal(gate(harness.kind, tuple(tools)))
        cwd_str = _GATE_REFUSED_CWD if signals else str(repo_root)
        signals = signals or _signals_from_result(harness.run(task))
    else:
        signals = _gate_refusal(gate(HeadlessClaudeHarness.KIND, tuple(tools)))
        cwd_str = _GATE_REFUSED_CWD
        if signals is None:
            cwd_str, signals = _run_in_isolation(repo_root, run_id, task, tools=tools,
                                                 max_turns=max_turns, model=model)

    idr_leaf = _append_receipt_idr(run_id=run_id, origin=origin, task_text=task_text,
                                   signals=signals, tools_allowed=tools)
    receipt = Receipt(
        origin=origin, cwd=cwd_str, model=signals.model,
        tools_allowed=list(tools), tools_used=list(signals.tools_used),
        cost=signals.cost, outcome=signals.outcome, idr_leaf=idr_leaf,
        started_at=started_at, ended_at=_now(), tldr=_tldr(signals),
        task=task_text, blocked_action=signals.blocked_action,
        error=signals.error,
    )
    receipt_path = receipts_dir / "receipt.json"
    write_receipt(receipt, receipt_path)
    return receipt, receipt_path


# ---------------------------------------------------------------------------
# CLI -- what bin/grip-run execs into.
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-run",
        description="Run a task through GRIP with no interactive session, "
                    "and write a verifiable receipt. See docs/headless-grip.md.",
    )
    parser.add_argument("task", help='the task text, as gg++ -p "<task>" receives it')
    parser.add_argument("--origin", choices=("operator", "relay", "cron"),
                        default="operator", help="who asked for this run")
    parser.add_argument("--tools", default=",".join(DEFAULT_TOOLS),
                        help="comma-separated tool allowlist")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, dest="max_turns")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        dest="timeout_s", help="wall-clock budget, seconds")
    parser.add_argument("--model", default=None,
                        help="explicit model id; omitted, the run takes the "
                             "account default, never the interactive pin")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    tools = tuple(t.strip() for t in args.tools.split(",") if t.strip())
    receipt, path = run_headless_task(
        args.task, origin=args.origin, tools=tools, max_turns=args.max_turns,
        timeout_s=args.timeout_s, model=args.model,
    )
    print(json.dumps(receipt.to_dict(), indent=2, sort_keys=True))
    print(f"receipt: {path}", file=sys.stderr)
    return 0 if receipt.outcome in ("completed", "awaiting-approval") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
