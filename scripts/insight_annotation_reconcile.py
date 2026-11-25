#!/usr/bin/env python3
"""W8.3 — nightly reconciliation of `# INSIGHT[<fp>]:` annotations.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W8.3. Walks every
`# INSIGHT[<fp>]:` annotation across the codebase. For each, classifies
into one of:

- LIVE          — fingerprint exists in learning/insights.jsonl OR
                   data/converge-fixed-points.jsonl AND any backing
                   hypothesis is not REFUTED.
- ORPHANED      — fingerprint not found anywhere; annotation is decorative
                   or stale.
- STALE         — fingerprint links to a hypothesis with verdict REFUTED;
                   the gate-skip is no longer empirically justified and
                   should be removed.
- PENDING       — fingerprint exists, hypothesis exists but no verdict yet.

Writes a drift report to ``data/annotation-drift-report.md`` and the
machine-readable tally to ``data/annotation-drift-report.jsonl`` for
downstream consumers (kill switches, dashboards). Does NOT auto-edit
sources — annotations are load-bearing and operator decides removal.

Exit codes:
- 0 — reconcile completed
- 1 — fatal (couldn't read codebase or write report)

Run via launchd hourly OR on-demand:
    python3 -m scripts.insight_annotation_reconcile [--root <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))

INSIGHT_RE = re.compile(r"#\s*INSIGHT\[([0-9a-f]{12,16})\]:\s*(.+)")

# Skip when scanning the codebase — these are derived/cache trees.
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".next", "target", "vendor", "Pods",
}


@dataclass(frozen=True)
class Annotation:
    file: Path
    line: int
    fingerprint: str
    hook: str


@dataclass
class ReconcileReport:
    live: list[Annotation] = field(default_factory=list)
    orphaned: list[Annotation] = field(default_factory=list)
    stale: list[Annotation] = field(default_factory=list)
    pending: list[Annotation] = field(default_factory=list)

    def counter(self) -> Counter:
        return Counter({
            "live": len(self.live),
            "orphaned": len(self.orphaned),
            "stale": len(self.stale),
            "pending": len(self.pending),
        })


def _walk_codebase(root: Path):
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def _extract_from_file(path: Path) -> list[Annotation]:
    """Pull every annotation out of a single file (delegated single-loop)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[Annotation] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        match = INSIGHT_RE.search(line)
        if match:
            out.append(Annotation(
                file=path, line=line_num,
                fingerprint=match.group(1), hook=match.group(2).strip(),
            ))
    return out


def find_annotations(root: Path) -> list[Annotation]:
    """Collect every annotation across the codebase rooted at ``root``.

    Single-pass file walk; per-file extraction is delegated to
    ``_extract_from_file`` to keep this loop linear in file count
    and avoid a nested-loop in this scope.
    """
    out: list[Annotation] = []
    for path in _walk_codebase(root):
        out.extend(_extract_from_file(path))
    return out


def _read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file into a list of dicts. Skips malformed lines."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _extract_fingerprints(records: list[dict]) -> set[str]:
    """Pull the canonical fp from each record (any of fingerprint/id/hypothesis_id)."""
    out: set[str] = set()
    for record in records:
        fp = record.get("fingerprint") or record.get("hypothesis_id") or record.get("id")
        if fp:
            out.add(str(fp)[:16])
    return out


def _load_known_fingerprints(claude_dir: Path) -> set[str]:
    """Collect every known insight fingerprint from learning/ and data/."""
    out: set[str] = set()
    for rel in ("learning/insights.jsonl", "data/converge-fixed-points.jsonl"):
        out.update(_extract_fingerprints(_read_jsonl(claude_dir / rel)))
    return out


def _verdict_from_record(record: dict) -> tuple[str, str] | None:
    """Extract (fingerprint, lowercased_verdict) from a hypothesis record."""
    fp = record.get("fingerprint") or record.get("hypothesis_id") or record.get("id", "")
    verdict = record.get("verdict") or record.get("status")
    if not (fp and verdict):
        return None
    return str(fp)[:16], str(verdict).lower()


def _load_hypothesis_verdicts(claude_dir: Path) -> dict[str, str]:
    """Map fingerprint -> verdict if hypothesis_engine state exists."""
    out: dict[str, str] = {}
    records = _read_jsonl(claude_dir / "learning" / "hypotheses.jsonl")
    for record in records:
        pair = _verdict_from_record(record)
        if pair:
            out[pair[0]] = pair[1]
    return out


def classify(
    annotations: list[Annotation], known_fps: set[str], verdicts: dict[str, str]
) -> ReconcileReport:
    report = ReconcileReport()
    for a in annotations:
        if a.fingerprint not in known_fps:
            report.orphaned.append(a)
            continue
        verdict = verdicts.get(a.fingerprint, "")
        if verdict == "refuted":
            report.stale.append(a)
        elif verdict in ("confirmed", "verified"):
            report.live.append(a)
        else:
            report.pending.append(a)
    return report


def write_report(out_dir: Path, report: ReconcileReport) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "annotation-drift-report.md"
    jsonl = out_dir / "annotation-drift-report.jsonl"

    counts = report.counter()
    md_text = f"""# Insight Annotation Drift Report

Generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

| Bucket | Count |
|--------|-------|
| LIVE | {counts['live']} |
| PENDING | {counts['pending']} |
| ORPHANED (fp not in registry) | {counts['orphaned']} |
| STALE (REFUTED hypothesis) | {counts['stale']} |

## STALE — annotation backing hypothesis is REFUTED

These gate-skips are no longer empirically justified. Operator should
remove the annotation OR re-verify the hypothesis.

"""
    for a in report.stale:
        md_text += f"- `{a.file}:{a.line}` `INSIGHT[{a.fingerprint}]` — {a.hook}\n"
    md_text += "\n## ORPHANED — fingerprint not found in any registry\n\n"
    for a in report.orphaned:
        md_text += f"- `{a.file}:{a.line}` `INSIGHT[{a.fingerprint}]` — {a.hook}\n"
    md_text += "\n## PENDING — backing hypothesis has no verdict yet\n\n"
    for a in report.pending:
        md_text += f"- `{a.file}:{a.line}` `INSIGHT[{a.fingerprint}]` — {a.hook}\n"

    md.write_text(md_text, encoding="utf-8")
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **counts,
        }) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default=str(Path.home() / ".claude"),
        help="Codebase root to scan",
    )
    parser.add_argument(
        "--out", default=str(Path.home() / ".claude" / "data"),
        help="Output directory for reports",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    out_dir = Path(args.out)
    annotations = find_annotations(root)
    known_fps = _load_known_fingerprints(root)
    verdicts = _load_hypothesis_verdicts(root)
    report = classify(annotations, known_fps, verdicts)
    write_report(out_dir, report)
    print(json.dumps(dict(report.counter())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
