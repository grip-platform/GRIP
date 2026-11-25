"""Which pooled Claude subscription account still has room, read from HAL's ledger.

A usage cap moves work to the next pooled subscription account, never to the paid
key. V>> ruled on 2026-10-12 at 01:03 UTC, after watching the wire carry five
capped sessions onto another account: "This is the way it should work!! Not via
paid API KEY". HAL's wire already does that for every wired session (ADR-0016).
A spawn that does not ride the wire takes its account from the token store, and
the relay is the first such spawn. This module tells such a spawn whether its
account is spent and which pooled account is not.

Room comes only from HAL's own ledger (``account-ledger.json``), which holds the
vendor's ``anthropic-ratelimit-unified-*`` headers as HAL last saw them. An
account the ledger has never read counts as neither spent nor free, so a guess
never moves billing. Read-only: nothing here changes the token store, so a spawn
goes back to its own account by itself once that account's window resets.

HAL has a brake on moving work between accounts (its contract, "The enforcement
stop"): after the service answers any account with a revocation or a suspension,
HAL stops all switching until the operator turns it back on. No stand-in is named
while that brake is engaged (GRIP#8255).

Standard library only, on purpose. The relay's self-deploy ships an explicit set
of files (``scripts/relay-selfdeploy.sh``), and three outages came from a relay
import outside that set. ``statusline_limits`` reads the same ledger, but it
imports ``file_lock``, so reusing it would pull the statusline's dependencies
into every relay host; this module adds one file instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

#: A window at or above this share counts as spent until its reset time passes.
#: Below 100 because a window at 99% caps on the next turn.
SPENT_PERCENT = 95.0
#: The two account-wide windows, under either name HAL writes. Overage windows are
#: paid usage, not subscription room, so they decide nothing here.
ACCOUNT_WINDOWS = ("five_hour", "5h", "seven_day", "7d")
#: The largest reading HAL's ledger can hold: it accepts a fraction from 0 to 100
#: (``_FRACTION_RANGE`` in ``~/.hal/lib/hal/account_ledger.py``), so 10,000%. A
#: larger value was not written by HAL, and scaling one could overflow to infinity.
#: Twin of ``_PERCENT_CEILING`` in ``lib/statusline_limits.py`` and
#: ``_FRACTION_CEILING`` in ``lib/fanout_preflight.py``.
_FRACTION_CEILING = 100.0
#: A reset outside this range is treated as unknown, as statusline_limits does.
_EPOCH_BOUNDS = (0, 10_000_000_000)
#: HAL's off marker for account switching, beside its ledger. While it exists HAL
#: moves no work between accounts, and neither does anything that asks this module.
SWITCH_OFF_MARKER = "account-switch.off"


def hal_home() -> Path:
    """HAL's home: ``$HAL_HOME`` when set, else ``~/.hal``."""
    return Path(os.environ.get("HAL_HOME") or str(Path.home() / ".hal"))


def ledger_path() -> Path:
    """HAL's account ledger, under :func:`hal_home`."""
    return hal_home() / "state" / "hal" / "account-ledger.json"


def accounts_in(text: str) -> dict:
    """The ``accounts`` mapping in a ledger's text, or ``{}`` when the text is not a
    ledger. For a caller that has already read the file and must answer from those
    same bytes."""
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    accounts = doc.get("accounts") if isinstance(doc, dict) else None
    return accounts if isinstance(accounts, dict) else {}


def ledger_accounts(path: Path | None = None) -> dict:
    """The ledger's ``accounts`` mapping, or ``{}`` when it cannot be read."""
    try:
        return accounts_in((path or ledger_path()).read_text())
    except (OSError, ValueError):
        return {}


def percent_used(value) -> float | None:
    """One usage value HAL wrote, as a percentage, or ``None`` when it is not a reading.
    HAL stores the vendor's header verbatim: a fraction, which goes over 1.0 when the
    window is over its limit (HAL recorded 1.01 on 2026-10-10). The service sets no
    ceiling, so every value HAL's ledger can hold is a reading. A large one is a
    window deep in paid extra usage, and it is spent; leaving it unread would make
    that account look free. A value HAL cannot hold (above ``_FRACTION_CEILING``,
    negative, infinite, not a number) is not a reading. The range is checked before
    scaling, so a huge value cannot overflow to infinity."""
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= _FRACTION_CEILING):
        return None
    return value * 100.0


def _window(raw) -> tuple | None:
    """``(percent used, reset epoch or None)`` from one ledger window, or ``None`` when
    the window holds no reading (:func:`percent_used`)."""
    if not isinstance(raw, dict):
        return None
    used = percent_used(raw.get("utilization"))
    if used is None:
        return None
    reset = raw.get("reset")
    known = (isinstance(reset, (int, float)) and not isinstance(reset, bool)
             and _EPOCH_BOUNDS[0] <= reset <= _EPOCH_BOUNDS[1])
    return used, int(reset) if known else None


def reset_passed(resets, now: float) -> bool:
    """Whether a window's known reset time has passed. None is unknown; a reset of 0
    is a known time long past, not a missing one (Codex on GRIP#8283), as the status
    line and the fan-out preflight read it. The one home of this comparison here and
    in the ladder preview, so the next edge case is fixed once."""
    return resets is not None and resets <= now


def window_spent(used: float, resets, now: float) -> bool:
    """A window that is spent and has not yet reset."""
    return used >= SPENT_PERCENT and not reset_passed(resets, now)


def account_windows(entry) -> list:
    """``(name, percent used, reset epoch or None)`` for each account-wide window one
    ledger entry holds, in the ledger's order, or ``[]`` when it holds none."""
    quota = entry.get("quota") if isinstance(entry, dict) else None
    windows = quota.get("windows") if isinstance(quota, dict) else None
    if not isinstance(windows, dict):
        return []
    return [(name, *window) for name, raw in windows.items()
            if name in ACCOUNT_WINDOWS and (window := _window(raw))]


def _room(entry, now: float) -> bool | None:
    """One account's verdict: ``True`` with room in every account-wide window,
    ``False`` with one spent, ``None`` when the entry holds no such window."""
    seen = account_windows(entry)
    if not seen:
        return None
    return not any(window_spent(used, resets, now) for _name, used, resets in seen)


def rooms(accounts: dict, now: float) -> dict:
    """``{label: True}`` for each account with room and ``{label: False}`` for each
    with a spent window, from a ledger's ``accounts`` mapping. An account that holds
    no account-wide window is absent."""
    room: dict = {}
    for label, entry in accounts.items():
        verdict = _room(entry, now)
        if verdict is not None:
            room[label] = verdict
    return room


def ledger_room(now: float | None = None, path: Path | None = None) -> dict:
    """``{label: True}`` for each account the ledger shows with room, ``{label: False}``
    for each with a spent window. An account the ledger has not read is absent, and
    an absent or unreadable ledger gives ``{}``, so nothing is ever handed over."""
    return rooms(ledger_accounts(path), time.time() if now is None else now)


def pool(active: Path) -> list:
    """The pooled labels beside the active token file, in billing order."""
    try:
        text = active.with_name(active.name + ".pool").read_text()
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def labelled(active: Path, label: str) -> Path:
    """Where ``label``'s stored token lives beside the active token file."""
    return active.with_name(f"{active.name}.{label}")


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def active_label(active: Path, labels: list) -> str:
    """The pooled label whose stored token is the active one, or ``""``."""
    current = _digest(active)
    if not current:
        return ""
    return next((label for label in labels if _digest(labelled(active, label)) == current), "")


def next_with_room(labels: list, current: str, room: dict) -> str:
    """The first pooled label, in billing order, that is not ``current`` and that the
    ledger shows with room, or ``""``. This is the one rule for where a spent
    account's work goes next. The ladder preview (``claude_account_ladder``) calls it
    too, so what the preview says and what the relay does cannot differ. Only an
    explicit ``True`` is room: anything else a caller might pass is not a reading."""
    return next((label for label in labels
                 if label != current and room.get(label) is True), "")


def switching_on(path: Path | None = None) -> bool:
    """HAL's brake on moving work between accounts, read as HAL reads it
    (``lib/hal/account_switch.py``): on unless the off marker exists, and off when
    the marker cannot be checked, so the brake holds when in doubt. HAL engages it by
    itself when the service answers any account with a revocation or a suspension,
    and only the operator releases it. ``os.stat``, not ``Path.exists``, which
    answers False on a permission error. The marker is read beside the ledger in
    use: ``path`` when a caller supplies one, so a caller that names its own ledger
    is never answered from another HAL home."""
    try:
        os.stat((path or ledger_path()).with_name(SWITCH_OFF_MARKER))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def token_digest(path: Path) -> str:
    """HAL's name for a stored token: twelve hex characters of SHA-256 of its text,
    never the token. ``""`` when the file cannot be read."""
    try:
        text = path.read_text().strip()
    except (OSError, ValueError):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def token_health(entry, token: Path | None = None) -> str:
    """``ok``, ``dead`` or ``unknown``, as HAL's ledger holds it for one account. HAL
    writes ``dead`` when the service refused the token (it needs a new sign-in), and
    records which token it judged by its digest. Given the stored token, a verdict
    about a different one (a sign-in since) reads as ``unknown``."""
    info = entry.get("token") if isinstance(entry, dict) else None
    if not isinstance(info, dict):
        return "unknown"
    judged = info.get("digest")
    if token is not None and judged is not None and judged != token_digest(token):
        return "unknown"
    return str(info.get("health") or "unknown")


def candidates(active: Path, labels: list, accounts: dict, usable=None) -> list:
    """The pooled labels that could take over, in billing order. One is left out when
    HAL holds its stored token as refused, or when ``usable(label)`` says the caller
    cannot use it. So the search for a stand-in goes on past an account that cannot
    serve, as HAL's wire does (GRIP#8253). Without ``usable`` only the ledger decides."""
    return [label for label in labels
            if token_health(accounts.get(label), labelled(active, label)) != "dead"
            and (usable is None or usable(label))]


def _choice(active: Path, now: float | None, path: Path | None, usable=None) -> tuple:
    """``(spent label, pooled label that can take over)`` from the pool and one reading
    of the ledger, or ``("", "")``. It does not ask whether the hand-over is allowed."""
    labels = pool(active)
    current = active_label(active, labels)
    accounts = ledger_accounts(path)
    room = rooms(accounts, time.time() if now is None else now)
    if not current or room.get(current) is not False:
        return ("", "")
    other = next_with_room(candidates(active, labels, accounts, usable), current, room)
    return (current, other) if other else ("", "")


def held(active: Path, now: float | None = None, path: Path | None = None,
         usable=None) -> tuple:
    """``(spent label, pooled label)`` for a hand-over that HAL's brake is holding
    back right now, or ``("", "")``. For a caller that wants to say why it stayed on
    a spent account. It answers only when :func:`stand_in` does not: the two read one
    choice, and the brake decides which of them names it."""
    return ("", "") if switching_on(path) else _choice(active, now, path, usable)


def stand_in(active: Path, now: float | None = None, path: Path | None = None,
             usable=None) -> tuple:
    """``(spent label, pooled label to use instead)``, or ``("", "")`` to keep the
    active token. A stand-in is named only when the ledger shows the active account
    spent AND another pooled account with room that can take over
    (:func:`candidates`), and never while HAL's account switch is off
    (:func:`switching_on`). ``usable(label)`` lets the caller say which stored tokens
    it can use."""
    choice = _choice(active, now, path, usable)
    return choice if choice[1] and switching_on(path) else ("", "")
