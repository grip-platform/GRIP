"""Verbind's Claude Code adapter, library half: envelope and instructions.

The only module in the package that knows about Claude Code. The hook
(``hooks/verbind-hook.py``) and the CLI's default envelope both use it.

Origin, from Claude Code's own markers, measured on 2.1.288 (2026-10-03) with a
probe UserPromptSubmit hook in a fenced session:

  interactive session  CLAUDE_CODE_ENTRYPOINT=cli      CLAUDE_CODE_SESSION_ATTENDED=1
  headless claude -p   CLAUDE_CODE_ENTRYPOINT=sdk-cli  CLAUDE_CODE_SESSION_ATTENDED=0

The rules are fail-closed -- anything not positively the operator is untrusted:

  channel   the prompt carries a ``<channel ...>`` tag (a grip-channel message)
  relay     ``GRIP_RELAY_WRITE_ROOT`` is set (a relay co-steerer spawn)
  operator  entrypoint ``cli`` AND attended ``1``
  headless  any other entrypoint; every relay spawn is headless
  unknown   no entrypoint marker at all

The trust boundary, stated plainly: these are environment variables, so a
process able to set them can forge an operator origin. v1 bounds the blast
radius instead: only reviewed read-only operations are bindable, and a relay
session for an untrusted sender runs without a shell to set anything
(rules/untrusted-content-capability-lock.md).
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Mapping

from .gate import Envelope, GateResult, describe_operation

__all__ = [
    "CLI",
    "HARNESS",
    "apply_instructions",
    "envelope",
    "is_interactive",
    "koppel_instructions",
    "origin",
]

HARNESS = "claude-code"
CLI = '"${CLAUDE_DIR:-$HOME/.claude}/bin/grip-verbind"'
_CHANNEL_TAG = re.compile(r"<channel\b", re.IGNORECASE)


def is_interactive(environ: Mapping[str, str]) -> bool:
    return environ.get("CLAUDE_CODE_ENTRYPOINT") == "cli"


def origin(prompt: str, environ: Mapping[str, str]) -> str:
    if _CHANNEL_TAG.search(prompt or ""):
        return "channel"
    if environ.get("GRIP_RELAY_WRITE_ROOT"):
        return "relay"
    entry = environ.get("CLAUDE_CODE_ENTRYPOINT") or ""
    if not entry:
        return "unknown"
    if entry == "cli" and environ.get("CLAUDE_CODE_SESSION_ATTENDED") == "1":
        return "operator"
    return "headless"


def envelope(prompt: str, environ: Mapping[str, str], session: str = "", ref: str = "") -> Envelope:
    return Envelope(origin(prompt, environ), HARNESS, str(session or ""), str(ref or ""))


def _how(kind: str, target: str) -> str:
    if kind == "agent":
        return f"dispatch the `{target}` agent (Agent tool, subagent_type `{target}`)"
    if kind == "mode":
        return f"run `/mode {target}`"
    return f"invoke the Skill tool with skill `{target}`"


def apply_instructions(result: GateResult) -> str:
    """What the model should run for a bound verb, in one paragraph."""
    m = result.binding.meaning
    args = result.args or "(no further words)"
    return (f"[VERBIND] /{result.verb} is bound to {describe_operation(m)} ({m.kind} `{m.target}`). "
            f"Apply it now: {_how(m.kind, m.target)}, passing these words from the operator's "
            f"prompt: {json.dumps(args)}. Then carry on as that operation directs. "
            f"Mention in one line that /{result.verb} ran as {describe_operation(m)}, and that "
            f"`/verbind refine {result.verb}` corrects it.")


def koppel_instructions(result: GateResult) -> str:
    """How the model runs one Koppel round and reports the answer back."""
    payload = result.koppel_payload or {}
    verb, args = result.verb, shlex.quote(result.args or "")
    offered = "; ".join(f'"{o["label"]}" -> --kind {o["kind"]} --target {o["slug"]}'
                        for o in payload.get("offered", [])) or "none"
    return "\n".join([
        f"[VERBIND] /{verb} is a coined verb with no binding yet "
        f"(Koppel round {result.round} of 11). Run this round before anything else:",
        f"1. Call AskUserQuestion with exactly these questions: {json.dumps(payload.get('questions', []))}",
        f"2. If the operator picks an offered operation ({offered}), run: "
        f"{CLI} refine {verb} --kind <kind> --target <slug> --args {args} "
        f'--note "<their reason, in a few words>"',
        f"3. If they choose to explain, ask what /{verb} should do, pick ONE operation from "
        f"`{CLI} list --readonly` that fits, confirm it with them, then run refine as in step 2. "
        f"If nothing on that list fits, run: {CLI} refine {verb} --defer",
        f"4. If they choose to defer, run: {CLI} refine {verb} --defer, then answer the "
        f"prompt as plain text.",
        "Never bind a meaning the operator did not choose. The refine output says what to run next.",
    ])
