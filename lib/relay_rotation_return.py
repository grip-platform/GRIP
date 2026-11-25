"""Return the relay to the account that an automatic rotation left (GRIP#8291).

With ``GRIP_RELAY_AUTO_ROTATE=1``, a spawn that fails on a usage cap runs
``grip-relay-bill next``, which rewrites the relay's active account. Until this
module, nothing moved it back once the first account reset, so a relay that
started on one account stayed on the next one until that one capped too.

A rotation now leaves a record beside ``last-rotate.txt``: the account it left,
the account it moved to, and when. Before each message the relay reads the
record and decides, in this order:

- the active account is not the one the rotation moved to: someone switched by
  hand, so the hand switch wins and the record is dropped;
- the account it left is no longer pooled: the pool was changed by hand, so the
  record is dropped;
- HAL's ledger shows the account it left with room: switch back;
- HAL's ledger shows that account spent: wait;
- HAL's ledger has no reading for it (the VPS has no ledger): switch back once
  ``GRIP_RELAY_RETURN_AFTER_S`` (default five hours, the shortest usage window)
  has passed since the rotation. If that account is still capped, the next
  spawn caps and the rotation runs again, so the message is still answered; the
  cost is one failed spawn in each period.

A second rotation while a record stands keeps the first account as the one to
return to. Every function here is pure or touches only the record file; the
relay decides whether HAL's brake allows the switch and makes it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

RECORD_NAME = "rotated-from.json"
DEFAULT_RETURN_AFTER_S = 5 * 3600.0

RETURN = "return"
WAIT = "wait"
DROP_HAND_SWITCH = "drop:hand-switch"
DROP_UNPOOLED = "drop:unpooled"


def record_path(rotate_state: Path) -> Path:
    """Where the record lives: beside the rotation's cooldown file."""
    return Path(rotate_state).with_name(RECORD_NAME)


def read_record(path: Path) -> dict | None:
    """The record as ``{"left", "to", "at"}``, or None when there is none or it
    cannot be read as one. A record that cannot be read never moves the relay."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    left, to = doc.get("left"), doc.get("to")
    if not (isinstance(left, str) and left and isinstance(to, str) and to):
        return None
    try:
        at = float(doc.get("at"))
    except (TypeError, ValueError):
        return None
    return {"left": left, "to": to, "at": at}


def write_record(path: Path, record: dict) -> None:
    """Replace the record in one step, readable by its owner only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, scratch = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out)
            out.write("\n")
        os.replace(scratch, path)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise


def drop_record(path: Path) -> None:
    """Remove the record; a record that is already gone is not an error."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def after_rotation(path: Path, before: str, after: str, now: float) -> dict | None:
    """Record a rotation from ``before`` to ``after`` and return the record kept.

    When a record stands and the rotation started from the account it moved to,
    this is a second rotation in a row, and the first account stays the one to
    return to. A rotation whose accounts are unknown or the same leaves no
    record, so it can never move the relay later."""
    current = read_record(path)
    left = current["left"] if current and current["to"] == before else before
    if not left or not after or left == after:
        drop_record(path)
        return None
    record = {"left": left, "to": after, "at": float(now)}
    write_record(path, record)
    return record


def return_after_s(env=None) -> float:
    """How long to wait without a ledger reading, from ``GRIP_RELAY_RETURN_AFTER_S``.
    A value that is missing, not a number or negative gives the default."""
    raw = (os.environ if env is None else env).get("GRIP_RELAY_RETURN_AFTER_S")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_RETURN_AFTER_S
    except (TypeError, ValueError):
        return DEFAULT_RETURN_AFTER_S
    return value if value >= 0 else DEFAULT_RETURN_AFTER_S


def decide(record: dict, active: str, pooled: list, room: dict, now: float,
           after_s: float) -> str:
    """What to do with a standing record: ``RETURN``, ``WAIT`` or a ``DROP_*``.

    ``room`` is the ledger's verdict by label (``subscription_room.ledger_room``):
    only an explicit True is room and only an explicit False is spent, so an
    account the ledger has not read falls back to the time since the rotation."""
    if active != record["to"]:
        return DROP_HAND_SWITCH
    if record["left"] not in pooled:
        return DROP_UNPOOLED
    verdict = room.get(record["left"])
    if verdict is True:
        return RETURN
    if verdict is False:
        return WAIT
    return RETURN if now - record["at"] >= after_s else WAIT
