#!/usr/bin/env python3
"""Locked + atomic read-modify-write for shared genome JSON files.

Closes the last live residual of #3462 (bug #3). GRIP runs 5+ concurrent
``claude`` sessions sharing ``~/.claude``, and several modules do
*read -> mutate one gene's fitness -> write back* on the SAME genome file
(``cache/genome.json`` via the hypothesis bridge; ``data/genomes/*.json`` via
the insights learner and the CC task-metrics bridge). Those writers used a raw
``Path.write_text(json.dumps(...))`` with no lock and no atomic rename, so:

  * two sessions read-modify-writing at once lost one session's gene update
    (last-writer-wins on the shared fitness artefact);
  * a crash mid-write left a half-written file the next reader could not parse
    (torn read -> fitness silently falls back to a default).

Every genome writer now routes through :func:`update_genome` (RMW) or
:func:`save_genome` (full overwrite). Both hold a **per-file sidecar**
``FileLock`` for the WHOLE critical section — for :func:`update_genome` that
span covers the read, the mutation, and the write, which is what actually
closes the lost-update race (a write-only lock still lets two RMW cycles both
read the stale copy first). The write itself lands atomically (unique tmp +
``os.replace``). The lock is keyed to the genome path, so writers of the same
file serialise while writers of different genome files never contend.

Serialisation is canonical: ``json.dumps(genome, indent=2, ensure_ascii=False)``
plus a trailing newline. In-tree genomes carry ASCII gene names, so this is
byte-identical to the pre-fix output for every current caller.

Reference patterns: #3466 (atomic gate sidecars) and #3464 (locked RMW in
``insights_learner.ingest_learnings``) — this generalises both into one shared
genome helper (YSH; the pattern was proven >=4x before earning the abstraction).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

# Path shim so sibling lib modules import both as ``genome_store`` (lib on path)
# and when this file is run directly. Mirrors lib/embeddings/engine.py.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from atomic_write import atomic_write  # noqa: E402
from file_lock import FileLock, LockTimeoutError  # noqa: E402

__all__ = ["update_genome", "save_genome", "LockTimeoutError", "DEFAULT_TIMEOUT"]

# 10s matches the #3464 insights-lock timeout: long enough to outlast a normal
# write under contention from 5+ sessions, short enough never to wedge a hook.
DEFAULT_TIMEOUT: float = 10.0


def _serialise(genome: dict) -> str:
    """Canonical genome JSON text (indent=2, unicode preserved, trailing \\n)."""
    return json.dumps(genome, indent=2, ensure_ascii=False) + "\n"


def save_genome(
    path: Path | str, genome: dict, *, timeout: float = DEFAULT_TIMEOUT
) -> None:
    """Overwrite ``path`` with ``genome`` atomically under an exclusive sidecar
    lock. Use when the full genome is computed independently of the on-disk copy
    (no read-back needed).

    Raises ``LockTimeoutError`` if the lock cannot be acquired within ``timeout``
    seconds, ``OSError`` on write failure. Never leaves a partially written file
    (tmp is discarded on error).
    """
    path = Path(path)
    with FileLock(path, timeout=timeout, sidecar=True):
        atomic_write(path, _serialise(genome))


def update_genome(
    path: Path | str,
    mutate: Callable[[dict], dict],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Atomically apply ``mutate(genome) -> genome`` to the JSON file at ``path``.

    Holds an exclusive sidecar lock across the read, the mutation, and the
    atomic write — the lost-update-safe replacement for the
    ``g = json.loads(path.read_text()); ...; path.write_text(...)`` idiom that
    raced across concurrent sessions.

    ``mutate`` receives the parsed genome dict and returns the dict to persist
    (mutating in place then returning it is fine). Returns the persisted dict.

    Propagates (callers keep their existing handling by catching these — the
    pre-fix code already guarded read/write with the same types):
      * ``FileNotFoundError`` — ``path`` absent;
      * ``json.JSONDecodeError`` / ``UnicodeDecodeError`` — on-disk genome
        unparseable (e.g. git-crypt ciphertext);
      * ``LockTimeoutError`` — lock contention beyond ``timeout``;
      * ``OSError`` — read or write I/O failure.
    """
    path = Path(path)
    with FileLock(path, timeout=timeout, sidecar=True):
        genome = json.loads(path.read_text(encoding="utf-8"))
        genome = mutate(genome)
        atomic_write(path, _serialise(genome))
    return genome
