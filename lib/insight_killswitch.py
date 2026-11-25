"""W11 — three kill switches for the auto-insight pipeline.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W11. Each kill-switch
auto-disables the deny-tier enforcement when its threshold is breached,
preventing the system from becoming net-negative under adverse conditions:

K1 — False-positive cascade
    Operator-marked "wrong propagation" 7-day count exceeds 25% of total
    propagations in the same window. Revert deny-on-ship hook to
    advisory-only for 7 days; alert via lib.grip_notify.

K2 — Bypass overuse
    GRIP_INSIGHT_PROPAGATION_BYPASS=1 events average >3/day over 5
    consecutive days. Auto-revert + alert (ratio > 0.20 means the gate
    is theatre; H-AUTO-8 falsifier).

K3 — Canonical pollution
    rules/operational-context.md OR MEMORY.md grows >20%/week. Auto-
    disable propagation to that target until manual review.

Apply via ``check_killswitches()`` + ``apply_killswitches()``. Wired as
hourly launchd job (state/launchd/com.codetonight.grip.insight-killswitch.plist).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from path_encoding import encode_project_path
from claude_dir import get_claude_dir


_CLAUDE_DIR = get_claude_dir()
_DATA_DIR = _CLAUDE_DIR / "data"
_LOGS_DIR = _CLAUDE_DIR / "logs"

# Killswitch thresholds (operator-tunable via constants)
K1_FP_RATIO_THRESHOLD = 0.25   # >25% wrong over 7d trips K1
K2_BYPASS_PER_DAY_THRESHOLD = 3  # >3/day averaged over K2_WINDOW_DAYS
K2_WINDOW_DAYS = 5
K3_GROWTH_PERCENT_PER_WEEK = 20.0  # >20%/wk growth trips K3
K3_WATCHED_TARGETS = (
    "rules/operational-context.md",
    f"projects/{encode_project_path(_CLAUDE_DIR)}/memory/MEMORY.md",
)


@dataclass(frozen=True)
class KillswitchEvent:
    name: str            # K1 / K2 / K3
    tripped: bool
    threshold: float
    observed: float
    window: str          # human-readable e.g. "7d", "5d avg", "1w"
    remedy: str          # what apply_killswitches will do


# ---------------------------------------------------------------------------
# Helpers — read JSONL telemetry files
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
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


def _within_seconds(ts_iso: str, max_seconds: float, *, now: float | None = None) -> bool:
    """True iff the ISO timestamp is within ``max_seconds`` of ``now``."""
    now = now if now is not None else time.time()
    iso = ts_iso.strip()
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        import datetime as _dt
        ts = _dt.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return False
    return ts >= now - max_seconds


# ---------------------------------------------------------------------------
# K1 — false-positive cascade
# ---------------------------------------------------------------------------


def _k1_check(
    feedback_path: Path | None = None,
    propagation_path: Path | None = None,
    *,
    now: float | None = None,
) -> KillswitchEvent:
    """Compute K1 trip status from operator feedback + propagation log."""
    feedback_path = feedback_path or _DATA_DIR / "auto-insight-feedback.jsonl"
    propagation_path = propagation_path or _CLAUDE_DIR / "learning" / "propagation_log.jsonl"

    feedback = _read_jsonl(feedback_path)
    propagations = _read_jsonl(propagation_path)
    window_seconds = 7 * 86400

    wrong_count = sum(
        1 for r in feedback
        if _within_seconds(r.get("ts", ""), window_seconds, now=now)
        and r.get("action", "").endswith("wrong")
    )
    total_count = sum(
        1 for r in propagations
        if _within_seconds(r.get("timestamp", ""), window_seconds, now=now)
    )
    ratio = wrong_count / max(1, total_count)
    tripped = ratio > K1_FP_RATIO_THRESHOLD
    return KillswitchEvent(
        name="K1",
        tripped=tripped,
        threshold=K1_FP_RATIO_THRESHOLD,
        observed=ratio,
        window="7d",
        remedy=(
            "Revert hooks/insight-deny-on-ship-hook.py to advisory-only; "
            "alert operator. Re-evaluate after 7 days."
        ),
    )


# ---------------------------------------------------------------------------
# K2 — bypass overuse
# ---------------------------------------------------------------------------


def _k2_check(
    bypass_log: Path | None = None,
    *,
    now: float | None = None,
) -> KillswitchEvent:
    """K2 trips when bypass count averages >3/day over 5 consecutive days."""
    bypass_log = bypass_log or _LOGS_DIR / "insight-propagation-bypass.log"
    records = _read_jsonl(bypass_log)
    window_seconds = K2_WINDOW_DAYS * 86400
    recent = [r for r in records if _within_seconds(r.get("ts", ""), window_seconds, now=now)]
    avg_per_day = len(recent) / float(K2_WINDOW_DAYS)
    tripped = avg_per_day > K2_BYPASS_PER_DAY_THRESHOLD
    return KillswitchEvent(
        name="K2",
        tripped=tripped,
        threshold=float(K2_BYPASS_PER_DAY_THRESHOLD),
        observed=avg_per_day,
        window=f"{K2_WINDOW_DAYS}d avg",
        remedy=(
            "Revert deny-on-ship hook to advisory; alert operator. "
            "Bypass overuse means the gate is theatre — investigate why "
            "operators routinely bypass."
        ),
    )


# ---------------------------------------------------------------------------
# K3 — canonical pollution
# ---------------------------------------------------------------------------


def _k3_check(
    snapshot_path: Path | None = None,
    *,
    now: float | None = None,
) -> KillswitchEvent:
    """K3 trips on >20%/wk growth in any watched canonical file."""
    snapshot_path = snapshot_path or _DATA_DIR / "canonical-growth.jsonl"
    snapshots = _read_jsonl(snapshot_path)
    if not snapshots:
        return KillswitchEvent(
            name="K3", tripped=False,
            threshold=K3_GROWTH_PERCENT_PER_WEEK, observed=0.0,
            window="1w", remedy="No data yet — taking baseline snapshot.",
        )
    growth = _max_growth_pct(snapshots, now=now)
    tripped = growth > K3_GROWTH_PERCENT_PER_WEEK
    return KillswitchEvent(
        name="K3",
        tripped=tripped,
        threshold=K3_GROWTH_PERCENT_PER_WEEK,
        observed=growth,
        window="1w",
        remedy=(
            "Auto-disable propagation to the bloated target until manual "
            "review. Likely cause: classifier false-positive cascade — "
            "check K1 status."
        ),
    )


def _max_growth_pct(snapshots: list[dict], *, now: float | None = None) -> float:
    """Maximum 7-day growth percentage across watched targets."""
    now = now if now is not None else time.time()
    week_seconds = 7 * 86400
    by_target: dict[str, list[tuple[float, int]]] = {}
    for snap in snapshots:
        target = snap.get("target", "")
        ts_iso = snap.get("ts", "")
        line_count = snap.get("line_count")
        if not target or line_count is None:
            continue
        try:
            import datetime as _dt
            iso = ts_iso[:-1] + "+00:00" if ts_iso.endswith("Z") else ts_iso
            ts = _dt.datetime.fromisoformat(iso).timestamp()
        except ValueError:
            continue
        by_target.setdefault(target, []).append((ts, int(line_count)))
    max_growth = 0.0
    for target, points in by_target.items():
        max_growth = max(max_growth, _growth_for_target(points, now, week_seconds))
    return max_growth


def _growth_for_target(
    points: list[tuple[float, int]], now: float, window_seconds: float
) -> float:
    """Percent growth in line_count over the last ``window_seconds``."""
    points = sorted(points, key=lambda p: p[0])
    in_window = [p for p in points if p[0] >= now - window_seconds]
    older = [p for p in points if p[0] < now - window_seconds]
    if not in_window:
        return 0.0
    if not older:
        baseline_count = in_window[0][1]
    else:
        baseline_count = older[-1][1]
    latest_count = in_window[-1][1]
    if baseline_count <= 0:
        return 0.0
    return 100.0 * (latest_count - baseline_count) / float(baseline_count)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_killswitches(
    *, now: float | None = None,
    feedback_path: Path | None = None,
    propagation_path: Path | None = None,
    bypass_log: Path | None = None,
    snapshot_path: Path | None = None,
) -> list[KillswitchEvent]:
    """Evaluate all three kill switches; return event list."""
    return [
        _k1_check(feedback_path, propagation_path, now=now),
        _k2_check(bypass_log, now=now),
        _k3_check(snapshot_path, now=now),
    ]


def apply_killswitches(
    events: list[KillswitchEvent], *, audit_log: Path | None = None
) -> int:
    """For each tripped event, append to the audit log and send a notification.

    Returns the number of trip events recorded. The actual hook-revert is
    delegated to the operator (idempotent: even if the log records repeats,
    the hook revert is already in place after the first trip until manually
    re-armed).
    """
    audit_log = audit_log or _DATA_DIR / "auto-insight-killswitch.jsonl"
    audit_log.parent.mkdir(parents=True, exist_ok=True)
    tripped = [e for e in events if e.tripped]
    if not tripped:
        return 0
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with audit_log.open("a", encoding="utf-8") as fh:
        for e in tripped:
            fh.write(json.dumps({
                "ts": ts,
                "killswitch": e.name,
                "tripped": True,
                "threshold": e.threshold,
                "observed": e.observed,
                "window": e.window,
                "remedy": e.remedy,
            }) + "\n")
    _notify(tripped)
    return len(tripped)


def _notify(events: list[KillswitchEvent]) -> None:
    """Best-effort notification via grip_notify (no-op if module missing)."""
    try:
        from grip_notify import notify  # type: ignore
        title = f"Auto-insight killswitch tripped ({len(events)})"
        body = "\n".join(
            f"{e.name}: observed={e.observed:.3f} > {e.threshold} ({e.window})"
            for e in events
        )
        notify(title=title, body=body, tier="warning")
    except Exception:
        pass


def main() -> int:
    """Entry point for cron — check + apply, exit 0."""
    events = check_killswitches()
    apply_killswitches(events)
    print(json.dumps([
        {"name": e.name, "tripped": e.tripped, "observed": e.observed}
        for e in events
    ]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
