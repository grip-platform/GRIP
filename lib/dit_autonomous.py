#!/usr/bin/env python3
"""DIT Phase 8: Autonomous periodic measurement for Developer Improvement Tracking.

Runs via launchd 3x/day. Discovers recent PRs/commits, scores them, persists to
~/.claude/data/dit-measurements.jsonl, and alerts on statistical anomalies.

Usage:
    python3 -m lib.dit_autonomous
    python3 -m lib.dit_autonomous --since-hours 8
"""

import argparse
import json
import os
import statistics
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.dit_scorer import score_pr, save_score


# -- Paths -----------------------------------------------------------------

def _home() -> Path:
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def _measurements_path() -> Path:
    path = _home() / ".claude" / "data" / "dit-measurements.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# -- Activity Discovery ----------------------------------------------------

def _run(args: list[str], timeout: int = 30) -> str:
    """Run subprocess, return stdout or empty string on failure."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _parse_pr_refs(text: str) -> list[tuple[str, str, int]]:
    """Extract (org, repo, pr_number) tuples from gh pr list JSON output."""
    try:
        items = json.loads(text) if text else []
    except json.JSONDecodeError:
        return []
    refs = []
    for item in items:
        url = item.get("url", "")
        # url format: https://github.com/org/repo/pull/N
        parts = url.rstrip("/").split("/")
        if len(parts) >= 7 and parts[-2] == "pull":
            try:
                refs.append((parts[-4], parts[-3], int(parts[-1])))
            except (ValueError, IndexError):
                continue
    return refs


def _fetch_repo_prs(org: str, repo: str) -> list[tuple[str, str, int]]:
    """Fetch merged PRs for a single repo via gh CLI."""
    raw = _run([
        "gh", "pr", "list",
        "--repo", f"{org}/{repo}",
        "--state", "merged",
        "--limit", "20",
        "--json", "url,mergedAt,author",
    ])
    return _parse_pr_refs(raw)


def _fetch_cross_repo_prs(since_hours: int) -> list[tuple[str, str, int]]:
    """Fetch recently-merged PRs across repos via gh search."""
    since_ts = _run(["date", "-u", f"-v-{since_hours}H", "+%Y-%m-%dT%H:%M:%SZ"])
    if not since_ts:
        return []
    raw = _run([
        "gh", "search", "prs",
        "--merged-at", f">={since_ts}",
        "--limit", "30",
        "--json", "url",
    ], timeout=45)
    return _parse_pr_refs(raw)


def scan_recent_activity(since_hours: int = 8) -> list[tuple[str, str, int]]:
    """Discover PRs merged in the last `since_hours` hours, deduplicated."""
    candidates: list[tuple[str, str, int]] = []

    remote_url = _run(["git", "remote", "get-url", "origin"])
    if remote_url:
        slug = remote_url.replace("git@github.com:", "").replace("https://github.com/", "").rstrip(".git")
        parts = slug.split("/")
        if len(parts) == 2:
            candidates.extend(_fetch_repo_prs(parts[0], parts[1]))

    candidates.extend(_fetch_cross_repo_prs(since_hours))
    # dict.fromkeys preserves order and deduplicates in O(n)
    return list(dict.fromkeys(candidates))


# -- Measurement -----------------------------------------------------------

@dataclass
class Measurement:
    """Single autonomous measurement record."""

    measured_at: str
    org: str
    repo: str
    pr_number: int
    author: str
    total: int
    dimension_scores: dict

    @classmethod
    def from_pr_score(cls, pr_score: PRScore) -> "Measurement":
        dim_scores = {d.name: d.effective for d in pr_score.dimensions}
        return cls(
            measured_at=datetime.now(timezone.utc).isoformat(),
            org=pr_score.org,
            repo=pr_score.repo,
            pr_number=pr_score.pr_number,
            author=pr_score.author,
            total=pr_score.total,
            dimension_scores=dim_scores,
        )


def _load_measured_keys(path: Path) -> set[tuple[str, str, int]]:
    """Return set of (org, repo, pr_number) already persisted. O(n) single pass."""
    if not path.exists():
        return set()
    keys: set[tuple[str, str, int]] = set()
    with open(path) as f:
        for line in f:  # O(1): single pass over file lines
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                keys.add((e["org"], e["repo"], e["pr_number"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def _score_and_persist(org: str, repo: str, pr_number: int, path: Path) -> Optional[Measurement]:
    """Score one PR and append result to measurements file. Returns None on error."""
    try:
        pr_score = score_pr(org, repo, pr_number)
    except Exception as exc:  # noqa: BLE001
        print(f"[DIT] skip PR #{pr_number} ({org}/{repo}): {exc}")
        return None
    save_score(pr_score)
    m = Measurement.from_pr_score(pr_score)
    with open(path, "a") as f:
        f.write(json.dumps(asdict(m)) + "\n")
    print(f"[DIT] scored PR #{pr_number} ({org}/{repo}) author={m.author} total={m.total}")
    return m


def run_measurement(refs: Optional[list[tuple[str, str, int]]] = None,
                    since_hours: int = 8) -> list[Measurement]:
    """Score each discovered PR and persist to dit-measurements.jsonl."""
    if refs is None:
        refs = scan_recent_activity(since_hours)
    path = _measurements_path()
    measured = _load_measured_keys(path)
    results = []
    for org, repo, pr_number in refs:
        if (org, repo, pr_number) in measured:
            continue
        m = _score_and_persist(org, repo, pr_number, path)
        if m is not None:
            results.append(m)
    return results


# -- Alerting --------------------------------------------------------------

def _index_history_by_author(history: list[dict]) -> dict[str, list[int]]:
    """Build author -> [totals] index in O(n). Call once, reuse per measurement."""
    index: dict[str, list[int]] = {}
    for h in history:  # O(1): single pass to build hash index
        author = h.get("author", "")
        total = h.get("total")
        if author and isinstance(total, int):
            index.setdefault(author, []).append(total)
    return index


def check_alerts(latest: Measurement, author_totals: list[int]) -> list[str]:
    """Alert if latest.total deviates from rolling mean by > 1 std dev.

    Args:
        latest: The freshly-scored measurement.
        author_totals: Pre-indexed list of prior totals for latest.author.

    Returns:
        List of alert strings (empty = no alerts).
    """
    if len(author_totals) < 3:
        return []
    mean = statistics.mean(author_totals)
    try:
        std = statistics.stdev(author_totals)
    except statistics.StatisticsError:
        return []
    if std == 0 or abs(latest.total - mean) <= std:
        return []
    direction = "above" if latest.total > mean else "below"
    return [
        f"[DIT ALERT] {latest.author} PR #{latest.pr_number}: "
        f"score {latest.total} is {direction} rolling mean "
        f"({mean:.1f} \u00b1 {std:.1f}) by {abs(latest.total - mean):.1f} points"
    ]


def _load_history() -> list[dict]:
    """Load all measurement records from dit-measurements.jsonl."""
    path = _measurements_path()
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:  # O(1): single pass over file lines
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# -- CLI / launchd entrypoint ----------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="DIT autonomous periodic measurement")
    parser.add_argument(
        "--since-hours", type=int, default=8,
        help="Scan PRs from the last N hours (default: 8)",
    )
    args = parser.parse_args()

    print(f"[DIT] starting measurement run — scanning last {args.since_hours}h")
    measurements = run_measurement(since_hours=args.since_hours)

    if not measurements:
        print("[DIT] no new PRs to score")
        return

    history = _load_history()
    # Pre-compute exclusion set (O(k)) then filter history (O(n)) — no nesting
    new_pr_nums = {m.pr_number for m in measurements}
    prior_history = [h for h in history if h.get("pr_number") not in new_pr_nums]
    history_index = _index_history_by_author(prior_history)
    for m in measurements:  # O(1): bounded by recently-merged PRs (≤50)
        alerts = check_alerts(m, history_index.get(m.author, []))
        for alert in alerts:  # O(1): alerts list is always ≤1 item
            print(alert)

    print(f"[DIT] run complete — {len(measurements)} PR(s) scored")


if __name__ == "__main__":
    main()
