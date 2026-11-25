#!/usr/bin/env python3
"""HAL substrate health probe — makes "Sentinel + RAILLM functioning ALWAYS" PROVABLE.

verify-canonical-not-claimed applied to the economic reasoning substrate's uptime:
"functioning always" must be continuously VERIFIED, never asserted. This probe runs
(scheduled, opt-in) and checks, exogenously:
  • RAILLM / HAL infer responds (a tiny cross-provider inference actually returns),
  • HAL Sentinel state is healthy (no provider pinned at/over the reroute ceiling).
It appends every result to logs/hal-health.jsonl (the always-on evidence trail) and
WhatsApps the operator ONLY on a FAILURE transition (never on healthy — no spam).

CBC: probe + log + (on failure) notify-operator only. No external act surface; I/O
injected (DIP) so tests run with zero network. Fail-soft: a probe error is itself a
recorded 'down' signal, never a crash.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from wa_identities import operator_jid  # noqa: E402

CLAUDE_DIR = get_claude_dir()
HAL_LIMITS = Path(os.environ.get("HAL_LIMITS_PATH", str(Path.home() / ".hal" / "state" / "hal" / "limits.json")))
LOG_FILE = CLAUDE_DIR / "logs" / "hal-health.jsonl"
STATE_FILE = CLAUDE_DIR / "state" / "hal-health-last.json"
OPERATOR_JID = operator_jid("GRIP_PROACTIVE_TICK_RECIPIENT")  # None: nothing is sent
SENTINEL_CEILING = float(os.environ.get("HAL_SENTINEL_CEILING", "0.85"))  # reroute threshold


def sentinel_state() -> dict:
    """Read HAL Sentinel limits → {ok, providers, degraded:[names]}. Pure read; a
    missing file means 'no limits recorded' = healthy (clean)."""
    try:
        d = json.loads(HAL_LIMITS.read_text())
    except (OSError, ValueError):
        return {"ok": True, "providers": 0, "degraded": []}
    degraded = [k for k, v in d.items()
                if isinstance(v, dict) and float(v.get("utilisation", 0)) >= SENTINEL_CEILING]
    return {"ok": not degraded, "providers": len(d), "degraded": degraded}


def evaluate(*, raillm_text: str | None, raillm_err: str | None, sentinel: dict) -> dict:
    """Pure verdict from probe inputs. raillm_ok iff a non-empty answer came back."""
    raillm_ok = bool(raillm_text and raillm_text.strip()) and not raillm_err
    return {
        "raillm_ok": raillm_ok,
        "sentinel_ok": bool(sentinel.get("ok")),
        "healthy": raillm_ok and bool(sentinel.get("ok")),
        "sentinel_degraded": sentinel.get("degraded", []),
        "providers": sentinel.get("providers", 0),
        "raillm_err": raillm_err,
    }


def _read_last_healthy() -> bool | None:
    """The last recorded state; None when unknown, including an alert still owed."""
    try:
        value = json.loads(STATE_FILE.read_text()).get("healthy")
    except (OSError, ValueError):
        return None
    return None if value is None else bool(value)


def _send_alert(verdict: dict, raillm_err, notify_fn) -> bool:
    """Send the down alert. True only when the transport reports it delivered."""
    detail = ("RAILLM down (%s)" % raillm_err) if not verdict["raillm_ok"] else \
             ("Sentinel degraded: %s" % ", ".join(verdict["sentinel_degraded"]))
    try:
        res = notify_fn("◈ GRIP here — HAL substrate ALERT: the economic reasoning "
                        "substrate is DOWN.\n\n" + detail + "\n\nAutonomous loops that "
                        "rely on HAL will degrade until it recovers.")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(res, dict) and bool(res.get("ok"))


def run_probe(*, infer_fn, notify_fn, now: float | None = None) -> dict:
    """One health probe. infer_fn(prompt)->str (RAILLM); notify_fn(text)->dict
    (operator-only, called on a healthy→down transition). Returns the verdict.

    Notify policy = edge-triggered down-transition only: alert when it WAS healthy
    (or unknown) and is now down; stay silent while healthy AND while still-down
    (no repeat-spam). An alert counts as made only when the transport says it was
    delivered: while one is still owed (refused, failed, or no operator configured)
    the last state is recorded as unknown, so the next probe tries again (Codex on
    GRIP#7729). Always logs. Never raises.
    """
    now = time.time() if now is None else now
    raillm_text, raillm_err = None, None
    try:
        raillm_text = infer_fn("reply with: ok")
    except Exception as exc:  # noqa: BLE001 — a probe failure is a 'down' signal, not a crash
        raillm_err = f"{type(exc).__name__}:{str(exc)[:80]}"
    verdict = evaluate(raillm_text=raillm_text, raillm_err=raillm_err, sentinel=sentinel_state())
    verdict["ts"] = now
    was_healthy = _read_last_healthy()
    owed = (not verdict["healthy"]) and (was_healthy is None or was_healthy)
    if owed:
        verdict["alerted"] = _send_alert(verdict, raillm_err, notify_fn)
        owed = not verdict["alerted"]
    state = {"healthy": None if owed else verdict["healthy"], "ts": now}
    for f, rec in ((LOG_FILE, verdict), (STATE_FILE, state)):
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a" if f is LOG_FILE else "w") as fh:
                fh.write(json.dumps(rec) + ("\n" if f is LOG_FILE else ""))
        except OSError:
            pass
    return verdict


def _default_infer(prompt: str) -> str:
    from hal_llm_adapter import HALLLMAdapter
    return HALLLMAdapter().infer(prompt, audit=False).text or ""


def _default_notify(text: str) -> dict:
    from wa_identities import MISSING_HINT
    if not OPERATOR_JID:
        return {"ok": False, "error": MISSING_HINT}
    from whatsapp_transport import post
    return post(recipient=OPERATOR_JID, text=text)


def main() -> int:
    v = run_probe(infer_fn=_default_infer, notify_fn=_default_notify)
    print(f"[hal-health] healthy={v['healthy']} raillm={v['raillm_ok']} "
          f"sentinel={v['sentinel_ok']} providers={v['providers']}")
    return 0  # always 0 — a scheduled probe must never look like a crash to launchd


if __name__ == "__main__":
    raise SystemExit(main())
