#!/usr/bin/env python3
"""PostToolUse hook: grant destructive approvals from the ANSWER, never the question.

Why this hook exists
--------------------
Until #4809 the approval grant lived in the PreToolUse path
(``hooks/context-gate-hook.py::_gate_0_always_allow`` ->
``_process_approval_question``). That fired when the question was *asked* —
before any answer existed — and matched keywords in the QUESTION TEXT. So an
agent that merely wrote "force push" into a question held ``force_push``
approval for the full 600s TTL whether the operator clicked yes or no. The same
held for ``deploy`` (3600s), ``tailscale_ssh`` (14400s) and the phrase that
unlocks ``GRIP_AUTONOMOUS``. Nothing read ``tool_response``, and the single
``clear_destructive_approval`` callsite is consumption-after-use, so a "no"
cleared nothing.

PostToolUse is the only seam where the human's answer exists. The payload shape
is verified against real transcripts (``toolUseResult`` for an AskUserQuestion
tool_use, e.g. ``projects/-Users-you--grip/5d9e47a9-*.jsonl``)::

    {"questions": [...], "answers": {"<question text>": "<selected label>"},
     "annotations": {}}

and, when the call errored, a bare string ("Error: ...") instead of a dict.

Design invariants
-----------------
* DEFAULT DENY — a missing, non-dict, unparseable, negative or merely ambiguous
  answer grants nothing at all. The decision lives in
  ``gates.approval_manager.grant_from_answer``.
* NEVER BLOCKS — this hook has no verdict to give; it always exits 0. Its
  failure withholds a privilege, it does not withhold a tool call.
* NO QUESTION-ONLY GRANTS — the question selects *which* action is on the
  table; the answer decides *whether* it is granted.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent  # where THIS file lives — used only for imports

# State root resolved exactly as lib/gates/approval_manager.py:23 and
# hooks/context-gate-hook.py:123 resolve it, so the approval store and the plan
# cache can never land in two different trees. Deriving it from __file__ instead
# would split them on a machine where ~/.claude and ~/.grip are separate real
# directories rather than a symlink (A>>'s Windows box is exactly that).
# Honouring CLAUDE_DIR_OVERRIDE also keeps tests off live state — learned the
# hard way while writing them: a hardcoded path flipped the operator's real
# cache/last-plan.json to status=executing.
_CLAUDE = Path(os.environ.get("CLAUDE_DIR_OVERRIDE") or (Path.home() / ".claude"))
for _p in (str(_REPO / "lib"), str(_REPO / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gates.approval_manager import (  # noqa: E402
    AFFIRMATIVE,
    classify_answer,
    configure_logging,
    grant_from_answer,
    mark_plan_executing,
    question_requests_plan_execution,
)

HOOK_LOG = _CLAUDE / "logs" / "hooks.log"  # override-aware: tests log to their tmp dir
PLAN_CACHE = _CLAUDE / "cache" / "last-plan.json"


def log(level: str, message: str) -> None:
    """Append one line to the shared hook log. Never raises."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with HOOK_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [ASKUSERQUESTION-APPROVAL][{level}] {message}\n")
    except OSError:
        pass


configure_logging(log)


def extract_answers(payload) -> dict:
    """Pull the {question: answer} map out of a PostToolUse payload.

    Returns {} for anything that is not an AskUserQuestion carrying a dict of
    answers — including the error case, where tool_response is a plain string.
    An empty map grants nothing, which is the safe direction.
    """
    if not isinstance(payload, dict):
        return {}
    if payload.get("tool_name") != "AskUserQuestion":
        return {}
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return {}
    answers = response.get("answers")
    return answers if isinstance(answers, dict) else {}


def record_autonomous_consent() -> bool:
    """Stamp autonomous-mode consent. Returns True when the stamp was written.

    Imported lazily: hooks/autonomous_mode.py pulls in its own state paths, and
    a failure there must not stop the ordinary approval grants above it.

    KNOWN LIMIT: autonomous_mode.AUTONOMOUS_STATE_FILE is pinned to
    ``Path.home()/".claude"`` and honours no override, so this write always lands
    on the real state file. Delegating anyway is deliberate — a second writer of
    that file would drift from the module that owns its format. The consequence
    is that this one path is exercised in-process with the module's state file
    patched (tests/test_answer_gated_approval.py), never by subprocess.
    """
    try:
        from autonomous_mode import _load_autonomous_state, _save_autonomous_state
    except ImportError as e:
        log("ERROR", f"autonomous_mode import failed, consent NOT granted: {e}")
        return False
    try:
        state = _load_autonomous_state()
        state["consent_ts"] = datetime.now(timezone.utc).isoformat()
        _save_autonomous_state(state)
    except OSError as e:
        log("ERROR", f"Failed to write autonomous consent: {e}")
        return False
    log("INFO", "Autonomous mode consent granted from an affirmative answer")
    return True


def consent_phrase_answered(question: str, answer) -> bool:
    """True when the human affirmatively accepted autonomous-mode responsibility.

    The phrase may appear in the question or in the answer, but EITHER WAY the
    answer must classify AFFIRMATIVE. The phrase selects which privilege is on
    the table; it never decides that the privilege was granted.

    Origin of the AFFIRMATIVE requirement on the answer branch: until now the
    phrase-in-answer test short-circuited with a bare `return True`, so
    "No - I accept autonomous mode responsibility only after review"
    (classify_answer -> NEGATIVE) still stamped consent. That reintroduced, on
    the highest-privilege grant in the set, the exact text-confers-a-privilege
    defect this hook exists to close, and contradicted the module docstring's
    "a ... negative or merely ambiguous answer grants nothing at all".
    Aggravating: the answer string is an AGENT-AUTHORED option label, so an
    agent could put the phrase in its own decline label and be granted consent
    whichever button the operator pressed.

    Falsifier: an operator who types the phrase verbatim into the Other box
    still classifies AFFIRMATIVE, because "accept" is an affirmative marker —
    if a legitimate free-typed consent is ever refused here, the affirmative
    marker set is too narrow, not this gate.
    """
    try:
        from autonomous_mode import AUTONOMOUS_CONSENT_PHRASE
    except ImportError:
        return False
    if classify_answer(answer) != AFFIRMATIVE:
        return False
    phrase = AUTONOMOUS_CONSENT_PHRASE.lower()
    answer_text = answer.lower() if isinstance(answer, str) else ""
    question_text = question.lower() if isinstance(question, str) else ""
    return phrase in answer_text or phrase in question_text


def process(answers: dict) -> list[str]:
    """Grant everything the answers affirmatively authorise. Returns action keys."""
    granted: list[str] = []
    for question, answer in answers.items():
        granted.extend(grant_from_answer(question, answer))
        if consent_phrase_answered(question, answer):
            if record_autonomous_consent():
                granted.append("autonomous_consent")
        # Gate P's marker moves here for the same reason the grants did: a
        # cached plan is "executing" because the operator said so, not because
        # a question mentioned it.
        if question_requests_plan_execution(question) and classify_answer(answer) == AFFIRMATIVE:
            if mark_plan_executing(PLAN_CACHE):
                granted.append("plan_executing")
    return granted


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        log("WARN", "Unparseable payload — no approval granted")
        sys.exit(0)

    answers = extract_answers(payload)
    if not answers:
        sys.exit(0)

    try:
        granted = process(answers)
    except Exception as e:  # noqa: BLE001 — withholding a privilege is the safe failure
        log("ERROR", f"Approval pass failed, nothing granted: {e!r}")
        sys.exit(0)

    if granted:
        log("INFO", f"Approvals granted from answers: {granted}")
    sys.exit(0)


if __name__ == "__main__":
    main()
