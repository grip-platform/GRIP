#!/usr/bin/env python3
"""Verbind UserPromptSubmit hook -- the Claude Code adapter (docs/VERBIND.md section 7).

Builds the envelope from Claude Code's own markers, hands one prompt to the
Verbind gate, and turns the result into a UserPromptSubmit response:

  HITL + payload  additionalContext telling the model to run the Koppel round
                  with the given AskUserQuestion payload, then report the
                  answer with ``grip-verbind refine``; the one SURFACE line as
                  systemMessage
  APPLY           additionalContext naming the bound operation to run, unless
                  it has left the read-only list since it was bound
  anything else   the SURFACE line alone, or nothing at all

Which prompts reach the gate. Measured on Claude Code 2.1.288 (2026-10-03): in
an INTERACTIVE session an unknown leading ``/verb`` is rejected by the CLI and
no UserPromptSubmit hook runs for it. So a bare ``/verb`` that does reach this
hook interactively is a command Claude Code knows, and it is passed over
without loading anything. In an interactive session Verbind is reached through
the explicit form, ``/verbind <verb> <words>``. In a headless session the bare
form does arrive, and the gate sees it -- under an untrusted origin, so it can
never bind.

Zero cost by default: a prompt that does not start with "/" is passed over
after one JSON parse, before any library is imported or any signing check is
made. Only a prompt that may carry a verb re-execs under a signing-capable
interpreter (``lib/pq_reexec.py``); the payload rides across that exec in an
environment variable, because ``execv`` keeps the environment but not stdin
already consumed.

Never blocks: the response schema used has no field that blocks a prompt, and
every error exits 0 with no output. Self-exempt: ``/verbind`` subcommands
(refine, list, forget, ...) are the skill's own and never reach the gate.
Kill switch: ``GRIP_VERBIND_HOOK=off``. Audit: ``logs/verbind-hook.jsonl``,
hashes and enums only.

NOT BOUND in settings.json or the hooks registry by this change: activation is
the operator's step (the registry entry is in the PR that added this file).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent  # the checkout this hook lives in
PAYLOAD_ENV = "GRIP_VERBIND_HOOK_PAYLOAD"
KILL_ENV = "GRIP_VERBIND_HOOK"
#: The largest payload, in UTF-8 bytes, carried across the re-exec in an
#: environment variable -- bytes because that is what execve carries and limits.
#: Anything larger skips the re-exec and runs here, sealing as this interpreter
#: can. This machine's ARG_MAX is 1 MiB for arguments and
#: environment together, so 64 KiB leaves the rest of the environment its room.
MAX_CARRIED_PAYLOAD = 65536


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))


def _screen(raw: str):
    """The zero-cost screen: the parsed payload if it might carry a verb, else None."""
    if os.environ.get(KILL_ENV, "").strip().lower() in ("off", "0", "false", "no"):
        return None
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    return payload if isinstance(prompt, str) and prompt.lstrip().startswith("/") else None


def _audit(fields: dict) -> None:
    try:
        path = _claude_dir() / "logs" / "verbind-hook.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 **fields}, sort_keys=True) + "\n")
    except OSError:
        pass


def _response(result) -> dict | None:
    from verbind import claude_code
    from verbind.gate import Action

    context = None
    if result.action is Action.ASK and result.koppel_payload:
        context = claude_code.koppel_instructions(result)
    elif result.action is Action.APPLY and result.binding is not None:
        if (result.application or {}).get("still_permitted"):
            context = claude_code.apply_instructions(result)
    out: dict = {}
    if context:
        out["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit",
                                     "additionalContext": context}
    if result.surface:
        out["systemMessage"] = f"[verbind] {result.surface}"
    return out or None


def main(payload: dict) -> int:
    started = time.perf_counter()
    sys.path.insert(0, str(_REPO / "lib"))
    from verbind import claude_code
    from verbind.catalogue import leading_verb

    prompt = payload["prompt"]
    lv = leading_verb(prompt)
    if lv is None or (lv.form == "bare" and claude_code.is_interactive(os.environ)):
        return 0  # no verb, a /verbind subcommand, or a command Claude Code knows

    from verbind.gate import gate
    from verbind.runtime import Runtime, verb_hash

    env = claude_code.envelope(prompt, os.environ, payload.get("session_id", ""),
                               payload.get("prompt_id", ""))
    result = gate(prompt, env, Runtime.build(_claude_dir()).deps)
    out = _response(result)
    if out:
        print(json.dumps(out))
    _audit({"verdict": result.verdict.value, "action": result.action.value,
            "reason": result.reason, "form": lv.form, "origin": env.origin,
            "verb_hash": verb_hash(result.verb) if result.verb else None,
            "receipt_id": result.receipt_id,
            "ms": round((time.perf_counter() - started) * 1000, 1)})
    return 0


if __name__ == "__main__":
    try:
        raw = os.environ.pop(PAYLOAD_ENV, None)
        raw = sys.stdin.read() if raw is None else raw
        payload = _screen(raw)
        if payload is not None:
            sys.path.insert(0, str(_REPO / "lib"))
            from pq_reexec import reexec_if_incapable as _reexec

            # Stdin is gone once read, so the new process gets the payload from
            # the environment or not at all. An oversize one would arrive empty and
            # the prompt would be dropped without a word, so it stays here and
            # seals as this interpreter can.
            if len(raw.encode("utf-8")) <= MAX_CARRIED_PAYLOAD:
                os.environ[PAYLOAD_ENV] = raw  # survives execv; stdin does not
                _reexec(Path(__file__))
                os.environ.pop(PAYLOAD_ENV, None)
            main(payload)
    except Exception as exc:  # noqa: BLE001 -- fail-open: a defect never disturbs a prompt
        _audit({"error": type(exc).__name__})
    sys.exit(0)
