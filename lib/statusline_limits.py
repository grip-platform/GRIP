"""Live Claude usage-limit segment for GRIP's status line, plus the
once-per-window pace warning broadcast to other running sessions.

Origin: V>>, 2026-10-08 -- two Claude accounts silently crossed 100% of
their weekly window and the work moved onto paid extra usage before anyone
noticed. The status line only showed context-window percentage; nothing
showed account-level usage, and nothing told other sessions to slow down.

Two sources, read in priority order
------------------------------------
1. Claude Code's OWN stdin JSON (``rate_limits.five_hour.used_percentage``
   / ``.resets_at``, ``rate_limits.seven_day.*`` -- documented at
   docs.claude.com/en/docs/claude-code/statusline). This is THIS session's
   own account: authoritative, already on stdin, zero extra read. Present
   for claude.ai Pro/Max subscribers (or a gateway that sets a spend limit)
   from Claude Code roughly v2.1.25x onward; independently absent per
   window, and dropped once a window's own ``resets_at`` passes.
2. HAL's account ledger (``~/.hal/lib/hal/account_ledger.py``), read here
   as a plain JSON file -- never imported, so this module carries no
   import-time dependency on a sibling repo, and no assumption about HAL
   being installed at all. Used ONLY when source 1 is silent (an API-key
   session, a Claude Code build predating ``rate_limits``, or a non-
   subscriber). Honest about its own limit: it knows only accounts whose
   traffic passed through the HAL wire, so it is a sample, not a census.

A window over its limit reads above 100%, and it is shown that way rather
than dropped (GRIP#8274). HAL recorded the fraction 1.01 on 2026-10-10. The
service sets no ceiling, and Claude Code 2.1.296 computes its own figure as
``Math.round(u * 1000) / 10``, so both sources keep every reading HAL's ledger
can hold: up to 10,000% (``_PERCENT_CEILING``). A large reading is a window
deep in paid extra usage, which is exactly when it must show. A larger figure,
or a value that is no share at all (negative, infinite, not a number), is left
out. The relay's reader in ``lib/subscription_room.py`` follows the same rule.

Every parsing/formatting function below is pure -- no file I/O, no
subprocess, no clock reads beyond what is handed in -- so each is testable
with fake JSON input and a fake ledger, per the brief. The pace-warning
step is not: :func:`process_warnings` reads and writes the warned-state
file and invokes GRIP's existing ``bin/mesh broadcast`` itself (never a
new channel), the whole thing serialised across every concurrently
running status line by a non-blocking lock on ``<state file>.lock`` --
when another process already holds it, this refresh skips the step
entirely rather than waiting, and a mark is persisted only for a
broadcast that actually succeeded, so a failed send is retried on the
next refresh instead of being silently swallowed for the rest of the
window (GRIP#7762 review). ``main()`` is the only caller of that side
effect.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from file_lock import FileLock, LockTimeoutError  # noqa: E402

THRESHOLD_DEFAULT = 70.0
CC_WINDOWS = ("five_hour", "seven_day")
_CC_LABEL = {"five_hour": "5h", "seven_day": "wk"}
_WINDOW_WORDS = {"five_hour": "5-hour", "seven_day": "7-day"}
#: A `resets_at` outside this range is treated as unknown rather than
#: rendered -- generous enough for any real epoch, tight enough to catch a
#: garbled value (mirrors the care in HAL's own account_ledger.py, without
#: sharing its exact constant).
_EPOCH_BOUNDS = (0, 10_000_000_000)
#: The largest reading HAL's ledger can hold: it accepts a fraction from 0 to 100
#: (``_FRACTION_RANGE`` in ``~/.hal/lib/hal/account_ledger.py``), so 10,000%. A
#: larger figure was not written by HAL, and scaling one could overflow to
#: infinity, so it is left out. Twin of ``_FRACTION_CEILING`` in
#: ``lib/subscription_room.py`` and ``lib/fanout_preflight.py``; a test holds all
#: three readers to the same answer.
_PERCENT_CEILING = 10_000.0
#: Drop a warned mark once its window is this stale -- keeps the state file
#: from growing forever; a mark with no known reset never ages out this way.
_PRUNE_AFTER_S = 14 * 86400
#: Bound on the broadcast subprocess -- a hung `mesh` must not hang the
#: status line it is reporting on.
_BROADCAST_TIMEOUT_S = 5

Window = Tuple[float, Optional[int]]  # (percent used, over 100 past the limit; resets_at epoch|None)


# --------------------------------------------------------------------------- #
# Reading each source into the same shape.
# --------------------------------------------------------------------------- #
def cc_windows(payload: object) -> Dict[str, Window]:
    """``{'five_hour': (pct, resets_at), 'seven_day': (pct, resets_at)}`` for
    whichever windows Claude Code's stdin JSON carries under
    ``rate_limits``. Absent, non-numeric, or out-of-range values are left
    out entirely -- never fabricated as 0."""
    out: Dict[str, Window] = {}
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return out
    for name in CC_WINDOWS:
        parsed = _one_window(limits.get(name), pct_key="used_percentage",
                              reset_key="resets_at", scale=1.0)
        if parsed is not None:
            out[name] = parsed
    return out


def hal_ledger_windows(ledger: object) -> Dict[str, Window]:
    """``{'<label>:<window>': (pct, resets_at)}`` for every account/window
    HAL's ledger has observed. ``ledger`` is the parsed
    ``account-ledger.json`` object (``None`` or any other shape -> ``{}``).
    One account at a time, via :func:`_hal_windows_of` -- kept as its own
    function (not inlined) so each account's small, fixed-size window dict
    is a single bounded lookup, never a loop nested inside this one."""
    out: Dict[str, Window] = {}
    accounts = ledger.get("accounts") if isinstance(ledger, dict) else None
    if not isinstance(accounts, dict):
        return out
    for label, entry in accounts.items():
        if isinstance(label, str) and isinstance(entry, dict):
            out.update(_hal_windows_of(label, entry))
    return out


def _hal_windows_of(label: str, entry: dict) -> Dict[str, Window]:
    """Every window one account's ledger entry carries, keyed
    ``'<label>:<window-name>'``. At most 4 fixed window names per account
    (``account_ledger.WINDOWS``) -- O(1) per account, not a scaling loop."""
    quota = entry.get("quota")
    windows = quota.get("windows") if isinstance(quota, dict) else None
    if not isinstance(windows, dict):
        return {}
    found: Dict[str, Window] = {}
    for name, window in windows.items():  # O(1): at most 4 fixed window names
        # scale=100: HAL's ledger stores the raw anthropic-ratelimit-unified-*
        # header verbatim, a FRACTION: 0.0-1.0, and over 1.0 when the window
        # is over its limit (confirmed against this machine's real
        # ledger: 0.43, 0.22, 1.0, 0.0 -- NOT an already-scaled percentage like
        # Claude Code's own `used_percentage`). Rendering it unscaled would
        # show "0%" for an account sitting at 43% used.
        parsed = _one_window(window, pct_key="utilization", reset_key="reset", scale=100.0)
        if parsed is not None:
            found[f"{label}:{name}"] = parsed
    return found


def _one_window(window: object, *, pct_key: str, reset_key: str,
                 scale: float) -> Optional[Window]:
    """One ``{pct_key, reset_key}`` pair as a validated ``Window``, or
    ``None`` when the percentage is missing, non-numeric, or out of range.
    ``scale`` converts the raw value to a percentage (``1.0`` when the
    source already reports one, ``100.0`` when it reports a fraction) --
    applied AFTER the type guard and the range check, so a boolean ``True``
    is rejected before it could be scaled into a plausible-looking number.
    The range is checked on the raw value (0 to ``_PERCENT_CEILING /
    scale``), the same check the relay's reader makes, so no figure can
    overflow. A window over its limit is kept, above 100%, so it shows as
    over its cap instead of vanishing. A bad or missing reset still keeps a
    good percentage -- unknown reset time is not the same defect as unknown
    usage."""
    if not isinstance(window, dict):
        return None
    raw = window.get(pct_key)
    if (isinstance(raw, bool) or not isinstance(raw, (int, float))
            or not 0.0 <= raw <= _PERCENT_CEILING / scale):
        return None
    pct = raw * scale
    resets = window.get(reset_key)
    # Bounds before int(), as the relay's reader does: JSON can carry Infinity
    # and NaN, and int() of either raises (Codex on GRIP#8283).
    known = (isinstance(resets, (int, float)) and not isinstance(resets, bool)
             and _EPOCH_BOUNDS[0] <= resets <= _EPOCH_BOUNDS[1])
    return float(pct), int(resets) if known else None


def current_windows(windows: Dict[str, Window], now: float) -> Dict[str, Window]:
    """The windows whose reading still describes the present. A window whose
    known reset has passed is over, and the account has renewed; HAL's ledger
    keeps its last reading until fresh traffic replaces it, so that reading is
    neither drawn nor warned about (Codex on GRIP#8283). A window with no known
    reset is kept. Pure: ``now`` is handed in."""
    return {key: window for key, window in windows.items()
            if window[1] is None or window[1] > now}


def worst_hal_window(hal: Dict[str, Window]) -> Optional[Tuple[str, float, Optional[int]]]:
    """``(label, pct, resets_at)`` for the single highest-utilization HAL
    window, or ``None`` when ``hal`` is empty. First-seen wins a tie."""
    best: Optional[Tuple[str, float, Optional[int]]] = None
    for key, (pct, resets) in hal.items():
        label = key.split(":", 1)[0]
        if best is None or pct > best[1]:
            best = (label, pct, resets)
    return best


# --------------------------------------------------------------------------- #
# Rendering.
# --------------------------------------------------------------------------- #
def format_segment(cc: Dict[str, Window],
                    hal_worst: Optional[Tuple[str, float, Optional[int]]]) -> str:
    """The short status-line text: this session's own figures when Claude
    Code's stdin carries them -- e.g. ``5h 39% (10:29) · wk 21%`` --
    else the single most-used HAL-observed window, clearly prefixed
    ``hal <label>`` so it can never be mistaken for this session's own
    account, else ``''`` (fail quiet; never a label with no number)."""
    if cc:
        parts = [_cc_part(name, cc[name]) for name in CC_WINDOWS if name in cc]
        return " · ".join(parts)
    if hal_worst is not None:
        label, pct, resets = hal_worst
        when = f" ({_local_hhmm(resets)})" if resets is not None else ""
        return f"hal {label} {pct:.0f}%{when}"
    return ""


def _cc_part(name: str, window: Window) -> str:
    pct, resets = window
    label = _CC_LABEL[name]
    # Reset time only on the 5-hour figure: close enough to matter at a
    # glance. The weekly reset is routinely days away and would just add
    # clutter for a date that is not "today".
    if name == "five_hour" and resets is not None:
        return f"{label} {pct:.0f}% ({_local_hhmm(resets)})"
    return f"{label} {pct:.0f}%"


def _local_hhmm(epoch: int) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%H:%M")
    except (ValueError, OSError, OverflowError):
        return "?"


# --------------------------------------------------------------------------- #
# The pace warning: once per window crossing, never per refresh.
# --------------------------------------------------------------------------- #
def merge_windows(cc: Dict[str, Window], hal: Dict[str, Window]) -> Dict[str, Window]:
    """Every window worth watching for a pace warning, keyed so the two
    sources can never collide: ``cc:<window>`` or ``hal:<label>:<window>``."""
    merged = {f"cc:{name}": window for name, window in cc.items()}
    merged.update({f"hal:{key}": window for key, window in hal.items()})
    return merged


def pending_warnings(windows: Dict[str, Window], warned: Iterable[str],
                      threshold: float = THRESHOLD_DEFAULT
                      ) -> Tuple[List[Tuple[str, float, Optional[int]]], Set[str]]:
    """Windows at or over ``threshold`` whose exact ``(key, resets_at)`` pair
    is not already in ``warned`` -- so a window that rolls over to a new
    ``resets_at`` warns again, and one still inside the same window never
    repeats. Pure: returns ``(due, updated_marks)`` where ``updated_marks``
    is every mark that WOULD apply if every ``due`` entry were sent --
    :func:`process_warnings` is the one place that decides which of them
    actually were, and persists only those."""
    due: List[Tuple[str, float, Optional[int]]] = []
    updated = set(warned)
    for key, (pct, resets) in windows.items():  # O(1): at most a handful of windows
        if pct < threshold:
            continue
        mark = f"{key}@{resets}"
        if mark in updated:
            continue
        due.append((key, pct, resets))
        updated.add(mark)
    return due, updated


def warning_message(key: str, pct: float, resets: Optional[int]) -> str:
    """The sentence broadcast to every other session via GRIP's existing
    ``bin/mesh broadcast`` -- never a new channel."""
    source, _, rest = key.partition(":")
    if source == "hal":
        label, _, window = rest.rpartition(":")
        who = f"account {label} (via HAL)"
    else:
        window = rest
        who = "this session's account"
    window_word = _WINDOW_WORDS.get(window, window)
    when = f"; resets {_local_hhmm(resets)}" if resets is not None else ""
    return (f"Pace warning: {who} {window_word} Claude usage window is at "
            f"{pct:.0f}%{when}. Slow down fan-outs.")


def run_mesh_broadcast(mesh_bin: str, message: str) -> bool:
    """Invoke GRIP's existing cross-session broadcast (``bin/mesh
    broadcast``). ``True`` only on a clean exit (returncode 0); ``False``
    for a missing or non-executable binary, a non-zero exit, a timeout, or
    anything ``subprocess`` can raise -- never raises itself, and bounded
    so a hung mesh process cannot hang the status line."""
    if not mesh_bin or not os.path.isfile(mesh_bin) or not os.access(mesh_bin, os.X_OK):
        return False
    try:
        result = subprocess.run([mesh_bin, "broadcast", message],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 timeout=_BROADCAST_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def process_warnings(windows: Dict[str, Window], *, state_path: Path, threshold: float,
                      mesh_bin: str, send: Optional[Callable[[str], bool]] = None) -> bool:
    """The whole once-per-window warning step, serialised across every
    concurrently running status line by a non-blocking lock on
    ``<state_path>.lock``: load the warned-state, broadcast each window
    newly at or over ``threshold``, and persist a mark ONLY for a
    broadcast that actually succeeded -- a broadcast that fails (binary
    missing, non-executable, erroring, timing out) leaves its mark
    unrecorded, so the very next refresh retries it rather than silently
    losing it for the rest of that window.

    Returns ``False``, having touched nothing at all, when another
    process already holds the lock -- the status line must never wait on
    it. Returns ``True`` once this call has run the step (whether or not
    any warning actually fired). Never raises: any failure here is
    strictly worse than a missed or duplicated warning, never worse than
    a broken status line."""
    do_send = send if send is not None else (lambda message: run_mesh_broadcast(mesh_bin, message))
    try:
        with FileLock(state_path, timeout=0, sidecar=True):
            warned = _load_warned(state_path)
            due, _ = pending_warnings(windows, warned, threshold)
            sent_marks = set(warned)
            for key, pct, resets in due:  # O(1): at most a handful of windows
                if do_send(warning_message(key, pct, resets)):
                    sent_marks.add(f"{key}@{resets}")
            if sent_marks != warned:
                _save_warned(state_path, sent_marks)
    except LockTimeoutError:
        return False
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# The only impure part: read stdin + 2 files, write 1 file, run 1 subprocess.
# --------------------------------------------------------------------------- #
def _read_json_quiet(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_warned(path: Path) -> Set[str]:
    data = _read_json_quiet(path)
    marks = data.get("warned") if isinstance(data, dict) else None
    return set(marks) if isinstance(marks, list) else set()


def _mark_is_fresh(mark: str, now: float) -> bool:
    _, _, resets_text = mark.rpartition("@")
    try:
        resets = float(resets_text)
    except ValueError:
        return True  # an unknown-reset mark ("...@None") never ages out
    return (now - resets) < _PRUNE_AFTER_S


def _save_warned(path: Path, marks: Set[str], *, now: Optional[float] = None) -> None:
    """Persist ``marks``, pruning stale ones, via a UNIQUELY NAMED temp
    file in the same directory (``tempfile.mkstemp``) followed by
    ``os.replace`` -- each call gets its own temp name, so two processes
    saving around the same time can never collide on one shared ``.tmp``
    path (the fixed-name scheme this replaces could, and the lock in
    :func:`process_warnings` is the primary defence against that anyway;
    this is the second, independent one)."""
    now = time.time() if now is None else now
    kept = {m for m in marks if _mark_is_fresh(m, now)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                                         suffix=".tmp")
    except OSError:
        return  # fail quiet: losing the warned-state means at most one re-warn
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"warned": sorted(kept)}))
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _default_hal_ledger() -> Path:
    home = os.environ.get("HAL_HOME") or str(Path.home() / ".hal")
    return Path(home) / "state" / "hal" / "account-ledger.json"


def _default_state_file() -> Path:
    return get_claude_dir() / "state" / "features" / "statusline-limits-warned.json"


def _default_mesh_bin() -> Path:
    return get_claude_dir() / "bin" / "mesh"


def main(argv: Optional[List[str]] = None) -> int:
    """Prints exactly one line: the status-line segment (possibly empty).
    Any due pace warning is broadcast as a side effect of this call (see
    :func:`process_warnings`) -- never printed for a separate caller to
    forward."""
    ap = argparse.ArgumentParser(
        description="GRIP statusline: live Claude usage limits + pace warning")
    ap.add_argument("--hal-ledger", default=None, help="HAL's account-ledger.json")
    ap.add_argument("--state-file", default=None, help="where crossed-threshold marks persist")
    ap.add_argument("--mesh-bin", default=None, help="GRIP's bin/mesh, for the pace broadcast")
    ap.add_argument("--threshold", type=float, default=THRESHOLD_DEFAULT)
    args = ap.parse_args(argv)

    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}

    hal_ledger_path = Path(args.hal_ledger) if args.hal_ledger else _default_hal_ledger()
    state_path = Path(args.state_file) if args.state_file else _default_state_file()
    mesh_bin = args.mesh_bin if args.mesh_bin else str(_default_mesh_bin())

    now = time.time()
    cc = current_windows(cc_windows(payload), now)
    hal = current_windows(hal_ledger_windows(_read_json_quiet(hal_ledger_path)), now)

    print(format_segment(cc, worst_hal_window(hal) if hal else None))

    process_warnings(merge_windows(cc, hal), state_path=state_path,
                      threshold=args.threshold, mesh_bin=mesh_bin)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
