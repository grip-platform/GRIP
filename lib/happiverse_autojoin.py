"""happiverse_autojoin — finish a machine the moment it becomes reachable.

Two machines on the fleet cannot be reached: they need one command typed at a
physical keyboard. That command can be run hours later, by which time the
session that asked for it is gone. This closes the loop so the keystroke is
SUFFICIENT: poll the pending machines, and when one starts answering, measure
it and — only if all four legs pass — flip its star to live and republish.

Idempotent by construction. A run against a fleet in which nothing changed
performs no writes and no publish, so it is safe on a timer: ``plan()`` decides
and returns actions, and the caller executes them. Deciding and doing are
separate so the decision is testable without a network, a fleet or a publish.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

PROMOTE, WAIT, ALREADY = "promote", "wait", "already-live"


@dataclass
class Pending:
    """A machine we are waiting on, and how to measure it once it answers."""
    nickname: str
    hostnames: Sequence[str] = field(default_factory=tuple)
    repo_dir: str = ""
    ssh: str = ""
    health_command: str = ""


def plan(records: Sequence[dict]) -> list:
    """Decide what to do for each measured machine. Pure.

    A machine is promoted ONLY on a full PASS. UNKNOWN never promotes — that is
    the whole point of the three-valued verdict, and a promotion on UNKNOWN
    would publish a claim about a machine nobody could measure.

    Demotion is deliberately NOT done here. A star reading live whose machine
    later fails a leg is a real situation — a tree drifts behind main within
    minutes on an active repo — and flapping a public page on that is worse than
    a stale star. The disagreement is reported instead, and `published_status_matches`
    already surfaces it."""
    out = []
    for rec in records:
        star = next((l for l in rec.get("legs", []) if l["leg"] == "star"), None)
        on_map = star is not None and star["verdict"] == "PASS"
        reads_live = star is not None and "'live'" in star.get("evidence", "")
        if rec.get("overall") != "PASS":
            failing = [l["leg"] for l in rec.get("legs", []) if l["verdict"] != "PASS"]
            out.append({"nickname": rec["nickname"], "action": WAIT,
                        "reason": f"overall {rec.get('overall')}; not passing: {', '.join(failing)}"})
        elif not on_map:
            out.append({"nickname": rec["nickname"], "action": WAIT,
                        "reason": "passes every leg but has no star on the deployed map"})
        elif reads_live:
            out.append({"nickname": rec["nickname"], "action": ALREADY,
                        "reason": "star already reads live and the measurement agrees"})
        else:
            out.append({"nickname": rec["nickname"], "action": PROMOTE,
                        "reason": "all four legs pass; star still reads joining"})
    return out


def needs_publish(actions: Sequence[dict]) -> bool:
    """A publish happens only when something is actually being promoted.

    Without this a timer would re-deploy the identical map every run, which is
    a public write with no change behind it."""
    return any(a["action"] == PROMOTE for a in actions)
