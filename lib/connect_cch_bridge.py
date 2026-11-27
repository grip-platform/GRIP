#!/usr/bin/env python3
"""GRIP#9272 (Fable B): a fresh-process bridge to the real HAL's CCH signer.

GRIP's own ``lib.hal`` package (``lib/hal/``) is a distinct, in-process provider
abstraction with no ``subscription.py`` or ``utils/cch.py`` -- those exist only in
the standalone HAL checkout (``$HAL_HOME``, default ``~/.hal``), which happens to
answer the SAME dotted import path. The moment anything in a /connect run has
already imported GRIP's own ``lib.hal`` (its cost tracker, its registry -- both
reachable from a dictated HAL seat), Python's import cache means a later
``from lib.hal.subscription import CCHSubscription`` resolves against GRIP's own
package and fails with ``ModuleNotFoundError: No module named
'lib.hal.subscription'`` -- reproduced while building this bridge. The two
``lib.hal`` packages cannot coexist in one process.

So this runs as its own process instead, exactly as /connect's other
non-in-process seats already do (``claude -p`` for the subprocess speaker,
Codex for the agent coder): read one JSON request from stdin, call the real
HAL's ``CCHSubscription``, print one JSON reply to stdout. Never raises past
its own ``main()`` -- any failure becomes an ``{"error": ...}`` reply with
exit 1, which the caller (``lib.connect._cch_call``) turns into a dead-seat
note, same as any other failed planner call.

Request:  {"prompt": "...", "model": "claude-fable-5-2", "max_tokens": 4096}
          (``max_tokens`` optional; omitted, the signer's own default applies)
Reply:    {"text": "...", "model": "claude-fable-5-2", "finish_reason": "end_turn"}
       or {"error": "no CCH subscription token (keychain grip-claude-oauth)"}

``HAL_HOME`` need not be the real ``~/.hal`` -- it can be any directory holding
a regular package at ``lib/hal/subscription.py`` (``lib/__init__.py`` and
``lib/hal/__init__.py`` both present, even empty): tests/test_connect.py's
``test_the_bridge_runs_in_its_own_interpreter_immune_to_this_processs_lib_hal``
builds exactly such a fake tree to test this bridge with no network call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _hal_home() -> Path:
    """``$HAL_HOME``, else ``~/.hal`` -- HAL's own convention (lib/hal/utils/cch.py)."""
    return Path(os.environ.get("HAL_HOME") or Path.home() / ".hal").expanduser()


def _reply_for(sub, request: dict) -> dict:
    """The JSON-able reply for one request, given an already-resolved subscription.

    ``sub`` is injected so this is testable without ever importing the real
    ``CCHSubscription`` in-process (see ``tests/test_connect_cch_bridge.py``).
    ``None`` means no subscription token was found.
    """
    if sub is None:
        return {"error": "no CCH subscription token (keychain grip-claude-oauth)"}
    kwargs = {"max_tokens": request["max_tokens"]} if request.get("max_tokens") else {}
    resp = sub.chat(request["prompt"], model=request["model"], **kwargs)
    return {"text": resp.text, "model": resp.model, "finish_reason": resp.finish_reason}


def main() -> int:
    """Never raises, really: path setup, stdin, the call AND the reply's own
    JSON-dump all sit in the one try, so a reply that turns out not to be
    serialisable lands in the SAME except and gets dumped again from a plain
    dict that always serialises -- the one `print` below never sees a value
    it has not already confirmed it can print."""
    # -I drops even the script's own directory from sys.path (confirmed: with
    # it, sys.path carries no third entry at all beyond the stdlib) -- put it
    # back FIRST, so a bare sibling import (secret_redactor, below) still
    # resolves. It has no lib/ of its own, so this never competes with
    # hal_home for "lib.hal.subscription".
    try:
        own_dir = str(Path(__file__).resolve().parent)
        if own_dir not in sys.path:
            sys.path.insert(0, own_dir)
        from secret_redactor import redact_secrets
    except Exception:  # noqa: BLE001 -- fail CLOSED on the security property: withhold rather than risk
        # printing something unredacted if the redactor itself cannot load.
        redact_secrets = lambda text: "[could not load the redactor; message withheld]"  # noqa: E731

    try:
        hal_home = str(_hal_home())
        if hal_home not in sys.path:
            sys.path.insert(0, hal_home)
        request = json.loads(sys.stdin.read())
        from lib.hal.subscription import CCHSubscription

        reply = _reply_for(CCHSubscription.from_env(), request)
        output = json.dumps(reply)
    except Exception as exc:  # noqa: BLE001 -- the caller records any failure, never crashes on it
        # This helper handles an OAuth token end to end; _attempt persists this
        # string into the run's own notes file, so an exception that happened
        # to echo a credential-shaped substring (the signer's own HTTP layer,
        # a library warning) is scrubbed before it ever leaves this process
        # (AGORA round 4, Qwen4-Max). Imported above, outside the try: an
        # import failing INSIDE this except would otherwise escape main()
        # entirely, undoing the very guarantee this docstring promises --
        # reproduced once while first wiring this in.
        reply = {"error": redact_secrets(f"{type(exc).__name__}: {exc}")}
        output = json.dumps(reply)
    print(output)
    return 0 if "error" not in reply else 1


if __name__ == "__main__":
    sys.exit(main())
