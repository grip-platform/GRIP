"""mesh_liveness — whether a presence row's session is running, gone, or cannot
be confirmed either way.

The same rule as grip-session-mesh ``presence/src/liveness.ts``, so that
``mesh broadcast``, ``mesh list`` and the presence server agree.

A heartbeat alone cannot decide it. Nothing beats on a timer: the only writer of
``last_heartbeat`` is hooks/mesh-auto-register-hook.py, bound to SessionStart and
PostToolUse, so a session idle at its prompt keeps a stale heartbeat for as long
as its operator is away (lib/mesh_gc.py says the same: "heartbeat fires only on
a tool call"). Claude Code itself keeps one record per running process,
``<config dir>/sessions/<pid>.json``, naming the session id that process is
serving now, and removes it when the process exits. The rule (V>>, 2026-10-02):

  * running      a record names the session and one of its pids is alive, or
                 the row beat within HEARTBEAT_WINDOW_S;
  * gone         a record names the session and every pid in it is dead;
  * unconfirmed  neither: no record and no recent beat. Other harnesses, other
                 machines, ids replaced by /clear, and local sessions whose
                 record Claude Code has already removed all land here.

A broadcast queues for running AND unconfirmed sessions and skips only the gone:
a missed message is the failure this rule exists to prevent, so when unsure,
queue.

The config dir is ``$CLAUDE_CONFIG_DIR``, Claude Code's own variable, else
GRIP's install dir. A session running under a different config dir has no
record here, so it is running while it beats and unconfirmed otherwise.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Three missed beats of a working session: lib/fleet_quit.py measured live
# sessions beating every 30-60 s and chose the same window.
HEARTBEAT_WINDOW_S = 180

RUNNING = "running"
UNCONFIRMED = "unconfirmed"
GONE = "gone"
STATES = (RUNNING, UNCONFIRMED, GONE)


def claude_sessions_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (Path(base) if base else get_claude_dir()) / "sessions"


def _read_record(path: Path) -> Optional[tuple[str, int]]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a record caught mid-write says nothing either way
    if not isinstance(record, dict):
        return None
    session_id, pid = record.get("sessionId"), record.get("pid")
    if isinstance(session_id, str) and isinstance(pid, int) and not isinstance(pid, bool):
        return session_id, pid
    return None


def claude_session_pids(sessions_dir: Path) -> dict[str, list[int]]:
    """sessionId -> pids of the Claude Code processes whose record names it."""
    try:
        paths = sorted(sessions_dir.glob("*.json"))
    except OSError:
        return {}
    index: dict[str, list[int]] = {}
    for path in paths:
        record = _read_record(path)
        if record:
            index.setdefault(record[0], []).append(record[1])
    return index


def pid_alive(pid: int) -> bool:
    """True while a process with this pid exists.

    On Windows ``os.kill(pid, 0)`` is not a probe: signal 0 is CTRL_C_EVENT
    there and would interrupt the target. Windows therefore answers True, which
    reads the session as running -- never gone -- rather than risk signalling it.
    """
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists; it belongs to another user
    except OSError:
        return False
    return True


def _parse_heartbeat(value: object) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _beat_recently(last_heartbeat: object, now: datetime, window_s: int) -> bool:
    beat = _parse_heartbeat(last_heartbeat)
    return beat is not None and (now - beat).total_seconds() <= window_s


def classify(row_id: str, last_heartbeat: object, pids: Mapping[str, list[int]],
             now: datetime, alive: Callable[[int], bool] = pid_alive,
             window_s: int = HEARTBEAT_WINDOW_S) -> str:
    """RUNNING, UNCONFIRMED or GONE for one presence row."""
    owners = pids.get(row_id[3:] if row_id.startswith("cc-") else row_id)
    if owners and any(alive(pid) for pid in owners):
        return RUNNING
    if _beat_recently(last_heartbeat, now, window_s):
        return RUNNING
    return GONE if owners else UNCONFIRMED


def classify_rows(rows: Iterable[tuple[str, object]],
                  sessions_dir: Optional[Path] = None) -> dict[str, list[str]]:
    """{state: ids} for ``(id, last_heartbeat)`` rows, each list in input order."""
    now = datetime.now(timezone.utc)
    pids = claude_session_pids(sessions_dir or claude_sessions_dir())
    groups: dict[str, list[str]] = {state: [] for state in STATES}
    for row_id, beat in rows:
        groups[classify(row_id, beat, pids, now)].append(row_id)
    return groups
