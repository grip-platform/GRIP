#!/usr/bin/env python3
"""Measure retry storms, stall kills and wasted tokens for Claude Code Workflow runs.

The falsifier instrument for GRIP#5136. The issue's falsification clause says: if a
workflow with stallMs raised to 600000 still stalls at the new limit, the diagnosis is
wrong. Answering that needs a repeatable measurement, and so do the acceptance criteria
("zero stalled (no progress) events"; "wasted tokens are zero against the 5,394,182").

WHY THIS READS STRUCTURE AND NOT TEXT — this is the load-bearing design note.

The first version of this file scanned journal and transcripts for the runner's own
stall strings ("stalled (no progress)", "no progress for ..."). Run against the known
catastrophic run wf_8460a5d5-393 — twelve attempts killed at 180.0s — it reported ZERO
stall events. Those strings are console output; they are never written to any file in
the run directory. The scan could not have failed, on any input, which made it an
oracle rather than a measurement (rules/tests-must-be-able-to-fail.md, shape 1).

What IS on disk is what the runner DID. journal.jsonl carries one `started` per attempt,
one `result` per lane that produced an answer, and one `failed` per lane that exhausted
its retries; attempts of the same lane share a content-addressed `key`. So:

    lanes           = distinct keys
    attempts        = count of `started`
    dead attempts   = started agentIds MINUS agentIds that produced a `result`
    wasted tokens   = usage summed over the dead attempts' own transcripts

On wf_8460a5d5-393 that yields 4 lanes, two of them with 6 attempts each and a matching
`failed`, two with 1 attempt and a `result` — 14 - 2 = 12 dead attempts, which is the
issue's forensic number exactly.

BE PRECISE ABOUT WHAT A RETRY PROVES. A dead attempt shows the runner discarded work; it
does not by itself name the cause. The stall signature is the DURATION: a stalled attempt
is killed at the stall budget, so its wall time clusters at ~180s (the observed dead
attempts ran 185s, being the 180s timer plus setup before it armed). Attempts are
therefore reported in two buckets — `stall_shaped` (duration within tolerance of a known
stall budget) and `other_dead` — and a stall is never claimed from a retry alone.

Exit codes are deliberately distinct so "unknown" can never be read as "clean":
  0  measured, no dead attempts
  1  measured, dead attempts present
  3  could not measure (missing/unreadable run directory) — NOT a pass
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Stall budgets a kill could land on: the runner default (er=180000 in the 2.1.251
# binary) and the 600000 GRIP sets explicitly.
#
# The window is ASYMMETRIC, and that is the whole point. A watchdog kill fires when the
# budget elapses, so the attempt's wall time is the budget PLUS whatever start-up ran
# before the timer armed -- never less. The twelve real kills on wf_8460a5d5-393 ran
# 184.9-205.9s against a 180s timer: every one at or above it.
#
# A symmetric window gets this wrong in the expensive direction. It was symmetric first,
# and on wf_321c57aa-e0a it counted deaths at 168.7s, 171.1s and 178.1s as watchdog
# kills. Those attempts died BEFORE the timer could fire, so whatever killed them, it was
# not the watchdog -- and counting them inflated the estate-wide total this tool exists
# to report honestly.
STALL_BUDGETS_S = (180.0, 600.0)
STALL_OVERSHOOT_S = 30.0

USAGE_INPUT_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _load_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping malformed lines rather than aborting the run."""
    rows: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _ids_by_type(entries: list[dict], wanted: str) -> list[str]:
    return [e.get("agentId", "") for e in entries if e.get("type") == wanted]


def _lane_attempts(entries: list[dict]) -> dict[str, list[str]]:
    """Map each lane's content-addressed key to the agentIds that attempted it, in order."""
    lanes: dict[str, list[str]] = {}
    for e in entries:
        if e.get("type") != "started":
            continue
        lanes.setdefault(e.get("key", "?"), []).append(e.get("agentId", ""))
    return lanes


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _duration_s(rows: list[dict]) -> float | None:
    """Wall seconds from an attempt's first to last recorded event."""
    stamps = [t for t in (_parse_ts(r.get("timestamp", "")) for r in rows) if t]
    if len(stamps) < 2:
        return None
    return (max(stamps) - min(stamps)).total_seconds()


def _is_stall_shaped(duration: float | None) -> bool:
    """True when the attempt's wall time lands AT a stall budget, or just past it.

    At or past, never before: the timer fires when the budget elapses, so an attempt that
    died sooner cannot have been killed by it.
    """
    if duration is None:
        return False
    return any(b <= duration <= b + STALL_OVERSHOOT_S for b in STALL_BUDGETS_S)


def _usage_tokens(rows: list[dict]) -> tuple[int, int]:
    """Token usage for one attempt, de-duplicated by message id.

    A streamed assistant message can appear more than once in a transcript; counting it
    twice inflates the very number this exists to report honestly.
    """
    seen: set[str] = set()
    total_in = total_out = 0
    for row in rows:
        msg = row.get("message") or {}
        usage = msg.get("usage") or row.get("usage") or {}
        mid = msg.get("id") or ""
        if not usage or (mid and mid in seen):
            continue
        if mid:
            seen.add(mid)
        total_in += sum(int(usage.get(f) or 0) for f in USAGE_INPUT_FIELDS)
        total_out += int(usage.get("output_tokens") or 0)
    return total_in, total_out


def _lane_states(ids: list[str], key: str, produced: set[str], failed: set[str]) -> list[str]:
    """Classify every attempt of ONE lane as alive / dead / in_flight.

    The in_flight state exists because this tool is run against LIVE workflows as well as
    finished ones. Without it, an agent that is still working — no `result` yet, by
    definition — reads as a dead attempt, and its perfectly healthy token spend is
    reported as waste. That is the same shape of error as the text-scanning version this
    file replaced: a number that cannot say the true thing about the input it was given.
    """
    if key in failed:
        return ["dead"] * len(ids)                       # lane exhausted: every attempt lost
    if any(a in produced for a in ids):
        return ["alive" if a in produced else "dead" for a in ids]
    # No result and no failure yet: earlier attempts were retried away, the last is running.
    return ["dead"] * (len(ids) - 1) + ["in_flight"]


def _attempt_record(run_dir: Path, agent_id: str, state: str) -> dict:
    """Measure one attempt from its own transcript — the artefact, not a status field."""
    rows = _load_jsonl(run_dir / f"agent-{agent_id}.jsonl")
    tin, tout = _usage_tokens(rows)
    duration = _duration_s(rows)
    return {
        "agent": agent_id,
        "state": state,
        "duration_s": round(duration, 1) if duration is not None else None,
        "stall_shaped": state == "dead" and _is_stall_shaped(duration),
        "input_plus_cache": tin,
        "output": tout,
        "events": len(rows),
    }


def _totals(attempts: list[dict]) -> dict:
    dead = [a for a in attempts if a["state"] == "dead"]
    stall_shaped = [a for a in dead if a["stall_shaped"]]
    return {
        "attempts": len(attempts),
        "in_flight": sum(1 for a in attempts if a["state"] == "in_flight"),
        "dead_attempts": len(dead),
        "stall_shaped": len(stall_shaped),
        "other_dead": len(dead) - len(stall_shaped),
        "wasted_input_plus_cache": sum(a["input_plus_cache"] for a in dead),
        "wasted_output": sum(a["output"] for a in dead),
        "total_input_plus_cache": sum(a["input_plus_cache"] for a in attempts),
        "total_output": sum(a["output"] for a in attempts),
    }


def measure(run_dir: Path) -> dict:
    """Measure one workflow run directory. Raises FileNotFoundError if unreadable."""
    if not run_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {run_dir}")
    journal = run_dir / "journal.jsonl"
    if not journal.exists():
        raise FileNotFoundError(f"no journal.jsonl in {run_dir}")

    entries = _load_jsonl(journal)
    lanes = _lane_attempts(entries)
    produced = set(_ids_by_type(entries, "result"))
    failed_keys = {e.get("key") for e in entries if e.get("type") == "failed"}

    attempts = [
        _attempt_record(run_dir, aid, state)
        for key, ids in lanes.items()
        for aid, state in zip(ids, _lane_states(ids, key, produced, failed_keys))
    ]

    return {
        "run_dir": str(run_dir),
        "lanes": len(lanes),
        "lanes_exhausted": len(failed_keys),
        "lanes_retried": sum(1 for ids in lanes.values() if len(ids) > 1),
        "max_attempts_on_one_lane": max((len(v) for v in lanes.values()), default=0),
        **_totals(attempts),
        "per_attempt": sorted(attempts, key=lambda a: (a["state"], -(a["duration_s"] or 0))),
    }


def summary_line(m: dict) -> str:
    live = f" in_flight={m['in_flight']}" if m["in_flight"] else ""
    return (
        f"lanes={m['lanes']} exhausted={m['lanes_exhausted']} retried={m['lanes_retried']} "
        f"attempts={m['attempts']}{live} dead={m['dead_attempts']} "
        f"stall_shaped={m['stall_shaped']} "
        f"wasted_tokens={m['wasted_input_plus_cache'] + m['wasted_output']:,}"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {Path(argv[0]).name} <workflow-run-dir> [--summary]", file=sys.stderr)
        return 3
    try:
        result = measure(Path(argv[1]).expanduser())
    except (FileNotFoundError, PermissionError) as exc:
        # Exit 3, never 0: an unmeasurable run must not read as a clean one.
        print(json.dumps({"error": str(exc), "measured": False}, indent=2))
        return 3
    print(summary_line(result) if "--summary" in argv else json.dumps(result, indent=2))
    return 1 if result["dead_attempts"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
