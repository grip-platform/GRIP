"""fleet_quit — tell every other live GRIP session to write up and exit.

WHY THIS EXISTS. With ~15 concurrent sessions there was no way to close the
fleet: a Claude Code session CANNOT terminate itself (lib/infinite_cycle.py),
no hook can force a stop, and a Stop hook can only *block* stopping. So the
exit has to be actuated from OUTSIDE, and the instruction has to reach a live
session through a channel it already drains. Both halves already existed and
are reused, not rebuilt: `mesh broadcast` (bin/mesh) inserts one inbox row per
live session, and hooks/mesh-inbox-poll-hook.py drains undelivered rows on
EVERY tool call, so a message lands within one tool-call round-trip. That hook
already carried a `kind` column that nothing consumed. This is its first
consumer.

THREE MEASUREMENTS DECIDED THIS DESIGN (2026-09-23, on the operator's machine).
They are recorded because each one refuted a simpler design that looked right.

1. THE 1-HOUR WINDOW SELECTS GHOSTS. `mesh broadcast` targets any session whose
   heartbeat is inside one hour. Of 16 rows, 7 matched — but only 2 were alive.
   Among the 5 ghosts were two one-shot `claude -p` model probes run minutes
   earlier: they register in the mesh, exit, and never drain an inbox again.
   Broadcasting to a ghost and waiting for it yields "timed-out", which is
   indistinguishable from a live session that FAILED TO SAVE. That conflation
   is the thing most likely to lose work, so liveness is decided before any
   message is sent.

2. A TIGHT HEARTBEAT WINDOW IS A SOUND LIVENESS TEST. Sampled twice ~30s apart:
   both live sessions advanced (23:11:08 -> 23:11:38, 23:10:27 -> 23:11:32)
   while every ghost stayed frozen (23:01:07 and 23:00:12, unmoved). Live
   sessions beat every ~30-60s; the nearest ghost was 10 minutes stale. The
   default window of 180s is therefore ~3 missed beats of slack and still
   excludes every ghost observed. It is a MEASURED threshold, not a guess, and
   DEFAULT_LIVE_WINDOW_S is the one place to change it.

3. THERE IS NO SOUND SESSION-ID -> PID MAP FROM OUTSIDE. The presence DB has no
   pid column; `lsof` on a session's own transcript returns no holder. Meanwhile
   `pgrep -x claude` reported 20 processes. So signalling "the pid for session X"
   from the initiator is a guess, and a wrong guess kills someone else's work.
   The fix inverts it: a session knows its OWN pid, so it WRITES that pid into
   its readiness marker. `--force` may then only ever signal a pid a session
   volunteered about itself. A session that never answered has no pid on file
   and CANNOT be force-killed — unresponsive degrades to "reported", never to
   "killed blind".

SAFETY INVARIANTS. Those marked [T] have a direct test in
tests/test_fleet_quit.py; the rest are enforced in code and covered only
indirectly — an audit on 2026-09-23 caught the original wording claiming a test
for every one of them, which was a universal claim the file could not support:
  * Self-exclusion is absolute — by session id AND by pid. Required by
    rules/gate-resolution-escape-hatch.md Invariant 1: a mechanism must never be
    able to kill the session that would fix it.
  * Dry-run is the default. A bare `grip-fleet-quit` changes nothing.
  * Protected sessions are never targeted, in dry-run or in `--go`.
  * An abort marker halts an in-flight run at the next phase boundary
    (the bin/grip-stop brake model).
  * Save precedes exit: a session that has not reported `saved` is not actuated
    unless `--force` is given.
  * `--force` is never the default and is not yolo-relaxable.

HONEST DEGRADATION. Actuation needs a pane. On the machine this was written on,
tmux was absent and Otty was installed but NOT RUNNING (socket missing), so
there was NO pane actuator at all. That case reports `degraded` — never a quiet
skip — because "nothing to do" and "something is needed and this machine cannot
do it" mean opposite things to an operator (the rule lib/infinite_cycle.py
states for the same reason).

KNOWN RISK, carried not hidden. hooks/mesh-inbox-poll-hook.py reads-and-marks
non-transactionally on a 2s busy timeout, so under concurrency a shutdown row
could be double-delivered or lost. Structural, NOT measured. The readiness
marker makes the protocol idempotent against it: a duplicate shutdown is
harmless (the marker is rewritten), and a lost one surfaces as `timed-out`
rather than as a silent success.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

# Measured, not guessed — see measurement 2 in the module docstring.
DEFAULT_LIVE_WINDOW_S = 180
DEFAULT_TIMEOUT_S = 180
# Grace between SIGTERM and SIGKILL on the --force path.
DEFAULT_KILL_GRACE_S = 10

STATE_DIRNAME = Path("state") / "fleet-quit"
PROTECTED_BASENAME = "protected.txt"
ABORT_BASENAME = "ABORT"

# Target states. `self` and `protected` are EXCLUSIONS, not failures.
ST_SELF = "self"
ST_PROTECTED = "protected"
ST_GHOST = "ghost"
ST_LIVE = "live"

# Outcome states, reported per target after the await phase.
OUT_SAVED = "saved"
OUT_TIMED_OUT = "timed-out"
OUT_DEGRADED = "degraded"
OUT_EXITED = "exited"


class FleetQuitError(RuntimeError):
    """Raised for operator-facing failures (bad input, unreadable state)."""


def normalise_session_id(raw: str) -> str:
    """Return the mesh form of a session id.

    The mesh stores `cc-<uuid>` (hooks/mesh-inbox-poll-hook.py::_resolve_session_id
    prefixes it), but an operator reads a bare uuid off a transcript path or a
    session banner and will type that. Accepting only one spelling would mean a
    protected session silently failing to match — the single worst bug this
    module could have, because the failure is an unwanted QUIT. Both spellings
    resolve to the same key.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    return s if s.startswith("cc-") else f"cc-{s}"


def get_dir() -> Path:
    """The GRIP install root, resolved the canonical way.

    Present so the command injected into a quitting session stays a short
    one-liner it can actually run — see hooks/mesh-inbox-poll-hook.py
    ::_shutdown_directive. Duplicating the resolution ladder in that string
    would put a second copy of rules/path-portability.md's contract in a place
    nothing tests.
    """
    try:
        from claude_dir import get_claude_dir
        return get_claude_dir()
    except Exception:
        return Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))


def _state_dir(claude_dir: Path) -> Path:
    return claude_dir / STATE_DIRNAME


def protected_path(claude_dir: Path) -> Path:
    return _state_dir(claude_dir) / PROTECTED_BASENAME


def abort_path(claude_dir: Path) -> Path:
    return _state_dir(claude_dir) / ABORT_BASENAME


def ready_path(claude_dir: Path, session_id: str) -> Path:
    # Normalised so a marker written under either spelling is found under both.
    return _state_dir(claude_dir) / f"{normalise_session_id(session_id)}.ready"


def load_protected(claude_dir: Path, extra: Iterable[str] = ()) -> set[str]:
    """Protected session ids: the persistent file plus any --exclude arguments.

    Blank lines and `#` comments are skipped so the operator can say WHY a
    session is protected next to the id. A missing file is not an error — it
    means nothing has been protected yet.
    """
    out: set[str] = {normalise_session_id(e) for e in extra if (e or "").strip()}
    p = protected_path(claude_dir)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.add(normalise_session_id(line))
    return out


def add_protected(claude_dir: Path, session_id: str, reason: str = "") -> Path:
    """Append a session to the persistent protected list, idempotently."""
    sid = normalise_session_id(session_id)
    if not sid:
        raise FleetQuitError("refusing to protect an empty session id")
    p = protected_path(claude_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if sid in load_protected(claude_dir):
        return p
    suffix = f"  # {reason}" if reason else ""
    with p.open("a") as fh:
        fh.write(f"{sid}{suffix}\n")
    return p


@dataclass(frozen=True)
class Target:
    session_id: str
    name: str
    machine: str
    last_heartbeat: str
    age_s: float
    state: str
    outcome: Optional[str] = None
    pid: Optional[int] = None
    pane: str = ""
    detail: str = ""

    @property
    def targetable(self) -> bool:
        return self.state == ST_LIVE


def _parse_iso_z(ts: str) -> Optional[float]:
    """Epoch seconds for the mesh's `YYYY-MM-DDTHH:MM:SS.mmmZ` stamps.

    Returns None on anything unparseable, and the caller treats None as
    INFINITELY stale — an unreadable heartbeat must never be read as fresh,
    because fresh is the answer that gets a session quit.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return time.mktime(time.strptime(ts, fmt)) - time.timezone
        except (ValueError, TypeError):
            continue
    return None


def classify(
    rows: Sequence[tuple],
    *,
    self_id: str,
    protected: set[str],
    now: float,
    live_window_s: float = DEFAULT_LIVE_WINDOW_S,
) -> list[Target]:
    """Split presence rows into self / protected / ghost / live.

    Pure: takes rows and a clock, touches nothing. `rows` are
    (id, name, machine, last_heartbeat) as read from the sessions table.

    ORDER IS LOAD-BEARING. Self and protected are decided BEFORE liveness, so a
    protected session that happens to look stale is still reported as protected
    rather than quietly reclassified. An exclusion must not depend on a timing
    measurement.
    """
    self_key = normalise_session_id(self_id)
    out: list[Target] = []
    for sid, name, machine, hb in rows:
        key = normalise_session_id(sid)
        beat = _parse_iso_z(hb or "")
        age = float("inf") if beat is None else max(0.0, now - beat)
        if key == self_key:
            state, detail = ST_SELF, "the initiating session — never quit"
        elif key in protected:
            state, detail = ST_PROTECTED, "on the protected list — never quit"
        elif age > live_window_s:
            state = ST_GHOST
            detail = ("heartbeat unreadable" if beat is None
                      else f"heartbeat {age / 60:.1f} min stale (> {live_window_s / 60:.1f} min)")
        else:
            state, detail = ST_LIVE, f"heartbeat {age:.0f}s ago"
        out.append(Target(session_id=key, name=name or "", machine=machine or "",
                          last_heartbeat=hb or "", age_s=age, state=state, detail=detail))
    # Live first, then protected, then self, then ghosts — the operator reads
    # the actionable rows at the top.
    rank = {ST_LIVE: 0, ST_PROTECTED: 1, ST_SELF: 2, ST_GHOST: 3}
    return sorted(out, key=lambda t: (rank.get(t.state, 9), t.age_s))


def read_sessions(db_path: Path) -> list[tuple]:
    """Every presence row. Liveness is decided in `classify`, never in SQL.

    Deliberately unfiltered: `mesh broadcast` filters in SQL with a one-hour
    window, which is how ghosts got selected (measurement 1). Reading them all
    lets the report NAME the excluded ghosts instead of silently omitting them.
    """
    if not db_path.exists():
        raise FleetQuitError(f"no presence database at {db_path}")
    con = sqlite3.connect(str(db_path), timeout=5)
    try:
        return list(con.execute(
            "SELECT id, name, machine, last_heartbeat FROM sessions"))
    finally:
        con.close()


def broadcast_shutdown(
    db_path: Path,
    *,
    initiator: str,
    targets: Sequence[Target],
    deadline_s: int,
    now_iso: Optional[str] = None,
    uuid_fn: Callable[[], str] = lambda: __import__("uuid").uuid4().hex,
) -> int:
    """Insert one `kind='shutdown'` inbox row per LIVE target. Returns the count.

    Mirrors bin/mesh's mesh_broadcast insert, but the recipient set is the
    already-classified live set rather than a one-hour SQL window — so a ghost
    cannot be enqueued, and therefore cannot later be reported as timed-out.
    """
    live = [t for t in targets if t.targetable]
    if not live:
        return 0
    stamp = now_iso or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    body = (f"FLEET-QUIT requested by {initiator}. Save your work, then exit. "
            f"Deadline {deadline_s}s.")
    con = sqlite3.connect(str(db_path), timeout=5)
    try:
        for t in live:
            con.execute(
                "INSERT INTO inbox(id, recipient_id, from_name, body, kind, created_at, delivered)"
                " VALUES(?,?,?,?,?,?,0)",
                (uuid_fn(), t.session_id, initiator, body, "shutdown", stamp))
        con.commit()
    finally:
        con.close()
    return len(live)


def _ancestor_pids(pid: int, *, max_depth: int = 6,
                   ppid_of: Optional[Callable[[int], Optional[int]]] = None) -> set[int]:
    """This pid and its ancestors, so a pane owned by a parent shell still matches."""
    def _default(p: int) -> Optional[int]:
        try:
            import subprocess as _sp
            r = _sp.run(["ps", "-o", "ppid=", "-p", str(p)],
                        capture_output=True, text=True, timeout=3)
            return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
        except Exception:
            return None
    step = ppid_of or _default
    out, cur = {pid}, pid
    for _ in range(max_depth):
        nxt = step(cur)
        if not nxt or nxt in out or nxt <= 1:
            break
        out.add(nxt)
        cur = nxt
    return out


def resolve_session_pid(pid: Optional[int] = None,
                        comm_of: Optional[Callable[[int], str]] = None,
                        ppid_of: Optional[Callable[[int], Optional[int]]] = None) -> Optional[int]:
    """The PID of the harness process that OWNS this session, or None.

    NOT os.getpid(). bin/grip-fleet-ready runs as a short-lived grandchild of the
    session (session -> Bash tool -> python), so its own pid is dead within a
    second of the marker being written. Recording it would make `--force` signal
    a corpse — and, once the OS recycles that number, an unrelated process. The
    audit on 2026-09-23 caught exactly that: the signal branch's only guard is
    `t.pid == self_pid`, which compares against the INITIATOR's pid and so cannot
    notice that the target's pid is somebody else's now.

    Walks ancestors for the nearest process whose command is a known harness.
    Returns None when it cannot prove one — and None is safe by construction,
    because actuation_plan degrades a --force target with no pid rather than
    guessing (there is no session->pid map from outside; that is measurement 3).
    """
    def _comm(q: int) -> str:
        try:
            import subprocess as _sp
            r = _sp.run(["ps", "-o", "comm=", "-p", str(q)],
                        capture_output=True, text=True, timeout=3)
            return r.stdout.strip().rsplit("/", 1)[-1] if r.returncode == 0 else ""
        except Exception:
            return ""
    def _ppid(q: int) -> Optional[int]:
        try:
            import subprocess as _sp
            r = _sp.run(["ps", "-o", "ppid=", "-p", str(q)],
                        capture_output=True, text=True, timeout=3)
            return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
        except Exception:
            return None
    comm, par = comm_of or _comm, ppid_of or _ppid
    cur = pid if pid is not None else os.getpid()
    for _ in range(8):
        if comm(cur) in ("claude", "codex", "opencode"):
            return cur
        nxt = par(cur)
        if not nxt or nxt <= 1:
            return None
        cur = nxt
    return None


def resolve_own_pane(panes: Sequence[dict], own_pids: set[int]) -> str:
    """The pane belonging to THIS session, or "" when it cannot be proven.

    Pure, so it is testable without Otty. Matches a pane's pid field (whatever
    the schema calls it) against this process and its ancestors, and returns a
    pane id ONLY on a unique match.

    A UNIQUE match is required, and an empty result is the safe answer. Otty's
    own transport records why (lib/otty_transport.py::_require_pane, learned
    live 2026-07-02): `otty pane close --pane ""` acts on the FOCUSED pane,
    which on a shared terminal belongs to someone else. Guessing here would
    quit another operator's session, so ambiguity degrades instead.
    """
    hits: list[str] = []
    for pane in panes:
        raw = None
        for key in ("pid", "process_id", "processId", "process_pid"):
            if isinstance(pane.get(key), (int, str)):
                raw = pane.get(key)
                break
        try:
            pane_pid = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if pane_pid in own_pids:
            for key in ("id", "pane_id", "paneId", "uuid"):
                val = pane.get(key)
                if isinstance(val, str) and val.strip():
                    hits.append(val.strip())
                    break
    return hits[0] if len(hits) == 1 else ""


def write_ready(claude_dir: Path, session_id: str, *, pid: Optional[int] = None,
                pane: str = "", note: str = "") -> Path:
    """Called BY a quitting session once its close ritual has run.

    The pid and pane are written by the only party that can know them
    (measurement 3): neither is derivable from outside, so both are volunteered
    here and the initiator never guesses either one.
    """
    p = ready_path(claude_dir, session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "session_id": normalise_session_id(session_id),
        # An UNRESOLVED session pid stays null. It used to fall back to
        # os.getpid(), which is the writer's own short-lived pid — dead within a
        # second, and once recycled, somebody else's process. A null here makes
        # --force degrade instead, which is the safe direction.
        "pid": (int(pid) if pid is not None else None),
        "pane": pane or "",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
    }, indent=2))
    return p


def read_ready(claude_dir: Path, session_id: str) -> Optional[dict]:
    p = ready_path(claude_dir, session_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        # A half-written marker is NOT readiness. Treating it as ready would
        # actuate an exit against a session that may still be saving.
        return None


def clear_ready(claude_dir: Path, targets: Sequence[Target]) -> None:
    """Remove stale markers before a run so a PREVIOUS run's marker cannot be
    mistaken for this run's readiness."""
    for t in targets:
        p = ready_path(claude_dir, t.session_id)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def abort_requested(claude_dir: Path) -> bool:
    return abort_path(claude_dir).exists()


def request_abort(claude_dir: Path, reason: str = "") -> Path:
    p = abort_path(claude_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": reason or "manual",
    }, indent=2))
    return p


def clear_abort(claude_dir: Path) -> None:
    p = abort_path(claude_dir)
    if p.exists():
        p.unlink()


def await_ready(
    claude_dir: Path,
    targets: Sequence[Target],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_s: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[Target]:
    """Poll for readiness markers until every live target reports or time runs out.

    Returns targets carrying an `outcome` and, where the session supplied one, a
    `pid`. Ghost/self/protected targets are passed through untouched — they were
    never asked, so they can neither save nor time out.

    Honours the abort marker at each poll boundary (the bin/grip-stop model):
    an in-flight run stops at a phase edge rather than mid-actuation.
    """
    live = [t for t in targets if t.targetable]
    others = [t for t in targets if not t.targetable]
    if not live:
        return list(targets)

    start = clock()
    ready: dict[str, dict] = {}
    # A HARD ITERATION CAP, so this function is total regardless of the clock.
    #
    # The timeout branch below compares clock() against start, so a clock that
    # does not ADVANCE makes it unreachable and the loop never ends. That is not
    # hypothetical: on 2026-09-23 a mutation run that removed the abort check was
    # killed three times by the OS for exhausting memory, because the brake test
    # freezes its clock at 0.0 and `sleep_fn` appends to a list — an unbounded
    # spin growing a list until the machine ran out of memory.
    #
    # Two things follow. A mutation must fail as an ASSERTION, never as a hang:
    # a hang burns a CI job's whole timeout and reads as infrastructure trouble
    # rather than as the test doing its job. And production is not immune either
    # — time.monotonic advancing is an assumption, and this makes it one the
    # function no longer depends on for termination.
    max_polls = max(1, int(timeout_s / poll_s) + 2) if poll_s > 0 else 1
    polls = 0
    while True:
        polls += 1
        for t in live:
            if t.session_id in ready:
                continue
            data = read_ready(claude_dir, t.session_id)
            if data is not None:
                ready[t.session_id] = data
        if len(ready) == len(live) or abort_requested(claude_dir):
            break
        if clock() - start >= timeout_s or polls >= max_polls:
            break
        sleep_fn(poll_s)

    resolved: list[Target] = []
    for t in live:
        data = ready.get(t.session_id)
        if data is None:
            resolved.append(Target(**{**t.__dict__, "outcome": OUT_TIMED_OUT,
                                      "detail": f"no readiness marker after {timeout_s:.0f}s"}))
        else:
            pid = data.get("pid")
            pane = data.get("pane")
            resolved.append(Target(**{**t.__dict__, "outcome": OUT_SAVED,
                                      "pid": int(pid) if isinstance(pid, int) else None,
                                      "pane": pane if isinstance(pane, str) else "",
                                      "detail": f"saved at {data.get('at', '?')}"}))
    return resolved + others


def actuation_plan(
    targets: Sequence[Target],
    *,
    force: bool,
    self_pid: int,
    pane_actuator: Optional[str],
) -> list[dict]:
    """Decide, without doing anything, what would be done to each target.

    Separated from execution so the dry-run and the real run share ONE decision
    procedure — a dry-run that previews a different algorithm than the one that
    runs is a lie the operator cannot catch.

    The gate order encodes the invariants:
      self / protected / ghost -> never actuated, whatever the flags say;
      not saved and not --force -> skipped, because save precedes exit;
      --force with a volunteered pid -> signal that pid (never a guessed one);
      a pane actuator present     -> send `/exit` to the pane;
      otherwise                   -> DEGRADED, reported, never a silent skip.
    """
    plan: list[dict] = []
    for t in targets:
        if t.state in (ST_SELF, ST_PROTECTED):
            plan.append({"target": t, "action": "skip", "why": t.detail})
            continue
        if t.state == ST_GHOST:
            plan.append({"target": t, "action": "skip",
                         "why": f"not a live session ({t.detail}) — never messaged"})
            continue
        if t.outcome != OUT_SAVED and not force:
            plan.append({"target": t, "action": "skip",
                         "why": "did not report saved; save precedes exit (use --force to override)"})
            continue
        if force and t.pid:
            if t.pid == self_pid:
                # Belt and braces behind the id check: a pid collision must not
                # be able to route a kill back at the initiator.
                plan.append({"target": t, "action": "skip",
                             "why": "pid matches this process — self-exclusion"})
                continue
            plan.append({"target": t, "action": "signal", "pid": t.pid,
                         "why": f"--force against the pid session {t.session_id} volunteered"})
            continue
        if force and not t.pid:
            plan.append({"target": t, "action": "degraded",
                         "why": "--force given but the session never volunteered a pid; "
                                "refusing to guess (there is no sound session->pid map)"})
            continue
        if pane_actuator and t.pane:
            plan.append({"target": t, "action": "pane-exit", "via": pane_actuator,
                         "pane": t.pane,
                         "why": f"send /exit to the {pane_actuator} pane {t.pane} it volunteered"})
            continue
        if pane_actuator and not t.pane:
            plan.append({"target": t, "action": "degraded",
                         "why": f"saved, and {pane_actuator} is up, but the session could not "
                                "prove which pane is its own — refusing to target a pane it did "
                                "not volunteer (an empty pane id hits the FOCUSED pane)"})
            continue
        plan.append({"target": t, "action": "degraded",
                     "why": "saved, but no pane actuator on this machine "
                            "(tmux absent and Otty not running) — cannot deliver /exit"})
    return plan


def summarise(targets: Sequence[Target], plan: Sequence[dict]) -> dict:
    """Counts for the report. Every target appears in exactly one bucket."""
    by_state: dict[str, int] = {}
    for t in targets:
        by_state[t.state] = by_state.get(t.state, 0) + 1
    by_action: dict[str, int] = {}
    for step in plan:
        by_action[step["action"]] = by_action.get(step["action"], 0) + 1
    return {"targets": len(targets), "by_state": by_state, "by_action": by_action}
