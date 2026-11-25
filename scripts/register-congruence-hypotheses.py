#!/usr/bin/env python3
"""Re-register the three Congruent Continuity genesis hypotheses (H-CC-1..3).

The genesis plan (docs/congruent-continuity-genesis.md, "Pre-registered
hypotheses") says they were to be registered at the W1 PR, and the build log
records ids H719-H721 for them -- but the live ledger's H719 today is an
unrelated hypothesis registered 2026-09-06, so the three have no live tracking.
The tracked copy of the ledger in a worktree is BEHIND the live one, so
registering from a worktree would mint colliding ids; this script is run once
against the live ledger instead (``CLAUDE_DIR`` decides which ledger).

Idempotent: a hypothesis whose claim already starts with its H-CC tag is
skipped, so a second run registers nothing. Prints one JSON line per tag.

    python3 scripts/register-congruence-hypotheses.py            # register
    python3 scripts/register-congruence-hypotheses.py --check    # report only
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import lib.hypothesis_engine as he  # noqa: E402

GENESIS = "docs/congruent-continuity-genesis.md"

HYPOTHESES = [
    {
        "tag": "H-CC-1",
        "claim": ("H-CC-1: within 30 days of the pre-hoc watcher arming a blocking tier, at least one "
                  "interrupt prevents an action that post-hoc settlement verifies would have been wrong "
                  f"({GENESIS})"),
        "metric": "continuity_prevented_wrong_actions",
        "prediction": ">= 1",
        "baseline": 0.0,
        "deadline": "2026-12-31",
        "measure_plan": ("Count continuity-verdict records with action_taken in (steer, halt-ask, block) "
                         "whose settled verdict is INCONGRUENT, in state/precog/continuity*.jsonl, over the "
                         "30 days after the watcher hook is bound in settings.json. The clock starts at "
                         "binding, not at registration; the hook is unbound today (locked decision D3)."),
    },
    {
        "tag": "H-CC-2",
        "claim": ("H-CC-2: post-hoc settlement localises at least 50 percent of sampled session failures "
                  f"to a specific divergence commit ({GENESIS})"),
        "metric": "continuity_failure_localisation_rate",
        "prediction": ">= 0.5",
        "baseline": 0.0,
        "deadline": "2026-10-31",
        "measure_plan": ("Sample 20 sessions with a recorded failure (a refuted hypothesis, a reverted PR, or "
                         "an operator-flagged wrong track); for each, run python3 -m lib.continuity_worker "
                         "--no-council over its continuity chain and check whether an INCONGRUENT verdict "
                         "names a commit whose contamination() closure covers the failing action. Rate = "
                         "localised / sampled."),
    },
    {
        "tag": "H-CC-3",
        "claim": ("H-CC-3: recording plus checking overhead stays under 10 percent of the session token "
                  f"budget with Pool-B checkers and severity-sampled commits ({GENESIS})"),
        "metric": "continuity_token_overhead_share",
        "prediction": "< 0.10",
        "baseline": 0.0,
        "deadline": "2026-10-31",
        "measure_plan": ("Over 10 sessions that write continuity commits, sum the tokens of every commit and "
                         "verdict record plus the council prompts sent by lib/continuity_council.py (the "
                         "sealed record's prompt field), divide by the session's total token usage from "
                         "the usage panel. Median share is the value."),
    },
]


def existing_tags() -> set[str]:
    """Tags whose hypothesis is already in the ledger (claim starts with 'H-CC-n:')."""
    claims = [str(getattr(h, "claim", "")) for h in he._load_hypotheses()]
    return {spec["tag"] for spec in HYPOTHESES
            if any(c.startswith(spec["tag"] + ":") for c in claims)}


def _one(spec: dict, present: set[str], check_only: bool) -> dict:
    if spec["tag"] in present:
        return {"tag": spec["tag"], "status": "already-registered"}
    if check_only:
        return {"tag": spec["tag"], "status": "missing"}
    h = he.register(spec["claim"], spec["metric"], spec["prediction"], pr_number=0,
                    baseline=spec["baseline"], deadline=spec["deadline"],
                    measure_plan=spec["measure_plan"])
    return {"tag": spec["tag"], "status": "registered", "id": h.id}


def register_missing(*, check_only: bool = False) -> list[dict]:
    present = existing_tags()
    return [_one(spec, present, check_only) for spec in HYPOTHESES]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rows = register_missing(check_only="--check" in args)
    for row in rows:
        print(json.dumps(row))
    return 0 if all(r["status"] != "missing" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
