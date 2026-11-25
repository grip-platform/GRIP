"""cc_insights_refresh — run Claude Code's /insights headlessly, and prove it produced something.

Claude Code's `/insights` writes one "facet" per analysed session
(`usage-data/facets/<session>.json`) and an HTML report (`usage-data/report.html`, always a
copy of the newest `report-*.html`). GRIP reads both: `lib/evo.py` takes the facets as its
Tier 0 corpus, `lib/insights_learner.py` takes the report and the facets, and
`lib/ultralearn.py` folds a coarse digest of them into the snapshot its council deliberates on.

Typing `/insights` inside a GRIP session produces a hollow report. Two stacked causes,
measured 2026-10-02 on Claude Code 2.1.283 with `claude -p "/insights" --debug-file`:

1. GRIP pins the model to the `default` alias (GRIP#6630, which keeps sessions on the 1M
   window). The main loop resolves the alias; the /insights side queries send it verbatim
   and every one is refused: 404 not_found_error "model: default".
2. With Opus 5.5 named explicitly the calls are accepted but come back empty. This build
   cannot switch Opus 5.5's thinking off (it is missing from Claude Code's list of models
   that accept thinking-off; so is Fable 5.1), and /insights caps each answer at 4,096 or
   8,192 tokens. The same run on Sonnet 5 analysed all 15 sessions.

A third trap: /insights only re-analyses a session whose transcript changed since its
cached stats (`usage-data/session-meta/<session>.json`) were written. A session whose
analysis failed keeps a fresh cache file and is never retried. `release_stuck_sessions`
deletes exactly those cache files; Claude Code regenerates them from the transcript.

A fourth, intermittent one: the report renderer trusts the model's section JSON. When the
friction section comes back with `examples` as a string instead of a list it throws
`TypeError: h.examples.map is not a function`, after the facets are saved but before the
report is written, prints nothing and exits 0. The error survives only as a
`<local-command-stderr>` entry in the nested session's transcript, which is where
`local_command_error` reads it from. A run whose report was not rewritten is retried once.

A fifth, measured on Claude Code 2.1.295 (2026-10-09, five runs with `--debug-file`): the
report-writing requests no longer follow `--model`. Every run sent its side requests to
`claude-opus-5-5[1m]`, the account's Opus default, and only the single main-loop request
to the requested model, so the six narrative sections came back empty (the Opus 5.5 shape
of cause 2). In the binary, /insights asks a helper (`ke()`) that returns either the Opus
default (`cg()`, which honours `ANTHROPIC_DEFAULT_OPUS_MODEL`) or another default (`et()`),
depending on account checks. Measured to have NO effect: `--model`, a scoped `--settings`
model, `ANTHROPIC_MODEL`, and `ANTHROPIC_DEFAULT_OPUS_MODEL`. The working lever is not yet
known. So every run now records which models actually answered (`answering_models`, read
from the debug log's dispatch lines and then deleted, because a debug log also carries
hook output) and names the cause in `diagnosis` when a report comes back incomplete.

A run is judged by the artefacts it leaves, never by its exit code: the Opus 5.5 run exited
0 with an empty report (rules/verify-canonical-not-claimed.md — a self-report is a claim).

Cost: /insights runs on the Claude subscription, which is the weekly limit
(rules/pool-a-offload-routing.md, Pool A). `refresh()` is therefore freshness-gated, by
default at most once per 24 hours, unless forced.

Only one refresh runs at a time. `refresh()` holds an flock on
`usage-data/.grip-insights.lock`, and a second call returns `busy` without spending
anything. The kernel drops that lock when the holding process exits, so a refresh that
crashes or is killed never leaves a stale lock behind. The lock comes from `_compat`,
which locks on Windows too (GRIP#8757).
"""
from __future__ import annotations

import argparse
import contextlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from itertools import chain
from pathlib import Path
from typing import Callable, Iterable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from _compat import fcntl as _fcntl  # noqa: E402  -- POSIX fcntl, or the LockFileEx lock on Windows
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_AGE_HOURS = 24.0
DEFAULT_TIMEOUT_S = 1800
DEFAULT_ATTEMPTS = 2  # one retry: the renderer crash is intermittent model variance
RELEASE_LIMIT = 50  # matches Claude Code's own cap of 50 facet analyses per run

_LOCAL_STDERR_RE = re.compile(r"<local-command-stderr>(.*?)</local-command-stderr>", re.S)

OK = "ok"
PARTIAL = "partial"
FRESH = "fresh-skip"
HOLLOW = "hollow"
FAILED = "failed"
UNAVAILABLE = "unavailable"
BUSY = "busy"  # another refresh holds the lock; it is doing the work, so this is not a failure

# Structural markers in Claude Code's report HTML (not prose: the closing "fun ending" is
# model-written, so a phrase like "0 of 12 sessions analyzed" proves nothing).
EMPTY_PANEL_MARKER = 'class="empty"'
_SECTION_RE = re.compile(r'<h2 id="section-([a-z-]+)"')
# The narrative sections of a complete 2.1.283 report. Each comes from its own model call,
# and a call that returns unusable JSON drops its section without any other sign
# (observed 2026-10-02: a run with `wins` missing). A future Claude Code that renames a
# section shows up here as `partial` naming it, which is the cue to update this set.
EXPECTED_SECTIONS = frozenset({"work", "wins", "friction", "features", "patterns", "horizon"})

EXIT_CODES = {OK: 0, FRESH: 0, BUSY: 0, PARTIAL: 2}  # everything else exits 1
LOCK_NAME = ".grip-insights.lock"
DEBUG_LOG_NAME = ".grip-insights-debug.log"  # written by the run, read once, then deleted
# One line per API request in a Claude Code 2.1.295 debug log:
#   "[API:timing] dispatching to firstParty model=claude-opus-5-5[1m]"
_DISPATCH_RE = re.compile(r"dispatching to \S+ model=(\S+)")


# ------------------------------------------------------------------------------ small reads
def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def default_model() -> str:
    return os.environ.get("GRIP_INSIGHTS_MODEL") or DEFAULT_MODEL


def default_max_age_hours() -> float:
    return _env_float("GRIP_INSIGHTS_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS)


def usage_dir(base: Optional[Path] = None) -> Path:
    return (CLAUDE_DIR if base is None else base) / "usage-data"


def facet_count(facets_dir: Path) -> int:
    return sum(1 for _ in facets_dir.glob("*.json"))


def report_age_hours(report: Path, now: Optional[float] = None) -> Optional[float]:
    """Hours since the report was last written; None when there is no report yet."""
    mtime = _mtime(report)
    if not mtime:
        return None
    return ((time.time() if now is None else now) - mtime) / 3600.0


# ------------------------------------------------------------------- the sticky-cache trap
def _analysable(meta: dict) -> bool:
    """Claude Code 2.1.283's own facet filter: at least 2 user messages and 1 minute."""
    return (meta.get("user_message_count") or 0) >= 2 and (meta.get("duration_minutes") or 0) >= 1


def stuck_sessions(udir: Path) -> list:
    """Cache files of analysable sessions that have no facet, newest session first."""
    facets_dir = udir / "facets"
    stuck = []
    for path in (udir / "session-meta").glob("*.json"):
        meta = _read_json(path)
        if _analysable(meta) and not (facets_dir / path.name).exists():
            stuck.append((str(meta.get("start_time") or ""), path))
    return [path for _, path in sorted(stuck, reverse=True)]


def release_stuck_sessions(udir: Path, limit: Optional[int] = None) -> int:
    """Delete up to `limit` (default RELEASE_LIMIT) stuck cache files so the next run
    re-analyses those sessions, newest first.

    The cache is Claude Code's own and is rebuilt from the transcript, so nothing is lost."""
    limit = RELEASE_LIMIT if limit is None else limit
    released = 0
    for path in stuck_sessions(udir)[:limit]:
        with contextlib.suppress(OSError):
            path.unlink()
            released += 1
    return released


# --------------------------------------------------------------------------------- the run
def claude_argv(claude_bin: str, model: str, debug_file: Optional[Path] = None) -> list:
    argv = [
        claude_bin, "-p", "/insights", "--model", model, "--max-turns", "1",
        # JSON output carries the nested session id, the only way back to an error that
        # /insights records in its transcript instead of printing.
        "--output-format", "json",
        # /insights needs no MCP server, and starting every configured one only adds
        # startup time and tool schemas to the single main-loop call.
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    if debug_file is not None:
        # The only record of which model each report-writing request went to.
        argv += ["--debug-file", str(debug_file)]
    return argv


def run_claude_insights(model: str, timeout_s: int) -> subprocess.CompletedProcess:
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        raise FileNotFoundError("the `claude` CLI is not on PATH")
    return subprocess.run(
        claude_argv(claude_bin, model, usage_dir() / DEBUG_LOG_NAME), capture_output=True,
        text=True, timeout=timeout_s, cwd=str(CLAUDE_DIR),
    )


def dispatched_models(debug_text: str) -> dict:
    """How many API requests went to each model, from a debug log's dispatch lines."""
    return dict(Counter(_DISPATCH_RE.findall(debug_text)))


def _answering_models(udir: Path) -> dict:
    """Read and delete the run's debug log; {} when the run left none.

    Deleted after reading because a debug log also carries hook output, and nothing
    else needs it once the dispatch lines are counted."""
    path = udir / DEBUG_LOG_NAME
    models = dispatched_models(_read_text(path))
    with contextlib.suppress(OSError):
        path.unlink()
    return models


def _diagnosis(status: str, model: str, models: dict) -> str:
    """Name the measured cause when an incomplete report was written by another model."""
    others = sorted(m for m in models if m != model)
    if status not in (PARTIAL, HOLLOW) or not others:
        return ""
    return (f"report-writing requests went to {', '.join(others)}, not {model}: on Claude Code "
            "2.1.295 /insights picks its own model and ignores --model, and Opus 5.5 returns "
            "empty sections inside /insights' answer limits")


def _verdict(regenerated: bool, facets_after: int, empty_panels: int, missing_sections: list) -> str:
    if not regenerated:
        return FAILED  # the run never wrote a report
    if facets_after == 0:
        return HOLLOW  # a report with no analysed session behind it
    if empty_panels or missing_sections:
        return PARTIAL  # sessions analysed, but some of the report came back empty
    return OK


def judge(udir: Path, report_mtime_before: float, facets_before: int) -> dict:
    """Read what the run left on disk and classify it. The exit code plays no part.

    "Regenerated" compares the report's modification time with its own previous value,
    not with a wall-clock start time: file timestamps can come from a coarser clock than
    time.time() (Linux), and a just-written file could otherwise look older than the run."""
    report = udir / "report.html"
    page = _read_text(report)
    facets_after = facet_count(udir / "facets")
    empty_panels = page.count(EMPTY_PANEL_MARKER)
    found = set(_SECTION_RE.findall(page))
    missing = sorted(EXPECTED_SECTIONS - found)
    regenerated = _mtime(report) > report_mtime_before
    return {
        "status": _verdict(regenerated, facets_after, empty_panels, missing),
        "report": str(report),
        "facets_before": facets_before,
        "facets_after": facets_after,
        "empty_panels": empty_panels,
        "narrative_sections": len(found),
        "missing_sections": missing,
    }


def _tail(text: Optional[str], limit: int = 400) -> str:
    return (text or "").strip()[-limit:]


def _session_id(stdout: Optional[str]) -> Optional[str]:
    """The nested session's id from `--output-format json`; None when it can't be read."""
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return None
    return data.get("session_id") if isinstance(data, dict) else None


def _transcript_texts(path: Path) -> Iterable[str]:
    for line in _read_text(path).splitlines():
        try:
            content = json.loads(line).get("message", {}).get("content")
        except (ValueError, AttributeError):
            continue
        if isinstance(content, str):
            yield content


def local_command_error(projects_dir: Path, session_id: Optional[str]) -> str:
    """The error /insights recorded inside its own session, which never reaches stdout.

    Claude Code logs a crashing local command as a `<local-command-stderr>` entry in the
    session transcript and still exits 0, so the transcript is the only place it survives."""
    if not session_id:
        return ""
    transcripts = list(projects_dir.glob(f"*/{session_id}.jsonl"))
    if not transcripts:
        return ""
    found = _LOCAL_STDERR_RE.findall("\n".join(_transcript_texts(transcripts[0])))
    return _tail(html.unescape(found[-1])) if found else ""


def _attempt(udir: Path, projects_dir: Path, runner: Callable, model: str, timeout_s: int) -> dict:
    """One `claude -p "/insights"` run, judged from disk. Raises if the run can't start."""
    facets_before = facet_count(udir / "facets")
    report_mtime_before = _mtime(udir / "report.html")
    with contextlib.suppress(OSError):  # a log left by a crashed run is not this run's
        (udir / DEBUG_LOG_NAME).unlink()
    try:
        proc = runner(model, timeout_s)
    finally:
        # Read and delete the debug log even when the run raised (a timeout after the
        # log was created): it carries hook output and must not outlive the run.
        models = _answering_models(udir)
    result = judge(udir, report_mtime_before, facets_before)
    result.update(model=model, exit_code=proc.returncode)
    if models:
        result["answering_models"] = models
    diagnosis = _diagnosis(result["status"], model, models)
    if diagnosis:
        result["diagnosis"] = diagnosis
    if result["status"] != OK:
        result["error"] = (local_command_error(projects_dir, _session_id(proc.stdout))
                           or _tail(proc.stderr) or _tail(proc.stdout))
    return result


@contextlib.contextmanager
def _single_run_lock(udir: Path):
    """Yield True when this process holds the refresh lock, False when another one does.

    Two refreshes started together would each see a stale report and each spend a full
    /insights run; the second is told `busy` instead. Non-blocking, and released on exit,
    including when the holding process dies."""
    udir.mkdir(parents=True, exist_ok=True)
    with (udir / LOCK_NAME).open("a") as fh:
        try:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)


def _run_attempts(udir: Path, base: Optional[Path], runner: Callable, model: str,
                  timeout_s: int, attempts: int) -> dict:
    projects_dir = (CLAUDE_DIR if base is None else base) / "projects"
    facets_start = facet_count(udir / "facets")
    released = release_stuck_sessions(udir)
    result: dict = {}
    for attempt in range(1, max(1, attempts) + 1):
        try:
            result = _attempt(udir, projects_dir, runner, model, timeout_s)
        except (OSError, subprocess.SubprocessError) as exc:
            result = {"status": UNAVAILABLE, "model": model, "error": f"{type(exc).__name__}: {exc}"}
        result.update(released=released, attempts=attempt)
        if result["status"] != FAILED:
            break
    if "facets_before" in result:
        result["facets_before"] = facets_start  # the whole refresh, not just the last attempt
    return result


def refresh(
    force: bool = False,
    max_age_hours: Optional[float] = None,
    model: Optional[str] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    base: Optional[Path] = None,
    runner: Optional[Callable] = None,
    now: Optional[float] = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> dict:
    """Refresh Claude Code's /insights unless the report is younger than `max_age_hours`.

    A run that did not rewrite the report is retried, up to `attempts` runs in all: that is
    the renderer crash, which is intermittent. A hollow run is not retried, because the
    same model would fail the same way. Only one refresh runs at a time; a concurrent call
    returns `busy`. `runner` (DIP) defaults to the real `claude -p` call; tests inject a
    fake that writes the artefacts a real run would. Fails soft: a missing CLI or a timeout
    returns an `unavailable` result instead of raising."""
    udir = usage_dir(base)
    max_age = default_max_age_hours() if max_age_hours is None else max_age_hours
    model = model or default_model()
    age = report_age_hours(udir / "report.html", now)
    if not force and age is not None and age < max_age:
        return {"status": FRESH, "age_hours": round(age, 1), "max_age_hours": max_age,
                "report": str(udir / "report.html")}
    with _single_run_lock(udir) as acquired:
        if not acquired:
            return {"status": BUSY, "report": str(udir / "report.html")}
        runner = run_claude_insights if runner is None else runner
        return _run_attempts(udir, base, runner, model, timeout_s, attempts)


def describe(result: dict) -> str:
    """One plain line for a terminal or a log."""
    status = result.get("status")
    if status == FRESH:
        return (f"{FRESH}: report is {result.get('age_hours')} h old, under the "
                f"{result.get('max_age_hours')} h limit ({result.get('report')})")
    if status == BUSY:
        return (f"{BUSY}: another grip-insights refresh is already running; "
                f"the latest report is {result.get('report')}")
    if status == UNAVAILABLE:
        return f"{UNAVAILABLE}: {result.get('error')}"
    line = (f"{status}: facets {result.get('facets_before')} -> {result.get('facets_after')}, "
            f"{result.get('narrative_sections')} sections, {result.get('empty_panels')} empty panels, "
            f"{result.get('released')} stuck sessions released, model {result.get('model')}, "
            f"attempts {result.get('attempts')}")
    if result.get("missing_sections"):
        line += f" | missing sections: {', '.join(result['missing_sections'])}"
    if result.get("answering_models"):
        answered = ", ".join(f"{m} x{n}" for m, n in sorted(result["answering_models"].items()))
        line += f" | answered by: {answered}"
    if result.get("diagnosis"):
        line += f" | cause: {result['diagnosis']}"
    if result.get("error"):
        line += f" | {result['error']}"
    return line


# ------------------------------------------------------------------------- digest for RSI
def _friction_items(facets: Iterable[dict]) -> Iterable:
    """Every (friction type, count) pair across all facets, as one flat stream."""
    return chain.from_iterable(
        facet["friction_counts"].items()
        for facet in facets
        if isinstance(facet.get("friction_counts"), dict)
    )


def _friction_totals(facets: Iterable[dict]) -> Counter:
    totals: Counter = Counter()
    for kind, n in _friction_items(facets):
        if isinstance(n, (int, float)) and n > 0:
            totals[kind] += n
    return totals


def digest(facets_dir: Path, top: int = 3) -> Optional[dict]:
    """A coarse summary of the facets that stays stable while nothing meaningful changes.

    Bucketed the way lib/ultralearn.introspect buckets its other fields, so its
    content-addressed cache keeps hitting: the facet count in tens, and the most frequent
    friction types. None when there are no facets, an honest "unavailable"."""
    facets = [f for f in (_read_json(p) for p in facets_dir.glob("*.json")) if f]
    if not facets:
        return None
    totals = _friction_totals(facets)
    ranked = sorted(totals, key=lambda kind: (-totals[kind], kind))[:top]
    return {"facets_milestone": len(facets) // 10, "top_friction": ranked}


# --------------------------------------------------------------------------------- CLI
def status(base: Optional[Path] = None) -> dict:
    """What a refresh would find right now. Spends nothing."""
    udir = usage_dir(base)
    age = report_age_hours(udir / "report.html")
    return {
        "report": str(udir / "report.html"),
        "age_hours": None if age is None else round(age, 1),
        "facets": facet_count(udir / "facets"),
        "stuck_sessions": len(stuck_sessions(udir)),
        "model": default_model(),
        "max_age_hours": default_max_age_hours(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-insights",
        description="Run Claude Code's /insights headlessly and check that it produced something.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="refresh the report (skipped while it is fresh)")
    run_p.add_argument("--force", action="store_true", help="ignore the freshness gate")
    run_p.add_argument("--max-age-hours", type=float, default=None,
                       help="skip when the report is younger than this (default 24, env GRIP_INSIGHTS_MAX_AGE_HOURS)")
    run_p.add_argument("--model", default=None,
                       help="model for the analysis (default claude-sonnet-5, env GRIP_INSIGHTS_MODEL)")
    run_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds before giving up")
    run_p.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                       help="runs in all when the report is not rewritten (default 2)")
    run_p.add_argument("--json", action="store_true", help="print the result as JSON")
    status_p = sub.add_parser("status", help="report age, facet count and stuck sessions; spends nothing")
    status_p.add_argument("--json", action="store_true", help="print the result as JSON")
    sub.add_parser("digest", help="print the coarse facet digest /ultralearn uses")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "digest":
        print(json.dumps(digest(usage_dir() / "facets"), indent=2))
        return 0
    if args.cmd == "status":
        info = status()
        print(json.dumps(info, indent=2) if args.json else
              f"report {info['age_hours']} h old · {info['facets']} facets · "
              f"{info['stuck_sessions']} stuck sessions · model {info['model']}")
        return 0
    result = refresh(force=args.force, max_age_hours=args.max_age_hours,
                     model=args.model, timeout_s=args.timeout, attempts=args.attempts)
    print(json.dumps(result, indent=2) if args.json else f"[grip-insights] {describe(result)}")
    return EXIT_CODES.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
