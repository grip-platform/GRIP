#!/usr/bin/env python3
"""Model-switch guard and ledger: harnesses Claude Code's PreModelSwitch and
PostModelSwitch hook events (Claude Code 2.1.251 and later).

PreModelSwitch is sequential and can block. It fires before a switch that the
operator or a client requested: ``/model``, the picker, the Model row in
``/config``, turning on fast mode, or an SDK ``set_model``. PostModelSwitch is
asynchronous and cannot block. It fires after ANY change, including automatic
fallbacks and the model Claude Code restores on ``--resume``.

What GRIP does with them, in WARN mode by default (keep the power, add a
brake, per rules/full-power-stop-override.md):

  PreModelSwitch   If the target model's context window in
                   config/model_catalog.yaml is below 1M, or the model is not in
                   the catalog at all, return a ``systemMessage`` naming the
                   model, its window and the re-cache cost Claude Code supplied.
                   The switch proceeds. ``GRIP_MODEL_SWITCH_GUARD=deny`` returns
                   ``permissionDecision: "deny"`` and cancels the switch;
                   ``=ask`` returns ``"ask"`` (only interactive ``/model`` can
                   show that prompt; every other surface treats it as a
                   refusal); ``=off`` returns nothing.
  PostModelSwitch  Append one line to state/model-switches.jsonl (from, to,
                   source and the cost fields) and hand Claude an
                   ``additionalContext`` line naming the model and window it
                   now has.

Escape hatch (rules/gate-resolution-escape-hatch.md, Invariant 1): WARN mode
blocks nothing. In deny mode the denied set is "switch to a sub-1M or
uncatalogued model"; the resolution (pick a catalogued 1M model, or unset
GRIP_MODEL_SWITCH_GUARD) lies outside that set, so the hook cannot block its
own resolution.

Fail-open: malformed stdin, a missing catalog or any exception exits 0 with no
output. This hook must never surface as a hook error.

Input fields (code.claude.com/docs/en/hooks, read 2026-09-02): from_model,
to_model, requested_model, source ("command" | "picker" | "sdk"; PostModelSwitch
adds "auto" and "resume"), context_tokens, prompt_cache_warm, cache_ttl,
estimated_cache_write_usd, pricing, plus the common fields session_id,
transcript_path, cwd and hook_event_name.

Origin: cc-auto-harness triage of Claude Code 2.1.227-2.1.257 (PR #5263),
backlog item T116.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CATALOG = CLAUDE_DIR / "config" / "model_catalog.yaml"
LEDGER = CLAUDE_DIR / "state" / "model-switches.jsonl"
ONE_MILLION = 1_000_000
MODES = ("warn", "deny", "ask", "off")

_ADDRESS = re.compile(r"^\s*-\s*address:\s*(\S+)\s*$")
_WINDOW = re.compile(r"^\s*context_window:\s*([0-9_]+)\s*$")


def canonical(model: object) -> str:
    """``anthropic/Claude-Opus-5[1m]`` -> ``claude-opus-5``.

    Drops the provider prefix and the ``[1m]`` suffix, and lower-cases, which is
    the same normalisation Claude Code applies before matching a hook's matcher.
    """
    name = str(model or "").strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[1]
    return re.sub(r"\[1m\]$", "", name)


def load_context_windows(catalog: Path = CATALOG) -> dict[str, int]:
    """Map canonical model id -> context window, read from the catalog.

    The catalog is a list of ``- address: <provider>/<model>`` items whose block
    carries a ``context_window: N`` line. Reading it line by line keeps the hook
    stdlib-only; a missing or unreadable catalog yields an empty map, and every
    model then reads as unknown, which WARN mode reports rather than blocks.
    """
    windows: dict[str, int] = {}
    if not catalog.is_file():
        return windows
    current: str | None = None
    for line in catalog.read_text(encoding="utf-8", errors="replace").splitlines():
        address = _ADDRESS.match(line)
        if address:
            current = canonical(address.group(1))
            continue
        window = _WINDOW.match(line)
        if window and current:
            windows[current] = int(window.group(1).replace("_", ""))
    return windows


def mode() -> str:
    """The guard's mode from GRIP_MODEL_SWITCH_GUARD; anything unrecognised is warn."""
    value = os.environ.get("GRIP_MODEL_SWITCH_GUARD", "warn").strip().lower()
    return value if value in MODES else "warn"


def describe_window(model: str, windows: dict[str, int]) -> tuple[int | None, str]:
    """Return (window, reason). The reason is empty for a catalogued 1M model."""
    window = windows.get(canonical(model))
    if window is None:
        return None, (
            f"{model} is not in config/model_catalog.yaml, so its context window "
            "is unknown to GRIP"
        )
    if window < ONE_MILLION:
        return window, (
            f"{model} has a {window:,}-token context window in "
            "config/model_catalog.yaml, below the 1M window GRIP pins"
        )
    return window, ""


def cost_sentence(payload: dict) -> str:
    """One sentence from the cost fields Claude Code supplies, or empty."""
    tokens = payload.get("context_tokens")
    usd = payload.get("estimated_cache_write_usd")
    if not isinstance(tokens, (int, float)) or not isinstance(usd, (int, float)):
        return ""
    warm = " while the prompt cache is warm" if payload.get("prompt_cache_warm") else ""
    pricing = payload.get("pricing", "unknown")
    return f" Re-caching {int(tokens):,} tokens{warm} is estimated at ${usd:.2f} ({pricing} pricing)."


def pre_switch(payload: dict, windows: dict[str, int], guard: str) -> dict:
    """Output object for PreModelSwitch, or ``{}`` when nothing needs saying."""
    if guard == "off":
        return {}
    to_model = str(payload.get("to_model") or "")
    _, reason = describe_window(to_model, windows)
    if not reason:
        return {}
    text = (
        f"GRIP model-switch guard: {payload.get('from_model', '?')} -> {to_model}: "
        f"{reason}.{cost_sentence(payload)}"
    )
    if guard in ("deny", "ask"):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreModelSwitch",
                "permissionDecision": guard,
                "permissionDecisionReason": text,
            }
        }
    return {
        "systemMessage": (
            text
            + " The switch proceeds (GRIP_MODEL_SWITCH_GUARD=warn); set it to deny "
            "or ask to brake it."
        )
    }


def post_switch(payload: dict, windows: dict[str, int], ledger: Path = LEDGER) -> dict:
    """Append the switch to the ledger and tell Claude which model and window it now has."""
    to_model = str(payload.get("to_model") or "")
    window, _ = describe_window(to_model, windows)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id"),
        "from_model": payload.get("from_model"),
        "to_model": to_model,
        "requested_model": payload.get("requested_model"),
        "source": payload.get("source"),
        "context_tokens": payload.get("context_tokens"),
        "prompt_cache_warm": payload.get("prompt_cache_warm"),
        "estimated_cache_write_usd": payload.get("estimated_cache_write_usd"),
        "context_window": window,
        "cwd": payload.get("cwd"),
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    window_text = (
        f"{window:,}-token context window" if window else "a context window unknown to GRIP's catalog"
    )
    return {
        "additionalContext": (
            f"GRIP: the session model is now {to_model} ({window_text}); "
            f"switch source: {payload.get('source', 'unknown')}."
        )
    }


def handle(payload: dict) -> dict:
    """Dispatch on the event name; unknown events produce no output."""
    event = payload.get("hook_event_name")
    if event == "PreModelSwitch":
        return pre_switch(payload, load_context_windows(), mode())
    if event == "PostModelSwitch":
        return post_switch(payload, load_context_windows())
    return {}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        output = handle(payload)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - a guard that crashes must never become a hook error
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
