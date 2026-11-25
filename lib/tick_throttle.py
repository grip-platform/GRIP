"""The tick's throttle act: a flag every GRIP-started run reads, raised when the weekly Claude spend reaches
the operator's soft cap and cleared at the weekly reset, under a signed grant (``lib/tick_grant.py``, act
``throttle``).

The flag is ``state/session/throttle.json``, beside the operator's brakes. Only the always-on tick
(``lib/grip_tick.py``) raises it: under a grant that names the act and the spend trigger, on a tick that
measured the weekly spend, and only when no flag is set. The file is created exclusively, so a flag another
writer put there between the decision and the write is left as found and the raise is abandoned. The raise
is sealed on the tick's own act ledgers as a decision leaf and a paired memory node, the same seal the
checkpoint act uses, and the leaf's id is then written into the flag. A flag that cannot be sealed is
removed; when the leaf was written but its memory node was not, a second leaf records the roll-back, so the
decision ledger never claims an act the flag does not show. A flag of the tick's own that carries no leaf (a
kill between the write and the seal) is removed by the next tick before it decides again. The tick clears a
flag it raised itself when a measurement shows every organization's weekly spend below the cap again: the
grant's cap while a grant is in force, the cap the flag records once the grant has expired, so an expired
grant never strands a flag. The reversal is sealed FIRST and the file removed after, naming the grant that
authorised it; a reversal that cannot be recorded does not happen, the flag stays, and the next measured
tick tries again. A flag written by anything else is never touched. Deleting the file is the reversal.

What the flag means to a launcher is decided by the operator (2026-10-29): a headless GRIP-started run
refuses to start while the flag is set, an interactive session prints one warning line and runs. The
launchers read it through ``lib/throttle_flag.py`` (``is_set``, ``describe``, and a command line for
``bin/grip``), a module that reaches no record writer, so a shebanged tool may import it without the
re-exec into the signing interpreter. This module re-exports that reader for the tick and its tests; it is
the half that writes and seals, and a tool that imports it must carry the re-exec (``tests/test_pq_reexec.py``).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from throttle_flag import BY, VERSION, describe, flag_path, is_own, is_set, read_flag  # noqa: E402

__all__ = ["BY", "VERSION", "SPEND_TRIGGER", "SealFailed", "FlagAppeared", "describe", "flag_path", "is_own",
           "is_set", "read_flag", "flag_cap", "drop_unsealed", "over_cap", "iso", "raise_flag", "clear_flag"]

SPEND_TRIGGER = "spend:cap"  # the trigger a throttle grant must name for the tick to raise the flag


class SealFailed(RuntimeError):
    """The act could not be recorded, so it did not happen."""


class FlagAppeared(RuntimeError):
    """A flag file appeared between the decision to raise and the write: the raise is abandoned, the file left as found."""


def flag_cap(flag: dict) -> Optional[float]:
    """The cap a flag records, as a share, or None when it is missing or outside 1..99 percent."""
    try:
        pct = int(flag.get("cap_pct"))
    except (TypeError, ValueError):
        return None
    return pct / 100.0 if 1 <= pct <= 99 else None


def drop_unsealed(path: Path) -> Optional[dict]:
    """Remove a flag of the tick's own that records no leaf: it was written and never sealed. The flag that
    went, or None when there was none to drop."""
    flag = read_flag(path)
    if not is_own(flag) or flag.get("leaf"):
        return None
    try:
        path.unlink()
    except OSError:
        return None
    return flag


def over_cap(weekly: Dict[str, float], cap: float) -> Dict[str, float]:
    """The organizations whose weekly spend is at or over the cap."""
    return {org: float(value) for org, value in weekly.items() if float(value) >= cap}


def _pct(x: float) -> int:
    """A share as a whole percent: the canonical JSON the seal signs takes no float."""
    return int(round(float(x) * 100))


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _compensate(decision: dict, leaf: str, exc: Exception, idr_path: Path) -> None:
    """A second leaf saying the act whose leaf is already on the ledger was rolled back, because its memory node
    could not be written. Best effort: the SealFailed that follows says the act did not happen either way, but the
    ledger alone should not claim an act the flag does not show."""
    try:
        from lib.precog.idr import append_idr, build_idr
        append_idr(build_idr(prompt="grip-tick throttle act", fingerprint="tick-throttle",
                             decision=dict(decision, state="rolled-back", rolled_back=leaf),
                             predecessor_idr=None, depth=0, kind="tick-act-throttle",
                             inputs={"reason": f"{type(exc).__name__}: {exc}"[:200]}), path=idr_path)
    except Exception:  # noqa: BLE001  nothing more can be recorded; the caller raises SealFailed
        pass


def _seal(decision: dict, inputs: dict, summary: str, idr_path: Path, context_path: Path,
          context_head: Path) -> Tuple[str, str]:
    """The act as a decision leaf on the tick's ledger and a paired memory node; raises SealFailed on any failure.
    A leaf written without its node is followed by a roll-back leaf (``_compensate``) before the raise."""
    leaf = ""
    try:
        from dataclasses import asdict

        from lib import context_chain
        from lib.precog.idr import append_idr, build_idr, content_addr
        idr = build_idr(prompt="grip-tick throttle act", fingerprint="tick-throttle", decision=decision,
                        predecessor_idr=None, depth=0, kind="tick-act-throttle", inputs=inputs)
        append_idr(idr, path=idr_path)
        leaf = idr.id
        node = context_chain.checkpoint(next_step=None, summary=summary[:600], title="tick throttle act",
                                        tier="feedback", records_idr=content_addr(asdict(idr)),
                                        path=context_path, head_pointer=context_head)
        return idr.id, node.id
    except Exception as exc:  # noqa: BLE001  one answer: the act is not recorded, so it did not happen
        if leaf:
            _compensate(decision, leaf, exc, idr_path)
        raise SealFailed(f"{type(exc).__name__}: {exc}"[:200]) from exc


def _dump(flag: dict) -> bytes:
    return (json.dumps(flag, indent=1, sort_keys=True) + "\n").encode("utf-8")


def _create(path: Path, flag: dict) -> bytes:
    """Write a new flag only where no file is: a flag another writer put there meanwhile is left as found."""
    content = _dump(flag)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise FlagAppeared(f"a flag appeared at {path} before the raise; left as found") from exc
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    return content


def _still_ours(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _replace_own(path: Path, expected: bytes, flag: dict) -> bool:
    """Replace the flag only while it still holds exactly what this raise wrote; False, and nothing written, when
    another writer's flag is there now."""
    if not _still_ours(path, expected):
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(_dump(flag))
    os.replace(tmp, path)
    return True


def _unlink_own(path: Path, expected: bytes) -> None:
    if _still_ours(path, expected):
        try:
            path.unlink()
        except OSError:
            pass


def raise_flag(path: Path, weekly: Dict[str, float], cap: float, grant_hash: str, now: float,
               idr_path: Path, context_path: Path, context_head: Path) -> dict:
    """Create the flag for the organizations at or over the cap (FlagAppeared if a file is there), seal it, then
    write the leaf's id into the flag while it is still this raise's own. On a seal failure the flag is removed and
    SealFailed raised; a flag left without a leaf by a kill between the write and the seal is what ``drop_unsealed``
    removes on the next tick."""
    hit = over_cap(weekly, cap)
    if not hit:
        raise ValueError("no organization is at or over the cap")
    flag = {"v": VERSION, "by": BY, "at": iso(now), "weekly_pct": {org: _pct(v) for org, v in hit.items()},
            "cap_pct": _pct(cap), "grant_hash": grant_hash}
    content = _create(path, flag)
    try:
        leaf, node = _seal({"act": "throttle", "state": "raised", "weekly_pct": flag["weekly_pct"], "cap_pct": flag["cap_pct"]},
                           {"grant_hash": grant_hash, "flag": str(path), "flag_sha256": hashlib.sha256(content).hexdigest()},
                           f"tick throttle raised: weekly spend {flag['weekly_pct']} percent at or over the {flag['cap_pct']}% cap under grant "
                           f"{grant_hash}; headless GRIP runs refuse until the weekly reset",
                           idr_path, context_path, context_head)
    except SealFailed:
        _unlink_own(path, content)
        raise
    sealed = dict(flag, leaf=leaf, node=node)
    _replace_own(path, content, sealed)
    return sealed


def clear_flag(path: Path, weekly: Dict[str, float], cap: float, now: float,
               idr_path: Path, context_path: Path, context_head: Path, *, grant_hash: str = "") -> dict:
    """Seal the reversal of the tick's own flag once every organization is below the cap, then remove the file.
    The seal goes first: a reversal that cannot be recorded does not happen, the flag stays, and the next measured
    tick tries again. ``grant_hash`` names the grant whose cap authorised the clear; empty, the cap the flag
    records did, after the grant expired, and the seal says which."""
    flag = read_flag(path)
    if not is_own(flag):
        raise ValueError("no flag of the tick's own to clear")
    if over_cap(weekly, cap):
        raise ValueError("an organization is still at or over the cap")
    source = "grant" if grant_hash else "flag"
    leaf, node = _seal({"act": "throttle", "state": "cleared", "weekly_pct": {org: _pct(v) for org, v in weekly.items()},
                        "cap_pct": _pct(cap), "cap_source": source},
                       {"raised_under": str(flag.get("grant_hash", "")), "cleared_under": grant_hash or f"flag:{_pct(cap)}",
                        "flag": str(path), "raised_at": str(flag.get("at", ""))},
                       f"tick throttle cleared at {iso(now)}: every organization below the {_pct(cap)}% cap again (the "
                       f"{source}'s cap); the flag raised {flag.get('at', '?')} is removed", idr_path, context_path, context_head)
    path.unlink()
    return {"leaf": leaf, "node": node, "was": flag}
