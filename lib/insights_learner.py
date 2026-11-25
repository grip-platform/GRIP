#!/usr/bin/env python3
"""
insights_learner.py — Reverse-dogfooding feedback loop.

Takes Claude Code's /insights report.html, versions it, extracts metrics,
computes delta from previous version, and feeds actionable learnings into
the GRIP learning pipeline.

This IS converge(GRIP_USAGE_DATA, actionable_improvements, learn_from_report).

Architecture:
  /insights (Claude Code) -> report.html
  insights_learner.py version -> report-YYYY-MM-DD.html
  insights_learner.py extract -> friction metrics + suggestions
  insights_learner.py delta -> compare with previous report
  insights_learner.py learn -> feed into learning/insights.jsonl
  insights_learner.py converge-check -> is the metric contracting?

The contraction metric: friction_density = friction_events / sessions_analysed
If this decreases across versions, the feedback loop IS a contraction mapping.

Gentner SMT verdict: STRUCTURAL MAP (not surface analogy) because:
  - converge() state = usage data (measurable)
  - converge() criterion = friction_density decreasing (falsifiable)
  - converge() improve = learn + genome encode (mechanical)
  - Fixed point = asymptotic minimal friction (provable limit exists)
"""

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path.home() / ".claude"
USAGE_DIR = CLAUDE_DIR / "usage-data"
FACETS_DIR = USAGE_DIR / "facets"
LEARNING_DIR = CLAUDE_DIR / "learning"
INSIGHTS_FILE = LEARNING_DIR / "insights.jsonl"
CONVERGENCE_FILE = LEARNING_DIR / "insights-convergence.jsonl"
REPORT_FILE = USAGE_DIR / "report.html"


def version_report() -> Path | None:
    """Copy report.html to report-YYYY-MM-DD.html. Returns versioned path."""
    if not REPORT_FILE.exists():
        print("No report.html found. Run /insights first.")
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    versioned = USAGE_DIR / f"report-{today}.html"

    # Don't overwrite if already versioned today
    if versioned.exists():
        # Check if content changed
        current_hash = hashlib.sha256(REPORT_FILE.read_bytes()).hexdigest()[:12]
        versioned_hash = hashlib.sha256(versioned.read_bytes()).hexdigest()[:12]
        if current_hash == versioned_hash:
            print(f"Report already versioned today: {versioned.name} (unchanged)")
            return versioned

    shutil.copy2(REPORT_FILE, versioned)
    print(f"Versioned: {versioned.name}")
    return versioned


def extract_metrics() -> dict[str, Any]:
    """Extract key metrics from facets directory."""
    if not FACETS_DIR.exists():
        return {"error": "No facets directory"}

    facet_files = sorted(FACETS_DIR.glob("*.json"))
    if not facet_files:
        return {"error": "No facet files"}

    total_sessions = len(facet_files)
    friction_events = 0
    friction_types: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    satisfaction: dict[str, int] = {}
    total_commits = 0
    total_messages = 0
    goals: dict[str, int] = {}

    for f in facet_files:
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Count friction events (friction_counts is {type: count} dict)
        fc = data.get("friction_counts", {})
        if isinstance(fc, dict):
            for ftype, count in fc.items():
                c = int(count) if isinstance(count, (int, float)) else 0
                friction_events += c
                friction_types[ftype] = friction_types.get(ftype, 0) + c

        # Count outcomes
        outcome = data.get("outcome", "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

        # Count satisfaction (user_satisfaction_counts is {level: count} dict)
        sat_counts = data.get("user_satisfaction_counts", {})
        if isinstance(sat_counts, dict):
            for level, count in sat_counts.items():
                c = int(count) if isinstance(count, (int, float)) else 0
                satisfaction[level] = satisfaction.get(level, 0) + c

        # Goals (goal_categories is {category: count} dict)
        goal_cats = data.get("goal_categories", {})
        if isinstance(goal_cats, dict):
            for goal, count in goal_cats.items():
                c = int(count) if isinstance(count, (int, float)) else 0
                goals[goal] = goals.get(goal, 0) + c

    # Compute derived metrics
    friction_density = round(friction_events / max(total_sessions, 1), 3)
    achievement_rate = round(
        (outcomes.get("fully_achieved", 0) + outcomes.get("mostly_achieved", 0))
        / max(total_sessions, 1),
        3,
    )

    # Satisfaction rate (satisfied + likely_satisfied + happy) / total responses
    total_sat_responses = sum(satisfaction.values())
    positive_sat = (
        satisfaction.get("satisfied", 0)
        + satisfaction.get("likely_satisfied", 0)
        + satisfaction.get("happy", 0)
    )
    satisfaction_rate = round(
        positive_sat / max(total_sat_responses, 1), 3
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sessions_analysed": total_sessions,
        "friction_events": friction_events,
        "friction_density": friction_density,
        "friction_types": dict(
            sorted(friction_types.items(), key=lambda x: -x[1])[:10]
        ),
        "achievement_rate": achievement_rate,
        "outcomes": outcomes,
        "satisfaction": satisfaction,
        "total_messages": total_messages,
        "satisfaction_rate": satisfaction_rate,
        "total_sat_responses": total_sat_responses,
        "top_goals": dict(sorted(goals.items(), key=lambda x: -x[1])[:10]),
    }


def compute_delta(current: dict, previous: dict | None) -> dict:
    """Compute metric deltas between two reports."""
    if not previous:
        return {"first_run": True, "metrics": current}

    for count_key in ("sessions_analysed", "facet_count"):
        if count_key in current and count_key in previous:
            count, old_count = current[count_key], previous[count_key]
            if count > 2 * old_count or count < old_count / 2:
                return {
                    "first_run": False,
                    "comparable": False,
                    "reason": f"not comparable: {count_key} changed from {old_count} to {count}",
                    "current": current,
                    "previous_timestamp": previous.get("timestamp"),
                }

    delta = {
        "comparable": True,
        "first_run": False,
        "sessions_delta": current.get("sessions_analysed", 0)
        - previous.get("sessions_analysed", 0),
        "friction_density_delta": round(
            current.get("friction_density", 0)
            - previous.get("friction_density", 0),
            4,
        ),
        "achievement_rate_delta": round(
            current.get("achievement_rate", 0)
            - previous.get("achievement_rate", 0),
            4,
        ),
        "is_contracting": current.get("friction_density", 1)
        < previous.get("friction_density", 1),
        "current": current,
        "previous_timestamp": previous.get("timestamp"),
    }
    return delta


def load_previous_metrics() -> dict | None:
    """Load the most recent convergence record."""
    if not CONVERGENCE_FILE.exists():
        return None

    last_line = None
    for line in CONVERGENCE_FILE.read_text().strip().split("\n"):
        line = line.strip()
        if line:
            last_line = line

    if not last_line:
        return None

    try:
        record = json.loads(last_line)
        return record.get("metrics") or record
    except json.JSONDecodeError:
        return None


def record_convergence(metrics: dict, delta: dict) -> None:
    """Append convergence record to JSONL."""
    CONVERGENCE_FILE.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": metrics["timestamp"],
        "metrics": metrics,
        "delta": delta,
        "is_contracting": delta.get("is_contracting", None),
        "friction_density": metrics.get("friction_density", 0),
        "achievement_rate": metrics.get("achievement_rate", 0),
        "snapshot": metrics,  # Full snapshot for next delta computation
    }

    with open(CONVERGENCE_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"Convergence recorded: friction_density={metrics['friction_density']}, "
          f"achievement_rate={metrics['achievement_rate']}")

    if delta.get("comparable") is False:
        print(f"  -> {delta['reason']}")
    elif delta.get("is_contracting"):
        print("  -> CONTRACTING (friction decreasing)")
    elif delta.get("first_run"):
        print("  -> BASELINE (first measurement)")
    else:
        print("  -> NOT CONTRACTING (friction stable or increasing)")


def parse_report_narrative(report_path: Path | None = None) -> list[dict]:
    """Extract actionable suggestions from report.html narrative content.

    The report has rich LLM-generated analysis that facet JSONs lack:
    friction categories, CLAUDE.md suggestions, feature recommendations,
    and usage patterns. This parses those into learning candidates.
    """
    if report_path is None:
        report_path = REPORT_FILE
    if not report_path.exists():
        return []

    try:
        html = report_path.read_text(errors="replace")
    except OSError:
        return []

    learnings = []
    now = datetime.now(timezone.utc).isoformat()

    # Extract CLAUDE.md suggestions (cmd-why divs contain the rationale)
    suggestions = re.findall(
        r'<div class="cmd-why">(.*?)</div>', html, re.DOTALL
    )
    for i, suggestion in enumerate(suggestions[:5]):
        clean = re.sub(r'<[^>]+>', '', suggestion).strip()
        if len(clean) < 30:
            continue
        lid = hashlib.sha256(f"report-suggestion-{i}-{now}".encode()).hexdigest()[:12]
        learnings.append({
            "id": lid,
            "text": f"Report suggestion: {clean[:200]}",
            "context": "insights_report_suggestion",
            "tags": ["insights-pipeline", "suggestion", "claude-md"],
            "created_at": now,
            "learning_score": 0.75,
            "source": "insights_report_narrative",
        })

    # Extract friction categories (friction-title + friction-desc)
    friction_titles = re.findall(
        r'<div class="friction-title">(.*?)</div>', html
    )
    friction_descs = re.findall(
        r'<div class="friction-desc">(.*?)</div>', html, re.DOTALL
    )
    for title, desc in zip(friction_titles[:5], friction_descs[:5]):
        clean_title = re.sub(r'<[^>]+>', '', title).strip()
        clean_desc = re.sub(r'<[^>]+>', '', desc).strip()
        lid = hashlib.sha256(f"report-friction-{clean_title}-{now}".encode()).hexdigest()[:12]
        learnings.append({
            "id": lid,
            "text": f"Friction: {clean_title} — {clean_desc[:150]}",
            "context": "insights_report_friction_category",
            "tags": ["insights-pipeline", "friction-category"],
            "created_at": now,
            "learning_score": 0.7,
            "source": "insights_report_narrative",
        })

    return learnings


def generate_learnings(metrics: dict, delta: dict) -> list[dict]:
    """Generate learning insights from metrics for ingestion into insights.jsonl."""
    learnings = []

    # Top friction patterns become learning candidates
    friction_types = metrics.get("friction_types", {})
    for ftype, count in list(friction_types.items())[:3]:
        density = round(count / max(metrics.get("sessions_analysed", 1), 1), 3)
        if density > 0.1:  # More than 10% of sessions
            learnings.append({
                "id": hashlib.sha256(
                    f"insights-friction-{ftype}-{metrics['timestamp']}".encode()
                ).hexdigest()[:12],
                "text": f"Friction pattern '{ftype}' occurs in {density*100:.1f}% "
                        f"of sessions ({count}/{metrics['sessions_analysed']}). "
                        f"Consider strengthening existing guardrails or creating "
                        f"new prevention skill.",
                "context": "insights_report_friction",
                "tags": ["insights-pipeline", "friction", ftype],
                "created_at": metrics["timestamp"],
                "learning_score": min(0.9, 0.5 + density),
                "source": "insights_learner",
            })

    if delta.get("comparable") is False:
        return learnings

    # Achievement rate trend
    if delta.get("achievement_rate_delta") is not None:
        d = delta["achievement_rate_delta"]
        if d < -0.05:
            learnings.append({
                "id": hashlib.sha256(
                    f"insights-achievement-drop-{metrics['timestamp']}".encode()
                ).hexdigest()[:12],
                "text": f"Achievement rate dropped by {abs(d)*100:.1f}% since last "
                        f"measurement. Investigate root cause in recent sessions.",
                "context": "insights_report_trend",
                "tags": ["insights-pipeline", "achievement", "regression"],
                "created_at": metrics["timestamp"],
                "learning_score": 0.85,
                "source": "insights_learner",
            })

    # Convergence status
    if not delta.get("first_run") and not delta.get("is_contracting"):
        learnings.append({
            "id": hashlib.sha256(
                f"insights-stall-{metrics['timestamp']}".encode()
            ).hexdigest()[:12],
            "text": "Insights feedback loop is NOT contracting — friction density "
                    "is stable or increasing. The self-improvement loop needs a "
                    "strategy change (Stromme velocity check: switch approach).",
            "context": "insights_report_convergence",
            "tags": ["insights-pipeline", "convergence", "stall"],
            "created_at": metrics["timestamp"],
            "learning_score": 0.9,
            "source": "insights_learner",
        })

    return learnings


def ingest_learnings(learnings: list[dict]) -> int:
    """Append learnings to insights.jsonl, de-duplicated by id.

    The dedup read (existing ids) + the append run together under a sidecar
    FileLock so two concurrent sessions can't BOTH pass the dedup check before
    either appends — a TOCTOU that produced duplicate insight rows across the
    5+ live GRIP sessions. Also dedups within the batch. Best-effort: if the
    lock library is unavailable the ingest still runs (unlocked, prior behaviour).
    """
    if not learnings:
        return 0

    INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        from file_lock import FileLock
        lock_cm = FileLock(INSIGHTS_FILE, timeout=10, sidecar=True)
    except ImportError:
        # ONLY a genuinely-absent lock lib degrades to unlocked — and it says so.
        # A broken install / AttributeError must NOT be swallowed into a silent
        # TOCTOU regression (AGORA #3464 Speaker C); those propagate.
        import contextlib
        print("[insights] WARNING: file_lock unavailable — ingesting UNLOCKED; "
              "dedup TOCTOU possible under concurrent sessions", file=sys.stderr)
        lock_cm = contextlib.nullcontext()

    new_count = 0
    with lock_cm:
        # Read existing ids INSIDE the lock so a concurrent appender can't slip
        # in between this dedup check and our append.
        existing_ids = set()
        if INSIGHTS_FILE.exists():
            # Stream line-by-line (bounded memory) — a large insights.jsonl must
            # not be slurped whole into RAM before the append (AGORA #3464 C).
            with open(INSIGHTS_FILE, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            existing_ids.add(json.loads(line).get("id", ""))
                        except json.JSONDecodeError:
                            pass

        with open(INSIGHTS_FILE, "a") as f:
            for learning in learnings:
                if learning["id"] not in existing_ids:
                    f.write(json.dumps(learning) + "\n")
                    existing_ids.add(learning["id"])  # dedup within this batch too
                    new_count += 1
                    print(f"  Ingested: {learning['text'][:80]}...")

    return new_count


def convergence_check() -> None:
    """Print convergence trajectory across all recorded measurements."""
    if not CONVERGENCE_FILE.exists():
        print("No convergence data yet. Run 'insights_learner.py run' first.")
        return

    records = []
    for line in CONVERGENCE_FILE.read_text().strip().split("\n"):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not records:
        print("No valid convergence records.")
        return

    print(f"\n{'='*60}")
    print(f"  INSIGHTS CONVERGENCE TRAJECTORY ({len(records)} measurements)")
    print(f"{'='*60}\n")
    print(f"  {'#':<4} {'Date':<12} {'Friction':<10} {'Achieve':<10} {'Status':<15}")
    print(f"  {'─'*4} {'─'*12} {'─'*10} {'─'*10} {'─'*15}")

    for i, r in enumerate(records):
        ts = r.get("timestamp", r.get("metrics", {}).get("timestamp", "?"))[:10]
        fd = r.get("friction_density", r.get("metrics", {}).get("friction_density", "?"))
        ar = r.get("achievement_rate", r.get("metrics", {}).get("achievement_rate", "?"))
        contracting = r.get("is_contracting")
        status = "CONTRACTING" if contracting else ("BASELINE" if contracting is None else "STABLE/UP")
        print(f"  {i+1:<4} {ts:<12} {fd:<10} {ar:<10} {status:<15}")

    # Overall verdict
    if len(records) >= 2:
        first_fd = records[0].get("friction_density", 1)
        last_fd = records[-1].get("friction_density", 1)
        if last_fd < first_fd:
            print(f"\n  VERDICT: CONTRACTING (friction {first_fd} -> {last_fd})")
            print(f"  The insights feedback loop is a genuine contraction mapping.")
        elif last_fd == first_fd:
            print(f"\n  VERDICT: FIXED POINT REACHED (friction stable at {last_fd})")
        else:
            print(f"\n  VERDICT: NOT CONTRACTING (friction {first_fd} -> {last_fd})")
            print(f"  Strategy change needed (Stromme velocity rule).")
    else:
        print(f"\n  VERDICT: BASELINE (need >= 2 measurements for trajectory)")

    print(f"\n{'='*60}\n")


def run() -> None:
    """Full pipeline: version -> extract -> delta -> learn -> record."""
    print("\n--- Insights Learner: Reverse-Dogfooding Pipeline ---\n")

    # Step 1: Version the report
    versioned = version_report()

    # Step 2: Extract metrics from facets
    print("\nExtracting metrics from facets...")
    metrics = extract_metrics()
    if "error" in metrics:
        print(f"Error: {metrics['error']}")
        return

    print(f"  Sessions: {metrics['sessions_analysed']}")
    print(f"  Friction events: {metrics['friction_events']}")
    print(f"  Friction density: {metrics['friction_density']}")
    print(f"  Achievement rate: {metrics['achievement_rate']}")
    print(f"  Satisfaction rate: {metrics['satisfaction_rate']}")
    print(f"  Top friction: {list(metrics.get('friction_types', {}).items())[:3]}")

    # Step 3: Load previous and compute delta
    print("\nComputing delta...")
    previous = load_previous_metrics()
    delta = compute_delta(metrics, previous)

    if delta.get("comparable") is False:
        print(f"  Delta {delta['reason']}")
    elif delta.get("first_run"):
        print("  First run — establishing baseline.")
    else:
        print(f"  Friction density delta: {delta.get('friction_density_delta', 0):+.4f}")
        print(f"  Achievement rate delta: {delta.get('achievement_rate_delta', 0):+.4f}")

    # Step 4: Generate learnings from metrics
    print("\nGenerating learnings from metrics...")
    learnings = generate_learnings(metrics, delta)

    # Step 4b: Parse report narrative for richer insights
    print("Parsing report narrative...")
    narrative_learnings = parse_report_narrative(versioned or REPORT_FILE)
    learnings.extend(narrative_learnings)
    print(f"  {len(narrative_learnings)} narrative insights extracted.")

    if learnings:
        new_count = ingest_learnings(learnings)
        print(f"  {new_count} new learnings ingested ({len(learnings)} total candidates).")
    else:
        print("  No new learnings (metrics within normal range).")

    # Step 5: Record convergence
    print("\nRecording convergence metric...")
    record_convergence(metrics, delta)

    # Step 6: Update genome fitness signals
    print("\nUpdating genome fitness signals...")
    update_genome_fitness(metrics)

    # Step 7: Facet evolution calibration (/evo)
    try:
        from evo import _is_inline_dispatch, run as evo_run
        if _is_inline_dispatch():
            print("Skipping /evo inline: rules/evo-dispatch-protocol.md requires an agent.")
        else:
            evo_run(max_depth=7, quiet=True)
    except ImportError:
        print("  evo.py not found — skipping facet calibration.")
    except Exception as e:
        print(f"  evo error: {e}")

    print("\n--- Pipeline complete ---\n")


def update_genome_fitness(
    metrics: dict[str, Any],
    genome_path: Path | None = None,
) -> bool:
    """Write friction/achievement fitness dimensions into the genome file.

    Computes:
      friction_fitness   = 1 / (1 + friction_density)   — normalised 0-1
      achievement_fitness = achievement_rate              — already 0-1

    These become gene-level fitness signals for the genome evolution loop,
    closing the reverse-dogfooding -> genome feedback path.

    Returns True if the genome was updated, False otherwise.
    """
    if genome_path is None:
        genome_path = CLAUDE_DIR / "data" / "genomes" / "laurie.json"

    if not genome_path.exists():
        print(f"  Genome file not found: {genome_path}")
        return False

    friction_density = metrics.get("friction_density", 0)
    friction_fitness = round(1.0 / (1.0 + friction_density), 3)
    achievement_fitness = metrics.get("achievement_rate", 0)

    def _apply(data: dict) -> dict:
        data["friction_fitness"] = friction_fitness
        data["achievement_fitness"] = achievement_fitness
        data["fitness_sessions_analysed"] = metrics.get("sessions_analysed", 0)
        data["fitness_updated_at"] = datetime.now(timezone.utc).isoformat()
        return data

    # #3462 bug #3: lock+atomic read-modify-write so concurrent sessions can't
    # lose the update (was a raw read + write_text race on the shared genome).
    from genome_store import update_genome as _locked_update, LockTimeoutError

    try:
        _locked_update(genome_path, _apply)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, LockTimeoutError) as exc:
        # Encrypted (git-crypt), corrupt, or write failure — skip silently.
        print(f"  Genome file unreadable (likely encrypted) or write failed: {exc}")
        return False
    print(f"  Genome updated: friction_fitness={friction_fitness}, "
          f"achievement_fitness={achievement_fitness}")
    return True


def check_stall_escalation(threshold: int = 3) -> dict[str, Any]:
    """Check if convergence has stalled for consecutive measurements.

    Reads the last N entries from evo-convergence.jsonl and checks if
    calibrated FD is non-contracting (stable or increasing) for all of them.

    Args:
        threshold: Number of consecutive non-contracting measurements to trigger.

    Returns:
        dict with 'stalled' (bool), 'consecutive' (int), 'suggestion' (str),
        and 'trajectory' (list of recent calibrated FD values).
    """
    evo_convergence = LEARNING_DIR / "evo-convergence.jsonl"
    if not evo_convergence.exists():
        return {"stalled": False, "consecutive": 0, "suggestion": "", "trajectory": []}

    records = []
    for line in evo_convergence.read_text().strip().split("\n"):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if len(records) < threshold:
        return {"stalled": False, "consecutive": len(records),
                "suggestion": f"Need {threshold}+ measurements", "trajectory": []}

    recent = records[-threshold:]
    trajectory = [r.get("calibrated", r.get("friction_density", 0)) for r in recent]

    # Check if each measurement is >= the previous one (non-contracting)
    non_contracting_count = 0
    for i in range(1, len(trajectory)):
        if trajectory[i] >= trajectory[i - 1]:
            non_contracting_count += 1

    # All transitions must be non-contracting to declare stall
    stalled = non_contracting_count >= (threshold - 1)

    suggestion = ""
    if stalled:
        suggestion = (
            f"Convergence stalled: calibrated FD non-contracting for "
            f"{threshold} consecutive measurements "
            f"({' -> '.join(f'{v:.3f}' for v in trajectory)}). "
            f"Strategy change needed (Stromme velocity rule)."
        )

    return {
        "stalled": stalled,
        "consecutive": non_contracting_count + 1,
        "suggestion": suggestion,
        "trajectory": trajectory,
    }


def suggest_schedule() -> str:
    """Suggest when to run /insights based on session activity."""
    session_meta_dir = USAGE_DIR / "session-meta"
    if not session_meta_dir.exists():
        return "weekly"

    meta_files = sorted(session_meta_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if len(meta_files) < 10:
        return "weekly"

    # Check recency of last measurement
    previous = load_previous_metrics()
    if previous:
        last_ts = previous.get("timestamp", "")
        try:
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            sessions_since = previous.get("sessions_analysed", 0)

            # After 50+ new sessions or 7+ days, suggest re-run
            current_count = len(meta_files)
            new_sessions = current_count - sessions_since
            if new_sessions >= 50 or days_since >= 7:
                return f"now (last: {days_since}d ago, +{new_sessions} sessions)"
            return f"in ~{max(1, 7 - days_since)}d ({new_sessions} new sessions since last)"
        except (ValueError, TypeError):
            pass

    return "now (no previous measurement)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 insights_learner.py <command>")
        print("  run              Full pipeline (version + extract + delta + learn)")
        print("  version          Version report.html only")
        print("  extract          Extract and print metrics only")
        print("  converge-check   Print convergence trajectory")
        print("  schedule         Suggest next /insights run time")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "run":
        run()
    elif cmd == "version":
        version_report()
    elif cmd == "extract":
        metrics = extract_metrics()
        print(json.dumps(metrics, indent=2))
    elif cmd == "converge-check":
        convergence_check()
    elif cmd == "schedule" or cmd == "suggest-schedule":
        suggestion = suggest_schedule()
        print(f"Next /insights suggested: {suggestion}")
    elif cmd == "stall-check":
        result = check_stall_escalation()
        if result["stalled"]:
            print(f"[STALL] {result['suggestion']}")
        else:
            print(f"No stall detected ({result['consecutive']} measurements checked)")
        print(f"Trajectory: {result['trajectory']}")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
