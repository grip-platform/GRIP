#!/usr/bin/env python3
"""
genome_insights.py -- Plain-language genome insight layer.

Deliverable A: explain_leaderboard(genome, n) -> str
Deliverable B: explain_changes(genome_a, genome_b, n) -> str
             + explain_timeline(snapshots) -> str
Shared helper: _contribution_why(gene) -> str
CLI: python lib/genome_insights.py leaderboard|changes|timeline [--n N]

Primary Author: V>>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_CLAUDE_DIR_ENV = os.environ.get("CLAUDE_DIR", "").strip()
CLAUDE_DIR = Path(_CLAUDE_DIR_ENV).expanduser() if _CLAUDE_DIR_ENV else Path.home() / ".claude"

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from genome_diff import GenomeDiff, diff_genomes, load_full_genome, load_genome_snapshots  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _entry_success(entry) -> bool:
    """Return True if a contribution_record entry represents a success."""
    return isinstance(entry, (list, tuple)) and len(entry) >= 2 and bool(entry[1])


def _contribution_why(gene: dict) -> str:
    """Plain-English WHY from contribution_record.

    Returns e.g. "9 of 9 recent uses succeeded" or "seeded, not yet used".
    """
    record = gene.get("contribution_record") or []
    if not record:
        return "seeded, not yet used"
    total = len(record)
    successes = sum(1 for e in record if _entry_success(e))
    return f"{successes} of {total} recent uses succeeded"


def _safe_fitness(gene: dict) -> Optional[float]:
    """Return fitness as float, or None if absent/None/invalid."""
    v = gene.get("fitness")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fit_str(gene: dict) -> str:
    """Fitness formatted to .3f, or 'no fitness recorded'."""
    f = _safe_fitness(gene)
    return f"{f:.3f}" if f is not None else "no fitness recorded"


# ---------------------------------------------------------------------------
# Deliverable A: leaderboard
# ---------------------------------------------------------------------------

def _partition_genes(genes: dict) -> tuple[list[tuple[str, dict]], int]:
    """Split genes into (active_sorted_desc, dormant_count)."""
    active: list[tuple[str, dict]] = []
    dormant = 0
    for name, gene in genes.items():
        if not isinstance(gene, dict):
            continue
        if gene.get("contribution_record"):
            active.append((name, gene))
        else:
            dormant += 1
    active.sort(key=lambda x: (_safe_fitness(x[1]) or 0.0), reverse=True)
    return active, dormant


def explain_leaderboard(genome: dict, n: int = 10) -> str:
    """Top-N active skills by fitness, plus dormant-tail summary."""
    genes: dict = genome.get("genes") or {}
    generation = genome.get("generation", "?")
    total = len(genes)
    active, dormant_count = _partition_genes(genes)

    lines: list[str] = [
        f"Genome Gen {generation} — {total} skills tracked "
        f"({len(active)} active, {dormant_count} dormant)",
        "",
        f"Top {min(n, len(active))} active skills by fitness:",
    ]
    for rank, (name, gene) in enumerate(active[:n], start=1):
        ctype = gene.get("component_type", "unknown")
        lines.append(
            f"  {rank:2d}. {name} ({ctype})  fitness={_fit_str(gene)}  — {_contribution_why(gene)}"
        )
    if dormant_count:
        lines += [
            "",
            f"Dormant tail: {dormant_count} skills have never been used "
            f"(seeded but not yet exercised in any session).",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deliverable B: changes over time — decomposed for CC compliance
# ---------------------------------------------------------------------------

# Classification thresholds (named, not magic). "Newly active" = a gene crosses
# UP through the active line (0.70). "Newly dormant" = it falls from a
# non-trivial fitness (>= 0.10) to ~zero (< 0.01); the dormant pair is a
# hysteresis band so a gene hovering near zero doesn't flip-flop. The two are
# intentionally different bands, not one shared threshold (AGORA #3401 r2).
_NEWLY_ACTIVE_AT = 0.70   # nw >= this AND old < this -> crossed into active
_DORMANT_FROM = 0.10      # old >= this ...
_DORMANT_TO = 0.01        # ... and nw < this -> newly dormant


def _classify_changes(diff: GenomeDiff):
    """Return (risers, fallers, newly_active, newly_dormant) from a diff.

    risers/fallers are always 4-tuples (name, old, new, delta) — the shape
    _format_change_rows unpacks. newly_active/newly_dormant are name lists.
    """
    changes = sorted(
        diff.fitness_changes.items(),
        key=lambda x: abs(x[1][2]),
        reverse=True,
    )
    risers = [(n, o, nw, d) for n, (o, nw, d) in changes if d > 0]
    fallers = [(n, o, nw, d) for n, (o, nw, d) in changes if d < 0]
    newly_active = [
        n for n, (o, nw, _) in diff.fitness_changes.items()
        if nw >= _NEWLY_ACTIVE_AT and o < _NEWLY_ACTIVE_AT
    ]
    newly_dormant = [
        n for n, (o, nw, _) in diff.fitness_changes.items()
        if o >= _DORMANT_FROM and nw < _DORMANT_TO
    ]
    return risers, fallers, newly_active, newly_dormant


def _format_change_rows(rows: list, genes_b: dict, sign: str) -> list[str]:
    """Render riser/faller rows with WHY from genes_b.

    ``rows`` are 4-tuples (name, old, new, delta) by construction from
    _classify_changes (risers/fallers) — its only caller.
    """
    return [
        f"  {sign} {name}  {old:.3f}→{new:.3f} ({delta:+.3f})  — {_contribution_why(genes_b.get(name) or {})}"
        for name, old, new, delta in rows
    ]


def explain_changes(genome_a: dict, genome_b: dict, n: int = 10) -> str:
    """Top-N risers/fallers by |delta|, salient-findings line, WHY from genome_b."""
    diff: GenomeDiff = diff_genomes(genome_a, genome_b)
    genes_b: dict = genome_b.get("genes") or {}
    risers, fallers, newly_active, newly_dormant = _classify_changes(diff)

    lines: list[str] = [
        f"Genome changes: Gen {diff.generation_a} → Gen {diff.generation_b}",
        "",
        f"Salient: {len(risers)} skills rose, {len(fallers)} fell, "
        f"{len(newly_active)} newly crossed into active (≥0.70), "
        f"{len(newly_dormant)} newly dormant. "
        f"Added: {len(diff.genes_added)}, Removed: {len(diff.genes_removed)}.",
    ]
    if newly_active:
        lines.append(f"  ↑ newly active: {', '.join(newly_active[:n])}")
    if newly_dormant:
        lines.append(f"  ↓ newly dormant: {', '.join(newly_dormant[:n])}")
    if risers:
        lines += ["", f"Top {min(n, len(risers))} risers:"]
        lines += _format_change_rows(risers[:n], genes_b, "+")
    if fallers:
        lines += ["", f"Top {min(n, len(fallers))} fallers:"]
        lines += _format_change_rows(fallers[:n], genes_b, "-")
    if diff.genes_added:
        tail = " …" if len(diff.genes_added) > 5 else ""
        lines.append(f"\nAdded ({len(diff.genes_added)}): {', '.join(diff.genes_added[:5])}{tail}")
    if diff.genes_removed:
        tail = " …" if len(diff.genes_removed) > 5 else ""
        lines.append(f"Removed ({len(diff.genes_removed)}): {', '.join(diff.genes_removed[:5])}{tail}")
    return "\n".join(lines)


def explain_timeline(snapshots: list[dict]) -> str:
    """One plain sentence on avg-fitness trajectory over the snapshot window.

    Order-independent: snapshots are sorted by (generation, timestamp) here, so
    an unsorted caller cannot produce a reversed delta or wrong trend label
    (AGORA #3401 r2). load_genome_snapshots already sorts; this makes the
    function correct for any caller regardless.
    """
    if not snapshots:
        return "Timeline unavailable: no genome snapshots found."
    ordered = sorted(
        snapshots,
        key=lambda s: (s.get("generation", 0) or 0, s.get("timestamp", "") or ""),
    )
    fits = [s.get("avg_fitness") for s in ordered if s.get("avg_fitness") is not None]
    if not fits:
        return "Timeline unavailable: no fitness data in snapshots."
    first, last, n = fits[0], fits[-1], len(fits)
    delta = last - first
    if abs(delta) < 0.005:
        trend, direction = "flat", "holding steady"
    elif delta > 0:
        trend, direction = "rising", f"rising by {delta:.3f}"
    else:
        trend, direction = "falling", f"falling by {abs(delta):.3f}"
    gen_first = ordered[0].get("generation", "?")
    gen_last = ordered[-1].get("generation", "?")
    return (
        f"Over {n} snapshots (Gen {gen_first}→{gen_last}), avg fitness is {direction} "
        f"({first:.3f}→{last:.3f}); overall trend: {trend}."
    )


def _snap_num(snap: dict, key: str) -> Optional[float]:
    """Numeric field from a summary snapshot, or None."""
    v = snap.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def explain_summary_changes(snap_a: dict, snap_b: dict) -> str:
    """Plain summary-level change between two genome-history snapshots.

    History snapshots are SUMMARY-ONLY (avg_fitness, living/dead counts,
    success_rate) — they carry NO per-gene data, so genuine per-gene deltas
    are not available here. For per-gene "skill X rose 0.50→1.00", use
    explain_changes() on two FULL genomes, or the per-gene IDR ledger (the
    next deliverable). This reports the summary-level movement honestly.
    Total over input: every field via .get(); missing fields render
    "(not recorded)" rather than crashing or fabricating a zero.
    """
    ga = snap_a.get("generation", "?")
    gb = snap_b.get("generation", "?")
    lines = [f"Genome summary change: Gen {ga} → Gen {gb}", ""]
    rows = [
        ("avg fitness", "avg_fitness", "{:.3f}"),
        ("living skills", "living_count", "{:.0f}"),
        ("dormant/dead", "dead_count", "{:.0f}"),
        ("success rate", "success_rate", "{:.3f}"),
    ]
    for label, key, fmt in rows:
        a, b = _snap_num(snap_a, key), _snap_num(snap_b, key)
        if a is None or b is None:
            lines.append(f"  {label}: (not recorded)")
        else:
            lines.append(f"  {label}: {fmt.format(a)} → {fmt.format(b)} ({b - a:+.3f})")
    lines += [
        "",
        "Note: history snapshots are summary-only — per-gene rise/fall detail "
        "requires the per-gene change ledger (IDR-backed, next deliverable).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI loaders (fail-soft)
# ---------------------------------------------------------------------------

def _load_genome_cli() -> Optional[dict]:
    try:
        return load_full_genome(CLAUDE_DIR / "cache" / "genome.json")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _load_snapshots_cli(window: int = 50) -> list[dict]:
    try:
        snaps = load_genome_snapshots(history_path=CLAUDE_DIR / "cache" / "genome-history.jsonl")
        return snaps[-window:] if len(snaps) > window else snaps
    except (OSError, json.JSONDecodeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Genome plain-language insights")
    parser.add_argument("command", choices=["leaderboard", "changes", "timeline"])
    parser.add_argument("--n", type=int, default=10, help="Top-N items to show")
    args = parser.parse_args()

    if args.command == "leaderboard":
        genome = _load_genome_cli()
        if not genome:
            print("Genome unavailable: cache/genome.json missing or unreadable.")
            return
        print(explain_leaderboard(genome, n=args.n))

    elif args.command == "changes":
        # History snapshots are summary-only, so CLI 'changes' reports the
        # honest summary-level delta between two snapshots — NOT a per-gene
        # diff (which needs two full genomes; explain_changes serves that).
        snaps = _load_snapshots_cli(window=200)
        if len(snaps) < 2:
            print("Genome change history unavailable: need at least 2 snapshots.")
            return
        print(explain_summary_changes(snaps[0], snaps[-1]))

    elif args.command == "timeline":
        print(explain_timeline(_load_snapshots_cli()))


if __name__ == "__main__":
    main()
