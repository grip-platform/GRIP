#!/usr/bin/env python3
"""Stop hook: record the RECOMMENDED NEXT STEP closer; when armed, continue `(mine)`.

GRIP ends every substantive turn with `RECOMMENDED NEXT STEP (mine|yours|<named party>)`.
This hook reads that line. It never holds a turn for the operator, never acts
on a `(yours)` question beyond recording it, and never sends him anything.

BY DEFAULT -- unarmed, which is how every machine starts (see WHERE THE SWITCH
LIVES) -- it only records, and nothing appears on the operator's screen:

  * every closer is logged with its session id, so the closer's ACCURACY is
    measurable; it cannot be measured without first being recorded.
  * a `(yours)` step is written to the cross-session pending store
    (state/pending-questions.json). The answer watcher (bin/grip-answer-watch,
    a launchd timer) parks it at the 20-minute ceiling and sends the operator
    one WhatsApp notice he can reply to, latched so a busy day cannot become a
    busy phone. That path does not depend on this hook being armed.
  * every `(yours)` step carries a materiality verdict (decision, chore or
    unknown, from `lib/pending_materiality.py`'s deterministic floor) on its
    record and its log line. SHADOW mode: only with
    GRIP_PENDING_MATERIALITY_GATE=1 set is a floor-named chore left off the store.

  It blocks nothing, sends nothing, and writes nothing but its own log line and
  the pending store. tests/test_next_step_recorder.py pins exactly that with
  the switch off, so the contract does not depend on any setting.

ARMED -- the operator's decision and never the agent's (lib/next_step_router.py,
"arming") -- one thing changes:

  * `(mine)`: the Stop is blocked, so the session carries on with the step it
    set itself, and keeps carrying on while the brakes allow: stall, a
    wall-time budget and an outer cap (lib/continuation_brake.py). A prompt the
    operator types starts a new chain (`chain_for_turn`), so the budget counts
    time since he last spoke, never since the session began, and progress is
    read from the turn's own tool use as well as the session's repository, so
    work done in another worktree or on GitHub is not a stall. Until
    2026-10-12 the harness's `stop_hook_active` flag ended every chain after
    one turn and a chain never reset: a long session spent its budget on its
    first day, its stall count stuck at five because its work lived in other
    worktrees, and every later `(mine)` stop went through with the reason on a
    stderr line the terminal never shows. V>>: "It doesn't make sense to
    halt." The brakes bound a chain's LENGTH; nothing bounds its
    effects beyond the session's normal rules and gates. Claude Code draws a
    block reason on the operator's screen as well as handing it to the model,
    and no Stop channel that keeps a turn going is hidden from him, so the
    reason is one plain line with nothing about him in the third person and no
    internal vocabulary. lib/operator_voice_detector.py checks it.

  A `(yours)` question is handled exactly as when unarmed. A closer that names
  a GRIP tool as its owner, such as `(grip-tree-sync)` or `(AGORA)`, counts as
  `(mine)` (lib/next_step_router.py, GRIP#9073).

WHY `(yours)` IS LEFT ALONE. Until 2026-10-02 an armed `(yours)` blocked the
Stop with a "HOLDING for V>> ... He can answer from any shim -- ref <hex>" reason:
text written for the model, drawn on the operator's screen, about him in the
third person. He could not decode it and called it noise. Measured from the live
store that day: 95 questions since arming on 2026-09-20, 2 answered, both in the
session itself and none from any other channel; a hold lasted exactly one forced
turn (main() allows on `stop_hook_active`) and skipped the brakes; its presence
check also fired on prompts the system injects; and nothing ever delivered an
answer into a held session. A session judged unread had its question parked at
once instead; the answer watcher skips parked questions, so the 32 parked that
way were never notified and kept the notice latch closed for every later one.
The ceiling and the notice belong to the answer watcher, which runs whether or
not anybody takes a turn.

WHERE THE SWITCH LIVES. In this machine's state directory:
<claude dir>/state/next-step-armed.json, ignored by git, set and cleared only by
the operator with `bin/grip-next-step arm|disarm`, and read at every Stop.
GRIP_NEXT_STEP_ARMED=1 or =0 overrides it for one process (the tests use that).
It used to be an env var in the tracked settings.json, where the session-start
merge (lib/settings_merge.py) erased it whenever config/settings.reference.json
changed: the live ~/.grip/settings.json had already lost it on 2026-10-02.
Carrying it in the reference would arm every install, and exempting it from the
merge would arm every checkout. Two non-Anthropic council sittings (seals
abfbd094e3f83a9f and 6b4c6089389cc8bb) ruled for the per-machine file.

Fail-open on every path: a recorder that can break a turn is worse than no
recorder, so every error resolves to "allow" and is logged rather than raised.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _allow() -> int:
    print("{}")
    return 0


def _repo_fingerprint(cwd: str) -> str:
    """An opaque summary of the repository: equal means nothing changed.

    HEAD plus the working-tree status covers both halves of "did this turn do
    anything" -- a commit moves HEAD, an uncommitted edit moves the status.

    On ANY git failure this returns a value that can never repeat. That is
    deliberate and it is the UNDER-block direction: a broken probe must not
    make working turns look identical and stop a healthy chain. It leaves the
    stall brake blind, which is precisely the case the outer cap is kept for.
    """
    import hashlib
    import subprocess
    import time as _t

    try:
        head = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        status = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if head.returncode != 0 or status.returncode != 0:
            return f"no-probe-{_t.time_ns()}"
        blob = head.stdout.strip() + "\n" + status.stdout
        return hashlib.sha256(blob.encode()).hexdigest()[:24]
    except Exception:
        return f"no-probe-{_t.time_ns()}"


def _transcript_rows(transcript: Path) -> list[dict]:
    """Every parseable row of a transcript, oldest first; unparseable lines skipped."""
    rows: list[dict] = []
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _is_human_prompt(rec: dict) -> bool:
    """A prompt a person typed, as lib/transcript_text.is_human decides (GRIP#9085).

    Claude Code writes a Stop hook's block reason back into the transcript as a
    user row with `isMeta: true` ("Stop hook feedback: Continuation 1 ..."), a
    tool result as a user row of `tool_result` blocks, and a task notification or
    a compaction summary as a user row with text. Nobody typed any of them, so
    none may restart the chain's budget or end the turn the stall check reads.
    Beside an older lib/ without is_human no row counts: the chain then keeps
    its budget, which stops a run sooner, never later.
    """
    try:
        from lib.transcript_text import is_human
    except ImportError:
        return False
    return is_human(rec)


def _parse_ts(value) -> float | None:
    """A transcript timestamp (ISO 8601, usually ending in Z) as epoch seconds."""
    from datetime import datetime

    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _last_human_prompt_at(rows: list[dict]) -> float | None:
    """When the operator last typed a prompt, from the transcript; None if unknown."""
    for rec in reversed(rows):
        if _is_human_prompt(rec):
            return _parse_ts(rec.get("timestamp"))
    return None


def _turn_activity(rows: list[dict]) -> str:
    """A fingerprint of the tools the session has used since the operator last spoke.

    The repository probe sees one checkout, the hook's cwd. A session that does
    its work in other worktrees, on GitHub or on the shared tree changes nothing
    there, so three real turns read as a stall (the 2026-10-12 case). Tool-use
    ids are unique per call, so any tool call since the previous Stop changes
    this fingerprint; a continuation that only talks leaves it as it was.
    """
    import hashlib

    ids: list[str] = []
    for rec in reversed(rows):
        if _is_human_prompt(rec):
            break
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, list):
            ids.extend(str(b.get("id") or "") for b in content
                       if isinstance(b, dict) and b.get("type") == "tool_use")
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def _closer(payload: dict):
    """The RECOMMENDED NEXT STEP of the message this Stop is about.

    Parsed from the payload's own copy of that message. The transcript can lag
    behind the Stop event, and reading it alone let 53 of 73 `(mine)` turns stop
    in the seven days to 2026-11-09, each on a step the session had just called
    its own (lib/transcript_text.py). main() has put the checkout on sys.path.
    """
    from lib.next_step_router import parse
    from lib.transcript_text import stop_message_text

    return parse(stop_message_text(payload))


def _continue_or_brake(chain, counts, sid, cap_file, step, fingerprint: str) -> int:
    """Decide whether an autonomous chain continues, and record its new state.

    Extracted from main() because it is a cohesive decision with its own
    inputs and its own single outcome, and because leaving it inline put
    main() at CC 31 against a ceiling of 25 -- every branch here is a path
    through a function that also parses a transcript and routes three
    ownership cases. Decomposing is the remedy the CC principle names.

    Three brakes, measuring three different things. A counter alone measures
    neither progress nor exposure: one continuation can do real damage, and
    eleven can be legitimate incremental work.

    Two outputs, both JSON on stdout. While the chain may continue:
    `{"decision": "block", "reason": "Continuation N: ..."}`, which Claude Code
    hands to the model and draws on the operator's screen. When a brake fires:
    `{"systemMessage": "STOPPING -- <why>"}` with no decision, so the stop goes
    through and the operator sees why; it costs no model turn (AGORA on
    GRIP#8197 asked for this to be written down).
    """
    import json
    import sys
    import time

    from lib.answer_watcher import afk_remaining_seconds
    from lib.continuation_brake import (
        DEFAULT_CAP, DEFAULT_STALL_LIMIT, Brake, assess, budget_seconds, explain,
    )

    budget = budget_seconds(afk_remaining_seconds())
    brake, chain = assess(chain, fingerprint, time.time(), budget=budget)
    counts[sid] = chain.to_dict()
    cap_file.parent.mkdir(parents=True, exist_ok=True)
    cap_file.write_text(json.dumps(counts))

    if brake is not Brake.NONE:
        why = explain(brake, chain, budget=budget, cap=DEFAULT_CAP,
                      stall_limit=DEFAULT_STALL_LIMIT)
        sys.stderr.write(why + "\n")
        # The terminal hides a Stop hook's stderr, so until 2026-10-12 a chain
        # ended in silence. A systemMessage is drawn for the operator and costs
        # no model turn; with no decision beside it the stop goes through.
        print(json.dumps({"systemMessage": why}))
        return 0

    print(json.dumps({"decision": "block",
                      "reason": _continue_reason(chain.used, step)}))
    return 0


def _continue_reason(used: int, step) -> str:
    """The block reason for a `(mine)` continuation: one plain, factual line.

    Claude Code draws a Stop block reason on the operator's screen ("Stop hook
    error: <reason>") as well as handing it to the model, and no Stop channel
    that keeps a turn going is hidden from him (measured against 2.1.283's code
    and documentation, 2026-10-02). So the reason carries no model-only
    instruction: it says what is happening, in words he can read, and nothing
    about him in the third person. `lib/operator_voice_detector.py` checks it.

    It used to add that nothing leaving the machine was approved, and to point
    at lib/timeout_effect_gate. Nothing calls that gate at runtime, the model
    acted outward after its refusal 3 times in 24 forced turns, and "a push" in
    the list contradicted GRIP's rule to push every commit at once. Two
    non-Anthropic council sittings (seals abfbd094e3f83a9f, 6b4c6089389cc8bb)
    ruled the bound must be enforced or dropped; enforcing it is a new gate and
    a decision about pushes, so it is dropped and the session's normal rules
    and gates govern what a continuation may do.

    A step whose closer named a GRIP tool as its owner says so, because the
    closer on screen reads `(grip-tree-sync)` or `(AGORA)`, not `(mine)`
    (lib/next_step_router.py, GRIP#9073). It names the matched tool, never the
    owner text as written, so no other text in the closer reaches this line.
    """
    if step.tool:
        return (f"Continuation {used}: the next-step line named {step.tool}, a "
                f"GRIP tool, as its owner, so this session carries on with that "
                f"step — {step.text[:180]}")
    return (f"Continuation {used}: carrying on with the next step this session "
            f"set itself — {step.text[:180]}")


def _floor_materiality(step):
    """The floor's materiality verdict on a `(yours)` step, or None. Never raises.

    FLOOR ONLY, on purpose. Measured 2026-09-29: this hook runs in 31-75 ms, while
    a model classification takes 1.0-1.6 s (median 1.26 s over 8 cold calls) and
    its read timeout is 45 s (`lib/hal_llm_adapter.py`), against the 10 s budget
    this hook is bound with in settings.json. A hook killed mid-call before
    `store.add` would lose the question, which is the one error this gate exists
    never to make. The model runs offline, in scripts/pending-materiality-report.py.

    None means "no verdict", and the writer keeps a question with no verdict: a
    classifier that cannot run must never cost the operator a question.
    """
    try:
        from lib.next_step_router import Owner
        from lib.pending_materiality import classify_floor

        if step.owner is not Owner.YOURS:
            return None
        return classify_floor(step.text)
    except Exception:
        return None


def _materiality_log(mat) -> dict:
    """What the materiality gate saw and did, for the shadow log. Never any text.

    `gate_would_drop` is logged whether or not the gate is armed, so the log shows
    what arming WOULD remove on every real turn; `gate_dropped` says whether it
    did. Never raises: this runs before the question is written, and anything
    that raised here would lose the question to the catch-all in main().
    """
    try:
        from lib.pending_materiality import gate_armed, should_drop

        return {
            "materiality": mat.as_dict() if mat is not None else None,
            "gate_armed": gate_armed(),
            "gate_would_drop": bool(mat is not None and mat.droppable),
            "gate_dropped": should_drop(mat),
        }
    except Exception:
        return {"materiality": None, "gate_armed": False,
                "gate_would_drop": False, "gate_dropped": False}


def _materiality_fields(mat) -> dict:
    """The Pending fields for a verdict; empty, so the defaults apply, for none."""
    if mat is None:
        return {}
    return {"materiality": mat.verdict, "why_yours": mat.why_yours}


def _log_token(state_root: Path, sid: str, step, verdict_log: dict) -> None:
    """Append one line to the accuracy corpus: enough to measure later, never any
    message content.

    The plan is logged even while DISARMED, so the record shows what the rule
    WOULD have done on every real turn. That shadow log is the only evidence that
    can answer whether arming is safe — a question that has been unanswerable
    because nothing recorded it. The materiality verdict rides on the same line
    for the same reason.
    """
    from lib.next_step_router import plan

    log = state_root / "logs" / "next-step-tokens.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(json.dumps({
            "session_id": sid,
            "owner": step.owner.value,
            "party": step.party,
            "question_id": step.question_id,
            "is_null": step.is_null,
            "would": plan(step),
            **verdict_log,
        }) + "\n")


def _add_question(store, step, sid: str, mat, verdict_log: dict):
    """Write a `(yours)` step to the shared store, with its materiality verdict.

    Returns the stored Pending, or None when nothing was added: the step is not
    `(yours)`, or the ARMED materiality gate named it a chore.
    """
    import time
    from lib.next_step_router import Owner
    from lib.pending_question import Pending

    if step.owner is not Owner.YOURS or verdict_log["gate_dropped"]:
        return None
    return store.add(Pending(
        question_id=step.question_id,
        session_id=sid,
        session_name=sid[:12] or "unknown",
        asked_at=time.time(),
        summary=step.text[:160] or "a decision is waiting",
        step_text=step.text,
        **_materiality_fields(mat),
    ))


def _may_be_own_step(step, payload: dict, switch: str) -> bool:
    """True when a `(yours)` closer may be the session's own step: the Stop is
    not already a continuation (a second block would loop), and the named
    switch is not set to "0". Armed or not: neither of the operator's
    instructions behind the callers depended on the arm switch."""
    import os

    from lib.next_step_router import Owner

    if step.owner is not Owner.YOURS or payload.get("stop_hook_active"):
        return False
    return os.environ.get(switch, "1").strip() != "0"


def _merge_yourself(step, payload: dict):
    """A `(yours)` closer that is only a `gh pr merge` of a pull request AGORA has
    passed is the session's own step (V>> 2026-10-12, rules/an-agora-mergeable-
    pr-is-merged-not-handed-over.md). Returns lib.merge_yourself's Decision, or
    None when the step is not that, the guard says no, or anything fails: a
    probe that cannot see never forces a merge."""
    if not _may_be_own_step(step, payload, "GRIP_MERGE_YOURSELF"):
        return None
    try:
        from lib.merge_yourself import decide

        return decide(step.text, root=Path(__file__).resolve().parent.parent)
    except Exception:
        return None


def _run_it_yourself(step, payload: dict):
    """A `(yours)` closer whose step is only a GRIP command the session can run
    is the session's own step (V>> 2026-11-16, after a session handed him
    `grip-py ... hypothesis_engine.py override`). Returns lib.run_it_yourself's
    Decision, or None when the guard says no or anything fails."""
    if not _may_be_own_step(step, payload, "GRIP_RUN_IT_YOURSELF"):
        return None
    try:
        from lib.run_it_yourself import decide

        return decide(step.text, root=Path(__file__).resolve().parent.parent)
    except Exception:
        return None


def _log_and_merge_block(step, payload: dict, state_root: Path, sid: str, verdict_log: dict):
    """Log the closer with any own-step verdict, and return the Stop block when
    the step is the session's own, else None. A merge is judged first, and the
    command check runs only when that check does not recognise the step, so a
    merge is never claimed twice. No question is parked for a step the session
    takes itself."""
    merge = _merge_yourself(step, payload)
    run = _run_it_yourself(step, payload) if merge is None else None
    if merge is not None:
        verdict_log = {**verdict_log, "merge_yourself": merge.log}
    if run is not None:
        verdict_log = {**verdict_log, "run_it_yourself": run.log}
    _log_token(state_root, sid, step, verdict_log)
    blocker = next((d for d in (merge, run) if d is not None and d.block), None)
    return {"decision": "block", "reason": blocker.reason} if blocker else None


def _armed(step, state_root: Path, sid: str, transcript: Path) -> int:
    """The ARMED path: continue a `(mine)` step. Nothing else is acted on.

    A `(yours)` question is recorded by main() and left open, armed or not.
    The answer watcher parks it at the 20-minute ceiling and sends the one
    notice; anything this hook did to it would only get in the way, because
    the watcher skips a question that is already parked. Until 2026-10-02 the
    hook held an attended `(yours)` turn and parked an unattended one at once:
    32 questions parked that way were never notified, and kept the notice
    latch closed for every question after them.

    What stands between this and a session that never ends is the brakes in
    lib/continuation_brake.py, none of them a judgement call: a chain that
    changes nothing three times in a row (the repository AND the turn's tool
    use both unchanged), a chain past its wall-time budget (the operator's
    /afk window, else two hours since he last spoke), and an outer cap of 40
    continuations for a broken sensor. The harness's `stop_hook_active` flag
    used to end every chain after one turn; since 2026-10-12 it does not, and
    the chain is reset by the operator's own prompt instead (`chain_for_turn`).
    """
    import os

    from lib.continuation_brake import Chain, chain_for_turn
    from lib.next_step_router import Owner

    if step.owner is not Owner.MINE:
        return _allow()
    cap_file = state_root / "state" / "next-step-continuations.json"
    try:
        counts = json.loads(cap_file.read_text())
    except Exception:
        counts = {}
    rows = _transcript_rows(transcript)
    chain = chain_for_turn(Chain.from_dict(counts.get(sid)), _last_human_prompt_at(rows))
    fingerprint = f"{_repo_fingerprint(os.getcwd())}|{_turn_activity(rows)}"
    return _continue_or_brake(chain, counts, sid, cap_file, step, fingerprint)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return _allow()

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lib.next_step_router import Owner, is_armed
        from lib.pending_question import PendingStore

        transcript = str(payload.get("transcript_path", ""))
        if not transcript or not Path(transcript).exists():
            return _allow()

        step = _closer(payload)
        if step.owner is Owner.ABSENT:
            return _allow()

        # Presence Wave 3, SHADOW mode: every `(yours)` step carries a verdict;
        # nothing is dropped unless GRIP_PENDING_MATERIALITY_GATE=1.
        mat = _floor_materiality(step)
        verdict_log = _materiality_log(mat)

        # The state dir is overridable so tests never touch the live store.
        root = Path(__file__).resolve().parent.parent
        state_root = Path(os.environ.get("GRIP_NEXT_STEP_STATE_DIR") or root)
        sid = str(payload.get("session_id", ""))
        blocked = _log_and_merge_block(step, payload, state_root, sid, verdict_log)
        if blocked is not None:
            print(json.dumps(blocked))
            return 0

        store = PendingStore(state_root / "state" / "pending-questions.json")
        _add_question(store, step, sid, mat, verdict_log)
        if not is_armed():
            return _allow()
        # The harness sets stop_hook_active on the Stop after a block. It used
        # to end every chain here after one turn; the brakes end a chain now,
        # and the flag only keeps a (yours) or third-party closer out of the
        # continuation path.
        if payload.get("stop_hook_active") and step.owner is not Owner.MINE:
            return _allow()
        return _armed(step, state_root, sid, Path(transcript))
    except Exception:
        # Recording is never worth breaking a turn over.
        return _allow()


if __name__ == "__main__":
    sys.exit(main())
