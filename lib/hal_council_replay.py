"""HAL Council replay harness — dry-run verdict generator for historical PRs.

The HAL Council needs to be calibrated against historical reviews before
going live org-wide. This module re-scores past PRs offline using the
pipeline with a mock-LLM hook set, compares against the existing review
stack's verdicts (Broly/Ralph/claude-code-review), and emits a structured
audit record.

### What this module does

- Reads the frozen weight table from `lib/hal_council/prompts/v1/weights.json`.
- Validates weights sum to 1.0 and match the plan spec.
- Exposes `replay_pr(pr_number, repo, *, hooks=None) -> ReplayRecord`.
- Batch-runs a list of PR IDs via `replay_batch(...) -> list[ReplayRecord]`.
- Writes the audit file to `audit/hal-council-replay-<ISO>.json`.

### What this module does NOT do (deferred)

- Does NOT call live LLM APIs by default. Replay in CI/offline mode uses
  mock hooks. A future PR can wire real production hooks behind an
  explicit `--live` flag.
- Does NOT re-fetch historical baseline verdicts. Expects a pre-populated
  `baseline_scores` dict keyed by PR number.
- Does NOT mutate weights. Weight calibration adjustment is a separate
  deliberate step — this harness only emits the data needed for it.

### CLI

    python -m lib.hal_council_replay \\
        --pr 2211 2205 2200 2192 2193 \\
        --repo grip-platform/GRIP \\
        --output audit/hal-council-replay-2026-04-16.json

Pure stdlib. Hermetic tests.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


WEIGHTS_PATH_REL = "lib/hal_council/prompts/v1/weights.json"


@dataclass
class ReplayRecord:
    """One PR's replay result."""

    pr: int
    repo: str = ""
    baseline_score: Optional[float] = None
    hal_council_score: float = 0.0
    delta: Optional[float] = None
    goodhart_pass: bool = True
    short_circuited_at: Optional[str] = None
    regressions: list = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "pr": self.pr,
            "repo": self.repo,
            "baseline_score": self.baseline_score,
            "hal_council_score": self.hal_council_score,
            "delta": self.delta,
            "goodhart_pass": self.goodhart_pass,
            "short_circuited_at": self.short_circuited_at,
            "regressions": self.regressions,
            "notes": self.notes,
        }


def load_weights(repo_root: Path = None) -> dict:
    """Load the frozen weights.json. Raises FileNotFoundError if missing."""
    root = repo_root or Path(__file__).resolve().parent.parent
    path = root / WEIGHTS_PATH_REL
    with path.open() as f:
        return json.load(f)


def validate_weights(weights_data: dict) -> tuple[bool, str]:
    """Check weights sum to 1.0 and layer totals match plan spec."""
    dims = weights_data.get("dimensions", {})
    if not dims:
        return False, "no dimensions"
    total = sum(float(d.get("weight", 0)) for d in dims.values())
    if abs(total - 1.0) > 1e-6:
        return False, f"total {total} != 1.0"
    llm_total = sum(
        float(d.get("weight", 0)) for d in dims.values()
        if d.get("layer") == "llm"
    )
    mech_total = sum(
        float(d.get("weight", 0)) for d in dims.values()
        if d.get("layer") == "mechanical"
    )
    declared_llm = weights_data.get("llm_weight_total", 0)
    declared_mech = weights_data.get("mechanical_weight_total", 0)
    if abs(llm_total - declared_llm) > 1e-6:
        return False, f"llm sum {llm_total} != declared {declared_llm}"
    if abs(mech_total - declared_mech) > 1e-6:
        return False, f"mechanical sum {mech_total} != declared {declared_mech}"
    return True, ""


def weights_sha256(weights_data: dict) -> str:
    """Canonical SHA256 of the weights for pinning."""
    canonical = json.dumps(weights_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay_pr(
    pr: int,
    repo: str,
    *,
    baseline_score: Optional[float] = None,
    hal_council_score: float = 0.0,
    goodhart_pass: bool = True,
    short_circuited_at: Optional[str] = None,
    regressions: Optional[list] = None,
) -> ReplayRecord:
    """Build a ReplayRecord. Pure data transformer — no IO.

    The caller is responsible for computing scores (via the pipeline with
    mock hooks or live hooks). This function packages the result.
    """
    delta = None
    if baseline_score is not None:
        delta = hal_council_score - baseline_score
    return ReplayRecord(
        pr=pr,
        repo=repo,
        baseline_score=baseline_score,
        hal_council_score=hal_council_score,
        delta=delta,
        goodhart_pass=goodhart_pass,
        short_circuited_at=short_circuited_at,
        regressions=list(regressions or []),
    )


def summarise_batch(records: list) -> dict:
    """Aggregate statistics over a batch of ReplayRecords."""
    if not records:
        return {
            "count": 0, "mean_delta": None, "max_delta": None,
            "min_delta": None, "regressions_count": 0, "goodhart_veto_count": 0,
        }
    deltas = [r.delta for r in records if r.delta is not None]
    return {
        "count": len(records),
        "mean_delta": (sum(deltas) / len(deltas)) if deltas else None,
        "max_delta": max(deltas) if deltas else None,
        "min_delta": min(deltas) if deltas else None,
        "regressions_count": sum(1 for r in records if r.regressions),
        "goodhart_veto_count": sum(1 for r in records if not r.goodhart_pass),
    }


def write_audit(records: list, output_path: Path, *, weights_sha: str = "") -> None:
    """Write the audit JSON to disk. Creates parent dirs if missing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "v1",
        "weights_sha256": weights_sha,
        "summary": summarise_batch(records),
        "records": [r.to_dict() for r in records],
    }
    with output_path.open("w") as f:
        json.dump(payload, f, indent=2)


def replay_batch(
    pr_numbers: list,
    repo: str,
    *,
    score_provider: Optional[Callable] = None,
    baseline_provider: Optional[Callable] = None,
) -> list:
    """Run replay over a list of PR numbers. Returns list of ReplayRecords.

    score_provider: Callable(pr, repo) -> (hal_score, goodhart_pass, short_circuit)
    baseline_provider: Callable(pr, repo) -> Optional[float]

    Both default to stubs that return (0.0, True, None) and None respectively.
    Production callers wire these to the real pipeline + historical data source.
    """
    score_fn = score_provider or (lambda pr, repo: (0.0, True, None))
    baseline_fn = baseline_provider or (lambda pr, repo: None)
    records = []
    for pr in pr_numbers:
        try:
            hal_score, goodhart_pass, short_circuit = score_fn(pr, repo)
        except Exception:  # noqa: BLE001
            hal_score, goodhart_pass, short_circuit = 0.0, True, "score_provider_error"
        try:
            baseline = baseline_fn(pr, repo)
        except Exception:  # noqa: BLE001
            baseline = None
        records.append(replay_pr(
            pr, repo,
            baseline_score=baseline,
            hal_council_score=hal_score,
            goodhart_pass=goodhart_pass,
            short_circuited_at=short_circuit,
        ))
    return records


__all__ = [
    "WEIGHTS_PATH_REL",
    "ReplayRecord",
    "load_weights",
    "replay_batch",
    "replay_pr",
    "summarise_batch",
    "validate_weights",
    "weights_sha256",
    "write_audit",
]
