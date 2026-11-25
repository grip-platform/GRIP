#!/usr/bin/env python3
"""Session-start cache + batch-close helper for task reconciliation (W3).

Per the 4-family council: reconcile ONCE at session-start and cache (TTL); never
probe per-read — per-item per-read probing explodes latency and a fluke 200 risks
a false done. This module is the stateful layer over the pure W1/W2 mechanism in
``task_reality_reconcile``: it caches per-task verdicts keyed by task id with a
freshness TTL, re-probing only stale/absent entries, and exposes the
``PROVABLY-DONE`` close-set for the main session to batch-close via one
``TaskUpdate(status='completed')`` run (closing is main-session-only, so the
mechanism reconciles-and-proposes; the main session executes the close).

Time is injected (``now`` epoch seconds; defaults to ``time.time()``) so tests
are deterministic. Cache read/write fail-open: a missing/corrupt cache ⇒ full
re-reconcile, never a crash and never a false verdict.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402
from task_reality_reconcile import (  # noqa: E402
    DEFAULT_PROBES,
    Probes,
    ReconcileReport,
    TaskVerdict,
    classify_task,
    probe_task,
    render_report,
)

__all__ = [
    "reconcile_cached",
    "close_set",
    "session_start_surface",
    "default_cache_path",
]

_DEFAULT_TTL_SEC = 6 * 3600  # one working session; re-probe next session-start


def default_cache_path() -> str:
    """Canonical cache location (gitignored ``state/`` — local, per-machine)."""
    return str(get_claude_dir() / "state" / "task-reconcile" / "cache.json")


def _split_task(task) -> Tuple[str, str]:
    if isinstance(task, dict):
        return str(task["id"]), (task.get("text") or "")
    return str(task[0]), (task[1] or "")


def _load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # fail-open: missing/corrupt cache ⇒ full re-reconcile


def _save_cache(path: str, cache: dict) -> None:
    """Atomically persist the cache, MERGING onto whatever is on disk *now* so a
    concurrent session-start write (one of N parallel agents) is not clobbered —
    a read-modify-write lost-update mitigation (AGORA #3367 Speaker C). Our
    entries win on key conflict (they were just probed at ``now``, so they are at
    least as fresh); another writer's entries for tasks we did not touch are
    preserved. The residual race window (re-read → ``os.replace``) is microseconds
    AND benign: a lost entry only forces a re-probe next call, never a wrong
    verdict — the cache is a pure performance optimisation, not a source of truth.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    merged = _load_cache(path)  # re-read just-before-write to absorb concurrent entries
    merged.update(cache)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)  # atomic
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _is_cacheable(tv: TaskVerdict) -> bool:
    """A verdict is cacheable only if every probe that produced it was reliable.
    A fail-open verdict (any ok=False fact ⇒ a probe error/timeout) is transient:
    caching it would freeze a "couldn't check" result until TTL and mask a later
    backend recovery (AGORA #3367 Speaker C). Re-probe such tasks on the next call."""
    return all(f.ok for f in tv.facts)


def _resolve(task, cache: dict, now: float, ttl_sec: float,
             probes: Probes) -> Tuple[TaskVerdict, bool]:
    """Return (verdict, cache_written). A fresh cache entry is reused with no
    probe; a stale/absent one is probed + classified. Only fully-reliable
    verdicts are written back — a fail-open (probe-error) verdict is returned but
    NOT cached, so a recovered backend is picked up on the next call."""
    tid, text = _split_task(task)
    entry = cache.get(tid)
    if entry and (now - float(entry.get("probed_at", 0))) < ttl_sec:
        return TaskVerdict(tid, entry["verdict"], entry.get("evidence", "")), False
    tv = classify_task(tid, text, probe_task(text, probes))
    if not _is_cacheable(tv):
        return tv, False  # transient probe failure — don't freeze it
    cache[tid] = {"verdict": tv.verdict, "evidence": tv.evidence, "probed_at": now}
    return tv, True


def reconcile_cached(tasks, path: Optional[str] = None, *, now: Optional[float] = None,
                     ttl_sec: float = _DEFAULT_TTL_SEC,
                     probes: Probes = DEFAULT_PROBES) -> ReconcileReport:
    """Reconcile ``tasks`` using a per-task TTL cache. Fresh entries are reused
    (no probe); stale/absent entries are probed + classified and the cache is
    rewritten once. ``now`` is injected for determinism (defaults to wall clock)."""
    if path is None:
        path = default_cache_path()
    if now is None:
        now = time.time()
    cache = _load_cache(path)
    resolved = [_resolve(t, cache, now, ttl_sec, probes) for t in tasks]
    if any(written for _, written in resolved):
        _save_cache(path, cache)
    return ReconcileReport(tuple(tv for tv, _ in resolved))


def close_set(report: ReconcileReport) -> List[str]:
    """The PROVABLY-DONE task ids — batch-close candidates for one main-session
    ``TaskUpdate(status='completed')`` run."""
    return [tv.task_id for tv in report.done]


def session_start_surface(tasks, path: Optional[str] = None, *,
                          now: Optional[float] = None,
                          ttl_sec: float = _DEFAULT_TTL_SEC,
                          probes: Probes = DEFAULT_PROBES) -> Tuple[str, List[str]]:
    """Session-start surface: reconcile (cached) + render. Returns
    (rendered_report, close_set) so the caller surfaces the report and the main
    session batch-closes the PROVABLY-DONE ids."""
    report = reconcile_cached(tasks, path, now=now, ttl_sec=ttl_sec, probes=probes)
    return render_report(report), close_set(report)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: reconcile a JSON task list (``[{"id":..,"text":..}, ...]``) from a
    file arg or stdin against the session cache; print the report + close-set."""
    argv = list(sys.argv[1:] if argv is None else argv)
    src = argv[0] if argv else "-"
    try:  # fail clean on bad input — never a raw traceback
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        tasks = json.loads(raw)
    except (OSError, ValueError) as exc:  # JSONDecodeError ⊂ ValueError
        sys.stderr.write(f"task-reconcile: cannot read task JSON from {src!r}: {exc}\n")
        return 2
    rendered, closes = session_start_surface(tasks, now=time.time())
    sys.stdout.write(rendered)
    if closes:
        sys.stdout.write("\nClose-set (one TaskUpdate status=completed): "
                         + ", ".join(f"#{c}" for c in closes) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
