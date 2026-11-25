#!/usr/bin/env python3
"""ULTRACLEAR session helper — the cheap-serialise + warm-reload layer over the
pure `lib/ultraclear.py` planner.

`plan_ultraclear` decides the WHAT (HOT verbatim / COLD ref-ids / FOLDED ids).
This module turns that plan into a JSON-serialisable CHECKPOINT a fresh session
can warm-reload — a SERIALIZE, not an LLM summary, so it is lossless by
construction and costs ~0 LLM tokens to produce.

The two operations:

  checkpoint(turns, kernel_tokens, ...) -> dict
      Run the planner, then emit a bounded manifest:
        * HOT turns VERBATIM (the warm-reload payload),
        * COLD ref-ids (for demand page-back — content stays in the cold store),
        * FOLDED ids (already in the holistic kernel — nothing to reload),
        * the falsifiable econ (reload/compact/net_savings/is_noop),
        * created_at (INJECTED — never datetime.now() argless, per GRIP
          determinism; a deterministic test passes a fixed `now`).

  reload_manifest(checkpoint_dict) -> list[Turn]
      Return ONLY the HOT turns to warm-load into the fresh window.

Zero-loss invariant (each pinned by a test in tests/test_ultraclear_session.py):
HOT round-trips byte-equal, COLD ref-ids are preserved for page-back, the econ is
carried through verbatim, and the manifest stays bounded (≤ planner's
manifest_tokens). The planner already proves HOT ∪ COLD ∪ FOLDED == every turn.

DIP / purity: stdlib only — no network, no SDK, no clock read, no filesystem.
The caller owns persistence (writing the dict to `state/ultraclear/*.json`) and
the wall-clock (`now`). The session-JSONL transcript adapter (parsing a live
Claude Code session log into `Turn`s) is OUT OF SCOPE here — turns are injected;
that adapter is a thin future front-end that constructs `Turn`s and calls
`checkpoint`.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Iterable

_LIB = pathlib.Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from ultraclear import (  # noqa: E402
    Plan,
    Turn,
    UltraclearConfig,
    plan_ultraclear,
)

CHECKPOINT_VERSION = 1

# Required keys on each serialised HOT turn dict — used to fail CLOSED on a
# malformed/truncated checkpoint rather than leaking a raw KeyError (AGORA #3410 r3).
_REQUIRED_TURN_KEYS = ("id", "role", "content", "tokens")


def _turn_to_dict(turn: Turn) -> dict:
    """Serialise a HOT turn VERBATIM — every field round-trips losslessly."""
    return {
        "id": turn.id,
        "role": turn.role,
        "content": turn.content,
        "tokens": turn.tokens,
        "pinned": turn.pinned,
        "in_kernel": turn.in_kernel,
    }


def _turn_from_dict(data: dict) -> Turn:
    """Inverse of `_turn_to_dict` — reconstruct the exact `Turn`."""
    return Turn(
        id=data["id"],
        role=data["role"],
        content=data["content"],
        tokens=data["tokens"],
        pinned=data.get("pinned", False),
        in_kernel=data.get("in_kernel", False),
    )


def _econ_of(plan: Plan) -> dict:
    """The falsifiable economics carried into the checkpoint verbatim."""
    return {
        "manifest_tokens": plan.manifest_tokens,
        "original_tokens": plan.original_tokens,
        "reload_cost": plan.reload_cost,
        "compact_cost": plan.compact_cost,
        "net_savings": plan.net_savings,
        "is_noop": plan.is_noop,
    }


def checkpoint(
    turns: Iterable[Turn],
    kernel_tokens: int,
    config: UltraclearConfig | None = None,
    *,
    now: str,
) -> dict:
    """Plan the partition and emit a JSON-serialisable warm-reload checkpoint.

    `now` is the caller-supplied creation timestamp (injected for determinism —
    this function NEVER reads the clock). `config` defaults to `UltraclearConfig()`
    when omitted.

    The returned dict has stable, JSON-only fields:
      version    — schema version (CHECKPOINT_VERSION)
      created_at — the injected `now`
      kernel_tokens — echoed (the FOLDED turns live in this kernel)
      hot        — list of VERBATIM turn dicts (the warm-reload payload)
      cold_ref_ids — list of COLD turn ids (content stays in the cold store)
      folded_ids — list of FOLDED turn ids (already in the kernel)
      econ       — the falsifiable economics (see `_econ_of`)
    """
    cfg = config if config is not None else UltraclearConfig()
    plan = plan_ultraclear(turns, kernel_tokens, cfg)
    return {
        "version": CHECKPOINT_VERSION,
        "created_at": now,
        "kernel_tokens": kernel_tokens,
        "hot": [_turn_to_dict(t) for t in plan.hot],
        "cold_ref_ids": [t.id for t in plan.cold],
        "folded_ids": [t.id for t in plan.folded],
        "econ": _econ_of(plan),
    }


def _validate_checkpoint_schema(checkpoint_dict: dict) -> None:
    """Reject a malformed / version-mismatched checkpoint LOUD + CLOSED (AGORA
    #3410 r3). Checkpoints round-trip through on-disk JSON (`state/ultraclear/*.json`),
    so a hand-edited, truncated, or post-CHECKPOINT_VERSION-bump file is a REAL
    input — guarding here means a corrupt manifest fails with a clear message, never
    a bare `KeyError` from a raw `checkpoint_dict["hot"]` / `_turn_from_dict` lookup.
    Symmetric with `reconstruct`'s loud-failure contract in the planner."""
    if not isinstance(checkpoint_dict, dict):
        raise ValueError(
            f"ultraclear checkpoint must be a dict, got {type(checkpoint_dict).__name__}"
        )
    version = checkpoint_dict.get("version")
    if version != CHECKPOINT_VERSION:
        raise ValueError(
            f"ultraclear checkpoint version {version!r} != supported "
            f"{CHECKPOINT_VERSION} — refusing to reload a version-mismatched "
            "manifest (fail-closed)"
        )
    hot = checkpoint_dict.get("hot")
    if not isinstance(hot, list):
        raise ValueError(
            "ultraclear checkpoint missing a list 'hot' manifest "
            f"(got {type(hot).__name__}) — fail-closed"
        )
    for i, entry in enumerate(hot):
        if not isinstance(entry, dict):
            raise ValueError(
                f"ultraclear checkpoint hot[{i}] must be a dict, "
                f"got {type(entry).__name__}"
            )
        missing = [k for k in _REQUIRED_TURN_KEYS if k not in entry]
        if missing:
            raise ValueError(
                f"ultraclear checkpoint hot[{i}] missing required field(s): {missing}"
            )


def reload_manifest(checkpoint_dict: dict) -> list[Turn]:
    """Return the HOT turns to warm-load from a checkpoint produced by
    `checkpoint`. COLD content is NOT reloaded here — it pages back on demand
    via its ref-id against the cold store; FOLDED content is already in the
    holistic kernel.

    Fails CLOSED with a clear ValueError on a malformed or version-mismatched
    checkpoint (AGORA #3410 r3) — see `_validate_checkpoint_schema`."""
    _validate_checkpoint_schema(checkpoint_dict)
    return [_turn_from_dict(d) for d in checkpoint_dict["hot"]]
