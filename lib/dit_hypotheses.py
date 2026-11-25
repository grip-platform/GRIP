#!/usr/bin/env python3
"""DIT Hypothesis Registration — falsifiable predictions for each DIT dimension.

Registers 4 hypotheses (one per DIT scoring dimension) with the GRIP
hypothesis engine. Each has a 2-month verification deadline.

Usage:
    python3 -m lib.dit_hypotheses register
    python3 -m lib.dit_hypotheses status
"""

import sys

from lib.hypothesis_engine import Hypothesis, status as engine_status, _next_id, _save_hypothesis


DIT_HYPOTHESES = [
    {
        "claim": "Cleanup dimension scores improve by 10+ points over 2 months for active developers",
        "metric": "dit_cleanup_avg_score",
        "prediction": "10+ point increase from baseline",
        "deadline": "2026-06-03",
    },
    {
        "claim": "Code quality dimension scores correlate negatively with PR revert rate",
        "metric": "dit_code_quality_vs_revert_rate",
        "prediction": "negative correlation (r < -0.3)",
        "deadline": "2026-06-03",
    },
    {
        "claim": "AI fluency scores increase for developers who adopt Claude Code",
        "metric": "dit_ai_fluency_adopter_delta",
        "prediction": "15+ point increase for Claude Code adopters vs non-adopters",
        "deadline": "2026-06-03",
    },
    {
        "claim": "GRIP upskilling scores correlate with reduced PR turnaround time",
        "metric": "dit_grip_upskilling_vs_turnaround",
        "prediction": "negative correlation (r < -0.3) between upskilling score and review-to-merge hours",
        "deadline": "2026-06-03",
    },
]


def register_dit_hypotheses(pr_number: int = 0) -> list[str]:
    """Register all 4 DIT hypotheses. Returns list of hypothesis IDs."""
    registered_ids: list[str] = []
    for spec in DIT_HYPOTHESES:
        hid = _next_id()
        h = Hypothesis(
            id=hid,
            pr_number=pr_number,
            registered=_now_iso(),
            claim=spec["claim"],
            metric=spec["metric"],
            prediction=spec["prediction"],
            deadline=spec["deadline"],
        )
        _save_hypothesis(h)
        registered_ids.append(hid)
    return registered_ids


def _now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _print_status() -> dict:
    """Print and return current hypothesis registry status."""
    s = engine_status()
    print(f"Hypothesis Registry: {s['total']} total")
    for st, count in s["by_status"].items():
        if count > 0:
            print(f"  {st}: {count}")
    if s["due_for_verification"] > 0:
        print(f"  Due for verification: {s['due_ids']}")
    print(f"  Confirmation rate: {s['confirmation_rate']:.1%}")
    return s


def main() -> None:
    """CLI entry point: register | status."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "register"
    dispatch = {
        "register": lambda: _run_register(),
        "status": _print_status,
    }
    action = dispatch.get(cmd)
    if action is None:
        print(f"Unknown command: {cmd}. Use 'register' or 'status'.")
        sys.exit(1)
    action()


def _run_register() -> None:
    ids = register_dit_hypotheses(pr_number=1721)
    print(f"Registered {len(ids)} DIT hypotheses: {', '.join(ids)}")
    for hid, spec in zip(ids, DIT_HYPOTHESES):
        print(f"  {hid}: {spec['claim'][:60]}...")


if __name__ == "__main__":
    main()
