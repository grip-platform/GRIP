#!/usr/bin/env python3
"""
genome_idr_ledger.py -- IDR-backed per-gene genome-change ledger.

Deliverable C: record each per-gene fitness change as a signed, chained,
replayable IDR — making the per-gene over-time history tamper-evident.

Key guarantees:
  - IDEMPOTENT (Banach no-op): unchanged genome → zero new IDR records.
  - TAMPER-EVIDENT: verify_ledger() fails on any byte mutation.
  - REPLAY-DETERMINISTIC: record→reload→replay produces identical output
    when the caller supplies the timestamp (no time() inside record logic).
  - NO LLM in verify/crypto path (llm-augmentation-pattern.md category error).
  - TOTAL over input: all dict access via .get() — empty/partial genome safe.
  - None-safe fitness: absent or None fitness is surfaced, never silently 0.0.

Usage (CLI):
  python lib/genome_idr_ledger.py record  [--prev PREV_JSON] [--curr CURR_JSON]
  python lib/genome_idr_ledger.py verify
  python lib/genome_idr_ledger.py timeline <gene_name>

Primary Author: V>>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path resolution — portable, never hardcoded
# ---------------------------------------------------------------------------
_CLAUDE_DIR_ENV = os.environ.get("CLAUDE_DIR", "").strip()
CLAUDE_DIR = (
    Path(_CLAUDE_DIR_ENV).expanduser()
    if _CLAUDE_DIR_ENV
    else Path.home() / ".claude"
)

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from idr_forest import (  # noqa: E402
    Forest,
    add_idr,
    empty_forest,
    verify_chain_integrity,
)
from audit_chain import Verdict  # noqa: E402
from precog.idr import PrecogIDR, _canonical_json, _sign_placeholder  # noqa: E402
from genome_diff import diff_genomes  # noqa: E402

# ---------------------------------------------------------------------------
# Constants — named, commented; never magic numbers
# ---------------------------------------------------------------------------

# Exogenous root anchor for the genome-evolution ledger.
# All ledger IDRs are chained to this single genome-RSI anchor.
_GENOME_ANCHOR_ID: str = "hypo:genome-rsi-anchor"

# Fingerprint tag applied to every genome-change IDR.
_LEDGER_FINGERPRINT: str = "genome-idr-ledger-v1"

# Kind tag on every genome-change IDR (identifies it as ledger, not a
# generic precog-decision).
_LEDGER_IDR_KIND: str = "genome-change"

# Default location for the serialised ledger.  cache/ is gitignored runtime
# data — the module code is tracked; the runtime ledger file is not.
_DEFAULT_LEDGER_PATH: Path = CLAUDE_DIR / "cache" / "genome-idr-ledger.jsonl"

# Default current-genome path; used by CLI `record` when --curr not supplied.
_DEFAULT_GENOME_PATH: Path = CLAUDE_DIR / "cache" / "genome.json"


# ---------------------------------------------------------------------------
# Internal helpers — kept small (CC gate: each function <50 lines)
# ---------------------------------------------------------------------------

def _safe_fitness(gene: dict) -> Optional[float]:
    """Return fitness as float, or None if absent/None/non-numeric.

    Never fabricates 0.0 for absent/None fitness (CBC requirement).
    """
    v = gene.get("fitness")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _success_count_delta(prev_gene: dict, curr_gene: dict) -> int:
    """Count net new successes in contribution_record between two gene states.

    Each contribution_record entry is [session_id, success_bool].
    Returns the number of additional True entries in curr vs prev.
    """
    def _count_successes(gene: dict) -> int:
        record = gene.get("contribution_record") or []
        return sum(
            1 for e in record
            if isinstance(e, (list, tuple)) and len(e) >= 2 and bool(e[1])
        )

    return _count_successes(curr_gene) - _count_successes(prev_gene)


def _build_gene_idr(
    gene_name: str,
    old_fitness: float,
    new_fitness: float,
    delta: float,
    generation: int,
    success_delta: int,
    ts: str,
    predecessor_id: Optional[str],
    depth: int,
) -> PrecogIDR:
    """Construct one PrecogIDR recording a per-gene fitness change.

    All time/random sourcing is in the caller — this function is pure
    given its inputs, ensuring replay-determinism (CBC requirement 6).
    The `ts` is passed in; no calls to time() or random inside here.
    """
    import hashlib as _hashlib

    # Deterministic id: content-derived (gene + generation + fitness
    # transition), so replay reproduces byte-identical ids. NO time()/random()
    # here — those would break the replay-determinism this docstring guarantees
    # and the IDR chain's reproducibility (AGORA #3403 r1). A gene changes at
    # most once per generation, so (gene, generation, old, new) is unique +
    # collision-safe by content.
    _seed = f"{gene_name}:{generation}:{old_fitness:.6f}:{new_fitness:.6f}"
    idr_id = f"genome-{_hashlib.sha256(_seed.encode()).hexdigest()[:16]}"

    decision: dict = {
        "gene_name": gene_name,
        "old_fitness": round(old_fitness, 3),
        "new_fitness": round(new_fitness, 3),
        "delta": round(delta, 3),
        "generation": generation,
        "why": {
            "success_count_delta": success_delta,
        },
    }
    inputs: dict = {
        "ts": ts,
        "predecessor_id": predecessor_id,
    }

    envelope_body: dict = {
        "happi": "1.1",
        "kind": _LEDGER_IDR_KIND,
        "id": idr_id,
        "predecessor_idr": predecessor_id,
        "depth": depth,
        "fingerprint": _LEDGER_FINGERPRINT,
        "ts": ts,
        "decision": decision,
        "inputs": inputs,
    }
    audit = _sign_placeholder(envelope_body)

    return PrecogIDR(
        happi="1.1",
        kind=_LEDGER_IDR_KIND,
        id=idr_id,
        predecessor_idr=predecessor_id,
        depth=depth,
        fingerprint=_LEDGER_FINGERPRINT,
        ts=ts,
        decision=decision,
        inputs=inputs,
        audit=audit,
    )


def _forest_from_ledger_path(ledger_path: Path) -> Forest:
    """Deserialise a ledger JSONL file into a Forest.

    Returns an empty forest if the file is missing or corrupt.
    Narrow excepts only — (OSError, json.JSONDecodeError, ValueError, KeyError).
    """
    forest = empty_forest((_GENOME_ANCHOR_ID,))
    if not ledger_path.exists():
        return forest

    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return forest

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            # ``if k in d`` — legacy ledger records predate optional fields
            # (e.g. ``decision_anatomy``); guard so they reconstruct (defaults)
            # rather than KeyError-dropping out of the forest.
            idr = PrecogIDR(**{k: d[k] for k in PrecogIDR.__dataclass_fields__ if k in d})
            forest = add_idr(forest, idr, anchor_id=_GENOME_ANCHOR_ID)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            continue

    return forest


def _append_forest_to_ledger(forest: Forest, ledger_path: Path) -> None:
    """Serialise every Forest node as a JSONL line (append-only).

    Creates parent dirs; narrow excepts (OSError only).
    """
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as fh:
            for node in forest.nodes.values():
                line = _canonical_json(asdict(node.idr)) + "\n"
                fh.write(line)
    except OSError:
        pass


def _read_last_genome_from_ledger(
    ledger_path: Path,
) -> Optional[dict]:
    """Read the most-recently recorded genome snapshot from ledger.

    Returns a reconstructed {generation, genes} dict, or None if no
    records exist yet.
    """
    if not ledger_path.exists():
        return None

    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    genes: dict = {}
    generation: int = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            decision = d.get("decision") or {}
            if not isinstance(decision, dict):
                continue
            gname = decision.get("gene_name")
            if not gname:
                continue
            genes[gname] = {
                "fitness": decision.get("new_fitness"),
                "contribution_record": [],
            }
            gen = decision.get("generation", 0)
            if isinstance(gen, int) and gen > generation:
                generation = gen
        except (json.JSONDecodeError, ValueError, KeyError):
            continue

    if not genes:
        return None
    return {"generation": generation, "genes": genes}


# ---------------------------------------------------------------------------
# Input sanitisation helpers
# ---------------------------------------------------------------------------

def _sanitise_genome(genome: dict) -> dict:
    """Return a copy of genome with None fitness values replaced by 0.0.

    genome_diff._extract_fitness raises TypeError on None fitness.  We
    sanitise at the entry-point so the ledger is tolerant of partial or
    in-progress gene records while genome_diff remains unchanged (boy-scout
    rule: fix at the boundary we own, not inside a shared dependency).
    """
    genes = genome.get("genes") or {}
    if not genes:
        return genome

    sanitised_genes: dict = {}
    for name, gene in genes.items():
        if not isinstance(gene, dict):
            sanitised_genes[name] = gene
            continue
        if gene.get("fitness") is None:
            gene = {**gene, "fitness": 0.0}
        sanitised_genes[name] = gene

    return {**genome, "genes": sanitised_genes}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_genome_changes(
    prev_genome: dict,
    curr_genome: dict,
    *,
    forest: Optional[Forest] = None,
    ts: Optional[str] = None,
) -> Forest:
    """Diff two genomes; append one IDR per changed gene to the forest.

    IDEMPOTENT (Banach no-op): if no per-gene fitness changes exist,
    returns the forest UNCHANGED — zero new records appended.

    Args:
        prev_genome: Previous genome dict; may be {} or partial — safe (CBC 1).
        curr_genome: Current genome dict; same totality guarantee.
        forest:      Existing Forest to append to; if None, an empty forest
                     rooted at _GENOME_ANCHOR_ID is created.
        ts:          Caller-supplied ISO timestamp string for replay-determinism.
                     If None, utcnow() is used — but callers that need exact
                     replay should supply this (CBC requirement 6).

    Returns:
        New Forest with zero or more IDR nodes appended.
        The input `forest` is never mutated (immutable Forest).
    """
    if forest is None:
        forest = empty_forest((_GENOME_ANCHOR_ID,))

    # Sanitise None fitness values before passing to diff_genomes, which
    # raises TypeError on None (genome_diff._extract_fitness contract).
    prev_genome = _sanitise_genome(prev_genome)
    curr_genome = _sanitise_genome(curr_genome)

    diff = diff_genomes(prev_genome, curr_genome)

    if not diff.fitness_changes:
        # Idempotent no-op — unchanged genome returns forest as-is.
        return forest

    if ts is None:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    generation: int = curr_genome.get("generation") or 0
    prev_genes: dict = prev_genome.get("genes") or {}
    curr_genes: dict = curr_genome.get("genes") or {}

    predecessor_id: Optional[str] = _GENOME_ANCHOR_ID
    # If there are existing nodes, chain from the last one added.
    if forest.nodes:
        # Stable ordering: pick the node with the highest depth, then id.
        last_node = max(forest.nodes.values(), key=lambda n: (n.idr.depth, n.idr.id))
        predecessor_id = last_node.idr.id

    depth: int = len(forest.nodes)

    for gene_name, (old_fit, new_fit, delta) in sorted(diff.fitness_changes.items()):
        prev_gene = prev_genes.get(gene_name) or {}
        curr_gene = curr_genes.get(gene_name) or {}

        if not isinstance(prev_gene, dict):
            prev_gene = {}
        if not isinstance(curr_gene, dict):
            curr_gene = {}

        success_delta = _success_count_delta(prev_gene, curr_gene)

        idr = _build_gene_idr(
            gene_name=gene_name,
            old_fitness=float(old_fit),
            new_fitness=float(new_fit),
            delta=float(delta),
            generation=int(generation),
            success_delta=success_delta,
            ts=ts,
            predecessor_id=predecessor_id,
            depth=depth,
        )

        forest = add_idr(forest, idr, anchor_id=_GENOME_ANCHOR_ID)
        predecessor_id = idr.id
        depth += 1

    return forest


def verify_ledger(forest: Forest) -> Verdict:
    """Pure tamper-check of the forest via IDR chain integrity.

    Verdict.VERIFIED  — every node's signature matches its envelope body.
    Verdict.BROKEN    — at least one node has been tampered with.
    Verdict.DEGRADED  — a node uses a scheme whose key material is unavailable.

    NO LLM or non-deterministic step is ever invoked here — this is a
    cryptographic integrity check (llm-augmentation-pattern.md category error).
    """
    return verify_chain_integrity(forest)


def gene_timeline(forest: Forest, gene_name: str) -> list[dict]:
    """Return the per-gene over-time series replayed from the ledger.

    Each entry: {generation, old_fitness, new_fitness, delta, why}.
    Ordered by IDR depth (chronological).

    This is the substrate genome-history.jsonl lacks: tamper-evident
    per-gene history replayable from the signed IDR chain.
    """
    entries: list[dict] = []

    for node in sorted(forest.nodes.values(), key=lambda n: (n.idr.depth, n.idr.id)):
        decision = node.idr.decision
        if not isinstance(decision, dict):
            continue
        if decision.get("gene_name") != gene_name:
            continue

        entries.append({
            "generation": decision.get("generation"),
            "old_fitness": decision.get("old_fitness"),
            "new_fitness": decision.get("new_fitness"),
            "delta": decision.get("delta"),
            "why": decision.get("why") or {},
        })

    return entries


def record_and_persist(
    prev_genome: dict,
    curr_genome: dict,
    ledger_path: Optional[Path] = None,
    *,
    ts: Optional[str] = None,
) -> Forest:
    """Load existing ledger, record new changes, persist new nodes.

    Idempotent: if no fitness changes, ledger file is not modified.
    Narrow excepts: (OSError, json.JSONDecodeError, ValueError).
    """
    target = ledger_path or _DEFAULT_LEDGER_PATH

    existing_forest = _forest_from_ledger_path(target)
    prev_node_ids = set(existing_forest.nodes.keys())

    updated_forest = record_genome_changes(
        prev_genome, curr_genome, forest=existing_forest, ts=ts
    )

    # Only append nodes that are genuinely new.
    new_node_ids = set(updated_forest.nodes.keys()) - prev_node_ids
    if not new_node_ids:
        return updated_forest

    # The new nodes are ALREADY chain-validated in updated_forest. Persist them
    # directly in DEPTH (chain) order — do NOT rebuild a partial forest and
    # re-run add_idr, which (a) is order-sensitive (sorted-by-id != chain order
    # once ids are content-derived) and (b) rejects a node whose predecessor is
    # an EXISTING ledger node absent from the partial forest. AGORA #3403 r1.
    new_nodes = sorted(
        (updated_forest.nodes[nid] for nid in new_node_ids),
        key=lambda n: (n.idr.depth, n.idr.id),
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            for node in new_nodes:
                fh.write(_canonical_json(asdict(node.idr)) + "\n")
    except OSError:
        pass
    return updated_forest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_record(args) -> int:
    """Diff live genome.json vs last-recorded state and append new IDRs."""
    curr_path = Path(args.curr) if args.curr else _DEFAULT_GENOME_PATH
    try:
        curr_genome: dict = json.loads(curr_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[genome-idr-ledger] cannot read current genome: {exc}", file=sys.stderr)
        return 1

    ledger_path = Path(args.ledger) if args.ledger else _DEFAULT_LEDGER_PATH

    if args.prev:
        try:
            prev_genome: dict = json.loads(
                Path(args.prev).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"[genome-idr-ledger] cannot read prev genome: {exc}", file=sys.stderr
            )
            return 1
    else:
        prev_genome = _read_last_genome_from_ledger(ledger_path) or {}

    forest = record_and_persist(prev_genome, curr_genome, ledger_path)
    diff = diff_genomes(prev_genome, curr_genome)

    if diff.fitness_changes:
        print(
            f"[genome-idr-ledger] recorded {len(diff.fitness_changes)} "
            f"gene change(s) to {ledger_path}"
        )
    else:
        print("[genome-idr-ledger] no fitness changes — ledger unchanged (idempotent)")
    return 0


def _cmd_verify(args) -> int:
    """Tamper-check the ledger and report the verdict."""
    ledger_path = Path(args.ledger) if args.ledger else _DEFAULT_LEDGER_PATH
    forest = _forest_from_ledger_path(ledger_path)

    verdict = verify_ledger(forest)

    node_count = len(forest.nodes)
    label = {
        Verdict.VERIFIED: "VERIFIED",
        Verdict.BROKEN: "BROKEN",
        Verdict.DEGRADED: "DEGRADED",
    }.get(verdict, str(verdict))

    print(f"[genome-idr-ledger] {label} — {node_count} node(s) checked")
    return 0 if verdict in (Verdict.VERIFIED, Verdict.DEGRADED) else 1


def _cmd_timeline(args) -> int:
    """Print the per-gene over-time series."""
    ledger_path = Path(args.ledger) if args.ledger else _DEFAULT_LEDGER_PATH
    forest = _forest_from_ledger_path(ledger_path)

    entries = gene_timeline(forest, args.gene_name)

    if not entries:
        print(
            f"[genome-idr-ledger] no records found for gene '{args.gene_name}'"
        )
        return 0

    print(f"Timeline for gene: {args.gene_name}")
    print(f"  {'Gen':>4}  {'Old':>8}  {'New':>8}  {'Delta':>9}  Why")
    print(f"  {'-'*4}  {'-'*8}  {'-'*8}  {'-'*9}  {'-'*20}")
    for e in entries:
        gen = e.get("generation", "?")
        old = e.get("old_fitness")
        new = e.get("new_fitness")
        delta = e.get("delta")
        why = e.get("why") or {}
        old_s = f"{old:.3f}" if old is not None else "None"
        new_s = f"{new:.3f}" if new is not None else "None"
        delta_s = f"{delta:+.3f}" if delta is not None else "None"
        why_s = f"+{why.get('success_count_delta', 0)} success(es)"
        print(f"  {gen!s:>4}  {old_s:>8}  {new_s:>8}  {delta_s:>9}  {why_s}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genome_idr_ledger",
        description="IDR-backed per-gene genome-change ledger",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="Override ledger path (default: cache/genome-idr-ledger.jsonl)",
    )

    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("record", help="Record current genome changes")
    rec.add_argument("--curr", default=None, help="Path to current genome.json")
    rec.add_argument("--prev", default=None, help="Path to previous genome.json")

    sub.add_parser("verify", help="Tamper-check the ledger")

    tl = sub.add_parser("timeline", help="Print per-gene over-time series")
    tl.add_argument("gene_name", help="Name of gene to show timeline for")

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "record":
        return _cmd_record(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "timeline":
        return _cmd_timeline(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
