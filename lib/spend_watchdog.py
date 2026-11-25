"""Advisory watchdog for a stalled or fast-burning Workflow run.

Origin: rules/fan-out-spends-the-weekly-limit.md. A Workflow run's depth-8
seats each made sixteen requests over four and a half hours with no
assistant turn ever landing -- the exact shape ``assess`` is built to catch.

Every function here is pure and takes paths or dicts it is handed, so tests
build the whole directory shape under ``tmp_path`` rather than depending on
a real session. Nothing here ever raises out of ``assess``: a missing
directory or an unreadable file reads as "nothing to report", never as an
error that could block anything, because this watchdog has no deny path at
all -- it can only print one advisory line.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import lib.workflow_stall_metrics as workflow_stall_metrics  # noqa: E402

STALL_MINUTES = 30
STALL_MIN_REQUESTS_PER_AGENT = 3
BURN_TOKENS = 5_000_000
BURN_WEEKLY_RISE = 0.05

# Every category the HAL ledger's usage object carries (confirmed against a
# live row, GRIP#8698). The previous version summed only cache_creation and
# output, so a session dominated by cache_read (the real incident's own
# shape: 103.6M cache_read against 9.9M output) under-counted its own spend
# by over 90% -- the exact thing BURN_TOKENS exists to catch.
_USAGE_FIELDS = ("cache_creation_tokens", "cache_read_tokens", "input_tokens", "output_tokens")

# Mirrors grip-worktree.sh's _SHARED_PROJECT_SUBDIRS: these two directory
# names under a project dir are shared infrastructure, never a session.
_EXCLUDED_PROJECT_SUBDIRS = {"memory", "grip"}

_EMPTY_WINDOW = {"tokens": 0, "weekly_delta": None, "weekly_latest": None}


def _parse_iso(ts: object) -> float | None:
    """Epoch seconds for an ISO-8601 timestamp, or None if it cannot be read.

    Duplicated (not imported) from lib/workflow_budget_gate.py on purpose:
    each gate module stays independently readable, the same way sibling
    hooks each carry their own tiny _read_stdin/_audit rather than sharing
    one.
    """
    if not isinstance(ts, str) or not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def resolve_session_id(payload: dict, env: dict, projects_dir: Path) -> str | None:
    """payload session_id -> $CLAUDE_SESSION_ID -> the newest session dir."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    sid = env.get("CLAUDE_SESSION_ID")
    if isinstance(sid, str) and sid:
        return sid
    if not projects_dir.is_dir():
        return None
    candidates = [p for p in projects_dir.iterdir()
                  if p.is_dir() and p.name not in _EXCLUDED_PROJECT_SUBDIRS]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime).name


def _scan_one_run(run_dir: Path) -> dict:
    """One workflow run's in-flight agents and last-activity time.

    In-flight classification is delegated to lib.workflow_stall_metrics.measure,
    which tracks retries by their content-addressed lane ``key`` rather than
    a raw started-minus-result/failed agentId set difference. The set
    difference this replaced mislabelled a lane as in flight FOREVER once any
    one of its attempts went unresolved, even after a LATER attempt on the
    SAME lane produced a result -- a false STALL the lane-aware tool was
    already built, and already proven, not to make (Codex review, GRIP#8698).
    """
    try:
        measured = workflow_stall_metrics.measure(run_dir)
    except (FileNotFoundError, PermissionError):
        measured = {"per_attempt": []}
    in_flight = sorted(a["agent"] for a in measured["per_attempt"] if a["state"] == "in_flight")
    try:
        journal_mtime = (run_dir / "journal.jsonl").stat().st_mtime
    except OSError:
        journal_mtime = None
    return {"run_id": run_dir.name, "run_dir": run_dir,
            "in_flight_agents": in_flight, "journal_mtime": journal_mtime}


# The harness keeps its own record of each run at `<session>/workflows/<run_id>.json`,
# and that record's `status` outranks anything the journal implies. A run the harness
# has killed leaves every unanswered lane "started" in its journal for ever, so a run
# killed on 2026-11-14 was still reported as "4 in flight ... STALL: stop it" on every
# prompt three days later. The values found on 22 records on 2026-11-17 were
# `completed` and `killed`; `failed` is the third way a run ends. Any other value, or
# no readable record, keeps the journal's reading, so a live state with a name this
# set does not know can never hide a real stall.
_FINISHED_RUN_STATUSES = frozenset({"completed", "killed", "failed"})


def run_record_status(session_dir: Path, run_id: str) -> str | None:
    """The harness's own status for one run, or None when its record is missing or unreadable."""
    try:
        record = json.loads((session_dir / "workflows" / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = record.get("status") if isinstance(record, dict) else None
    return status if isinstance(status, str) else None


def scan_journals(session_dir: Path) -> list[dict]:
    """One summary dict per `subagents/workflows/*/journal.jsonl` run, carrying the
    harness's own `status` for that run (None when it has no readable record)."""
    root = session_dir / "subagents" / "workflows"
    if not root.is_dir():
        return []
    runs = [_scan_one_run(run_dir) for run_dir in sorted(root.iterdir())
            if (run_dir / "journal.jsonl").is_file()]
    for run in runs:
        run["status"] = run_record_status(session_dir, run["run_id"])
    return runs


def is_in_flight(run: dict) -> bool:
    """A run is in flight when at least one lane has an unresolved attempt and the
    harness has not recorded the run itself as finished."""
    return bool(run["in_flight_agents"]) and run.get("status") not in _FINISHED_RUN_STATUSES


def minutes_since_last_activity(run: dict, now: float) -> float | None:
    """Minutes since the journal was last appended to (no in-line timestamps
    exist to read, so the file's own mtime is the proxy -- see the rule)."""
    mtime = run.get("journal_mtime")
    if mtime is None:
        return None
    return (now - mtime) / 60.0


def _count_requests_in_file(path: Path, cutoff: float) -> int:
    """``api-request`` entries in one agent transcript at or after ``cutoff``."""
    if not path.is_file():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # A bare '"api-request"' substring (no "type": prefix) stays
                # correct whether the line is minified (Claude Code's own
                # transcripts, no space after a colon) or spaced (json.dumps'
                # own default, used by this module's test fixtures) -- the
                # authoritative check is the parsed row.get("type") below.
                # It still excludes the sibling "api-request-shape" and
                # "api-request-blob" rows, which never carry a closing quote
                # right after "request".
                if '"api-request"' not in line:
                    continue
                try:
                    row = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                if row.get("type") != "api-request":
                    continue
                ts = _parse_iso(row.get("timestamp"))
                if ts is not None and ts >= cutoff:
                    count += 1
    except OSError:
        return count
    return count


def count_recent_api_requests(run_dir: Path, agent_ids: list[str], now: float,
                               within_minutes: int = 30) -> int:
    """Total ``api-request`` entries across the given agents' own transcripts."""
    cutoff = now - within_minutes * 60
    return sum(_count_requests_in_file(run_dir / f"agent-{aid}.jsonl", cutoff)
               for aid in agent_ids)


def _matching_ledger_row(line: str, session_id: str, cutoff: float) -> tuple[dict, float] | None:
    """This session's ledger row from ``line``, if it is recent enough."""
    if session_id not in line:
        return None
    try:
        row = json.loads(line.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if row.get("session_id") != session_id:
        return None
    ts = _parse_iso(row.get("ts"))
    if ts is None or ts < cutoff:
        return None
    return row, ts


def _apply_ledger_row(row: dict, ts: float, state: dict) -> None:
    """Fold one matched ledger row into the running token/weekly totals."""
    usage = row.get("usage") or {}
    for field in _USAGE_FIELDS:
        state["tokens"] += int(usage.get(field) or 0)
    weekly = row.get("seven_day_utilization")
    if not isinstance(weekly, (int, float)):
        return
    if state["first_ts"] is None or ts < state["first_ts"]:
        state["first_ts"], state["weekly_first"] = ts, weekly
    if ts >= state["latest_ts"]:
        state["latest_ts"], state["weekly_latest"] = ts, weekly


def _scan_ledger_file(path: Path, session_id: str, cutoff: float, state: dict) -> None:
    """Fold every one of THIS session's matching rows in one file into ``state``."""
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                matched = _matching_ledger_row(line, session_id, cutoff)
                if matched is not None:
                    _apply_ledger_row(matched[0], matched[1], state)
    except OSError:
        return


def session_ledger_window(ledger_path: Path, session_id: str, now: float,
                           within_minutes: int = 60) -> dict:
    """This session's token spend and seven_day_utilization movement in the
    last ``within_minutes``, read from the HAL usage ledger.

    Scans the rotated ``<name>.1`` sibling first when present, then the
    current file (HAL rotates the ledger at a size cap --
    lib/claude_cap_blackbox.py's history_paths does the same for this exact
    ledger): a window spanning a rotation used to see only the half of it in
    the current file (Codex review, GRIP#8698). The weekly figure is read as
    a CHRONOLOGICAL rise, latest minus earliest, clamped at zero -- a
    weekly-window RESET (0.90 falling to 0.10) is a decrease, not a spend,
    and the previous min/max-based delta misread it as a 0.80 spike
    (Codex review, GRIP#8698).
    """
    cutoff = now - within_minutes * 60
    state = {"tokens": 0, "first_ts": None, "weekly_first": None,
              "latest_ts": -1.0, "weekly_latest": None}
    older = ledger_path.with_name(ledger_path.name + ".1")
    for path in ([older, ledger_path] if older.is_file() else [ledger_path]):
        _scan_ledger_file(path, session_id, cutoff, state)
    delta = None
    if state["weekly_first"] is not None:
        delta = max(0.0, state["weekly_latest"] - state["weekly_first"])
    return {"tokens": state["tokens"], "weekly_delta": delta,
            "weekly_latest": state["weekly_latest"]}


def _request_window(run: dict, minutes: float | None, now: float) -> tuple[float, int]:
    """Anchor + width for counting this run's requests.

    Anchoring on "now" decays to zero for any run silent longer than the
    window -- exactly the long-dead case this check exists for -- so the
    anchor is the run's own last activity instead. The width widens to cover
    the whole stall once it passes 30 minutes, so "3+ requests per agent"
    means across the stall, not just its last half hour. Measured against a
    real stalled run (2026-10-26): a fixed 30-minute window anchored on "now"
    undercounted to 0; anchored on last activity it found 20 of the 76 total
    a window spanning the whole stall recovers, and only the wider count
    clears a 3-per-agent floor.
    """
    anchor = run["journal_mtime"] if run["journal_mtime"] is not None else now
    width = max(STALL_MINUTES, int(minutes) + 1) if minutes is not None else STALL_MINUTES
    return anchor, width


def _run_verdict(run: dict, now: float) -> dict:
    """One in-flight run's stats and STALL verdict, if it earns one.

    The floor is checked PER AGENT, not on the sum: one chatty agent clearing
    the whole aggregate while its siblings make zero requests used to earn
    a STALL verdict for a shape the detector does not actually promise --
    every in-flight agent retrying, not one of several (Codex review,
    GRIP#8698).
    """
    agents = run["in_flight_agents"]
    minutes = minutes_since_last_activity(run, now)
    anchor, window = _request_window(run, minutes, now)
    per_agent = {aid: count_recent_api_requests(run["run_dir"], [aid], anchor, within_minutes=window)
                 for aid in agents}
    requests = sum(per_agent.values())
    verdict = None
    if (agents and minutes is not None and minutes >= STALL_MINUTES
            and all(c >= STALL_MIN_REQUESTS_PER_AGENT for c in per_agent.values())):
        verdict = "STALL"
    return {"run_id": run["run_id"], "in_flight": len(agents),
            "minutes_since_result": minutes, "requests": requests, "verdict": verdict}


def assess(session_dir: Path, ledger_path: Path, session_id: str,
           now: float | None = None) -> dict:
    """The watchdog's one read: in-flight runs plus this session's spend."""
    now = time.time() if now is None else now
    runs = [_run_verdict(run, now) for run in scan_journals(session_dir) if is_in_flight(run)]
    window = session_ledger_window(ledger_path, session_id, now)
    burn = window["tokens"] >= BURN_TOKENS or (
        window["weekly_delta"] is not None and window["weekly_delta"] >= BURN_WEEKLY_RISE)
    return {"session_id": session_id, "weekly_latest": window["weekly_latest"],
            "weekly_delta_60m": window["weekly_delta"], "session_tokens_60m": window["tokens"],
            "burn": burn, "runs": runs}


def render_line(a: dict) -> str | None:
    """The one advisory line, or None when there is nothing worth printing."""
    stalls = [r for r in a["runs"] if r["verdict"] == "STALL"]
    if not stalls and not a["burn"]:
        return None
    weekly = a["weekly_latest"]
    delta = a["weekly_delta_60m"] or 0.0
    head = f"[SPEND] weekly {weekly:.2f} (+{delta:.2f} this session)" if weekly is not None else "[SPEND]"
    if not stalls:
        return head + " -> BURN: this session is spending fast, consider stopping"
    parts = [f"{r['run_id']}: {r['in_flight']} in flight, 0 results for "
             f"{int(r['minutes_since_result'] or 0)} min, {r['requests']} requests -> STALL: stop it"
             for r in stalls]
    line = head + " | " + " | ".join(parts)
    return line + " (BURN)" if a["burn"] else line
