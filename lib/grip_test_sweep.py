#!/usr/bin/env python3
"""grip_test_sweep -- run every tracked GRIP test file on its own, with a time limit.

One ``pytest`` process per file, in its own process group, so a hang or a crash
costs one file, never the run. Each file's result (return code, the pytest summary
line, the failed node ids, the seconds it took) is appended to a ``results.jsonl``
the moment it finishes, so a run that is killed, or that is deliberately resumed
with ``--resume``, never re-runs a file it already recorded.

Everything this run writes -- ``results.jsonl`` and each file's ``--basetemp`` --
lives OUTSIDE the checkout being swept, under the system temp directory by default
(``--out`` moves it). pytest creates the exact ``--basetemp`` leaf it is given, but
not that leaf's parent, and a missing parent once turned every ``tmp_path`` test in
the run red; this module creates that parent once, before the first file runs, and
gives it a neutral name (not one that names the task) because a task-named folder
has separately turned otherwise-correct tests red.

Scope is deliberately narrow: the checkout's own tracked ``tests/test_*.py`` files
(``git ls-files``), swept with ``CLAUDE_DIR`` set to that checkout. Anything wider
belongs to a different tool.

Sequential by default (one file at a time); ``--jobs N`` runs up to N files
at once in a thread pool -- each ``run_one`` call blocks on a subprocess, so the
GIL is released for the whole wait and real concurrency needs no process pool.

CLI::

    grip-test-sweep [--repo PATH] [--out DIR] [--resume RUN_DIR] [--jobs N]
                     [--limit SECONDS] [-k PATTERN] [--json]

Exit codes: 0 every file green, 1 some file red or timed out, 2 the sweep itself
could not run -- not a git checkout, ``git ls-files`` failed, or nothing matched
(no tracked test files at all, or a ``-k`` pattern nothing selects).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

RESULTS_NAME = "results.jsonl"
BASETEMP_DIRNAME = "basetemp"  # neutral on purpose -- see module docstring
DEFAULT_LIMIT_S = 180.0

# The driver's own regexes, unchanged: the last "N passed/failed/... in Ns" summary
# line, and every FAILED/ERROR node id pytest prints for a file.
_SUMMARY_RE = re.compile(r"^=*\s*((?:\d+ \w+(?:, )?)+) in [\d.]+s", re.M)
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


class SweepError(RuntimeError):
    """The sweep itself could not run (bad repo, ``git`` failed)."""


def default_repo() -> Path:
    """The checkout containing the current directory, else the current directory."""
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=10, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd()


def tracked_test_files(repo: Path) -> list[str]:
    """Every ``tests/test_*.py`` file tracked by git, sorted, relative to ``repo``."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "--", "tests/test_*.py"],
                             capture_output=True, text=True, timeout=60, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SweepError(f"could not list tracked test files in {repo}: {exc}") from exc
    return sorted(line for line in out.stdout.splitlines() if line.strip())


def _matches(rel: str, pattern: str) -> bool:
    """``pattern`` against ``rel`` as a glob; a pattern with no glob metacharacter is
    a plain substring, so ``-k save`` behaves like pytest's own ``-k``."""
    glob = pattern if any(ch in pattern for ch in "*?[") else f"*{pattern}*"
    return fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(Path(rel).name, glob)


def select_files(files: list[str], pattern: Optional[str]) -> list[str]:
    """``files``, narrowed to ``pattern`` when one is given."""
    return [f for f in files if _matches(f, pattern)] if pattern else list(files)


def new_run_dir(out: Optional[Path]) -> Path:
    """A fresh, timestamped run folder under ``out`` (default: the system temp dir)."""
    base = Path(out) if out else Path(tempfile.gettempdir()) / "grip-test-sweep"
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    return base / run_id


def load_done(results_path: Path) -> dict[str, dict]:
    """Rows already recorded in ``results_path``, keyed by file, so a resumed run
    skips them. A line that will not parse, or that parses to anything but an
    object with a string ``file``, is skipped rather than raised on -- a crash
    mid-write must not make the whole run unresumable. (``"file" in row`` alone
    raised on a bare number and passed a JSON string "file" -- GRIP#10119.)
    Bytes that are not UTF-8 are replaced before parsing, so one damaged byte
    cannot raise for the whole file either."""
    done: dict[str, dict] = {}
    if not results_path.exists():
        return done
    for line in results_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("file"), str):
            done[row["file"]] = row
    return done


def _end_torn_line(results_path: Path) -> None:
    """End a last line that has no newline before anything is appended. A run
    killed mid-write leaves such a line, and the next row appended would share
    it: both would then fail to parse, so the next resume would lose a row that
    was written in full and run that file again. The size check, the read of
    the last byte and the append share one open handle, so no reopen can find
    a different file from the one that was checked."""
    try:
        fh = results_path.open("rb+")
    except FileNotFoundError:
        return
    with fh:
        end = fh.seek(0, os.SEEK_END)
        if end == 0:
            return
        fh.seek(end - 1)
        if fh.read(1) != b"\n":
            fh.seek(end)
            fh.write(b"\n")


def _file_digest(repo: Path, rel: str) -> str:
    """sha256 of the file's current bytes, so a resumed run can tell a file that
    has been edited since its row was recorded apart from one that has not --
    a row kept by filename alone would otherwise report a stale outcome as
    current. A file that cannot be read gets an empty digest, and ``sweep``
    always re-runs a file whose digest is empty, so a stored row for it is
    never reused (its stored digest may be empty too -- GRIP#10119)."""
    try:
        return hashlib.sha256((repo / rel).read_bytes()).hexdigest()
    except OSError:
        return ""


def _repo_head(repo: Path) -> str:
    """The repo's current HEAD commit, stamped onto every row this run writes --
    for information only. A row is kept or re-run by its content digest, never
    by this."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


_KILL_DRAIN_S = 5.0  # bound on draining output after a group kill -- see _kill_group


def _kill_group(proc: subprocess.Popen) -> str:
    """Kill ``proc``'s whole process group (or just ``proc``, if that fails), then
    drain its output with a bound.

    A grandchild that escaped into its own session with ``os.setsid()`` is immune
    to the group kill by construction, and if it inherited our stdout pipe it keeps
    that pipe's write end open after everyone else in the group is dead -- an
    unbounded ``communicate()`` here would then wait forever for an EOF that never
    comes. Bounded, it gives up and returns whatever text arrived in time, and
    closes our side of the pipe so nothing here can block on it again.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        text, _ = proc.communicate(timeout=_KILL_DRAIN_S)
        return text or ""
    except subprocess.TimeoutExpired:
        return ""
    finally:
        if proc.stdout is not None:
            proc.stdout.close()


class _LiveProcs:
    """Every currently-running ``Popen``, so one interrupt can kill every running
    process group at once -- not only the one a caller happened to be blocked on.

    A signal such as Ctrl-C is only ever delivered to the main thread. Under
    ``--jobs`` every OTHER file's subprocess is being waited on from inside a
    worker thread that never sees the interrupt at all, so killing just the one
    process the main thread happened to be blocked on would leave every sibling
    running. This registry is how the main thread reaches them too.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set = set()

    def add(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)

    def discard(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    def kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _kill_group(proc)


def _wait_for(proc: subprocess.Popen, limit: float,
              live: Optional["_LiveProcs"]) -> tuple:
    """Wait up to ``limit`` seconds for ``proc``, killing its WHOLE group -- not
    just ``proc`` itself -- on a timeout, and on any OTHER exit path too (a
    ``KeyboardInterrupt``, or anything else) before that exception is re-raised:
    a bare ``except TimeoutExpired`` left every other exit path free to abandon
    the child, and with ``start_new_session=True`` a plain Ctrl-C never reaches a
    detached process group on its own.

    ``live``, when given, tracks ``proc`` alongside every other
    concurrently-running one, so a caller that catches an interrupt can kill
    every running group at once -- see ``_LiveProcs``.

    Returns ``(rc, text)``: ``rc`` is an int, or the string "timeout".
    """
    if live is not None:
        live.add(proc)
    try:
        try:
            text, _ = proc.communicate(timeout=limit)
            return proc.returncode, text
        except subprocess.TimeoutExpired:
            return "timeout", _kill_group(proc)
        except BaseException:
            _kill_group(proc)
            raise
    finally:
        if live is not None:
            live.discard(proc)


def run_one(py: str, rel: str, repo: Path, env: dict, limit: float, basetemp_root: Path,
            digest: str = "", head: str = "", live: Optional["_LiveProcs"] = None) -> dict:
    """Run one test file under ``py -m pytest``, in its own process group (see
    ``_wait_for`` for what happens to that group on a timeout or an interrupt).

    ``digest`` and ``head`` are stamped onto the returned row unchanged -- the
    caller computes them once (see ``sweep``), so every row one run writes shares
    the same ``head``, and a file's digest is read once rather than twice.

    Returns the row ``results.jsonl`` stores: file, rc (an int, or the string
    "timeout"), the last pytest summary line, the seconds taken, up to 20 failed
    node ids, the file's content digest, and the repo's HEAD (information only).
    """
    start = time.time()
    basetemp = basetemp_root / rel.replace("/", "_").replace("\\", "_")
    argv = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:warnings",
            "--basetemp", str(basetemp), rel]
    proc = subprocess.Popen(argv, cwd=repo, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    rc, text = _wait_for(proc, limit, live)
    summary = _SUMMARY_RE.findall(text or "")
    failed = _FAILED_RE.findall(text or "")
    return {"file": rel, "rc": rc, "summary": summary[-1] if summary else "",
            "seconds": round(time.time() - start, 1), "failed": failed[:20],
            "digest": digest, "head": head}


def summarize(run_dir: Path, rows: dict[str, dict]) -> dict:
    """The final report: counts by outcome, the red files, and every row."""
    ordered = [rows[f] for f in sorted(rows)]
    red = sorted(r["file"] for r in ordered if r["rc"] not in (0, "timeout"))
    timed_out = sorted(r["file"] for r in ordered if r["rc"] == "timeout")
    green = [r["file"] for r in ordered if r["rc"] == 0]
    return {"run_dir": str(run_dir), "total": len(ordered), "green": len(green),
            "red": len(red), "timeout": len(timed_out), "red_files": red,
            "timeout_files": timed_out, "results": ordered}


def _dispatch(pending: list[str], jobs: int, run_fn: Callable[[str], dict],
              record_fn: Callable[[dict], None], live: "_LiveProcs") -> None:
    """Run ``run_fn(file)`` for every file in ``pending`` -- sequentially, or (when
    ``jobs`` > 1) up to ``jobs`` at once in a thread pool -- calling ``record_fn``
    on each result as it completes.

    On any interrupt, every process ``live`` is tracking is killed before the
    exception is re-raised: a worker thread blocked on its own subprocess never
    sees a signal delivered to the main thread, so killing only the one process
    the main thread happened to be waiting on would leave every sibling running.
    """
    if jobs <= 1 or len(pending) <= 1:
        try:
            for rel in pending:
                record_fn(run_fn(rel))
        except BaseException:
            live.kill_all()
            raise
        return

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_fn, rel) for rel in pending]
        try:
            for future in as_completed(futures):
                record_fn(future.result())
        except BaseException:
            # Cancel BEFORE the group kill: the `with` block's own exit still
            # calls shutdown(wait=True), which would otherwise block until every
            # queued-but-not-yet-started file ran to completion too.
            for future in futures:
                future.cancel()
            live.kill_all()
            raise


def render(report: dict) -> str:
    """The report as text a terminal can read."""
    lines = [f"== grip-test-sweep: {report['total']} file(s) in {report['run_dir']} ==",
             f"green {report['green']} · red {report['red']} · timed out {report['timeout']}"]
    for rel in report["red_files"]:
        lines.append(f"  RED     {rel}")
    for rel in report["timeout_files"]:
        lines.append(f"  TIMEOUT {rel}")
    return "\n".join(lines)


def sweep(repo: Path, out: Optional[Path] = None, resume: Optional[Path] = None,
          jobs: int = 1, limit: float = DEFAULT_LIMIT_S, pattern: Optional[str] = None,
          on_result: Optional[Callable[[dict], None]] = None) -> dict:
    """Sweep ``repo``'s tracked test files, one process per file, and return the report.

    ``resume`` reopens an existing run folder and skips every selected file whose
    content digest still matches the row recorded for it; otherwise a fresh
    timestamped folder is made under ``out`` (default: the system temp directory
    -- never inside ``repo``). A file that was EDITED since its row was recorded
    is re-run even though its name is unchanged -- a row kept by filename alone
    would report that stale outcome as current. Run one sweep at a time per run
    folder: two sweeps resuming the same folder at once would both run the
    pending files and interleave their rows.

    The returned summary covers only the files THIS call selected (after
    ``pattern``), never every row ``results.jsonl`` happens to also hold from an
    earlier, differently-filtered run against the same ``run_dir``.

    Raises ``SweepError`` -- never a quiet, all-green-looking empty report --
    when nothing matches: a mistyped ``pattern`` and a genuinely-empty checkout
    both select zero files, and the caller (``main``) turns that into exit 2.
    """
    files = select_files(tracked_test_files(repo), pattern)
    if not files:
        reason = f"no tracked test file matches {pattern!r}" if pattern else \
            "no tracked test files (tests/test_*.py)"
        raise SweepError(f"nothing to sweep in {repo}: {reason}")

    run_dir = Path(resume).resolve() if resume else new_run_dir(out)
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / RESULTS_NAME
    basetemp_root = run_dir / BASETEMP_DIRNAME
    basetemp_root.mkdir(parents=True, exist_ok=True)  # pytest makes the leaf, not this

    digests = {f: _file_digest(repo, f) for f in files}
    rows = load_done(results_path)
    _end_torn_line(results_path)
    pending = [f for f in files
               if not digests[f] or rows.get(f, {}).get("digest") != digests[f]]
    head = _repo_head(repo)

    py = sys.executable
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["CLAUDE_DIR"] = str(repo)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # no stray __pycache__ inside the checkout

    write_lock = threading.Lock()

    def _record(row: dict) -> None:
        with write_lock:
            with results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            rows[row["file"]] = row
        if on_result is not None:
            on_result(row)

    live = _LiveProcs()
    run_fn = lambda rel: run_one(py, rel, repo, env, limit, basetemp_root,  # noqa: E731
                                 digests[rel], head, live)
    _dispatch(pending, jobs, run_fn, _record, live)

    selected = {f: rows[f] for f in files if f in rows}
    return summarize(run_dir, selected)


def _print_progress(row: dict) -> None:
    status = "TIMEOUT" if row["rc"] == "timeout" else ("green" if row["rc"] == 0 else "RED")
    print(f"{status:7s} {row['seconds']:7.1f}s  {row['file']}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Run every tracked GRIP test file on its own, "
                                             "with a time limit.")
    ap.add_argument("--repo", type=Path, default=None,
                    help="the checkout to sweep (default: the current checkout)")
    ap.add_argument("--out", type=Path, default=None,
                    help="where the run folder goes (default: the system temp directory)")
    ap.add_argument("--resume", type=Path, default=None, metavar="RUN_DIR",
                    help="continue a previous run, skipping files it already recorded")
    ap.add_argument("--jobs", type=int, default=1,
                    help="run this many test files at once (default: 1)")
    ap.add_argument("--limit", type=float, default=DEFAULT_LIMIT_S,
                    help="seconds before a file's run is killed (default: 180)")
    ap.add_argument("-k", "--filter", dest="pattern", default=None,
                    help="only sweep files matching this substring or glob")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(list(argv) if argv is not None else None)

    repo = (args.repo or default_repo()).resolve()
    try:
        report = sweep(repo, out=args.out, resume=args.resume, jobs=max(1, args.jobs),
                       limit=args.limit, pattern=args.pattern,
                       on_result=None if args.json else _print_progress)
    except SweepError as exc:
        print(f"grip-test-sweep: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=1) if args.json else render(report))
    return 1 if (report["red"] or report["timeout"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
