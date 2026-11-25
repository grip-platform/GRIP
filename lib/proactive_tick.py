#!/usr/bin/env python3
"""Proactive InfiniteMachine tick — BALANCED autonomy (act-safe / escalate-gated).

The proactive half of GRIP's "autonomous on the operator's behalf" — the relay is
REACTIVE (fires on inbound); this fires PROACTIVELY on a schedule (launchd), with
NO interactive session. Generalisable core substrate: domain-agnostic, advances
WHATEVER goal GRIP is currently tracking (auto-sourced), for any operator.

BALANCE (operator directive, V>> 2026-06-07): not propose-only. The tick spawns a
capability-locked, gate-governed GRIP executor that ACTS on the safe/reversible
envelope (read/edit/test/commit-to-feat-branch/open-PR — CI + AGORA review it) and
ESCALATES the genuinely-gated decisions to the operator's WhatsApp instead of
acting. The mechanical safety is GRIP's EXISTING PreToolUse gate stack — it fires
on the spawned executor's every tool call and blocks: secrets, destructive git,
push/merge to main, force-push, cross-client/external posts, sensitive-info.
So the autonomous envelope is bounded BY CONSTRUCTION to reviewable work; the
dangerous set is gated and surfaced to the operator (human-in-the-loop where it
matters, autonomy where it's safe).

Default OFF: absent ``GRIP_PROACTIVE_TICK`` this is a pure no-op (fresh install
byte-identical until opt-in). ``GRIP_PROACTIVE_TICK_MODE`` = ``act`` (default,
balanced) or ``propose`` (escalate-everything fallback).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
import throttle_flag  # noqa: E402  the tick's throttle flag, read-only: a headless run refuses while it is set
from wa_identities import operator_jid  # noqa: E402

CLAUDE_DIR = get_claude_dir()
FLAG = "GRIP_PROACTIVE_TICK"  # default unset/false ⇒ OFF (no-op)
MODE = os.environ.get("GRIP_PROACTIVE_TICK_MODE", "act").strip().lower()  # act | propose
GOAL_FILE = CLAUDE_DIR / "state" / "proactive-tick-goal.md"  # optional override
STAMP_FILE = CLAUDE_DIR / "state" / "proactive-tick-last.txt"
LOG_FILE = CLAUDE_DIR / "logs" / "proactive-tick.jsonl"
# The operator's own WhatsApp, the ONLY recipient -- never an external party. From
# GRIP_PROACTIVE_TICK_RECIPIENT, else the private file; None means nothing is sent.
OPERATOR_JID = operator_jid("GRIP_PROACTIVE_TICK_RECIPIENT")
MIN_INTERVAL_SEC = int(os.environ.get("GRIP_PROACTIVE_TICK_MIN_SEC", "3600"))  # ≥1h between ticks
STEP_TIMEOUT_SEC = int(os.environ.get("GRIP_PROACTIVE_TICK_TIMEOUT_SEC", "600"))
CLAUDE_BIN = os.environ.get("GRIP_RELAY_CLAUDE_BIN", os.path.expanduser("~/.local/bin/claude"))

_TRUTHY = {"1", "true", "yes", "on"}

# Capability lock for the autonomous executor: work tools only. The PreToolUse gate
# stack (NOT this list) is what blocks the dangerous SUBSET of these (push-to-main,
# force-push, secrets, external posts). Bash is included so it can run tests + git
# commit/branch/PR — the gates fence the dangerous bash, not its absence.
_EXEC_TOOLS = "Read,Grep,Glob,Write,Edit,Bash"

# Domain-agnostic. The executor advances ONE bounded step; acts on safe, escalates gated.
_ADVANCE_PROMPT = (
    "You are GRIP, autonomously advancing the operator's goal ONE bounded step on their behalf. "
    "This is a SCHEDULED proactive tick — no human is watching in real time.\n"
    "Operator's current goal(s):\n---\n{goal}\n---\n"
    "RULES:\n"
    "• loop-priority-external-over-theatre: bias the step to EXTERNAL value — infer the external "
    "dimension from the goal itself (a user / customer / revenue / external stakeholder / a shipped-"
    "and-adopted artifact). Internal tooling/RSI/refactor is LOWER priority unless it directly "
    "unblocks external value.\n"
    "• BALANCE: ACT on safe, reversible work — do NOT merely propose everything. But ESCALATE the "
    "genuinely-gated.\n"
    "ACT now (closed-loop, do it): read/analyse, edit code, add+run tests, commit to a feat/ branch, "
    "open a PR. CI converges and AGORA (the cross-provider council) ratifies — on AGORA-green (PASS, "
    "0 blockers, no REQUEST/DEFER) the loop MERGES via admin; that ratification is EXOGENOUS (AGORA + "
    "CI), NOT the operator. REQUEST_CHANGES → fix-loop; DEFER → file follow-up + merge. Merging a "
    "NORMAL PR to main is AGORA's decision, never the operator's. The safety gates block anything "
    "dangerous — trust them.\n"
    "ESCALATE (do NOT do — report for the operator) ONLY the AGORA-uncoverable set: irreversible/"
    "destructive ops NOT mediated by a PR (force-push, history rewrite, prod-data drop, deleting a "
    "memory/rule); sending ANY message to an external party or under the operator's identity; "
    "client/commercial/pricing decisions; a material A-vs-B direction fork; a bootstrap-authority PR "
    "that changes the merge gate itself (human-merged only).\n"
    "Bounded: ONE coherent step then stop. Report TERSELY in EXACTLY this shape:\n"
    "DID: <what you actually did + branch/PR if any>\n"
    "ESCALATE: <decisions needing the operator, or 'none'>"
)


def is_enabled() -> bool:
    """Opt-in flag; default OFF (fail-safe — absent flag ⇒ pure no-op)."""
    return os.environ.get(FLAG, "").strip().lower() in _TRUTHY


def _derive_goal_from_state() -> str | None:
    """Auto-source the operator's current goal from GRIP's own tracked next-step.

    Makes this a self-driving substrate (no hand-written file required), for ANY
    domain. Pure read; any failure ⇒ None. Uses the canonical next_up reader.
    """
    try:
        from next_step_block import read_latest_next_up  # GRIP's canonical 'what's next'
        nu = read_latest_next_up(CLAUDE_DIR)  # the GRIP project path (DIP arg, no global state)
        if isinstance(nu, dict):
            parts = [str(nu.get(k, "")).strip() for k in ("remaining", "notes", "blockers")]
            txt = " | ".join(p for p in parts if p)
            return txt or None
        if isinstance(nu, str) and nu.strip():
            return nu.strip()
    except Exception:  # noqa: BLE001 — substrate must not crash on a missing/odd source
        return None
    return None


def read_goal() -> str | None:
    """Operator's current goal(s): explicit override file, else auto-sourced from
    GRIP state. None ⇒ no-op. Domain-agnostic — not bound to any one goal."""
    try:
        text = GOAL_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return _derive_goal_from_state()


def _read_stamp() -> float:
    try:
        return float(STAMP_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def should_tick(*, enabled: bool, goal: str | None, last_stamp: float, now: float) -> bool:
    """Gate: enabled AND a goal exists AND the min interval has elapsed (anti-spam)."""
    return bool(enabled and goal and (now - last_stamp) >= MIN_INTERVAL_SEC)


def build_advance_prompt(goal: str) -> str:
    return _ADVANCE_PROMPT.format(goal=goal[:4000])


def run_tick(*, advance_fn, notify_fn, now: float | None = None) -> dict:
    """One tick: ADVANCE the goal one bounded step (advance_fn) → REPORT to the
    operator (notify_fn). advance_fn returns {'did':str,'escalate':str}.

    Safety is NOT in this function's structure (the executor genuinely acts) — it
    is in (a) advance_fn's capability lock + the external PreToolUse gate stack,
    and (b) the escalate-gated contract. I/O injected (DIP) ⇒ tests run with zero
    network/spawn. Fail-soft: never raises (a daemon must not crash-loop).
    """
    now = time.time() if now is None else now
    enabled, goal, last = is_enabled(), read_goal(), _read_stamp()
    if not should_tick(enabled=enabled, goal=goal, last_stamp=last, now=now):
        return {"ticked": False, "reason": "disabled" if not enabled
                else ("no-goal" if not goal else "throttled")}
    try:
        out = advance_fn(goal) or {}
    except Exception as exc:  # noqa: BLE001 — fail-soft
        return {"ticked": False, "reason": f"advance-error:{type(exc).__name__}"}
    did = (out.get("did") or "").strip()
    esc = (out.get("escalate") or "").strip()
    if not did and not esc:
        return {"ticked": False, "reason": "empty-step"}
    body = ("◈ GRIP here — proactive tick (I acted on the safe parts; the rest I'm "
            "leaving to you):\n\n*DID:* " + (did or "(nothing safe to act on this tick)")
            + "\n\n*ESCALATE (your call):* " + (esc or "none"))
    try:
        res = notify_fn(body)
    except Exception as exc:  # noqa: BLE001
        return {"ticked": False, "reason": f"notify-error:{type(exc).__name__}"}
    try:
        STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STAMP_FILE.write_text(str(now))
    except OSError:
        pass
    return {"ticked": True, "did_chars": len(did), "escalate_chars": len(esc),
            "notify_ok": bool(res and res.get("ok"))}


# --- production I/O (not exercised by tests; tests inject spies) ----------------

def _parse_did_escalate(text: str) -> dict:
    """Split the executor's 'DID:/ESCALATE:' report. Tolerant of either missing."""
    did, esc, cur = [], [], None
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if low.startswith("did:"):
            cur = did; did.append(line.split(":", 1)[1].strip())
        elif low.startswith("escalate:"):
            cur = esc; esc.append(line.split(":", 1)[1].strip())
        elif cur is not None:
            cur.append(line)
    return {"did": "\n".join(did).strip(), "escalate": "\n".join(esc).strip()}


def _default_advance(goal: str) -> dict:
    """Spawn the capability-locked, gate-governed GRIP executor for ONE step.

    In ``propose`` mode it does NOT spawn an actor — it asks HAL (economic) to
    PROPOSE the next step (escalate-everything). In ``act`` mode it spawns a
    ``claude -p`` executor whose tool set is locked to work tools and whose every
    tool call is policed by GRIP's PreToolUse gate stack (the real safety). yolo is
    activated short-TTL so halting gates relax for the unattended step while the
    PARAMOUNT safety gates keep firing.
    """
    if MODE == "propose":
        from hal_llm_adapter import HALLLMAdapter
        prompt = ("Propose (do not act) the single next EXTERNAL-value step for this goal, "
                  "for the operator to act on:\n" + goal[:4000])
        return {"did": "", "escalate": (HALLLMAdapter().infer(prompt, audit=False).text or "").strip()}
    # act mode — bounded autonomous executor under the gate stack. First the
    # tick's throttle flag (V>> 2026-10-29): a headless GRIP-started run refuses
    # to start while it is set, before yolo is armed and before any spawn; the
    # operator hears why on the tick's own report.
    throttled = throttle_flag.describe(CLAUDE_DIR / "state" / "session" / "throttle.json")
    if throttled:
        return {"did": "", "escalate": f"not run: {throttled}"}
    import subprocess  # local import: only the act path needs a spawn surface
    try:
        from gates.yolo_mode import activate_yolo
        activate_yolo("/proactive-tick", ttl_seconds=max(STEP_TIMEOUT_SEC, 1800),
                      reason="proactive autonomous tick (act-safe/escalate-gated)")
    except Exception:  # noqa: BLE001 — if yolo can't arm, the spawn's halting gates will simply stop it (fail-safe)
        pass
    argv = [CLAUDE_BIN, "-p", "--output-format", "text",
            "--dangerously-skip-permissions", "--strict-mcp-config",
            "--tools", _EXEC_TOOLS,
            "--append-system-prompt", build_advance_prompt(goal),
            "Advance the goal one bounded step now, per your instructions."]
    try:
        proc = subprocess.run(argv, cwd=str(CLAUDE_DIR), capture_output=True,
                              text=True, timeout=STEP_TIMEOUT_SEC)
        return _parse_did_escalate(proc.stdout or "")
    except subprocess.TimeoutExpired:
        return {"did": "", "escalate": "(executor timed out — no action taken this tick)"}


def _default_notify(text: str) -> dict:
    """WhatsApp to the OPERATOR himself — the only external surface (report-to-self)."""
    from wa_identities import MISSING_HINT
    if not OPERATOR_JID:
        return {"ok": False, "error": MISSING_HINT}
    from whatsapp_transport import post
    return post(recipient=OPERATOR_JID, text=text)


def main() -> int:
    result = run_tick(advance_fn=_default_advance, notify_fn=_default_notify)
    try:
        import json
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"ts": time.time(), "mode": MODE, **result}) + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(f"[proactive-tick mode={MODE}] {result}")
    return 0  # always 0 — a scheduled tick must never look like a crash to launchd


if __name__ == "__main__":
    raise SystemExit(main())
