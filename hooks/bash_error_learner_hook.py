#!/usr/bin/env python3
"""PostToolUse(Bash) hook — capture execution mistakes into the learning loop.

This is the **capture half** of GRIP's bash-execution-error learning loop. The
existing ``rsi-posttool-hook.py`` records commits, GRIP edits, skills, gate
blocks, and ★ Insight blocks — but it has **no** branch for a Bash tool call
that GRIP itself ran and got wrong (a zsh word-split, a bad git pathspec, a
guardrail-denied substitution, a macOS ``timeout`` that does not exist). Those
errors die in the transcript, so GRIP repeats the same shell mistakes session
after session.

This hook closes that gap:

1. Reads the PostToolUse payload from stdin (JSON).
2. For a ``Bash`` call, extracts the combined output + the exit code (robust to
   the several key shapes the harness uses).
3. Classifies via the pure ``lib/bash_execution_error_detector.classify``.
4. On a real error, Popens the EXISTING ``rsi_auto_collector.py learn-event``
   bridge (async, fire-and-forget) which persists the event to
   ``learning/insights.jsonl`` via ``learning-detector.persist_insight`` — the
   same path the ★ Insight branch already uses.

Safety / correctness contract:

* **Never blocks.** PostToolUse responses only inject context; this hook always
  ``respond()``-s with ``{}`` (allow, no message) and swallows every exception.
  A capture-side failure must never break a tool call.
* **Non-blocking side effect.** The collector runs via ``subprocess.Popen`` in a
  new session; the hook returns immediately.
* **No GNU ``timeout``.** Per ``rules/bash-safety.md`` macOS has no ``timeout``;
  the bridge call is a bare ``Popen`` (the collector itself is short-lived).
* **Path portability.** All paths resolve via ``lib/claude_dir.get_claude_dir``
  — never ``Path.home() / ".claude"`` and never a hardcoded ``~``
  (``rules/path-portability.md``, test ``test_no_hardcoded_operator_paths``).
* **Debounce.** Same-category errors are throttled to avoid flooding the loop
  during a retry storm.

Credential safety (PARAMOUNT — 2026-08-17)
------------------------------------------
Everything this hook emits is PERSISTED to ``learning/insights.jsonl`` (a
tracked file, not git-crypt) and broadcast cross-machine by
``learning_detector._mesh_broadcast``. It is therefore a publish surface, and
two independent leaks were reproduced in-process before being closed:

1. The raw failing command was captured verbatim (first 200 chars). An
   ``sk-ant-…`` token inside ``curl -H "Authorization: Bearer …"`` survived
   whole into the persisted string. **The command is no longer captured at
   all** — that is the primary, structural control, because no redactor is
   perfect and the command is the highest-density secret carrier.
2. The evidence line leaked independently, because a shell echoes the failing
   command back in stderr (``/bin/sh: deploy --token <SECRET>: No such file``).
   Evidence is now credential-scrubbed at its source by
   ``lib/secret_redactor.redact_secrets`` — defence-in-depth for the text that
   must still be captured to stay useful.

Every control that should have caught this was blind by construction: Gate E
(``hooks/secrets-output-hook.py``) reads ``tool_response`` and never
``tool_input``; ``hooks/sensitive-info-guard.py`` is scoped to ``prep/`` and
``exports/``; ``lib/gates/git_gates.py`` scans command text for sensitive PATHS,
not file contents; and the write is a ``subprocess.Popen`` from inside a hook,
so no PreToolUse Write/Edit gate observes it. Do not reintroduce raw
``tool_input`` capture here on the assumption that something downstream will
scrub it — nothing does.

Wiring (verified against origin/main 2026-08-17 — NOT against the local tree)
-----------------------------------------------------------------------------
* **canonical ``origin/main``**: BOUND — exactly one PostToolUse "Bash"
  handler, in the **path-portable** form::

      bash "${CLAUDE_DIR:-$HOME/.claude}/lib/grip-python.sh" \
           "${CLAUDE_DIR:-$HOME/.claude}/hooks/bash_error_learner_hook.py"

  It arrived via the Windows path-portability sweep (``aebd49b5`` →
  ``07f00013``), NOT via ``613ea497`` on ``fix/bind-bash-error-learner-hook``,
  which is a separate unmerged branch carrying an equivalent binding in the
  older ``$HOME/.claude`` form.
* **This machine's live ``settings.json``**: BOUND — one handler, still in the
  older ``$HOME/.claude`` form. Functionally equivalent here, and the portable
  form is the one ``rules/path-portability.md`` prescribes.
* **A fresh install gets the same portable form** (re-verified against
  ``origin/main`` 2026-10-17). The installer does not copy ``settings.json``:
  ``configure_settings_json`` in ``scripts/install.sh`` sources
  ``lib/settings-merge.sh`` and calls ``merge_settings_from_reference`` against
  ``config/settings.reference.json``, and that file binds this hook in the
  ``${CLAUDE_DIR:-$HOME/.claude}`` form shown above. An earlier revision of this
  bullet said a fresh install got the hardcoded ``$HOME/.claude`` form; the
  reference file's hook block had already been swept to the portable form
  (``0f72494c3``, #4804) by the time that revision reached ``origin/main``.
  ``tests/test_secret_redactor.py`` goes red whenever this bullet and the
  reference file disagree.
* **Canonical command form**: the ``lib/grip-python.sh`` wrapper, which resolves
  GRIP's venv interpreter — never a bare ``python3``.

Read that state from ``git show origin/main:settings.json``, never from the
local checkout: this tree was 64 commits behind origin/main when the above was
verified, and reading it locally gave the WRONG answer ("not on main")
— ``rules/shared-tree-is-not-canonical.md``.

An earlier revision of this docstring stated the hook was NOT wired and
prescribed a bare ``python3`` invocation. Both statements were stale: the first
contradicted the applied binding, and the second, if followed, would register a
SECOND binding under a different interpreter so the hook fired twice on every
Bash call. The footer now records the applied form.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- path-portable imports (rules/path-portability.md) ---------------------
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
_HOOKS = Path(__file__).resolve().parent
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

from claude_dir import get_claude_dir  # noqa: E402
from bash_execution_error_detector import classify  # noqa: E402

try:  # hook_utils is the canonical PostToolUse stdin/stdout helper
    from hook_utils import parse_hook_input, respond
except ImportError:  # pragma: no cover - fallback keeps the hook self-contained
    def parse_hook_input() -> dict:
        try:
            raw = sys.stdin.read()
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

    def respond(system_message: str = None) -> None:
        out = {}
        if system_message:
            out["systemMessage"] = system_message
        print(json.dumps(out))


CLAUDE_DIR = get_claude_dir()
# The UNDERSCORE module is the real file. The legacy hyphenated alias the older
# hook references does not exist — pointing here is the fix that makes the
# bridge actually fire.
COLLECTOR = CLAUDE_DIR / "lib" / "rsi_auto_collector.py"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
DEBOUNCE_FILE = CLAUDE_DIR / "state" / "rsi" / "bash-error-last.json"

# Minimum seconds between same-category captures.
DEBOUNCE_SECONDS = 5


def _log(level: str, message: str) -> None:
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [BASH-ERR-LEARN][{level}] {message}\n")
    except Exception:
        pass


def _extract_output_and_exit(tool_response) -> tuple[str, int]:
    """Pull combined output + exit code from a PostToolUse Bash tool_response.

    The harness has used several shapes over versions; be robust:
    * dict with ``stdout`` / ``stderr`` and one of
      ``exit_code`` / ``returncode`` / ``exitCode`` / ``code``.
    * a bare string (older shape) — exit code unknown, treat as 0 so only an
      output error-signature (e.g. a guardrail denial) can fire.
    """
    if isinstance(tool_response, dict):
        stdout = str(tool_response.get("stdout", "") or "")
        stderr = str(tool_response.get("stderr", "") or "")
        combined = (stdout + "\n" + stderr).strip()
        if not combined:
            combined = str(tool_response.get("output", "") or "").strip()
        exit_code = 0
        for key in ("exit_code", "returncode", "exitCode", "code", "status"):
            val = tool_response.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                exit_code = val
                break
            if isinstance(val, str) and val.lstrip("-").isdigit():
                exit_code = int(val)
                break
        # An explicit interrupted flag is a non-success signal.
        if exit_code == 0 and tool_response.get("interrupted") is True:
            exit_code = 130
        return combined, exit_code

    # Bare string / other — unknown exit; let output signatures decide.
    return str(tool_response or "").strip(), 0


def _is_debounced(category: str) -> bool:
    """Throttle same-category captures within DEBOUNCE_SECONDS."""
    state = {}
    if DEBOUNCE_FILE.exists():
        try:
            state = json.loads(DEBOUNCE_FILE.read_text())
        except Exception:
            state = {}
    now = time.time()
    last = state.get(category, 0)
    if isinstance(last, (int, float)) and now - last < DEBOUNCE_SECONDS:
        return True
    state[category] = now
    try:
        DEBOUNCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEBOUNCE_FILE.write_text(json.dumps(state))
    except Exception:
        pass
    return False


def _bridge_learn_event(description: str) -> None:
    """Fire-and-forget the existing learn-event collector bridge.

    Non-blocking: Popen in a new session, no GNU ``timeout`` (macOS lacks it;
    rules/bash-safety.md). The collector is short-lived by construction.
    """
    if not COLLECTOR.exists():
        _log("WARN", f"collector missing at {COLLECTOR}; capture skipped")
        return
    try:
        subprocess.Popen(
            [sys.executable, str(COLLECTOR), "learn-event", description,
             "bash_execution_error"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # never let capture break the tool call
        _log("ERROR", f"collector launch failed: {e}")


def main() -> None:
    try:
        payload = parse_hook_input()
        if not payload:
            return  # finally: respond() is the single response site

        if payload.get("tool_name") != "Bash":
            return  # finally: respond() is the single response site

        combined, exit_code = _extract_output_and_exit(
            payload.get("tool_response", "")
        )
        event = classify(combined, exit_code)
        if event is None:
            return  # finally: respond() is the single response site

        if _is_debounced(event.category):
            return  # finally: respond() is the single response site

        # SECURITY (2026-08-17): the raw command is NEVER persisted. See the
        # module docstring "Credential safety" for the reproduction that forced
        # this. ``event.evidence`` is credential-scrubbed at its source in
        # lib/bash_execution_error_detector._safe_evidence.
        description = (
            f"Bash execution error [{event.category}]: {event.summary} "
            f"Evidence: {event.evidence}"
        )
        _log("INFO", f"captured {event.category} (exit={exit_code})")
        _bridge_learn_event(description)
    except Exception as e:  # PARAMOUNT: capture must never break the tool call
        _log("ERROR", f"hook failed (swallowed): {e}")
    finally:
        respond()


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# ACTIVATION STATUS: APPLIED. Bound on canonical origin/main (arrived via the
# Windows path-portability sweep aebd49b5 -> 07f00013) and bound on this
# machine's live settings.json. See the module docstring for the three states
# and why they must be read from origin/main, not from the local checkout.
#
# The canonical binding, PostToolUse / matcher "Bash":
#
#   {
#     "matcher": "Bash",
#     "hooks": [
#       { "type": "command",
#         "command": "bash \"${CLAUDE_DIR:-$HOME/.claude}/lib/grip-python.sh\" \"${CLAUDE_DIR:-$HOME/.claude}/hooks/bash_error_learner_hook.py\"" }
#     ]
#   }
#
# Two properties are load-bearing. The grip-python.sh wrapper resolves GRIP's
# venv interpreter, and the ${CLAUDE_DIR:-$HOME/.claude} expansion keeps the
# path portable (rules/path-portability.md) — a literal ~/.claude breaks any
# install that relocates CLAUDE_DIR, and broke Windows.
#
# A bare `python3 .../bash_error_learner_hook.py` is NOT equivalent and must not
# be added: this file was previously documented that way, and following that
# stale note would register a SECOND binding under a different interpreter, so
# the hook would fire twice on every Bash call.
#
# Verify the wiring against CANONICAL state (expect exactly ONE binding):
#
#   git show origin/main:settings.json | python3 -c "import json,sys;\
# s=json.load(sys.stdin);print(sum('bash_error_learner' in str(h) \
# for g in s['hooks']['PostToolUse'] for h in g.get('hooks',[])))"
#
# It runs alongside (not instead of) the existing PostToolUse hooks and never
# returns a "decision" field, so it cannot block any tool call.
# ---------------------------------------------------------------------------
