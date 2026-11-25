"""Surface the deferred-concern queue, because nothing did.

`rules/deferred-followups-complete-or-surface.md` is PARAMOUNT and allows a
deferred follow-up exactly three exits: COMPLETE it, actively SURFACE it, or
STALE it as a recorded won't-do. Measured 2026-09-17: 407 open `agora-defer`
issues had taken NONE of them. `lib/agora_pr_review.py` was the only module
referencing the label and it is the FILER; no surfacer existed and no staler
existed, so exit (b) was unavailable rather than neglected.

The queue is diverging, not merely large. Measured by week, opened vs closed:

    10 Sep   219 /  42   net +177
    03 Sep    56 /  23   net  +33
    27 Aug    56 /   2   net  +54
    20 Aug    37 /   3   net  +34
    13 Aug   133 /   9   net +124
    06 Aug    82 /  85   net   -3

One week in six held. It went 407 -> 448 overnight while nobody was looking.

WHY THIS REPORTS A DERIVATIVE, NOT JUST A LEVEL. A level invites argument about
whether 448 is a lot. Arrivals running at 5x closures does not. The level is
lagging and contestable; the flow is neither.

WHY IT SURFACES A BOUNDED SLICE. Surfacing 448 items is not surfacing, it is
noise that trains a reader to skip the block — which is the silent drop wearing
a report's clothes. The honest unit is the TOTAL plus its TREND plus the few
oldest, so the reader sees the size, the direction, and a place to start.

Deliberately read-only. It closes no issue and files nothing: draining the queue
is a decision about 448 real findings, not a cleanup this module may take.
"""
from __future__ import annotations

import json
import subprocess

REPO = "grip-platform/GRIP"
LABEL = "agora-defer"


def _search_count(query: str) -> int:
    """Total matches for one GitHub search, or -1 when the probe itself failed.

    -1 rather than 0 on purpose: a failed probe and an empty queue are opposite
    findings, and collapsing them would report a clean queue for a network
    error — the exact fail-open shape this codebase keeps finding.
    """
    try:
        out = subprocess.run(
            ["gh", "api", "-X", "GET", "search/issues", "-f", f"q={query}",
             "--jq", ".total_count"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    if out.returncode != 0:
        return -1
    try:
        return int((out.stdout or "").strip())
    except ValueError:
        return -1


def open_count(repo: str = REPO, label: str = LABEL) -> int:
    return _search_count(f"repo:{repo} is:issue is:open label:{label}")


def window_flow(start: str, end: str, repo: str = REPO, label: str = LABEL) -> dict:
    """Arrivals and departures in one date window, as {opened, closed, net}.

    Uses the `created:A..B` RANGE form. The two-clause `created:>=A created:<B`
    spelling is accepted by the API and SILENTLY IGNORED — it returns the
    all-time count, which looks like a real answer. Verified 2026-09-17: the
    two-clause form returned an identical figure for four consecutive weeks.
    """
    opened = _search_count(f"repo:{repo} is:issue label:{label} created:{start}..{end}")
    closed = _search_count(f"repo:{repo} is:issue label:{label} closed:{start}..{end}")
    net = opened - closed if opened >= 0 and closed >= 0 else None
    return {"start": start, "end": end, "opened": opened, "closed": closed, "net": net}


def oldest(limit: int = 5, repo: str = REPO, label: str = LABEL) -> list:
    """The N longest-open items — a place to start, never the whole queue."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--label", label,
             "--state", "open", "--limit", str(limit), "--json",
             "number,title,createdAt", "--search", "sort:created-asc"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if out.returncode != 0:
            return []
        return json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def render(total: int, flow: dict, items: list) -> str:
    """One block a report can paste. Says UNKNOWN when a probe failed."""
    if total < 0:
        return "Deferred concerns: UNKNOWN (the probe failed — not an empty queue)."
    head = f"Deferred concerns open: {total}"
    if flow.get("net") is None:
        return head + " (flow UNKNOWN — probe failed)"
    arrow = "diverging" if flow["net"] > 0 else "draining" if flow["net"] < 0 else "level"
    lines = [f"{head} — {arrow} ({flow['opened']} in, {flow['closed']} out "
             f"since {flow['start']})"]
    for it in items:
        lines.append(f"  #{it['number']} {it['createdAt'][0:10]} "
                     f"{it['title'][:70]}")
    return "\n".join(lines)
