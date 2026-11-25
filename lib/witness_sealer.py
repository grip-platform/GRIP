#!/usr/bin/env python3
"""GRIP witness sealer -- the M1 spike's sealing leg (SCRATCH ledger only).

The witness mod (a Claude Code plugin, ``mods/grip-witness``) watches a session
and computes one keyed-HMAC *commitment* per event it is served. On a finished
turn it hands the accumulated batch to this CLI through ``$.process.run``. This
CLI appends that batch to a SCRATCH JSONL ledger named by ``--ledger``.

This is a SPIKE tool. It deliberately stops at a scratch file and NEVER writes
GRIP's real signed decision chain -- ``lib/audit_chain.py``'s
``logs/api-audit.jsonl``, ``lib/idr_public_emitter.py``'s
``data/idr-public.jsonl``, the tenant ``decision-log.jsonl`` files, or the key
and chain-state material under ``state/audit/``. The full M1 design seals
batches INTO that chain (see ``docs/designs/grip-witness-mod.md``); the spike
refuses to, so it is safe to run anywhere, CI included. The refusal is
mechanical (``is_real_chain_target``), not a comment.

Contract
--------
stdin   a JSON batch::

            {"batch_id": "...", "commitments": [{"event","seq","hmac",...}, ...]}

        A bare JSON list of commitments is also accepted (a batch_id is then
        derived). Each commitment must carry at least ``event`` and ``hmac``,
        the latter an HMAC-SHA256 as 64 lowercase hex characters. ``batch_id``
        and ``seal_time`` are the sealer's own fields and are refused on a
        commitment, so a batch cannot relabel or backdate what it seals.

--ledger PATH   the scratch JSONL ledger to append to (required). One line per
                commitment is appended, each self-describing: it carries the
                ``batch_id`` and the ``seal_time`` (UTC, ISO-8601) the batch was
                sealed at, beside the commitment's own fields. The append is
                idempotent by commitment identity ``(session_id, epoch, seq)``:
                a commitment the ledger already holds is skipped and counted,
                so a retry after a lost sealer response cannot duplicate rows.

stdout  one JSON summary line::

            {"sealed": N, "skipped": M, "batch_id": "...", "seal_time": "...",
             "ledger": "..."}

exit    0 on success; 2 on a usage/validation error or a real-chain refusal.

Nothing here calls a model, opens a network socket, or reads the live chain.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:  # imported as a sibling module (tests put lib/ on sys.path) ...
    from claude_dir import get_claude_dir
except ImportError:  # pragma: no cover - ... or run as ``python -m lib.witness_sealer``
    from lib.claude_dir import get_claude_dir

__all__ = [
    "REAL_CHAIN_LEDGER_NAMES",
    "SealerRefusal",
    "is_real_chain_target",
    "parse_batch",
    "seal_batch",
    "main",
]

#: Filenames that are always a real-chain ledger, wherever they sit. A scratch
#: ledger for the spike must never be named one of these.
REAL_CHAIN_LEDGER_NAMES = frozenset(
    {"api-audit.jsonl", "idr-public.jsonl", "decision-log.jsonl"}
)

#: Suffixes that mark a real chain's sidecar state, never a scratch ledger.
_REAL_CHAIN_SUFFIXES = (".chain-state.json", ".chain-lock")

#: What a commitment looks like on the wire: HMAC-SHA256 as 64 lowercase hex
#: characters. The sealer cannot verify a MAC (the key never leaves the mod),
#: so shape is the whole of what it can check -- and it does check it, so a
#: truncated or hand-typed value is refused rather than sealed as evidence.
COMMITMENT_SHAPE = re.compile(r"^[0-9a-f]{64}$")

#: Fields the sealer stamps itself. A commitment carrying one is refused: the
#: seal time and the batch label are the sealer's word, never the batch's.
RESERVED_FIELDS = ("batch_id", "seal_time")

#: What makes two commitments the same commitment: the mod numbers events per
#: session and per registration epoch, so this triple is the row's identity.
_IDENTITY_FIELDS = ("session_id", "epoch", "seq")


class SealerRefusal(ValueError):
    """Raised when a batch cannot be sealed safely (bad input, real-chain target)."""


def _utc_now_iso() -> str:
    """The seal time: UTC, ISO-8601, second resolution is enough for a spike."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_real_chain_target(ledger: Path, claude_dir: Path | None = None) -> bool:
    """True when ``ledger`` resolves onto GRIP's real signed decision chain.

    The spike sealer must never append there. This is the mechanical half of the
    docstring's promise: the real chain is ``logs/api-audit.jsonl`` and
    ``data/idr-public.jsonl`` under the install directory, the tenant
    ``decision-log.jsonl`` files, the key and chain-state material under
    ``state/audit/``, and any chain sidecar (``*.chain-state.json``,
    ``*.chain-lock``). A scratch ledger is anything else.
    """
    root = get_claude_dir() if claude_dir is None else Path(claude_dir)
    try:
        resolved = Path(ledger).expanduser().resolve()
    except (OSError, RuntimeError):  # an unresolvable path is not a safe target
        return True

    if resolved.name in REAL_CHAIN_LEDGER_NAMES:
        return True
    if resolved.name.endswith(_REAL_CHAIN_SUFFIXES):
        return True

    root_resolved = Path(root).expanduser().resolve()
    protected = [
        root_resolved / "logs" / "api-audit.jsonl",
        root_resolved / "data" / "idr-public.jsonl",
    ]
    if any(resolved == p for p in protected):
        return True

    audit_dir = root_resolved / "state" / "audit"
    try:
        resolved.relative_to(audit_dir)
        return True  # under state/audit/ -- key material and chain state
    except ValueError:
        return False


def _normalise_commitment(raw: object, index: int) -> dict:
    """Validate one commitment and return it as a plain dict.

    A commitment is the mod's keyed-HMAC over one witnessed event. It must be an
    object carrying at least ``event`` (what was served) and ``hmac`` (the keyed
    commitment, hex). ``seq`` (the per-session sequence number) is expected but
    not required, so a partial batch is still sealable. Anything else on the
    commitment is passed through untouched -- the sealer does not reinterpret
    the mod's fields.
    """
    if not isinstance(raw, dict):
        raise SealerRefusal(f"commitment {index} is not an object")
    event = raw.get("event")
    hmac = raw.get("hmac")
    if not isinstance(event, str) or not event:
        raise SealerRefusal(f"commitment {index} has no 'event' string")
    if not isinstance(hmac, str) or not hmac:
        raise SealerRefusal(f"commitment {index} has no 'hmac' string")
    if not COMMITMENT_SHAPE.match(hmac):
        raise SealerRefusal(
            f"commitment {index} 'hmac' is not 64 lowercase hex characters"
        )
    for field in RESERVED_FIELDS:
        if field in raw:
            raise SealerRefusal(
                f"commitment {index} carries the reserved field {field!r}, "
                "which only the sealer may set"
            )
    return dict(raw)


def _identity_of(row: dict) -> tuple:
    """The (session_id, epoch, seq) triple that names one commitment."""
    return tuple(row.get(field) for field in _IDENTITY_FIELDS)


def _sealed_identities(ledger: Path) -> set[tuple]:
    """The identities the ledger already holds, one per parsable row with a seq.

    A torn final line (a crash in the middle of an earlier append) is skipped,
    not fatal: this reader passes over it, and any other reader must too.
    """
    seen: set[tuple] = set()
    if not ledger.is_file():
        return seen
    with ledger.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn line names no commitment
            if isinstance(row, dict) and row.get("seq") is not None:
                seen.add(_identity_of(row))
    return seen


def _ends_mid_line(ledger: Path) -> bool:
    """True when the ledger's last byte is not a newline: a torn tail."""
    if not ledger.is_file() or ledger.stat().st_size == 0:
        return False
    with ledger.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) != b"\n"


def parse_batch(raw: str, batch_id_override: str | None = None) -> tuple[str, list[dict]]:
    """Parse the stdin batch into ``(batch_id, commitments)``.

    Accepts the canonical ``{"batch_id", "commitments"}`` object or a bare JSON
    list of commitments. Raises :class:`SealerRefusal` on anything malformed --
    the sealer fails closed rather than write a half-understood batch.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SealerRefusal(f"batch is not valid JSON: {exc}") from exc

    if isinstance(parsed, list):
        batch_id = batch_id_override or ""
        commitments = parsed
    elif isinstance(parsed, dict):
        batch_id = batch_id_override or str(parsed.get("batch_id", "") or "")
        commitments = parsed.get("commitments")
        if not isinstance(commitments, list):
            raise SealerRefusal("batch object has no 'commitments' list")
    else:
        raise SealerRefusal("batch is neither an object nor a list")

    if not batch_id:
        raise SealerRefusal("batch has no batch_id (give one in the batch or --batch-id)")

    normalised = [_normalise_commitment(c, i) for i, c in enumerate(commitments)]
    return batch_id, normalised


def seal_batch(
    batch_id: str,
    commitments: list[dict],
    ledger: Path,
    *,
    seal_time: str | None = None,
    claude_dir: Path | None = None,
) -> dict:
    """Append ``commitments`` to the scratch ``ledger``; return the summary.

    Refuses (raises :class:`SealerRefusal`) before writing anything when
    ``ledger`` resolves onto the real chain. One JSONL line per commitment, each
    carrying ``batch_id`` and ``seal_time`` beside the commitment's own fields.
    A commitment whose identity ``(session_id, epoch, seq)`` the ledger already
    holds is skipped and counted, so a retried batch is harmless.

    The batch goes out in one write followed by fsync. That is durability, not
    atomicity: a crash in the middle of the write can leave a torn final line,
    which this sealer and every reader skip, and the next append starts on a
    fresh line after it. All-or-nothing framing belongs to the signed chain the
    production sealer feeds, not to this scratch file.
    """
    ledger_path = Path(ledger).expanduser()
    if is_real_chain_target(ledger_path, claude_dir=claude_dir):
        raise SealerRefusal(
            f"refusing to seal onto a real-chain ledger: {ledger_path} "
            "(this spike writes scratch ledgers only)"
        )

    seen = _sealed_identities(ledger_path)
    stamped = seal_time or _utc_now_iso()
    lines = []
    skipped = 0
    for commitment in commitments:
        numbered = commitment.get("seq") is not None
        if numbered and _identity_of(commitment) in seen:
            skipped += 1
            continue
        row = dict(commitment)
        row["batch_id"] = batch_id
        row["seal_time"] = stamped
        lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
        if numbered:
            seen.add(_identity_of(commitment))

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lead = "\n" if lines and _ends_mid_line(ledger_path) else ""
    with ledger_path.open("a", encoding="utf-8") as handle:
        if lines:
            handle.write(lead + "\n".join(lines) + "\n")
            # Durability point B of the design's crash-loss window is the bytes
            # on the disk, not in the OS cache: sync before the summary claims it.
            handle.flush()
            os.fsync(handle.fileno())

    return {
        "sealed": len(lines),
        "skipped": skipped,
        "batch_id": batch_id,
        "seal_time": stamped,
        "ledger": str(ledger_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="witness_sealer.py",
        description=(
            "Append a batch of witness HMAC commitments to a SCRATCH JSONL "
            "ledger. Never writes GRIP's real signed decision chain."
        ),
    )
    parser.add_argument(
        "--ledger",
        required=True,
        help="scratch JSONL ledger to append to (refused if it is a real chain)",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="override the batch_id carried in the stdin batch",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Reads the batch on stdin, seals it, prints the summary."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    raw = sys.stdin.read()
    try:
        batch_id, commitments = parse_batch(raw, args.batch_id)
        summary = seal_batch(batch_id, commitments, Path(args.ledger))
    except SealerRefusal as exc:
        print(f"witness_sealer: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    raise SystemExit(main())
