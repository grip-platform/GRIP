"""lib/tick_seal.py: the seal a granted act of the always-on tick is recorded with.

An act is recorded twice on the tick's own side ledgers: a decision leaf on ``state/grip-tick/acts.idr.jsonl``
and a paired memory-chain node on ``state/grip-tick/acts.context.jsonl``, which states what the tick believed
when it acted. Both or neither: when the leaf is written and its node is not, a second leaf records that the
first was rolled back, and SealFailed is raised, so the decision ledger never claims an act whose record is
incomplete. When even that roll-back leaf cannot be written, the ledger may claim the act, so SealUncertain is
raised instead, naming the leaf, and the caller keeps the act's work rather than letting it happen twice. A side ledger never moves the installation's main decision head (``lib/precog/idr.py``,
``_advance_head``). Everything sealed must be canonical JSON with no float in it, because ``lib/jcs.py``
refuses floats: a share is a whole percent and a duration whole milliseconds.

The experiment act (``lib/tick_experiment.py``) is the first act to use this module. The checkpoint act
(``lib/tick_checkpoint.py``, ``seal``) and the throttle act (``lib/tick_throttle.py``, ``_seal``) still carry
their own copies of the same seal; GRIP#8854 moves them here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class SealFailed(RuntimeError):
    """The act could not be recorded, so it did not happen."""


class SealUncertain(SealFailed):
    """The leaf is on the ledger, and neither its memory node nor a roll-back leaf could be written after it, so the
    ledger may claim the act. The caller keeps what the act made and reports the incomplete record; it never treats
    the act as one that did not happen, which would let it happen a second time (Codex on GRIP#8856)."""

    def __init__(self, message: str, leaf: str) -> None:
        super().__init__(message)
        self.leaf = leaf


def _names(act: str) -> dict:
    """The record's fixed labels for one act: the same shape the checkpoint and throttle seals write."""
    return {"prompt": f"grip-tick {act} act", "fingerprint": f"tick-{act}", "kind": f"tick-act-{act}"}


def _rollback(act: str, decision: dict, leaf: str, exc: Exception, idr_path: Path) -> bool:
    """A second leaf saying the act whose leaf is already on the ledger was rolled back, because its memory node
    could not be written; True when it was written."""
    try:
        from lib.precog.idr import append_idr, build_idr
        append_idr(build_idr(decision=dict(decision, state="rolled-back", rolled_back=leaf), predecessor_idr=None,
                             depth=0, inputs={"reason": f"{type(exc).__name__}: {exc}"[:200]}, **_names(act)),
                   path=idr_path)
    except Exception:  # noqa: BLE001  the caller turns this into SealUncertain
        return False
    return True


def seal_act(act: str, decision: dict, inputs: dict, summary: str, idr_path: Path, context_path: Path,
             context_head: Path) -> Tuple[str, str]:
    """(leaf id, memory node id) for one act. SealFailed on any failure, after a roll-back leaf when the leaf was
    written and its node was not; SealUncertain, naming the leaf, when that roll-back leaf could not be written
    either."""
    leaf = ""
    try:
        from dataclasses import asdict

        from lib import context_chain
        from lib.precog.idr import append_idr, build_idr, content_addr
        idr = build_idr(decision=decision, predecessor_idr=None, depth=0, inputs=inputs, **_names(act))
        append_idr(idr, path=idr_path)
        leaf = idr.id
        node = context_chain.checkpoint(next_step=None, summary=summary[:600], title=f"tick {act} act",
                                        tier="feedback", records_idr=content_addr(asdict(idr)), path=context_path,
                                        head_pointer=context_head)
        return idr.id, node.id
    except Exception as exc:  # noqa: BLE001  one answer: the act is not recorded, so it did not happen
        message = f"{type(exc).__name__}: {exc}"[:200]
        if leaf and not _rollback(act, decision, leaf, exc, idr_path):
            raise SealUncertain(message, leaf) from exc
        raise SealFailed(message) from exc
