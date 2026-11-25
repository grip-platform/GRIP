"""Congruent Continuity — the post-hoc SETTLEMENT WORKER (one pass).

Composes the two settlers and closes the analysis loop the genesis names for
W3 (``docs/congruent-continuity-genesis.md`` "W3: post-hoc settlement worker
— deterministic probe floor + Pool-B triage; contamination computation"):

1. **Floor first** — ``lib/continuity_settle.py`` settles every ``probe:``
   falsifier deterministically (zero model cost, highest precision).
2. **Council second** — ``lib/continuity_council.py`` settles what a machine
   cannot, through an injectable council function. ``council_fn=None`` runs
   the floor only (offline; the prose worklist is reported, not settled).
3. **Contamination on every refutation** — for each INCONGRUENT verdict this
   pass wrote, ``contamination()`` is computed and appended as one row to the
   contamination log (``state/precog/continuity-contamination.jsonl``,
   session-namespaced like the chain). The log is a derived RCA surface:
   the truth is recomputable from the signed chain (``suspect()``), so the
   log is never signed and never authoritative — it is the record of WHEN
   each closure was first computed and by which verdict.

The corpus D3 needs ("a MEASURED checker-precision corpus") is the chain's
own verdict records — signed, and already the settlement side of the join;
the prediction side is the WARN-only hook's audit log
(``logs/continuity-watch.jsonl``). No duplicate corpus file is written.

``dry_run`` executes no probe, convenes no council, and writes nothing.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from lib.continuity_chain import (
    _STATE,
    _session_path,
    contamination,
    settlement_of,
)
from lib.continuity_council import CouncilFn, settle_council, sovereign_council_fn
from lib.continuity_settle import settle_floor

__all__ = [
    "contamination_log_path",
    "record_contamination",
    "read_contamination",
    "run_pass",
]

_DEFAULT_LOG_PATH = _STATE / "continuity-contamination.jsonl"


def contamination_log_path(log_path: Path | None = None) -> Path:
    """Resolve the contamination log (session-namespaced, same rule as the chain)."""
    return log_path or _session_path(_DEFAULT_LOG_PATH)


def record_contamination(
    refuted_addr: str,
    *,
    verdict_id: str | None = None,
    path: Path | None = None,
    log_path: Path | None = None,
) -> dict:
    """Compute ``contamination(refuted_addr)`` and append it as one JSONL row.
    Returns the row written. fsync'd so a crash after the settlement cannot
    lose the closure that settlement implied."""
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "refuted_addr": refuted_addr,
        "verdict_id": verdict_id,
        "suspect": list(contamination(refuted_addr, path=path)),
    }
    lp = contamination_log_path(log_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return row


def read_contamination(log_path: Path | None = None) -> list[dict]:
    """All contamination rows in file order; a missing log reads as empty."""
    lp = contamination_log_path(log_path)
    if not lp.exists():
        return []
    rows: list[dict] = []
    for line in lp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _settled(rows: list[dict], verifier_default: str | None = None) -> list[dict]:
    out = []
    for r in rows:
        if r.get("action") == "settled":
            out.append({**r, "verifier": r.get("verifier", verifier_default)})
    return out


def run_pass(
    *,
    council_fn: CouncilFn | None = sovereign_council_fn,
    path: Path | None = None,
    head_pointer: Path | None = None,
    cwd: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    log_path: Path | None = None,
    contamination_log: Path | None = None,
) -> dict:
    """One worker pass: floor -> council -> contamination. Returns a report
    ``{settled, dry_run, left_for_council, unavailable, contamination}``."""
    floor_rows = settle_floor(path=path, head_pointer=head_pointer, cwd=cwd, limit=limit, dry_run=dry_run)
    council_rows = (
        settle_council(council_fn=council_fn, path=path, head_pointer=head_pointer, limit=limit, dry_run=dry_run)
        if council_fn is not None else []
    )
    settled = _settled(floor_rows, "deterministic") + _settled(council_rows)
    if council_fn is None:
        left = [r["commit_addr"] for r in floor_rows if r.get("action") == "left-for-council"]
    else:
        left = [r["commit_addr"] for r in council_rows if r.get("action") in ("council-unavailable", "no-quorum")]
    log = contamination_log if contamination_log is not None else log_path
    closures = []
    if not dry_run:
        for r in settled:
            if r["verdict"] == "INCONGRUENT":
                v = settlement_of(r["commit_addr"], path=path)
                closures.append(record_contamination(
                    r["commit_addr"], verdict_id=getattr(v, "id", None), path=path, log_path=log))
    return {
        "settled": settled,
        "dry_run": [r for r in floor_rows + council_rows if r.get("action") == "dry-run"],
        "left_for_council": left,
        "unavailable": [r for r in council_rows if r.get("action") in ("council-unavailable", "no-quorum")],
        "contamination": closures,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="continuity_worker")
    p.add_argument("--chain", help="chain file override (tests)")
    p.add_argument("--head", help="HEAD pointer override (tests)")
    p.add_argument("--cwd", help="probe working directory")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true", help="execute no probe, convene nothing, write nothing")
    p.add_argument("--no-council", action="store_true", help="floor only (offline); report the prose worklist")
    p.add_argument("--contamination-log", dest="contamination_log", help="log file override (tests)")
    ns = p.parse_args(argv)
    report = run_pass(
        council_fn=None if ns.no_council else sovereign_council_fn,
        path=Path(ns.chain) if ns.chain else None,
        head_pointer=Path(ns.head) if ns.head else None,
        cwd=Path(ns.cwd) if ns.cwd else None,
        limit=ns.limit,
        dry_run=ns.dry_run,
        contamination_log=Path(ns.contamination_log) if ns.contamination_log else None,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
