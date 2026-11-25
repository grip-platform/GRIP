"""One tick: run the sources that are due, diff, format, send, log.

Seeding is per source. A source's first SUCCESSFUL collection records what exists
and alerts on none of it: the historical backlog is never pushed. The first run
(no state file) or ``--seed`` seeds every source at once; a source that could not
be read on that tick (gog refused, gh logged out) has no ``last_run`` and keeps
seeding until it succeeds, while the sources that did succeed run normally
(Codex on GRIP#8633). An operational change, such as the once-a-day "mail check
failing" line, is delivered even from a seeding source: it is news about the
monitor, not backlog.

Delivery. Immediate changes become lines on the tick that finds them. Roll-up
changes wait in ``state.rollup.items`` and fold into one line on the first tick
at or after 08:00 SAST, only on days with movement. Lines the transport could
not deliver (stop marker, WhatsApp down) wait in ``pending_alerts`` for the next
tick and are dropped after 24 h with a log line. A send the transport accepted
but could not verify counts as sent and is logged as unverified: holding it
would resend the same line every tick on a host with no delivery history.

One tick at a time: a lock file beside the state file (``state.lock``) is held
for the whole load-collect-send-save transaction, so a scheduled tick and a
manual ``--once`` cannot both send the same change or overwrite each other's
state. The second one logs that it was locked out and does nothing. The lock
is ``flock`` through ``lib/_compat.py``, which is the real module on POSIX and a
LockFileEx lock on Windows (GRIP#8759).

A dry run prints what it would send and writes nothing: no state, no log, no
message.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from _compat import fcntl  # the real module on POSIX, the LockFileEx lock on Windows

from .model import IMMEDIATE, Result
from .notify import clip, format_line, send_lines
from .schedule import due, iso, next_due, parse_iso, rollup_due, sast, sast_day
from .sources import SOURCES, github_repo, search_console, vercel_site
from .state import PENDING_TTL_S, append_log, default_state, load, save


@dataclass
class Runtime:
    config: dict
    state_path: Path
    log_path: Path
    deps: dict[str, object]           # source name -> that source's deps object
    send_fn: Callable[[str], dict]    # text -> {"ok": bool, ...}
    stop_marker: Path
    out: Callable[[str], None] = print


@dataclass
class _Run:
    name: str
    result: Result
    duration_ms: int
    seed: bool  # this source's first successful collection is its baseline, never alerted


def lock_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.stem + ".lock")


@contextmanager
def _lock(path: Path) -> Iterator[bool]:
    """True while this process holds the tick lock; False when another one does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _collect(rt: Runtime, state: dict, name: str, now: datetime, forced: bool, seeding: bool) -> _Run | None:
    sl = state["sources"].get(name, {})
    deps = rt.deps.get(name)
    if deps is None or (not forced and not due(name, now, state, rt.config)):
        return None  # no I/O wired for this source (a partial Runtime) or not yet due
    t0 = time.monotonic()
    result = SOURCES[name].collect(sl, deps, now=now, config=rt.config)
    return _Run(name, result, int((time.monotonic() - t0) * 1000), seed=seeding or not sl.get("last_run"))


def _apply(state: dict, run: _Run, now: datetime) -> None:
    sl = run.result.slice
    if run.result.skipped is None:
        sl["last_run"] = iso(now)
    sl["last_skipped"] = run.result.skipped
    state["sources"][run.name] = sl


def _log_run(rt: Runtime, run: _Run, now: datetime, alerts_sent: int) -> None:
    append_log(rt.log_path, {
        "ts": iso(now), "source": run.name, "due": True, "seed": run.seed,
        "ok": run.result.skipped is None, "skipped": run.result.skipped,
        "changes": len(run.result.changes), "alerts_sent": alerts_sent,
        "duration_ms": run.duration_ms, "detail": run.result.detail,
    })


def build_rollup_line(state: dict, now: datetime, config: dict) -> str | None:
    """The 08:00 line from the queued roll-up items and the last traffic read,
    or None when nothing moved."""
    stars: dict[str, list[str]] = {}
    for item in state.get("rollup", {}).get("items", []):
        if item.get("kind") == "star":
            stars.setdefault(item["repo"], []).append(item["login"])
    day = (now.astimezone(timezone.utc) - timedelta(days=1)).date().isoformat()  # GitHub counts UTC days
    labels = config.get("github", {}).get("labels", {})
    repos = state["sources"].get(github_repo.NAME, {}).get("repos", {})
    parts = []
    for repo in config.get("github", {}).get("repos", list(repos)):  # config order, not the saved (sorted) order
        r = repos.get(repo)
        if r is None:
            continue
        bit = github_repo.rollup_bits(repo, r, stars.get(repo, []), day, labels.get(repo, github_repo.repo_short(repo)))
        if bit:
            parts.append(bit)
    yesterday_sast = (sast(now) - timedelta(days=1)).date().isoformat()  # Vercel days are counted in SAST
    parts += vercel_site.rollup_clauses(state["sources"].get(vercel_site.NAME, {}), yesterday_sast)
    parts += search_console.rollup_clauses(state["sources"].get(search_console.NAME, {}))
    return clip("radar 08:00: " + "; ".join(parts)) if parts else None


def _live_pending(state: dict, now: datetime) -> tuple[list[dict], int]:
    kept = []
    for entry in state.get("pending_alerts", []):
        first = parse_iso(entry.get("first_ts"))
        if first is None or (now - first).total_seconds() <= PENDING_TTL_S:
            kept.append(entry)
    return kept, len(state.get("pending_alerts", [])) - len(kept)


def _held(pending: list[dict], held_lines: list[str], now: datetime) -> list[dict]:
    first_ts = {p["line"]: p.get("first_ts") for p in pending}
    return [{"line": line, "first_ts": first_ts.get(line) or iso(now)} for line in held_lines]


def _counted(run: _Run, change: dict) -> bool:
    """A seeding source's changes are baseline, except operational ones."""
    return not run.seed or bool(change.get("operational"))


def _changes(runs: list[_Run]) -> tuple[list[dict], list[dict]]:
    """(immediate, roll-up) across the tick's runs, baseline changes left out."""
    counted = [c for run in runs for c in run.result.changes if _counted(run, c)]
    return ([c for c in counted if c["delivery"] == IMMEDIATE],
            [c for c in counted if c["delivery"] != IMMEDIATE])


def _seed_lines(rt: Runtime, seeds: list[_Run], dry_run: bool, fresh: bool, why: str | None) -> None:
    done = [r for r in seeds if r.result.skipped is None]
    failed = [r for r in seeds if r.result.skipped]
    if done:
        desc = ", ".join(f"{r.name} ({SOURCES[r.name].describe(r.result)})" for r in done)
        verb = ("first run: would seed" if fresh else "would seed") if dry_run else "seeded"
        note = f" (previous state {why})" if why not in (None, "absent") else ""
        rt.out(f"[radar] {verb} {desc}{note}")
    for run in failed:
        rt.out(f"[radar] not yet seeded: {run.name} (skipped: {run.result.skipped}), retried next tick")


def _dry_report(rt: Runtime, live: list[_Run], lines: list[str], switched: list[dict]) -> dict:
    for run in live:
        verdict = f"skipped: {run.result.skipped}" if run.result.skipped else "ok"
        rt.out(f"[radar] {run.name}: {verdict}, changes={len(run.result.changes)}, detail={run.result.detail}")
    for line in lines:
        rt.out(f"[radar] would send: {line}")
    for c in switched:
        rt.out(f"[radar] would switch {c['source']} to thread_watch (thread {c['thread_id']})")
    if not lines and live:
        rt.out("[radar] nothing to send")
    return {"dry_run": True, "sources": [r.name for r in live], "lines": lines, "sent": [], "held": [], "reason": None}


def _rollup_lines(state: dict, now: datetime, config: dict) -> list[str]:
    """The 08:00 line when it is time, resetting the queue either way."""
    if not rollup_due(now, state):
        return []
    line = build_rollup_line(state, now, config)
    state["rollup"] = {"last_day": sast_day(now), "items": []}
    return [line] if line else []


def _alerts_of(run: _Run) -> int:
    return sum(1 for c in run.result.changes if c["delivery"] == IMMEDIATE and _counted(run, c))


def _deliver(rt: Runtime, state: dict, runs: list[_Run], now: datetime, pending: list[dict],
             all_lines: list[str], dropped: int) -> dict:
    outcome = send_lines(all_lines, rt.send_fn, stop_marker=rt.stop_marker)
    state["pending_alerts"] = _held(pending, outcome["held"], now)
    for run in runs:
        _log_run(rt, run, now, alerts_sent=_alerts_of(run) if outcome["sent"] else 0)
    append_log(rt.log_path, {"ts": iso(now), "source": "tick", "due": [r.name for r in runs],
                             "sent": len(outcome["sent"]), "held": len(outcome["held"]),
                             "dropped": dropped, "reason": outcome["reason"], "verified": outcome.get("verified")})
    save(rt.state_path, state)
    return {"dry_run": False, "sources": [r.name for r in runs], "lines": all_lines,
            "sent": outcome["sent"], "held": outcome["held"], "reason": outcome["reason"],
            "verified": outcome.get("verified")}


def _finish(rt: Runtime, state: dict, runs: list[_Run], now: datetime, dry_run: bool, fresh: bool,
            why: str | None) -> dict:
    seeds = [r for r in runs if r.seed]
    if seeds:
        _seed_lines(rt, seeds, dry_run, fresh, why)
    if fresh:
        state["seeded_at"] = iso(now)
        state["rollup"]["last_day"] = sast_day(now)  # the first roll-up is tomorrow's, not the seed tick's
    immediate, deferred = _changes(runs)
    state["rollup"]["items"].extend(deferred)
    lines = [format_line(c) for c in immediate] + _rollup_lines(state, now, rt.config)
    pending, dropped = _live_pending(state, now)
    all_lines = [p["line"] for p in pending] + lines
    extra = {"seeded": fresh, "late_seeds": [r.name for r in seeds if not fresh and r.result.skipped is None]}
    if dry_run:
        live = [r for r in runs if not r.seed]
        return {**_dry_report(rt, live, all_lines, [c for c in immediate if c.get("switched")]), **extra}
    return {**_deliver(rt, state, runs, now, pending, all_lines, dropped), **extra}


def _tick(rt: Runtime, now: datetime, dry_run: bool, seed: bool, only: str | None) -> dict:
    state, why = load(rt.state_path)
    fresh = seed or state is None
    if fresh:
        state = default_state()
    runs = []
    for name in ([only] if only else list(SOURCES)):
        run = _collect(rt, state, name, now, forced=fresh or bool(only), seeding=fresh)
        if run is not None:
            _apply(state, run, now)
            runs.append(run)
    return _finish(rt, state, runs, now, dry_run, fresh, why)


def run_tick(rt: Runtime, *, now: datetime | None = None, dry_run: bool = False, seed: bool = False,
             only: str | None = None) -> dict:
    """One tick. ``only`` runs that source now, due or not. A tick that finds the
    lock held by another returns ``{"locked": True}`` and does nothing."""
    now = now or datetime.now(timezone.utc)
    with _lock(lock_path(rt.state_path)) as held:
        if not held:
            rt.out("[radar] another tick holds the lock; nothing done")
            if not dry_run:
                append_log(rt.log_path, {"ts": iso(now), "source": "tick", "locked": True})
            return {"locked": True, "seeded": False, "dry_run": dry_run, "sources": [], "lines": [],
                    "sent": [], "held": [], "reason": "locked", "late_seeds": []}
        return _tick(rt, now, dry_run, seed, only)


def status_report(rt: Runtime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    state, why = load(rt.state_path)
    if state is None:
        return f"[radar] no state ({why}): run `bin/grip-radar --seed` first"
    rows = [("source", "mode", "last_run", "next_due", "skip/note")]
    for name in SOURCES:
        sl = state["sources"].get(name, {})
        nxt = iso(next_due(name, now, state, rt.config)) if sl.get("last_run") else "now"
        rows.append((name, sl.get("mode", "-"), sl.get("last_run") or "-", nxt,
                     sl.get("last_skipped") or sl.get("note") or "-"))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    table = "\n".join("  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows)
    rollup = state.get("rollup", {})
    unseeded = [name for name in SOURCES if not state["sources"].get(name, {}).get("last_run")]
    foot = (f"seeded_at={state.get('seeded_at')} pending_alerts={len(state.get('pending_alerts', []))} "
            f"rollup.last_day={rollup.get('last_day')} rollup.items={len(rollup.get('items', []))}"
            + (f" not_yet_seeded={','.join(unseeded)}" if unseeded else ""))
    return table + "\n" + foot
