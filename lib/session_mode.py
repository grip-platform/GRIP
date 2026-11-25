"""Generic session-marker lifecycle — the shared shape behind grip-mode / afk / rc.

A "session mode" is a named, TTL-bounded marker file at
``<CLAUDE_DIR>/state/session/<name>-mode.json`` that any surface can read to
know a mode is active. This is the reusable extraction (YSH, N=3) of the
``on | off | status`` marker-flip that ``lib/gates/yolo_mode`` and the
hand-rolled ``bin/grip-afk`` each implemented independently — so the next
marker verb (rc, and future ones) does not clone a third copy.

``grip-yolo`` is deliberately NOT folded in: its marker is consumed by the gate
pipeline with preset/bypass semantics (``lib/gates/yolo_mode``), a legitimate
specialisation. Folding a gate-integrated verb into this generic shim would
violate KISS and risk the live gate, so the name ``yolo`` is RESERVED here and
this module refuses it — a caller who wants yolo must go through ``grip-yolo``.

Improvements over the hand-rolled afk marker this generalises:
  * ``status`` HONOURS the deadline — an expired marker reads INACTIVE and is
    reaped, fixing afk's file-exists-only staleness bug.
  * atomic write (temp + ``os.replace``) so a reader never sees a half file.
  * the name is validated as a single safe path component (no traversal),
    because it becomes an on-disk path segment.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Names owned by a gate-integrated specialisation; the generic shim must never
# write their markers (a different schema would break the consuming gate).
RESERVED = {"yolo"}
# A mode name becomes the ``<name>-mode.json`` path segment, so it must be a
# single safe path component — lowercase, no separators, no traversal.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_MAX_TTL = 43200  # 12h ceiling, matching the afk convention
MIN_TTL = 1
CLI_DEFAULT_TTL = 3600  # 1h, matching the grip-yolo manual convention
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"invalid mode name {name!r}: must match [a-z0-9][a-z0-9_-]* "
            "(lowercase, no path separators)"
        )
    if name in RESERVED:
        raise ValueError(
            f"mode name {name!r} is reserved for a gate-integrated verb; use "
            "its own tool (e.g. grip-yolo), never this generic shim"
        )
    return name


def _base_dir(claude_dir=None) -> Path:
    return Path(claude_dir) if claude_dir else get_claude_dir()


def marker_path(name: str, claude_dir=None) -> Path:
    """Absolute path of ``name``'s marker file (validates ``name`` first)."""
    _validate_name(name)
    return _base_dir(claude_dir) / "state" / "session" / f"{name}-mode.json"


def _log_path(name: str, claude_dir=None) -> Path:
    return _base_dir(claude_dir) / "logs" / f"{name}-mode.log"


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def _audit(name: str, action: str, note: str, claude_dir=None) -> None:
    # Audit is best-effort: a logging failure must never fail the flip itself.
    try:
        p = _log_path(name, claude_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = _now().strftime(_ISO)
        with p.open("a") as f:
            f.write(f"{stamp} {name} {action} {note}".rstrip() + "\n")
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    # NamedTemporaryFile owns the fd and closes it on with-exit (no fd leak even
    # if json.dump raises); delete=False keeps the temp for the atomic os.replace,
    # and any failure unlinks it so no partial temp is left behind.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as f:
            tmp = f.name
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def activate(name: str, ttl_seconds: int, note: str = "", activator=None,
             extra=None, max_ttl: int = DEFAULT_MAX_TTL, claude_dir=None,
             now=None) -> Path:
    """Write ``name``'s marker active for ``ttl_seconds`` (clamped to [1, max_ttl])."""
    _validate_name(name)
    ttl = int(ttl_seconds)
    ttl = max(MIN_TTL, min(ttl, max_ttl))
    started = _now(now)
    deadline = started + timedelta(seconds=ttl)
    payload = {
        "marker": name,
        "active": True,
        "activator": activator or os.environ.get("GRIP_OPERATOR", "V>>"),
        "note": note or "",
        "started_at": started.strftime(_ISO),
        "deadline": deadline.strftime(_ISO),
        "ttl_seconds": ttl,
        "pid": os.getpid(),
        "extra": dict(extra) if extra else {},
    }
    path = marker_path(name, claude_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, payload)
    _audit(name, "ON", note or "", claude_dir)
    return path


def deactivate(name: str, claude_dir=None, audit_action: str = "OFF") -> bool:
    """Remove ``name``'s marker. Returns True iff a marker existed.

    A non-FileNotFoundError OSError (permission denied, locked file) is a REAL
    failure and PROPAGATES rather than being masked as "already absent" — an
    operator must be able to see a marker that cannot be removed.

    ``audit_action`` is what the audit log records: OFF when someone turned
    the mode off, EXPIRED when ``status`` reaped a lapsed marker, so the log
    can tell a return from a lapse (GRIP#9244).
    """
    path = marker_path(name, claude_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    _audit(name, audit_action, "", claude_dir)
    return True


def status(name: str, now=None, claude_dir=None):
    """Return the marker dict if active, else None. Honours the deadline:
    an expired marker is reaped and reported inactive."""
    path = marker_path(name, claude_dir)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # missing / unreadable / corrupt (e.g. a concurrent partial write) => inactive.
        # json.JSONDecodeError is a ValueError subclass listed explicitly; bare
        # ValueError is NOT caught, so an unexpected ValueError still propagates.
        return None
    if not isinstance(data, dict):
        return None
    deadline_s = data.get("deadline")
    if deadline_s:
        try:
            deadline = datetime.strptime(deadline_s, _ISO).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            deadline = None
        if deadline is not None:
            cur = _now(now)
            if cur >= deadline:
                try:  # best-effort reap of the stale marker, logged as a lapse
                    deactivate(name, claude_dir, audit_action="EXPIRED")
                except OSError:
                    pass  # reap failed (locked); the marker is still expired => INACTIVE
                return None
            data["remaining_seconds"] = int((deadline - cur).total_seconds())
    return data


def is_active(name: str, now=None, claude_dir=None) -> bool:
    return status(name, now=now, claude_dir=claude_dir) is not None


def _is_stamp(text: str) -> bool:
    try:
        datetime.strptime(text, _ISO)
    except ValueError:
        return False
    return True


def open_window(name: str, claude_dir=None):
    """The start and note of ``name``'s last ON that no OFF has closed, read
    from its audit log, or None.

    An EXPIRED line leaves the window open: the marker is gone, but nobody has
    turned the mode off, so `/back` still needs the start for its digest
    (GRIP#9244). Each log line is "<stamp> <name> <action> <note>"; a line
    whose stamp is not a real time is skipped, so a torn or hand-edited line
    can never become a `git log --since` anchor (the AGORA #3964 class).
    """
    _validate_name(name)
    try:
        lines = _log_path(name, claude_dir).read_text().splitlines()
    except OSError:
        return None
    window = None
    for fields in (line.split(" ", 3) for line in lines):
        if len(fields) < 3 or fields[1] != name or not _is_stamp(fields[0]):
            continue
        if fields[2] == "ON":
            window = {"started_at": fields[0], "note": fields[3] if len(fields) > 3 else ""}
        elif fields[2] == "OFF":
            window = None
    return window


# --- CLI (argv-passthrough backend; bin/grip-mode is a thin exec wrapper) ---

_USAGE = """grip-mode — generic session-marker toggle (YSH shim behind afk/rc/...).

Usage:
  grip-mode <name> on  [--ttl SECONDS] [--note "text"] [--activator WHO]
  grip-mode <name> off                       # alias: back
  grip-mode <name> status
  grip-mode <name> window      # start + note of the last ON no OFF has closed

<name> is a lowercase single path component ([a-z0-9][a-z0-9_-]*). The marker
lives at state/session/<name>-mode.json and expires after --ttl (default 3600s,
max 43200s). 'yolo' is reserved — use grip-yolo (it is gate-integrated).
"""


def _parse_opts(name: str, rest: list):
    import argparse

    parser = argparse.ArgumentParser(prog=f"grip-mode {name}", add_help=False)
    parser.add_argument("--ttl", type=int, default=CLI_DEFAULT_TTL)
    parser.add_argument("--note", default="")
    parser.add_argument("--activator", default=None)
    return parser.parse_known_args(rest)


def _emit_status(name: str) -> int:
    st = status(name)
    if st is None:
        print(f"{name}: INACTIVE")
        return 0
    print(f"{name}: ACTIVE | remaining={st.get('remaining_seconds', '?')}s")
    print(f"  activator: {st.get('activator')}")
    print(f"  note:      {st.get('note')}")
    print(f"  started:   {st.get('started_at')}")
    print(f"  deadline:  {st.get('deadline')}")
    return 0


def _emit_window(name: str) -> int:
    """The open window in the same "  started:" / "  note:" lines as status, so
    bin/grip-afk parses both alike; it outlives a lapsed marker."""
    win = open_window(name)
    if win is None:
        print(f"{name}: NO WINDOW")
        return 0
    print(f"{name}: OPEN WINDOW")
    print(f"  started:   {win['started_at']}")
    print(f"  note:      {win['note']}")
    return 0


def _turn_off(name: str) -> int:
    """Remove the marker; when it had already lapsed, still log an OFF, so the
    window the lapse left open is closed and a later `off` finds none."""
    removed = deactivate(name)
    if not removed and open_window(name) is not None:
        _audit(name, "OFF", "after a lapse")
    print(f"{name}: DEACTIVATED" + ("" if removed else " (no active marker)"))
    return 0


def _dispatch(action: str, name: str, opts) -> int:
    if action == "on":
        path = activate(name, opts.ttl, note=opts.note, activator=opts.activator)
        st = status(name)
        remaining = st.get("remaining_seconds") if st else opts.ttl
        print(f"{name}: ACTIVE | ttl={opts.ttl}s | remaining={remaining}s | marker={path}")
        return 0
    if action in ("off", "back"):
        return _turn_off(name)
    if action == "status":
        return _emit_status(name)
    if action == "window":
        return _emit_window(name)
    sys.stderr.write(f"grip-mode: unknown action {action!r}\n")
    sys.stdout.write(_USAGE)
    return 1


def _cli(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(_USAGE)
        return 0 if argv[:1] in (["-h"], ["--help"], ["help"]) else 1
    name, action, rest = argv[0], (argv[1] if len(argv) > 1 else "status"), argv[2:]
    try:
        opts, unknown = _parse_opts(name, rest)
    except SystemExit:
        return 2
    if unknown:
        sys.stderr.write(f"grip-mode: unknown option(s): {' '.join(unknown)}\n")
        return 2
    try:
        return _dispatch(action, name, opts)
    except ValueError as exc:
        sys.stderr.write(f"grip-mode: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
