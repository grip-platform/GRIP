"""Is the operator actually reading this, or is nobody there?

THE DEFECT THIS CLOSES. On 2026-09-22 a session ended a turn with
`RECOMMENDED NEXT STEP (yours) — /context` and the Stop hook turned that line
into a twenty-minute hold. V>>'s `/afk` marker was live at the time with 12,202
seconds left on it: he had declared, in GRIP's own state directory, that he was
away. The hold tested `CLAUDE_NON_INTERACTIVE` instead — a variable that
separates a HEADLESS run from a REPL run and says nothing whatever about whether
a human is reading. `afk_remaining_seconds()` was already imported into the very
function that made the call, for a different purpose. The decisive evidence was
in the room and was not consulted at the decision point.

THE FIX IS A LADDER, NOT A FLAG. Presence is not one bit read from one variable;
it is the best available evidence, taken in order of how much it is worth:

    1. he SAID he is away (`/afk`)          -> ABSENT   (his declaration wins)
    2. there is no REPL at all              -> ABSENT   (the old test, demoted)
    3. he typed something T seconds ago     -> PRESENT
    4. anything else                        -> ABSENT   (the default he asked for)

Rung 1 outranks rung 3 deliberately. A stamp five minutes old and a live `/afk`
marker can coexist — he typed `/afk` and left — and when they disagree, the
sentence he wrote himself beats an inference drawn from a timestamp.

WHY THE DEFAULT IS ABSENT, AND WHY THAT IS THE SAFE DIRECTION. The two errors
cost wildly different amounts. A false ABSENT costs nothing at all: the question
parks on the board, the turn ends, and if he is in fact at the keyboard he types
and is heard immediately. A false PRESENT costs twenty minutes of a session doing
nothing, which is exactly what happened. So every uncertain case, every missing
file and every raised exception resolves to ABSENT.

That also makes fail-safe and fail-open the same direction here, which is worth
saying out loud because they usually fight: ABSENT means "park and let the turn
end", so the cautious answer is also the non-blocking one. There is no reading of
this module under which an error can hold a session.

WHAT THIS MODULE IS NOT. It is not a model, and rung 3's window is NOT a measured
number -- see `PRESENT_WITHIN_SECONDS`. Rungs 1, 2 and 4 alone prevent the stall
that motivated it, with no inference of any kind; rung 3 only ever moves a case
from ABSENT to PRESENT, so a badly-chosen window can cost latency and can never
cost a wrongly-held session beyond the hold ceiling the caller already enforces.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "PresenceState",
    "Presence",
    "presence",
    "stamp_path",
    "record_prompt",
    # Exported so a downstream reader can inspect the raw age without
    # reimplementing the stamp reader -- a second reader is a second
    # resolution, which is the defect `_presence_dir` exists to close.
    "seconds_since_prompt",
    "PRESENT_WITHIN_SECONDS",
    "WINDOW_ENV",
]

# How recently he must have typed for rung 3 to call him PRESENT.
#
# THIS NUMBER IS A PLACEHOLDER, NOT A FIT, and is labelled so rather than quietly
# shipped as if it were a finding. It was chosen to match the five minutes GRIP
# already treats as "he is probably still reading"
# (`next_step_router.SILENT_WAIT_SECONDS`). The measurement it was waiting for
# now exists: docs/measurements/2026-09-29-operator-presence-threshold.md, made
# by scripts/presence-threshold-report.py. It found no window, 300 s included,
# that beats always answering ABSENT on held-out sessions, so nothing was
# adopted and no claim is made that this value is right. Out of sample, about
# half of its PRESENT verdicts were followed by no answer within the ceiling.
#
# It is env-overridable so a measured value can be adopted without a code change,
# and so the window can be taken to zero to disable rung 3 entirely.
PRESENT_WITHIN_SECONDS = 300.0
WINDOW_ENV = "GRIP_PRESENCE_WINDOW_SECONDS"


class _Unset:
    """Sentinel: distinguishes "not injected" from an injected None.

    Needed because None is a MEANINGFUL injected value here -- "pretend no /afk
    marker is live" -- so it cannot double as "the caller said nothing".
    Defaulting the parameter to None instead of to this sentinel silently
    disables rung 1, which is the exact rung the module exists to consult. That
    is not hypothetical: the first draft of this file did it.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging affordance
        return "<unset>"


_UNSET = _Unset()


class PresenceState(Enum):
    """Whether a human is expected to read the next thing this session says."""

    PRESENT = "present"
    ABSENT = "absent"


@dataclass(frozen=True)
class Presence:
    """A verdict plus the reason for it.

    `signal` and `evidence` exist so a caller can log WHY, not merely WHAT. The
    defect this module replaces was invisible precisely because the old test
    recorded nothing: a hold happened, and no line anywhere said what had been
    consulted to justify it.
    """

    state: PresenceState
    signal: str
    evidence: str = ""

    @property
    def is_present(self) -> bool:
        return self.state is PresenceState.PRESENT

    @property
    def is_absent(self) -> bool:
        return self.state is PresenceState.ABSENT

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "signal": self.signal,
            "evidence": self.evidence,
        }


def _session_dir(state_dir: Path | str | None = None) -> Path:
    """Where the per-prompt stamps live.

    Resolved through `get_claude_dir()` rather than a `$HOME` literal so a
    machine with `CLAUDE_DIR` set does not end up with the writer and the reader
    pointing at two different directories -- a disagreement that would render
    exactly as "he is never present", silently.
    """
    if state_dir is not None:
        return Path(state_dir)
    return get_claude_dir() / "state" / "session"


def stamp_path(session_id: str, state_dir: Path | str | None = None) -> Path:
    """The stamp file for one session.

    Per-session rather than global: several sessions run at once on this machine,
    and a shared file would let a peer session's typing make THIS session look
    attended. That is the false-PRESENT direction, which is the expensive one.
    """
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "unknown"
    return _session_dir(state_dir) / f"last-prompt-{safe}.json"


def record_prompt(
    session_id: str,
    *,
    now: float | None = None,
    state_dir: Path | str | None = None,
) -> bool:
    """Stamp "a human typed into this session just now". Never raises.

    Returns True when the stamp was written. Called from the UserPromptSubmit
    hook, which must never block input, so every failure is swallowed and
    reported as False -- a missing stamp reads as ABSENT downstream, which is
    the harmless direction.
    """
    try:
        p = stamp_path(session_id, state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "ts": time.time() if now is None else float(now),
            "sid": str(session_id),
        }))
        return True
    except Exception:
        return False


def seconds_since_prompt(
    session_id: str,
    *,
    now: float | None = None,
    state_dir: Path | str | None = None,
) -> float | None:
    """Age of this session's last prompt stamp, or None if there isn't one.

    None covers every unreadable case -- no file, unparseable JSON, a stamp in
    the future, a clock that moved backwards. The caller treats all of them the
    same way, as no evidence rather than as evidence of absence.
    """
    try:
        rec = json.loads(stamp_path(session_id, state_dir).read_text())
        ts = float(rec["ts"])
    except Exception:
        return None
    age = (time.time() if now is None else float(now)) - ts
    return age if age >= 0 else None


def _window(env: dict[str, str] | None = None) -> float:
    """Rung 3's window, env-override honoured, malformed values ignored."""
    raw = (env if env is not None else os.environ).get(WINDOW_ENV, "")
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return PRESENT_WITHIN_SECONDS


def presence(
    session_id: str | None = None,
    *,
    now: float | None = None,
    env: dict[str, str] | None = None,
    state_dir: Path | str | None = None,
    afk_seconds: float | None | object = _UNSET,
) -> Presence:
    """Measure whether a human is reading, and say what the answer rests on.

    `afk_seconds` is injectable purely so the ladder can be tested without a
    marker on disk; left alone it consults the live `/afk` state, which is the
    whole point -- this is the evidence the defect failed to consult.
    """
    environ = env if env is not None else os.environ
    sid = session_id or environ.get("CLAUDE_SESSION_ID") or ""

    # RUNG 1 -- his own declaration. Outranks everything below it.
    left = _afk_remaining(afk_seconds)
    if left is not None and left > 0:
        return Presence(
            PresenceState.ABSENT, "afk-marker",
            f"/afk live, {int(left)}s remaining",
        )

    # RUNG 2 -- no REPL exists, so there is nobody to ask. The old sole test,
    # correct as far as it goes and demoted to one rung of four.
    if environ.get("CLAUDE_NON_INTERACTIVE"):
        return Presence(
            PresenceState.ABSENT, "non-interactive",
            "CLAUDE_NON_INTERACTIVE is set",
        )

    # RUNG 3 -- the only rung that can return PRESENT.
    window = _window(environ)
    age = seconds_since_prompt(sid, now=now, state_dir=state_dir) if sid else None
    if age is not None and window > 0 and age < window:
        return Presence(
            PresenceState.PRESENT, "recent-prompt",
            f"typed {int(age)}s ago, window {int(window)}s",
        )

    # RUNG 4 -- the default V>> asked for. Absence of evidence that he is here
    # is NOT evidence that he is, and the cheap error is this one.
    detail = "no prompt stamp" if age is None else f"last typed {int(age)}s ago"
    return Presence(PresenceState.ABSENT, "no-recent-prompt", detail)


def _afk_remaining(injected: float | None | object) -> float | None:
    """Seconds left on a live `/afk`, or None. Never raises.

    A broken AFK read falls through to the lower rungs rather than
    manufacturing presence -- and since those default to ABSENT, a failure here
    costs at worst a parked question, never a held session.
    """
    if injected is not _UNSET:
        return injected  # type: ignore[return-value]
    try:
        from lib.answer_watcher import afk_remaining_seconds

        return afk_remaining_seconds()
    except Exception:
        return None
