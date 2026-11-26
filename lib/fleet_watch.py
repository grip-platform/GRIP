#!/usr/bin/env python3
"""fleet_watch -- are the agent fleets stuck or burning tokens?

The watch measures what the harness can see without a model call, once per tick:

* every Claude Code transcript (session and subagent) written inside the window,
  with its growth in bytes since the previous tick, because a transcript grows only
  when a model turn or a tool result lands;
* a HOT flag when one transcript grows more than ``--hot-mb`` in a single tick;
* a STUCK flag when the bytes a transcript gained since the previous tick carry the
  same failed tool result ``STUCK_REPEATS`` times or more;
* every headless harness process (``claude -p``, ``codex exec``, ``opencode run``,
  ``claude --cloud``), because those run with nobody reading them. On Windows the
  process list comes from psutil, since the ``ps`` that ships with Git for Windows has
  no ``-o``; when no list can be read, the report says "unknown", never "none".

The watch never stops anything. Stopping is the caller's job, and only for work the
caller started itself (memory: kill only processes you started, 2026-10-11). The
rule it serves is ``rules/workflows-never-stuck-never-burning.md``; the skill is
``skills/efficiency``; the agent that reads it is ``agents/efficiency-meta-agent.md``.

It reads Claude Code's transcript layout: ``projects/<cwd-slug>/<session>.jsonl`` for a
session and ``projects/<cwd-slug>/<session>/subagents/agent-*.jsonl`` for its
subagents, or ``.../subagents/workflows/<run>/agent-*.jsonl`` for the agents of a
Workflow run. ``tests/test_fleet_watch.py`` pins that layout, so a change in it fails
there first. The baseline it measures growth against is kept per session, so two
sessions ticking at the same moment never overwrite each other's and read a false
QUIET. Every tick appends one JSON line to ``logs/fleet-watch.jsonl``, which is where
a report's quoted verdict can be checked.

CLI::

    grip-fleet-watch [--json] [--window 600] [--hot-mb 5] [--state PATH] [--log PATH] [--root DIR]

Exit codes: 0 quiet, 1 something hot or stuck, 2 the projects root could not be read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Iterable, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

DEFAULT_WINDOW_S = 600
DEFAULT_HOT_BYTES = 5 * 1024 * 1024
STUCK_REPEATS = 5
TAIL_BYTES = 400_000
TOP_N = 20

# A headless claude: `claude` followed later by -p or --print as a word of its own (the
# plain `claude -p <prompt>` included), or by --output-format.
_HEADLESS_RE = re.compile(r"claude[^ ]* (.* )?(-p|--print)( |$)|claude[^ ]* .*--output-format"
                          r"|codex exec|opencode run|claude --cloud")
_NOISE_RE = re.compile(r" rg |fleet_watch|grip-fleet-watch")


def projects_root() -> Path:
    """Where Claude Code keeps transcripts: ``$CLAUDE_DIR/projects``."""
    return Path(get_claude_dir()) / "projects"


_SESSION_KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "OTTY_PANE_ID",
                 "TERM_SESSION_ID", "ITERM_SESSION_ID")


def session_key() -> str:
    """A short stable id for the calling session, or "" when nothing names it.

    Claude Code exports ``CLAUDE_CODE_SESSION_ID`` into the Bash tool's environment
    (measured 2026-10-28; ``CLAUDE_SESSION_ID`` is the name hooks see); a terminal
    pane names itself in ``OTTY_PANE_ID``, ``TERM_SESSION_ID`` or ``ITERM_SESSION_ID``.
    The value is hashed so a pane prefix shared by every window cannot collide.
    """
    for var in _SESSION_KEYS:
        value = os.environ.get(var, "").strip()
        if value:
            return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return ""


def default_state_path() -> Path:
    """One baseline file per session.

    Two sessions ticking at the same moment against one shared file would each
    save over the other's baseline and read a growth of zero, a false QUIET
    (AGORA on GRIP#8765). With a session key, each session keeps its own file;
    without one, the shared file is the only choice left.
    """
    key = session_key()
    name = f"sizes-{key}.json" if key else "sizes.json"
    return Path(get_claude_dir()) / "state" / "fleet-watch" / name


def default_log_path() -> Path:
    return Path(get_claude_dir()) / "logs" / "fleet-watch.jsonl"


def append_log(path: Path, report: "Report") -> None:
    """One JSON line per tick. Fail-open: the log is evidence, never a gate."""
    line = {"at": report.at, "verdict": report.verdict, "written": len(report.transcripts),
            "hot": [t.owner for t in report.hot], "stuck": [t.owner for t in report.stuck],
            "headless": None if report.headless is None else len(report.headless),
            "session": session_key() or None}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


@dataclass
class Transcript:
    path: str
    kind: str          # "session" or "subagent"
    owner: str         # the session id (first 8 chars) that owns the transcript
    size: int
    growth: int        # bytes since the previous tick; 0 on the first sighting
    age_s: float       # seconds since the last write
    hot: bool
    stuck: str = ""    # the repeated error text, or "" when none repeats


@dataclass
class Report:
    at: str
    window_s: int
    hot_bytes: int
    prev_known: bool
    transcripts: List[Transcript] = field(default_factory=list)
    headless: Optional[List[str]] = field(default_factory=list)  # None: the list could not be read

    @property
    def hot(self) -> List[Transcript]:
        return [t for t in self.transcripts if t.hot]

    @property
    def stuck(self) -> List[Transcript]:
        return [t for t in self.transcripts if t.stuck]

    @property
    def verdict(self) -> str:
        if self.hot or self.stuck:
            return "ACT"
        return "QUIET"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        return d


def load_sizes(state: Path) -> dict:
    try:
        return json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_sizes(state: Path, sizes: dict) -> None:
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps(sizes), encoding="utf-8")
    os.replace(tmp, state)


def classify(path: Path) -> Tuple[str, str]:
    """(kind, owner) from the transcript's path relative to the projects root.

    The owner of a subagent is the session folder just above ``subagents``, not the
    transcript's grandparent: a Workflow agent sits two folders deeper, and its
    grandparent is ``workflows``. The path is relative to the root, so a folder
    named ``subagents`` above the root counts for nothing; with no session folder
    in view the owner is empty, never part of a file name.
    """
    parts = path.parts
    if "subagents" in parts:
        i = parts.index("subagents")
        return "subagent", parts[i - 1][:8] if i else ""
    return "session", path.stem[:8]


def _block_text(block: dict, record: dict) -> str:
    """The text of one tool_result block: its content, or the record's toolUseResult string."""
    content = block.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        joined = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict)).strip()
        if joined:
            return joined
    result = record.get("toolUseResult")
    return result.strip() if isinstance(result, str) else ""


def failed_tool_results(record: dict) -> List[str]:
    """The text of every failed tool_result block in one transcript record.

    Claude Code writes ``{"type": "user", "message": {"content": [{"type":
    "tool_result", "tool_use_id", "content", "is_error"}]}}`` plus a top-level
    ``toolUseResult`` string on failure (``lib/continuity_features.py``). The
    blocks are read as data, so field order and content shape do not matter.
    """
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [_block_text(block, record)[:120] for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")]


def repeated_error(path: Path, start: int, end: int) -> str:
    """The failed-tool text that repeats STUCK_REPEATS times in bytes [start, end), or "".

    Only the bytes appended since the previous tick are read, so a transcript that
    failed five times, recovered and kept working is not reported STUCK on the
    next tick for errors that are already history (Codex on GRIP#8765).
    """
    start = max(start, end - TAIL_BYTES, 0)
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(max(end - start, 0)).decode("utf-8", "replace")
    except OSError:
        return ""
    texts: List[str] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            texts.extend(t for t in failed_tool_results(json.loads(line)) if len(t) >= 10)
        except ValueError:
            continue
    if not texts:
        return ""
    text, count = Counter(texts).most_common(1)[0]
    return text if count >= STUCK_REPEATS else ""


def scan_transcripts(root: Path, prev: dict, now: float, window_s: int,
                     hot_bytes: int) -> Tuple[List[Transcript], dict]:
    """Every transcript that changed since the previous tick or was written inside
    the window, and the sizes of all of them.

    Growth is measured against the previous tick's size. A transcript absent from
    an established baseline was born after it, so its whole size is growth. A
    transcript that changed since the baseline is reported however old its
    timestamp, so a tick that runs late never loses the event (Codex on GRIP#8765).
    """
    rows: List[Transcript] = []
    sizes: dict = {}
    baseline = bool(prev)
    for path in root.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        key, size = str(path), st.st_size
        sizes[key] = size
        age = now - st.st_mtime
        before = prev[key] if key in prev else (0 if baseline else size)
        growth = size - before
        if growth <= 0 and age > window_s:
            continue
        kind, owner = classify(path.relative_to(root))
        stuck = repeated_error(path, before, size) if growth > 0 else ""
        rows.append(Transcript(key, kind, owner, size, growth, age, growth > hot_bytes, stuck))
    rows.sort(key=lambda t: -t.growth)
    return rows, sizes


def headless_processes(ps_text: str) -> List[str]:
    """The headless harness processes in a ``ps -eo pid,etime,command`` listing."""
    hits = []
    for line in ps_text.splitlines():
        if _HEADLESS_RE.search(line) and not _NOISE_RE.search(line):
            hits.append(line.strip()[:140])
    return hits


def _etime(seconds: float) -> str:
    """Elapsed time in the ``[[dd-]hh:]mm:ss`` form ``ps`` prints."""
    left = max(0, int(seconds))
    days, left = divmod(left, 86400)
    hours, left = divmod(left, 3600)
    minutes, secs = divmod(left, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}" if days or hours else f"{minutes:02d}:{secs:02d}"
    return f"{days}-{clock}" if days else clock


def _windows_line(info: dict, now: float) -> Optional[str]:
    """One ``pid etime command`` line, or None when the argv cannot be read.

    argv[0] is cut to its file stem, so a full path to ``claude.exe`` followed by ``-p``
    reads ``claude -p``, as ``ps`` shows a command elsewhere, and ``_HEADLESS_RE`` matches
    it unchanged."""
    argv = info.get("cmdline")
    if not argv:
        return None
    command = " ".join([PureWindowsPath(argv[0]).stem, *argv[1:]])
    return f"{info['pid']:>7} {_etime(now - (info.get('create_time') or now)):>11} {command}"


def _windows_listing() -> Optional[str]:
    """The process list on Windows, read with psutil, because the ``ps`` that ships with
    Git for Windows has no ``-o`` (GRIP#8769). None when psutil is missing or fails."""
    try:
        import psutil
    except ImportError:
        return None
    now = time.time()
    try:
        lines = [_windows_line(p.info, now)
                 for p in psutil.process_iter(["pid", "create_time", "cmdline"])]
    except psutil.Error:
        return None
    return "\n".join(["    PID     ELAPSED COMMAND", *filter(None, lines)]) + "\n"


def _posix_listing() -> Optional[str]:
    """``ps -eo pid,etime,command``, or None when ``ps`` fails or prints nothing."""
    try:
        done = subprocess.run(["ps", "-eo", "pid,etime,command"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 and done.stdout.strip() else None


def _ps_listing() -> Optional[str]:
    """Every process as a ``pid etime command`` line, or None when the list cannot be
    read, which the report shows as "unknown", never as "none"."""
    return _windows_listing() if sys.platform == "win32" else _posix_listing()


def scan(root: Optional[Path] = None, state: Optional[Path] = None,
         window_s: int = DEFAULT_WINDOW_S, hot_bytes: int = DEFAULT_HOT_BYTES,
         now: Optional[float] = None, ps_text: Optional[str] = None) -> Report:
    """One tick. Reads the previous tick's sizes, writes this tick's, returns the report."""
    root = root or projects_root()
    state = state or default_state_path()
    now = time.time() if now is None else now
    prev = load_sizes(state)
    rows, sizes = scan_transcripts(root, prev, now, window_s, hot_bytes)
    save_sizes(state, sizes)
    listing = _ps_listing() if ps_text is None else ps_text
    stamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(now))
    headless = None if listing is None else headless_processes(listing)
    return Report(stamp, window_s, hot_bytes, bool(prev), rows, headless)


def render(report: Report) -> str:
    """The tick as text a reader can act on."""
    lines = [f"== fleet watch {report.at} == verdict {report.verdict}",
             f"transcripts written in the last {report.window_s // 60} min: {len(report.transcripts)}"
             + ("" if report.prev_known else " (first tick: growth shows 0 until the next tick)")]
    for t in report.transcripts[:TOP_N]:
        flag = " <-- HOT" if t.hot else ""
        lines.append(f"  +{t.growth / 1024:8.0f} KB  {t.size / 1048576:7.1f} MB  {t.age_s / 60:4.0f} min ago"
                     f"  {t.kind:8s} {t.owner}{flag}")
    for t in report.stuck:
        lines.append(f"  STUCK? {t.owner} ({t.kind}): the same tool error repeats in the tail: {t.stuck[:100]}")
    if report.headless is None:
        lines.append("headless harness processes: unknown (the process list could not be read)")
    else:
        lines.append("headless harness processes: " + (str(len(report.headless)) if report.headless else "none"))
        lines.extend(f"  {h}" for h in report.headless)
    lines.append(f"hot (>{report.hot_bytes // 1048576} MB growth this tick): {len(report.hot)}"
                 f" · stuck: {len(report.stuck)}")
    if report.verdict == "ACT":
        lines.append("ACT: stop only the work you started yourself (TaskStop, CronDelete, the loop's own stop);"
                     " report anything else by session id and pid.")
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Are the agent fleets stuck or burning tokens?")
    ap.add_argument("--json", action="store_true", help="one JSON object instead of text")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_S, help="seconds of history to look at")
    ap.add_argument("--hot-mb", type=float, default=DEFAULT_HOT_BYTES / 1048576,
                    help="growth in one tick that counts as HOT")
    ap.add_argument("--state", type=Path, default=None, help="where the previous tick's sizes live")
    ap.add_argument("--log", type=Path, default=None,
                    help="where each tick's verdict is appended (default logs/fleet-watch.jsonl under CLAUDE_DIR)")
    ap.add_argument("--root", type=Path, default=None, help="the projects root to scan")
    args = ap.parse_args(list(argv) if argv is not None else None)
    root = args.root or projects_root()
    if not root.is_dir():
        print(f"fleet watch: projects root not found: {root}", file=sys.stderr)
        return 2
    report = scan(root, args.state, args.window, int(args.hot_mb * 1048576))
    append_log(args.log or default_log_path(), report)
    print(json.dumps(report.to_dict(), indent=1) if args.json else render(report))
    return 1 if report.verdict == "ACT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
