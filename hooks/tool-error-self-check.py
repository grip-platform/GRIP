#!/usr/bin/env python3
"""PostToolUse advisory: suspect your OWN command flags before blaming a tool.

Fires on Bash results. One trigger, one corroborator, never a deny. The
trigger is the COMMAND carrying a known footgun flag in the argument list of a
real invocation (``rg -r`` is ``--replace``, bare ``sed -i`` on macOS, an
unguarded ``grep -c`` tail, zsh ``for x in $VAR``). The corroborator is the
OUTPUT showing the ``--replace`` mangling signature — the same single letter
sitting where identifiers belong in two or more distinct tokens — which adds
detail to an advisory the command already earned but cannot raise one alone
(#4421). Fails open on any internal error: a broken safety hook must never
block work.

Detector: lib/tool_error_self_check.py (pure, zero side effects)
Tests:    tests/test_tool_error_self_check.py
Audit:    logs/tool-error-self-check.jsonl (one line per flagged result)
Origin:   2026-07-14 RTK incident (hypothesis H798) — an "rg -rn" typo mangled
          reads and an innocent external tool took the blame for two sessions.

Activation is the operator-gated step (wire into settings.json PostToolUse
for Bash). Until wired, this file is inert — zero blast radius from merging.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.tool_error_self_check import (
        blind_negative_candidates,
        blind_negative_message,
        scan_command,
        scan_output,
        substitution_capable,
    )
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block work
    sys.exit(0)

_AUDIT = _CLAUDE / "logs" / "tool-error-self-check.jsonl"

_WARN_TEMPLATE = (
    "[SELF-CHECK] verify your OWN command before blaming an external tool. "
    "{detail}"
)

# The shape scan_output() returns when it finds nothing — used when the output
# scan is skipped entirely, so downstream code sees one uniform verdict shape.
_NO_MANGLING = {"flagged": False, "letter": None, "tokens": [], "message": ""}


def _audit(record: dict) -> None:
    """Append one JSONL audit record; audit loss never blocks anything."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[tool-error-self-check] audit write failed ({exc})", file=sys.stderr)


def _record(command: str, cmd_verdict: dict, out_verdict: dict,
            suppressed: bool) -> None:
    """One audit line. ``suppressed`` marks an output signal seen but not raised."""
    _audit({"command": command[:400],
            "command_hits": [h["name"] for h in cmd_verdict["hits"]],
            "output_mangling": out_verdict["flagged"],
            "suppressed": suppressed})


def _emit_warn(detail: str) -> None:
    """Inject the advisory as PostToolUse additional context (never a deny)."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": _WARN_TEMPLATE.format(detail=detail),
    }}))


def _ignored_paths(paths: list[str]) -> list[str]:
    """Subset of ``paths`` that git actually ignores, resolved through the shell.

    ``git check-ignore -q`` exits 0 when the path IS ignored. A tilde is expanded
    here because the recorded command string keeps it literal, and a literal
    ``~/...`` never matches a real path. Any git failure yields an empty list:
    an advisory that cannot prove ignored-ness must stay silent rather than guess.
    """
    import subprocess  # local: the hook must import cheaply on the common path

    hits = []
    for raw in paths:
        target = os.path.expanduser(raw)
        # Probe the CONTENT, not the container. A rule like `projects/**` ignores
        # everything INSIDE the directory while leaving the directory itself
        # un-ignored, so `check-ignore projects` exits 1 even though every file
        # under it is skipped by fd — which is exactly the case that produced
        # this detector. Measured 2026-08-18: `projects` -> 1, `projects/x` -> 0.
        # A child probe is pure string matching in git; the file need not exist.
        for candidate in (os.path.join(target, "__grip_ignore_probe__"), target):
            try:
                proc = subprocess.run(
                    ["git", "check-ignore", "-q", "--", candidate],
                    cwd=str(_CLAUDE), capture_output=True, timeout=5, check=False,
                )
            except Exception:  # noqa: BLE001 — fail-open, never block on a probe
                break
            if proc.returncode == 0:
                hits.append(raw)
                break
    return hits


def _blind_negative_detail(command: str, output: str, candidates_fn, message_fn):
    """Advisory text when an ignore-aware search returned nothing over ignored content.

    Returns ``None`` unless the pure detector flags the conjunction AND git
    confirms at least one named path is genuinely ignored. Both halves are
    required: the pure half alone would nag on every empty search, and the git
    half alone would nag on every search of an ignored directory.
    """
    candidates = candidates_fn(command, output)
    if not candidates:
        return None
    ignored = _ignored_paths(candidates)
    if not ignored:
        return None
    return message_fn(ignored)


def _response_text(payload: dict) -> str:
    """Best-effort plain text of the tool's response across payload shapes."""
    response = payload.get("tool_response") or payload.get("toolResponse") or ""
    if isinstance(response, dict):
        parts = [str(response.get(k, "")) for k in ("stdout", "stderr", "output")]
        return "\n".join(p for p in parts if p)
    return str(response)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return 0
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool != "Bash":
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    command = tool_input.get("command") or ""

    # Blind negative first: it is a DIFFERENT failure from the footgun table and
    # must be reported even when the command carries no footgun flag at all (the
    # originating command was flagless and correct-looking). See the module
    # comment for why this needs a conjunction rather than a pattern.
    blind = _blind_negative_detail(command, _response_text(payload),
                                   blind_negative_candidates, blind_negative_message)
    if blind:
        _audit({"command": command[:400], "blind_negative": True, "detail": blind})
        _emit_warn(blind)
        return 0

    cmd_verdict = scan_command(command)
    # The COMMAND is the only thing that can raise an advisory. The output scan
    # corroborates; it never testifies alone.
    #
    # Output is arbitrary data, and a scan for the shape of a mangled identifier
    # cannot tell corruption from a document that happens to contain the same
    # shape — including documentation about this very footgun. On 326 live
    # advisories the output signal fired 100 times with no corroborating command
    # flag, and the sampled commands were ordinary reads. The advisory's own
    # words are "check your OWN command flags", which is unactionable advice
    # when the command carries no such flag (#4421).
    #
    # The rewrite-capability gate from #4409 still applies on top, so a
    # ``grep -c`` advisory never acquires a bogus mangling claim.
    out_verdict = (scan_output(_response_text(payload)) if substitution_capable(command)
                   else _NO_MANGLING)
    if not cmd_verdict["flagged"]:
        # Silent for the operator, but still WRITTEN DOWN. The demotion's own
        # falsifier is "a real mangling went unreported because nothing
        # corroborated it", and a suppression that leaves no trace cannot be
        # falsified at all. Raised by AGORA on #4510.
        if out_verdict["flagged"]:
            _record(command, cmd_verdict, out_verdict, suppressed=True)
        return 0

    _record(command, cmd_verdict, out_verdict, suppressed=False)
    _emit_warn(" | ".join(v["message"] for v in (cmd_verdict, out_verdict) if v["flagged"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail-open: never block work on a hook defect
        sys.exit(0)
