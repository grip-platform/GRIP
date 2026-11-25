"""GRIP Dream Cycle — nightly maintenance pipeline for memory substrate.

Inspired by Thoth (siddsachar/Thoth) Dream Cycle but applied to GRIP's
KONO + insights + MEMORY.md substrates. 5-phase sequential pipeline
with --dry-run mode for safety. Every mutation is journaled.

Phases (executed in order — order encodes dependency):

1. similarity_dedup   — find KONO entries with high cosine similarity (>=0.93)
2. stale_decay        — age learning/insights.jsonl entries by mtime
3. hub_cap            — flag MEMORY.md hubs with > MAX_HUB_REFS inbound links
4. rejection_log      — track rejected inferences (prevents retry loops)
5. dream_journal      — append-only audit of every mutation across phases

Anti-contamination protocol:
- --dry-run by default; mutations gated behind --apply (v1: dry-run only)
- Every change journaled BEFORE being applied
- Layered: phase output cannot corrupt phase input

Score gate: each phase returns PhaseResult{phase, status, findings, fixes}.

Usage:
    python3 lib/grip_dream_cycle.py                # dry-run, all phases
    python3 lib/grip_dream_cycle.py --phase hub_cap # one phase only
    python3 lib/grip_dream_cycle.py --json         # machine-readable

Origin: Wave 6 of /absorb-knowledge sprint 2026-05-03.
Master hypothesis H316; cluster B sub-hypothesis H318 (Thoth Dream Cycle
to GRIP dream-cycle Gentner mapping >= 60) confirmed at 100/100.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from path_encoding import encode_project_path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Tunables — constants, not LLM-configured
DECAY_THRESHOLD_DAYS = 90.0       # entries older than this get decayed
DECAY_FACTOR = 0.95               # multiplier applied per cycle
MAX_HUB_REFS = 10                 # MEMORY.md links exceeding this flagged
SIMILARITY_THRESHOLD = 0.93       # KONO cosine similarity for dedup proposals

REJECTION_LOG_PATH = "data/dream_rejection.jsonl"
JOURNAL_PATH = "data/dream_journal.jsonl"

HUB_REF_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


@dataclass
class PhaseResult:
    """Result of one Dream Cycle phase."""
    phase: str
    status: str  # "ok" | "skipped" | "error"
    findings_count: int
    findings: list[dict] = field(default_factory=list)
    error: str = ""


@dataclass
class CycleReport:
    """Aggregate report from one cycle."""
    started_at: str
    completed_at: str
    dry_run: bool
    phases: list[PhaseResult] = field(default_factory=list)
    journal_appended_count: int = 0


# ── Phase 1: similarity_dedup (stub for v1) ─────────────────────────────


def phase_similarity_dedup(claude_dir: Path) -> PhaseResult:
    """Find KONO entries with cosine similarity >= SIMILARITY_THRESHOLD.

    v1: stub. Returns skipped status with reason. KONO sqlite-vec
    integration has its own scope (see project_kono_uses_embeddings memory)
    and ships in a separate PR.
    """
    return PhaseResult(
        phase="similarity_dedup",
        status="skipped",
        findings_count=0,
        findings=[{
            "reason": "kono_sqlite_vec_integration_pending",
            "v2_scope": (
                "load embeddings.db, query cosine_distance < "
                f"{1 - SIMILARITY_THRESHOLD}, propose merges"
            ),
        }],
    )


# ── Phase 2: stale_decay ────────────────────────────────────────────────


def _decay_one(entry: dict, age_days: float) -> dict | None:
    """Compute decayed score for one entry, or None if no decay applies."""
    if age_days < DECAY_THRESHOLD_DAYS:
        return None
    score = entry.get("score", entry.get("fitness"))
    if score is None or not isinstance(score, (int, float)):
        return None
    decayed = round(float(score) * DECAY_FACTOR, 4)
    return {
        "entry_id": entry.get("name") or entry.get("id") or "?",
        "old_score": float(score),
        "new_score": decayed,
        "age_days": round(age_days, 1),
    }


def _decay_proposals(path: Path, age_days: float) -> list[dict]:
    """Walk insights.jsonl and return decay proposals (flat single-loop helper)."""
    proposals: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        proposal = _decay_one(entry, age_days)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def phase_stale_decay(claude_dir: Path) -> PhaseResult:
    """Age-decay entries in learning/insights.jsonl whose scores are stale.

    Reads the file, applies decay to each entry past DECAY_THRESHOLD_DAYS.
    --apply rewrites the file; --dry-run returns proposals only (v1).
    """
    insights = claude_dir / "learning" / "insights.jsonl"
    if not insights.exists():
        return PhaseResult(
            phase="stale_decay",
            status="skipped",
            findings_count=0,
            error="learning/insights.jsonl missing",
        )
    file_age_days = (time.time() - insights.stat().st_mtime) / 86400
    proposals = _decay_proposals(insights, file_age_days)
    return PhaseResult(
        phase="stale_decay",
        status="ok",
        findings_count=len(proposals),
        findings=proposals[:50],  # cap at 50 for output bound
    )


# ── Phase 3: hub_cap ────────────────────────────────────────────────────


def _count_refs_in_file(path: Path) -> dict[str, int]:
    """Count `[text](file.md)` refs per target in a single file."""
    counts: dict[str, int] = {}
    for match in HUB_REF_PATTERN.finditer(path.read_text(errors="replace")):
        target = match.group(1)
        counts[target] = counts.get(target, 0) + 1
    return counts


def phase_hub_cap(claude_dir: Path) -> PhaseResult:
    """Flag MEMORY.md targets referenced > MAX_HUB_REFS times.

    Hub-diversity protection: one entity becoming the hub of every
    inferred relation collapses the network into a star.
    """
    project_path = encode_project_path(CLAUDE_DIR)
    memory_md = claude_dir / "projects" / project_path / "memory" / "MEMORY.md"
    # Fallback: any project's MEMORY.md
    if not memory_md.exists():
        projects = claude_dir / "projects"
        if projects.exists():
            for proj in projects.iterdir():
                cand = proj / "memory" / "MEMORY.md"
                if cand.exists():
                    memory_md = cand
                    break
    if not memory_md.exists():
        return PhaseResult(
            phase="hub_cap",
            status="skipped",
            findings_count=0,
            error="MEMORY.md missing",
        )
    refs = _count_refs_in_file(memory_md)
    hubs = [
        {"target": target, "ref_count": count, "threshold": MAX_HUB_REFS}
        for target, count in refs.items()
        if count > MAX_HUB_REFS
    ]
    return PhaseResult(
        phase="hub_cap",
        status="ok",
        findings_count=len(hubs),
        findings=hubs,
    )


# ── Phase 4: rejection_log ──────────────────────────────────────────────


def _read_jsonl_entries(path: Path) -> list[dict]:
    """Return parsed entries from a JSONL file (flat single-loop helper)."""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return out


def is_rejected(entry_id: str, claude_dir: Path | None = None) -> bool:
    """Check whether entry_id has been previously rejected."""
    base = claude_dir if claude_dir is not None else CLAUDE_DIR
    entries = _read_jsonl_entries(base / REJECTION_LOG_PATH)
    return any(e.get("entry_id") == entry_id for e in entries)


def append_rejection(
    entry_id: str, reason: str, claude_dir: Path | None = None
) -> None:
    """Append a rejection record to the log."""
    base = claude_dir if claude_dir is not None else CLAUDE_DIR
    rejection_path = base / REJECTION_LOG_PATH
    rejection_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "entry_id": entry_id,
        "reason": reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(rejection_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def phase_rejection_log(claude_dir: Path) -> PhaseResult:
    """Verify rejection log is readable and report its size."""
    rejection_path = claude_dir / REJECTION_LOG_PATH
    if not rejection_path.exists():
        return PhaseResult(
            phase="rejection_log",
            status="ok",
            findings_count=0,
            findings=[{"note": "no rejections recorded yet"}],
        )
    entries = _read_jsonl_entries(rejection_path)
    return PhaseResult(
        phase="rejection_log",
        status="ok",
        findings_count=len(entries),
        findings=[{
            "rejections_total": len(entries),
            "log_path": REJECTION_LOG_PATH,
        }],
    )


# ── Phase 5: dream_journal ──────────────────────────────────────────────


def journal_append(entry: dict, claude_dir: Path | None = None) -> None:
    """Append one journal entry (used by other phases when --apply is set)."""
    base = claude_dir if claude_dir is not None else CLAUDE_DIR
    journal_path = base / JOURNAL_PATH
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **entry,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def phase_dream_journal(claude_dir: Path) -> PhaseResult:
    """Verify journal is readable and report its size."""
    journal_path = claude_dir / JOURNAL_PATH
    if not journal_path.exists():
        return PhaseResult(
            phase="dream_journal",
            status="ok",
            findings_count=0,
            findings=[{"note": "no journal entries yet"}],
        )
    entries = _read_jsonl_entries(journal_path)
    return PhaseResult(
        phase="dream_journal",
        status="ok",
        findings_count=len(entries),
        findings=[{
            "journal_entries_total": len(entries),
            "log_path": JOURNAL_PATH,
        }],
    )


# ── Phase dispatch ──────────────────────────────────────────────────────

PHASES: dict[str, Callable[[Path], PhaseResult]] = {
    "similarity_dedup": phase_similarity_dedup,
    "stale_decay": phase_stale_decay,
    "hub_cap": phase_hub_cap,
    "rejection_log": phase_rejection_log,
    "dream_journal": phase_dream_journal,
}


def _select_phases(only_phase: str | None) -> list[str]:
    """Return the list of phase names to run."""
    if only_phase is None:
        return list(PHASES.keys())
    return [only_phase]


def _run_one_phase(phase_name: str, base: Path) -> PhaseResult:
    """Dispatch one phase by name."""
    phase_fn = PHASES.get(phase_name)
    if phase_fn is None:
        return PhaseResult(
            phase=phase_name,
            status="error",
            findings_count=0,
            error=f"unknown phase: {phase_name}",
        )
    return phase_fn(base)


def run_cycle(
    claude_dir: Path | None = None,
    dry_run: bool = True,
    only_phase: str | None = None,
) -> CycleReport:
    """Run all (or one) Dream Cycle phases."""
    base = claude_dir if claude_dir is not None else CLAUDE_DIR
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results: list[PhaseResult] = []
    for phase_name in _select_phases(only_phase):
        results.append(_run_one_phase(phase_name, base))
    completed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return CycleReport(
        started_at=started,
        completed_at=completed,
        dry_run=dry_run,
        phases=results,
        journal_appended_count=0,  # v1 dry-run only
    )


# ── Output formatting ──────────────────────────────────────────────────


def _format_finding_lines(findings: list[dict]) -> list[str]:
    """Format up to 3 findings as preview lines."""
    return [
        f"    {json.dumps(f, ensure_ascii=False)[:120]}"
        for f in findings[:3]
    ]


def _format_phase_summary(phases: list[PhaseResult]) -> list[str]:
    """One-line summary per phase + first 3 findings (flat single-loop)."""
    out: list[str] = []
    for p in phases:
        marker = {"ok": "✓", "skipped": "○", "error": "✗"}.get(p.status, "?")
        out.append(
            f"\n{marker} {p.phase} [{p.status}] — "
            f"{p.findings_count} findings"
        )
        if p.error:
            out.append(f"    error: {p.error}")
        out.extend(_format_finding_lines(p.findings))
    return out


def _format_text(report: CycleReport) -> str:
    """Human-readable cycle report."""
    lines = [
        f"\nGRIP Dream Cycle ({'dry-run' if report.dry_run else 'apply'})",
        f"Started: {report.started_at}",
        f"Completed: {report.completed_at}",
        "=" * 60,
    ]
    lines.extend(_format_phase_summary(report.phases))
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    """CLI entry point. Returns 0 on success."""
    parser = argparse.ArgumentParser(description="GRIP Dream Cycle")
    parser.add_argument("--phase", help="Run only one phase by name")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply mutations (default: dry-run)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.apply:
        print(
            "[error] --apply not implemented in v1; dry-run only",
            file=sys.stderr,
        )
        return 2

    report = run_cycle(dry_run=True, only_phase=args.phase)
    if args.json:
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
