#!/usr/bin/env python3
"""
grip-heartbeat.py -- GRIP's autonomous learning pulse.

The heartbeat that makes GRIP alive. Runs /evo + /learn pipeline
on a schedule without human invocation.

Cross-platform: macOS, Linux, Windows (Python 3.9+).

Gates (all must pass before execution):
  1. Config check: heartbeat.enabled in config/grip.json
  2. Cooldown: >= N seconds since last run (configurable)
  3. Freshness: some learning-pipeline input newer than last heartbeat
  4. No active session: no claude CLI process running

Usage:
  python3 scripts/grip-heartbeat.py           # Run if gates pass
  python3 scripts/grip-heartbeat.py --force    # Skip gates
  python3 scripts/grip-heartbeat.py --status   # Show gate status
  python3 scripts/grip-heartbeat.py --install  # Install scheduler
  python3 scripts/grip-heartbeat.py --pause    # Disable heartbeat
  python3 scripts/grip-heartbeat.py --resume   # Re-enable heartbeat
  python3 scripts/grip-heartbeat.py --health   # Is the pulse alive? (exit 1 if stale)
  python3 scripts/grip-heartbeat.py --cc-staleness-check  # Is the CC release corpus stale? (prints one line, exit 1 if so)
"""

# Keeps the "Python 3.9+" contract above honest. Without this, PEP 604 annotations
# (`X | None`, used below) are evaluated at def-time and raise TypeError on 3.9 --
# and the launchd agent runs /usr/bin/python3, which is 3.9.6 on macOS.
from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

def _claude_dir() -> Path:
    """Resolve CLAUDE_DIR, falling back to ~/.claude only if it is unset.

    The fallback must stay lazy. Written as
    os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")) the default is
    built eagerly, so Path.home() runs even when CLAUDE_DIR is set -- and
    Path.home() raises RuntimeError in any environment that cannot name a home
    directory. On Windows that means USERPROFILE and HOMEDRIVE/HOMEPATH, none of
    which a scheduled task or a sandboxed subprocess necessarily inherits, so the
    heartbeat died with a traceback despite having been told exactly where to look.
    """
    configured = os.environ.get("CLAUDE_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".claude"


CLAUDE_DIR = _claude_dir()
CONFIG_FILE = CLAUDE_DIR / "config" / "grip.json"
STAMP_FILE = CLAUDE_DIR / "state" / "rsi" / "grip-heartbeat-last"  # state_paths.RSI_HEARTBEAT_LAST
LOG_FILE = CLAUDE_DIR / "learning" / "heartbeat.jsonl"
DEFAULT_COOLDOWN = 7200  # 2 hours
INSIGHTS_FILE = CLAUDE_DIR / "learning" / "insights.jsonl"  # captured bash-error rows (learning_proposer input)

# Inputs the pipeline actually consumes. The freshness gate opens when ANY of them
# has changed since the last run -- NOT when facets alone have.
#
# facets/ and report.html are written by Claude Code's own /insights command, which
# a HUMAN runs by hand (docs/insights-guide.md: "Run it | /insights in any Claude
# Code session", "Frequency | Weekly recommended"). GRIP has no writer for either:
# verified 2026-08-10, every in-repo reference to FACETS_DIR is a read or a test
# mock. So keying the gate on facets alone made it self-silencing -- the first run
# stamped now, every facet became older than the stamp, and all seven pipeline
# steps stayed blocked forever, six of which never touch a facet (#4468).
FACETS_DIR = CLAUDE_DIR / "usage-data" / "facets"
REPORT_FILE = CLAUDE_DIR / "usage-data" / "report.html"

# How long a facets directory may sit untouched before the producer is reported as
# unfed rather than the pipeline being reported as idle. Two missed cycles of the
# "weekly recommended" cadence above -- long enough not to nag, short enough that a
# genuinely dead producer surfaces while the trail is warm.
FACET_PRODUCER_STALE_DAYS = 14

# Interpreter the pipeline's child steps run under. The launchd agent starts this
# script with the system python so that it can always run and always report; the
# CHILDREN need GRIP's dependencies and modern syntax, so they get the venv.
# See pipeline_python.
VENV_PYTHON = CLAUDE_DIR / "venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python3"
)

# Every SCHEDULED invocation appends exactly one dated row here -- gate-blocked,
# completed, or crashed alike. LOG_FILE only records runs whose gates PASSED, so it
# cannot answer "did the agent wake up at all?". This file can, which is the whole
# point: a dead agent and a healthy-but-idle one must not look identical (#4443).
PULSE_FILE = CLAUDE_DIR / "learning" / "heartbeat-pulse.jsonl"
# A pulse older than this many multiples of the cooldown means the agent is not waking.
STALE_PULSE_FACTOR = 2


def _ts() -> str:
    """ISO-8601 UTC stamp. Every line this script emits carries one."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _say(msg: str, err: bool = False) -> None:
    """Emit one TIMESTAMPED line.

    Undated output is why #4443 was misdiagnosed: 182 identical, undated tracebacks
    sat in heartbeat-stderr.log looking exactly like a live failure, when in fact the
    newest was 25 minutes OLDER than the commit that fixed it. A stale log must be
    visibly stale.
    """
    print(f"[heartbeat {_ts()}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def record_pulse(outcome: str, **fields: object) -> None:
    """Append one dated proof-of-wakeup row. Never raises -- telemetry must not kill the pulse."""
    try:
        PULSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": _ts(),
            "outcome": outcome,  # blocked | ran | error
            "pid": os.getpid(),
            "python": "%d.%d.%d" % sys.version_info[:3],
            **fields,
        }
        with open(PULSE_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        # Telemetry must not kill the pulse -- but a SILENT telemetry failure would
        # recreate this PR's own bug one layer down, leaving the operator unable to
        # tell "agent dead" from "pulse log unwritable". So warn, best-effort, and
        # still return normally.
        try:
            _say(f"pulse write FAILED ({PULSE_FILE}): {type(exc).__name__}: {exc}", err=True)
        except Exception:  # noqa: BLE001 -- stderr itself is gone; nothing left to do
            pass


def _last_pulse() -> dict | None:
    """Newest parseable pulse row, or None if the agent has never recorded a wakeup."""
    try:
        lines = [ln for ln in PULSE_FILE.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def pulse_health() -> tuple[str, float | None, dict | None]:
    """Liveness verdict: (status, age_seconds, last_row), status in OK/STALE/NEVER.

    This is the missing assertion from #4443 -- "has the agent produced output within
    its own interval" -- made directly queryable instead of inferred from mtimes.
    """
    row = _last_pulse()
    if row is None:
        return "NEVER", None, None
    try:
        last = datetime.fromisoformat(str(row.get("ts", "")))
    except (ValueError, TypeError):
        return "NEVER", None, row
    # A NAIVE stamp -- hand-edited row, a truncated line, an older or third-party
    # writer -- would make the subtraction below raise TypeError and kill the very
    # check that exists to report trouble. We only ever write UTC, so read a naive
    # stamp AS UTC rather than discarding a usable liveness signal.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return ("OK" if age <= _stale_after_seconds() else "STALE"), age, row


def _stale_after_seconds() -> float:
    """How old a pulse may get before it counts as STALE.

    One definition, because two copies of it can drift: the verdict and the
    number printed beside it would then disagree, and a health report that
    contradicts itself is worse than no health report.
    """
    return _load_config().get("cooldown_seconds", DEFAULT_COOLDOWN) * STALE_PULSE_FACTOR


def show_health() -> int:
    """Print the liveness verdict. Returns a shell exit code (0 healthy, 1 not)."""
    status, age, row = pulse_health()
    limit = _stale_after_seconds()
    print("\n--- GRIP Heartbeat Liveness ---\n")
    print(f"  Status:    {status}")
    print(f"  Pulse log: {PULSE_FILE}")
    if row is None or age is None:
        print("  Last woke: never -- no pulse recorded\n")
        return 1
    print(f"  Last woke: {row.get('ts')}  ({age/3600:.1f}h ago, stale after {limit/3600:.0f}h)")
    print(f"  Outcome:   {row.get('outcome')}  (python {row.get('python')})\n")
    return 0 if status == "OK" else 1


def _newest_release(cc_dir: Path) -> tuple[date | None, str | None]:
    """The newest release file (live or archive/) by its "date" field, as (date, version).

    Only v*.json are releases: the directory also holds non-release records
    (cma-*.json, model-*.json) with their own "date" fields, and a fresh one of
    those must not mask a stale release corpus. Unreadable or undated files are
    skipped.
    """
    newest_date, newest_ver = None, None
    for f in sorted(cc_dir.glob("v*.json")) + sorted(cc_dir.glob("archive/v*.json")):
        try:
            data = json.loads(f.read_text())
            dt = date.fromisoformat(data.get("date", ""))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError, OSError):
            continue
        if newest_date is None or dt > newest_date:
            newest_date, newest_ver = dt, data.get("version", f.stem)
    return newest_date, newest_ver


def _last_refresh_error() -> str:
    """The error of the newest pulse row that recorded a refresh, if that refresh failed.

    Blocked and pipeline wakes both record ``cc_refresh``. Rows written before
    pipeline wakes recorded it carry none, so rows without the field are skipped
    rather than treated as a clean bill of health.
    """
    try:
        lines = [ln for ln in PULSE_FILE.read_text().splitlines() if ln.strip()]
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "cc_refresh" in row:
            return str(row.get("cc_refresh_error") or "unknown error") if row["cc_refresh"] == "error" else ""
    return ""


def cc_releases_staleness_check() -> str:
    """One line saying why the CC release corpus is stale and how to fix it; "" when fresh.

    Three conditions fire it: no dated release file under data/cc-releases/
    (live or archive/), so the corpus is missing, empty or unreadable; a newest
    release dated more than 7 days ago; or a failed last recorded refresh.
    """
    cc_dir = CLAUDE_DIR / "data" / "cc-releases"
    fix = f"cd {CLAUDE_DIR} && venv/bin/python3 lib/cc_auto_harness.py refresh"
    newest_date, newest_ver = _newest_release(cc_dir)
    if newest_date is None:
        return f"no dated release file under {cc_dir} — run: {fix}"
    if newest_date < date.today() - timedelta(days=7):
        age_days = (date.today() - newest_date).days
        return (f"newest release {newest_ver} dated {newest_date} "
                f"({age_days}d old, >7d limit) — run: {fix}")
    error = _last_refresh_error()
    if error:
        return f"last heartbeat refresh failed ({error}) — run: {fix}"
    return ""


def _load_config() -> dict:
    """Load heartbeat config from config/grip.json."""
    if not CONFIG_FILE.exists():
        return {"enabled": True, "cooldown_seconds": DEFAULT_COOLDOWN}
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg.get("heartbeat", {"enabled": True, "cooldown_seconds": DEFAULT_COOLDOWN})
    except (json.JSONDecodeError, OSError):
        return {"enabled": True, "cooldown_seconds": DEFAULT_COOLDOWN}


def _save_heartbeat_config(enabled: bool) -> None:
    """Update heartbeat.enabled in config/grip.json."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            cfg = {}
    else:
        cfg = {}
    cfg.setdefault("heartbeat", {})["enabled"] = enabled
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def _is_paused() -> bool:
    return not _load_config().get("enabled", True)


def _cooldown_ok() -> tuple[bool, float]:
    """Check if cooldown has elapsed. Returns (ok, seconds_remaining)."""
    cooldown = _load_config().get("cooldown_seconds", DEFAULT_COOLDOWN)
    if not STAMP_FILE.exists():
        return True, 0.0
    try:
        last = float(STAMP_FILE.read_text().strip())
    except (ValueError, OSError):
        return True, 0.0
    elapsed = time.time() - last
    return elapsed >= cooldown, max(0, cooldown - elapsed)


def _stamp_time() -> float | None:
    """Epoch of the last completed heartbeat, or None if never run / unreadable.

    One reader for the stamp, shared by every freshness check below, so they cannot
    drift apart on what "unreadable" means (they each open-coded this before).
    """
    if not STAMP_FILE.exists():
        return None
    try:
        return float(STAMP_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _newest_facet_mtime() -> float | None:
    """Newest facet mtime, or None when no facet has ever been written."""
    mtimes = [f.stat().st_mtime for f in FACETS_DIR.glob("*.json")] if FACETS_DIR.exists() else []
    return max(mtimes) if mtimes else None


def facet_producer_health() -> tuple[str, float | None]:
    """(status, age_in_days) for the EXTERNAL facet producer. OK | STALE | NEVER.

    The distinction this exists to make: an empty facet delta means either "nothing
    happened since the last run" or "nobody has run /insights in weeks". Those are
    different problems with different fixes, and before #4468 they were reported
    identically -- the silent-failure shape `rules/tests-must-be-able-to-fail.md`
    is about, and the same reason `grip_doctor.check_telemetry_fields` returns RED
    rather than YELLOW for a missing log.
    """
    newest = _newest_facet_mtime()
    if newest is None:
        return "NEVER", None
    age_days = (time.time() - newest) / 86400.0
    return ("STALE" if age_days > FACET_PRODUCER_STALE_DAYS else "OK"), age_days


def _facets_stale() -> bool:
    """Check if facets are newer than last heartbeat."""
    if not FACETS_DIR.exists():
        return False
    last = _stamp_time()
    if last is None:
        return True  # Never run before
    newest = _newest_facet_mtime()
    return newest is not None and newest > last


def _inputs_stale() -> bool:
    """True when ANY learning-pipeline input has changed since the last heartbeat.

    Supersedes the facets-only check as the gate's predicate. Facets still count --
    a human running /insights is genuinely new data -- but they are no longer the
    ONLY thing that counts, which is what made the gate self-silencing (#4468).
    `learning/insights.jsonl` moves on real session activity and `report.html`
    moves independently of the facet write (measured blackbook 2026-08-10:
    insights.jsonl 08-10, report.html 07-31, newest facet 07-17).
    """
    if _facets_stale():
        return True
    last = _stamp_time()
    if last is None:
        return True
    return any(p.exists() and p.stat().st_mtime > last
               for p in (INSIGHTS_FILE, REPORT_FILE))


def _stale_reason() -> str:
    """Why the freshness gate is closed -- naming the CAUSE, not just the symptom."""
    status, age_days = facet_producer_health()
    if status == "OK":
        return "STALE: no new learning input since last heartbeat"
    seen = "never written" if age_days is None else f"last written {age_days:.0f}d ago"
    return (f"STALE: no new learning input; the facet producer looks unfed "
            f"({seen}) -- run /insights in a Claude Code session to feed it")


def _session_active() -> bool:
    """Report whether a real Claude Code CLI session is running.

    Matches the process NAME exactly (``pgrep -x``) rather than searching the
    whole command line (``pgrep -f``). The command-line form matched this very
    script: launchd invokes it as ``/usr/bin/python3 $CLAUDE_DIR/scripts/
    grip-heartbeat.py``, and the default CLAUDE_DIR is ``~/.claude``, so the
    substring "claude" was always present in the heartbeat's own argv. The gate
    therefore reported a session on every run and the learning cycle could
    never execute — on any machine using the default install path.

    The same looseness matched every GRIP hook (all live under ``.claude/``)
    and any process whose exported PATH merely contained ``.claude/bin``, so
    the gate stayed closed even with no session running at all.

    Measured 2026-08-10 on blackbook: ``pgrep -f claude`` matched a bare
    heartbeat-shaped process and 8 unrelated ones; ``pgrep -x claude`` matched
    the 3 genuine sessions and nothing else.

    Failure direction is deliberate: if ``-x`` ever misses a session the
    heartbeat merely runs alongside it (CPU contention), whereas the old
    behaviour silenced the pulse entirely.

    Windows has no ``pgrep``. The ``tasklist`` check that stood in for it also
    matched the Claude desktop app's ``claude.exe`` processes, so the learning
    run was skipped whenever the app was open. There psutil now counts console
    ``claude.exe`` processes only (``lib/windows_processes.py``, GRIP#8835).
    """
    if sys.platform == "win32":
        return _windows_session_active()
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False  # pgrep missing or too slow: assume no session


def _windows_session_active() -> bool:
    """True while a console ``claude.exe`` runs; False when that cannot be read.

    Loads ``lib/windows_processes.py`` from the install the way
    ``_load_consumer`` loads its module, so ``sys.path`` is left as it was.
    """
    path = CLAUDE_DIR / "lib" / "windows_processes.py"
    spec = importlib.util.spec_from_file_location("grip_hb_windows_processes", path)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError):
        return False
    return bool(module.console_pids(("claude",)))


def check_gates(force: bool = False) -> tuple[bool, list[str]]:
    """Check all gates. Returns (all_passed, reasons)."""
    if force:
        return True, ["--force: all gates bypassed"]

    reasons = []

    if _is_paused():
        reasons.append(f"PAUSED: heartbeat.enabled=false in {CONFIG_FILE.name} (use --resume)")
        return False, reasons

    cd_ok, remaining = _cooldown_ok()
    if not cd_ok:
        reasons.append(f"COOLDOWN: {remaining/60:.0f}min remaining")
        return False, reasons

    if not _inputs_stale():
        reasons.append(_stale_reason())
        return False, reasons

    if _session_active():
        reasons.append("SESSION: active Claude session detected")
        return False, reasons

    return True, ["all gates passed"]


def _run_evo(lib_dir: Path, python: str) -> list[dict]:
    """Run evo.py json step. Returns list of step results."""
    evo_path = lib_dir / "evo.py"
    if not evo_path.exists():
        return [{"name": "evo", "ok": False, "error": "evo.py not found"}]
    try:
        r = subprocess.run(
            [python, str(evo_path), "json"],
            capture_output=True, text=True, timeout=120,
            cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0:
            evo_data = json.loads(r.stdout)
            return [{"name": "evo", "ok": True, "data": evo_data}]
        return [{"name": "evo", "ok": False, "error": r.stderr[:200]}]
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return [{"name": "evo", "ok": False, "error": str(e)[:200]}]


def _run_learning(lib_dir: Path, python: str) -> list[dict]:
    """Run learning_detector consolidate + generate-rules. Returns list of step results."""
    detector = lib_dir / "learning_detector.py"
    if not detector.exists():
        return []
    steps = []
    for subcmd in ["consolidate", "generate-rules"]:
        try:
            r = subprocess.run(
                [python, str(detector), subcmd],
                capture_output=True, text=True, timeout=60,
                cwd=str(CLAUDE_DIR),
            )
            steps.append({
                "name": f"learn:{subcmd}", "ok": r.returncode == 0,
                "output": r.stdout[:200] if r.returncode == 0 else r.stderr[:200],
            })
        except subprocess.TimeoutExpired as e:
            steps.append({"name": f"learn:{subcmd}", "ok": False, "error": str(e)[:200]})
    return steps


def _run_insights(lib_dir: Path, python: str, report: Path) -> list[dict]:
    """Run insights_learner full pipeline (version + extract + learn + converge-check)."""
    learner = lib_dir / "insights_learner.py"
    if not learner.exists():
        return []
    steps = []
    # Run full pipeline if report exists, otherwise just converge-check
    subcmd = "run" if report.exists() else "converge-check"
    try:
        r = subprocess.run(
            [python, str(learner), subcmd],
            capture_output=True, text=True, timeout=120,
            cwd=str(CLAUDE_DIR),
        )
        steps.append({
            "name": f"insights:{subcmd}", "ok": r.returncode == 0,
            "output": r.stdout[:300],
        })
    except subprocess.TimeoutExpired as e:
        steps.append({"name": f"insights:{subcmd}", "ok": False, "error": str(e)[:200]})
    # Schedule check: suggest when to re-run /insights
    try:
        r = subprocess.run(
            [python, str(learner), "schedule"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0 and "now" in r.stdout.lower():
            steps.append({
                "name": "insights:schedule", "ok": True,
                "output": r.stdout.strip()[:200],
                "action": "suggest_insights_rerun",
            })
    except (subprocess.TimeoutExpired, OSError):
        pass
    return steps


def _run_harness_refresh(lib_dir: Path, python: str, module: str, step_name: str) -> list[dict]:
    """Run one keep-up loop's ``refresh`` -- the Claude Code and REA auto-harnesses
    share this shape: absent module means no step, a timeout is a failed step,
    and a non-zero exit carries its stderr."""
    harness = lib_dir / module
    if not harness.exists():
        return []
    try:
        r = subprocess.run(
            [python, str(harness), "refresh"],
            capture_output=True, text=True, timeout=180,
            cwd=str(CLAUDE_DIR),
        )
    except subprocess.TimeoutExpired:
        return [{"name": step_name, "ok": False, "error": "timeout"}]
    step = {"name": step_name, "ok": r.returncode == 0, "output": r.stdout[:300]}
    stderr = (r.stderr or "").strip()[-300:]
    if r.returncode != 0:
        # The cause lives on stderr. Keeping only stdout is how the 2026-09-28
        # run, which could not even find `gh`, was logged as a success.
        step["error"] = stderr or f"exit {r.returncode}"
    elif stderr:
        # A refresh that exits 0 can still warn (a feed that answered late, a
        # release it skipped). The warning stays on the row under the same cap
        # as a failure's cause, instead of vanishing with the success (GRIP#7999).
        step["stderr"] = stderr
    return [step]


def _run_cc_harness(lib_dir: Path, python: str) -> list[dict]:
    """Run CC auto-harness refresh to scan for new Claude Code features."""
    return _run_harness_refresh(lib_dir, python, "cc_auto_harness.py", "cc:refresh")


def _run_rea_harness(lib_dir: Path, python: str) -> list[dict]:
    """Run the REA keep-up refresh (lib/rea_auto_harness.py). REA ships every few
    hours, and a release the pin has not absorbed means tools the policy gate
    denies as unknown, so the loop runs on the heartbeat's cadence instead of
    waiting for a hand-run (V>> 2026-10-10 and 2026-10-11: a hypothesis
    deadline is not a schedule for the loop that produces its data)."""
    return _run_harness_refresh(lib_dir, python, "rea_auto_harness.py", "rea:refresh")


def _run_grip_harness(python: str) -> list[dict]:
    """Sync GRIP innovations to HAL standalone runtime.

    Takes the interpreter as an argument like every sibling step. It used to hard-code
    ``sys.executable``, which under the launchd agent is the system python — the one
    HAL's own modern-syntax modules cannot be imported by (see ``pipeline_python``).
    """
    hal_home = Path.home() / ".hal"
    if not hal_home.exists():
        return []
    grip_harness = hal_home / "lib" / "hal" / "grip_harness.py"
    if not grip_harness.exists():
        return []
    try:
        r = subprocess.run(
            [python, "-c",
             "import sys; sys.path.insert(0, str(sys.argv[1])); "
             "from lib.hal.grip_harness import GRIPHarness; "
             "h = GRIPHarness(); report = h.scan(); "
             "actions = h.absorb(report); "
             "print(f'{len(actions)} absorbed, {len(report.cc_dependent)} CC-dependent')",
             str(hal_home)],
            capture_output=True, text=True, timeout=30,
            cwd=str(hal_home),
            env={**os.environ, "PYTHONPATH": str(hal_home)},
        )
        return [{"name": "grip-harness:sync", "ok": r.returncode == 0,
                 "output": r.stdout.strip()[:300]}]
    except subprocess.TimeoutExpired:
        return [{"name": "grip-harness:sync", "ok": False, "error": "timeout"}]


def _proposer_stale() -> bool:
    """True if captured bash-error insights have landed since the last heartbeat.

    Cheap mtime gate (mirrors ``_facets_stale``) so the proposer stage is skipped
    when nothing new was captured. The precise per-category recurrence threshold
    (``DEFAULT_THRESHOLD``) lives INSIDE ``lib.learning_proposer.propose`` — not
    duplicated here (DRY). The proposer is idempotent, so a needless run is a
    harmless no-op.
    """
    if not INSIGHTS_FILE.exists():
        return False
    last = _stamp_time()
    if last is None:
        return True  # never run before
    return INSIGHTS_FILE.stat().st_mtime > last


def _run_learning_proposer(lib_dir: Path, python: str) -> list[dict]:
    """Route recurring captured shell mistakes to reviewable fix PROPOSALS.

    Invokes ``lib.learning_proposer route`` — which groups bash-execution-error
    insights and, for any category at/over threshold, stages ONE proposal + a
    GitHub-issue body + a signed IDR. It **NEVER self-applies**
    (``rules/exogenous-anchor-law.md``): a proposal is data routed TO the
    human/AGORA anchor, never past it. Fires only when new insights have landed
    (``_proposer_stale``); the proposer itself is idempotent (propose∘propose=propose).
    """
    proposer = lib_dir / "learning_proposer.py"
    if not proposer.exists():
        return []
    if not _proposer_stale():
        return [{"name": "learning-proposer:skip", "ok": True,
                 "output": "no new captured insights since last heartbeat"}]
    try:
        r = subprocess.run(
            [python, "-m", "lib.learning_proposer", "route"],
            capture_output=True, text=True, timeout=120,
            cwd=str(CLAUDE_DIR),
            env={**os.environ, "PYTHONPATH": str(CLAUDE_DIR)},
        )
        return [{"name": "learning-proposer:route", "ok": r.returncode == 0,
                 "output": (r.stdout.strip() or r.stderr.strip())[:300]}]
    except subprocess.TimeoutExpired as e:
        return [{"name": "learning-proposer:route", "ok": False, "error": str(e)[:200]}]


def _load_consumer(lib_dir: Path) -> object | None:
    """Load the holistic_backlog_consumer module. Returns the module or None."""
    path = lib_dir / "holistic_backlog_consumer.py"
    if not path.exists():
        return None
    prev_path = list(sys.path)
    try:
        lib_str = str(lib_dir)
        if lib_str not in sys.path:
            sys.path.insert(0, lib_str)
        spec = importlib.util.spec_from_file_location("grip_hb_consumer", path)
        if spec is None or spec.loader is None:
            return None
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        return m
    except Exception as exc:
        _say(f"backlog-consumer import failed: {exc}", err=True)
        return None
    finally:
        sys.path[:] = prev_path


def _run_backlog_consumer(lib_dir: Path) -> list[dict]:
    """Wire the critical-reasoning-gated backlog consumer into the heartbeat pulse.

    Uses ``holistic_backlog_consumer.drain()``: read top-k backlog, apply the
    reasoning gate (ACT/DEFER/SKIP), persist seen-set for cross-pulse idempotency.
    ACT is a proposal — never self-execution (exogenous-anchor law).
    """
    consumer = _load_consumer(lib_dir)
    if consumer is None:
        return [{"name": "backlog-consumer:unavailable", "ok": False,
                 "error": "holistic_backlog_consumer not loadable"}]
    try:
        proposals = consumer.drain(k=3)
    except Exception as exc:
        return [{"name": "backlog-consumer:drain-error", "ok": False, "error": str(exc)[:200]}]
    if not proposals:
        return [{"name": "backlog-consumer:idle", "ok": True,
                 "output": "no actionable backlog items found"}]
    rows = []
    for p in proposals:
        rows.append({
            "name": "backlog-consumer:propose",
            "ok": True,
            "verdict": p.verdict,
            "item": {"id": p.item_id, "title": p.title, "source": p.source,
                     "verdict": p.verdict, "reason": p.reason, "score": round(p.score, 2)},
        })
    acts = sum(1 for p in proposals if p.verdict == "ACT")
    defers = sum(1 for p in proposals if p.verdict == "DEFER")
    skips = sum(1 for p in proposals if p.verdict == "SKIP")
    rows.insert(0, {
        "name": "backlog-consumer:drained",
        "ok": True,
        "output": f"{len(proposals)} proposals: {acts} ACT, {defers} DEFER, {skips} SKIP",
    })
    return rows


def _venv_python_usable() -> bool:
    """True when the GRIP venv interpreter exists AND actually starts.

    Existence alone is not enough. A venv records an absolute path to the
    interpreter that built it — on the reporting machine,
    ``/opt/homebrew/Cellar/python@3.14/3.14.6/...`` — so a routine Homebrew
    upgrade leaves ``venv/bin/python3`` present as a symlink to a binary that is
    gone. Launching it then fails per child step instead of once, loudly, here.
    """
    if not VENV_PYTHON.exists():
        return False
    try:
        probe = subprocess.run(
            [str(VENV_PYTHON), "-c", "import sys; sys.exit(0)"],
            capture_output=True, timeout=15,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def venv_rebuild_hint() -> str:
    """The command that rebuilds the venv, on the OS actually running.

    DERIVED from ``VENV_PYTHON`` rather than written out, so it cannot drift
    from the interpreter path the same message has just reported. Written by
    hand it read ``./venv/bin/pip`` everywhere -- advice that is wrong on
    Windows, in the very message that had already printed the correct Windows
    interpreter path two lines above. A reader who follows it there gets a
    path that does not exist, from a tool telling them how to repair things.
    """
    windows = sys.platform == "win32"
    pip = VENV_PYTHON.parent / ("pip.exe" if windows else "pip")
    return (f"{'python' if windows else 'python3'} -m venv venv "
            f"&& {pip} install -r requirements.txt")


def pipeline_python() -> tuple[str, dict | None]:
    """Interpreter for the pipeline's child steps, plus a step row when it is not the venv.

    The launchd agent deliberately starts THIS script with the system python
    (macOS 3.9.6) — see the module docstring — so ``sys.executable`` is 3.9.6 for
    every child too. Measured 2026-08-10 under that interpreter:

        lib/insights_learner.py  TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
        lib/cc_auto_harness.py   TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'

    Both import cleanly under the venv (3.14.6). So the children run under the
    venv when it works, and fall back to ``sys.executable`` with a FAILED step
    row when it does not — never a silent degrade, which is the class of bug
    #4443 is about.
    """
    if _venv_python_usable():
        return str(VENV_PYTHON), None
    return sys.executable, {
        "name": "interpreter:venv-unusable",
        "ok": False,
        "error": (
            f"GRIP venv interpreter unusable at {VENV_PYTHON}; pipeline steps fell back to "
            f"{sys.executable} (Python {'.'.join(str(v) for v in sys.version_info[:3])}). "
            "Steps needing modern syntax or GRIP dependencies will fail. "
            f"Rebuild with: {venv_rebuild_hint()}"
        ),
    }


def run_pipeline() -> dict:
    """Execute the learning pipeline and surface one backlog proposal."""
    results = {"timestamp": datetime.now(timezone.utc).isoformat(), "steps": []}
    lib_dir = CLAUDE_DIR / "lib"
    python, interpreter_warning = pipeline_python()
    report = REPORT_FILE

    if interpreter_warning is not None:
        results["steps"].append(interpreter_warning)

    results["steps"].extend(_run_evo(lib_dir, python))
    results["steps"].extend(_run_learning(lib_dir, python))
    results["steps"].extend(_run_insights(lib_dir, python, report))
    results["steps"].extend(_run_cc_harness(lib_dir, python))
    results["steps"].extend(_run_rea_harness(lib_dir, python))
    results["steps"].extend(_run_grip_harness(python))
    results["steps"].extend(_run_learning_proposer(lib_dir, python))
    results["steps"].extend(_run_backlog_consumer(lib_dir))

    results["steps_ok"] = sum(1 for s in results["steps"] if s.get("ok"))
    results["steps_total"] = len(results["steps"])
    return results


def log_result(result: dict) -> None:
    """Append result to heartbeat log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(result) + "\n")


def update_stamp() -> None:
    """Write current timestamp to stamp file."""
    STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(str(time.time()))


def _read_heartbeat_log() -> list[dict]:
    """Read heartbeat log, returning all parseable entries (one per line)."""
    if not LOG_FILE.exists():
        return []
    entries: list[dict] = []
    try:
        for line in LOG_FILE.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries


def _is_valid_heartbeat(entry: dict) -> bool:
    """True if entry conforms to the run_pipeline schema (timestamp + steps_ok/total)."""
    return (
        isinstance(entry.get("timestamp"), str)
        and isinstance(entry.get("steps_total"), int)
        and isinstance(entry.get("steps_ok"), int)
    )


def show_trend(n: int = 10) -> None:
    """Display the last N heartbeat entries as a tabular trend.

    Schema-mismatched legacy records (e.g. wave-mode `{ts, wave, pr}` lines)
    are skipped cleanly; the count of skipped records is reported at the end.
    """
    entries = _read_heartbeat_log()
    valid = [e for e in entries if _is_valid_heartbeat(e)]
    skipped = len(entries) - len(valid)

    if not valid:
        print(f"[heartbeat] no valid entries in {LOG_FILE.name}")
        if skipped:
            print(f"[heartbeat] skipped {skipped} malformed/legacy records")
        return

    recent = valid[-n:]
    print(f"\n--- GRIP Heartbeat Trend (last {len(recent)} of {len(valid)}) ---\n")
    print(f"  {'Timestamp':<22} {'Steps':>8}   Failed")
    print(f"  {'-' * 22} {'-' * 8}   {'-' * 30}")
    for entry in recent:
        ts = entry["timestamp"][:19].replace("T", " ")
        ratio = f"{entry['steps_ok']}/{entry['steps_total']}"
        failed_steps = [s.get("name", "?") for s in entry.get("steps", []) if not s.get("ok")]
        failed_str = ", ".join(failed_steps)[:60] if failed_steps else "—"
        print(f"  {ts:<22} {ratio:>8}   {failed_str}")
    if skipped:
        plural = "s" if skipped != 1 else ""
        print(f"\n  ({skipped} legacy/malformed record{plural} skipped)")
    print()


def show_status() -> None:
    """Display gate status."""
    print("\n--- GRIP Heartbeat Status ---\n")
    hb_cfg = _load_config()
    print(f"  Enabled:   {'no (paused)' if not hb_cfg.get('enabled', True) else 'yes'}")
    print(f"  Config:    {CONFIG_FILE}")
    cd_ok, remaining = _cooldown_ok()
    print(f"  Cooldown:  {'ready' if cd_ok else f'{remaining/60:.0f}min remaining'}")
    print(f"  Inputs:    {'new data available' if _inputs_stale() else 'up to date'}")
    fp_status, fp_age = facet_producer_health()
    fp_detail = "no facet ever written" if fp_age is None else f"newest {fp_age:.1f}d old"
    print(f"  Facets:    {fp_status} ({fp_detail}) -- written by /insights, not by GRIP")
    print(f"  Session:   {'ACTIVE (will skip)' if _session_active() else 'none'}")

    if STAMP_FILE.exists():
        try:
            last = float(STAMP_FILE.read_text().strip())
            last_dt = datetime.fromtimestamp(last, tz=timezone.utc)
            print(f"  Last run:  {last_dt.strftime('%Y-%m-%d %H:%M UTC')}")
        except (ValueError, OSError):
            print("  Last run:  unknown")
    else:
        print("  Last run:  never")

    if LOG_FILE.exists():
        lines = [l for l in LOG_FILE.read_text(errors="replace").strip().split("\n") if l.strip()]
        print(f"  Log:       {len(lines)} entries in {LOG_FILE.name}")

    passed, reasons = check_gates()
    print(f"\n  Gates:     {'PASS' if passed else 'BLOCKED'}")
    for r in reasons:
        print(f"    {r}")
    print()


def install_scheduler() -> None:
    """Install platform-native scheduler."""
    if sys.platform == "darwin":
        _install_launchd()
    elif sys.platform == "linux":
        _install_systemd()
    elif sys.platform == "win32":
        _install_windows()
    else:
        print(f"Unsupported platform: {sys.platform}")
        sys.exit(1)


def _install_launchd() -> None:
    """Install macOS launchd agent."""
    plist_src = CLAUDE_DIR / "config" / "heartbeat" / "com.grip.heartbeat.plist"
    plist_dst = Path.home() / "Library" / "LaunchAgents" / "com.grip.heartbeat.plist"
    if not plist_src.exists():
        print(f"Error: {plist_src} not found")
        sys.exit(1)
    plist_dst.parent.mkdir(parents=True, exist_ok=True)
    # Read, substitute $HOME, write
    content = plist_src.read_text().replace("$HOME", str(Path.home()))
    plist_dst.write_text(content)
    subprocess.run(["launchctl", "unload", str(plist_dst)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist_dst)], check=True)
    print(f"Installed: {plist_dst}")
    print("Heartbeat will run every 2 hours. Use `launchctl list | grep grip` to verify.")


def _install_systemd() -> None:
    """Install Linux systemd timer."""
    user_dir = Path.home() / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("grip-heartbeat.service", "grip-heartbeat.timer"):
        src = CLAUDE_DIR / "config" / "heartbeat" / fname
        if not src.exists():
            print(f"Error: {src} not found")
            sys.exit(1)
        content = src.read_text().replace("$HOME", str(Path.home()))
        (user_dir / fname).write_text(content)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "grip-heartbeat.timer"], check=True)
    print(f"Installed: {user_dir}/grip-heartbeat.timer")
    print("Heartbeat will run every 2 hours. Use `systemctl --user status grip-heartbeat.timer` to verify.")


def _install_windows() -> None:
    """Install Windows Task Scheduler task."""
    ps_src = CLAUDE_DIR / "config" / "heartbeat" / "Install-GripHeartbeat.ps1"
    if not ps_src.exists():
        print(f"Error: {ps_src} not found")
        sys.exit(1)
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_src)], check=True)
    print("Installed via Task Scheduler. Use `schtasks /query /tn GripHeartbeat` to verify.")


def _parse_trend_n(args: list[str]) -> int:
    """Extract the optional N value following --trend (default 10)."""
    idx = args.index("--trend")
    if idx + 1 < len(args) and args[idx + 1].lstrip("-").isdigit():
        return max(1, int(args[idx + 1]))
    return 10


def _cmd_pause() -> None:
    # _say, not print: its contract is "every line this script emits carries a
    # timestamp", and an operator command's output lands in the same log as the
    # scheduled pulse's. #4443 was misdiagnosed precisely because undated lines
    # there were indistinguishable from live ones -- pausing the heartbeat must
    # not be the one action that reintroduces that.
    _save_heartbeat_config(enabled=False)
    _say(f"Paused. Set heartbeat.enabled=false in {CONFIG_FILE}")
    _say("Resume with: python3 scripts/grip-heartbeat.py --resume")


def _cmd_resume() -> None:
    _save_heartbeat_config(enabled=True)
    _say(f"Resumed. Set heartbeat.enabled=true in {CONFIG_FILE}")


# Each keep-up refresh step and the pulse-row field that carries its outcome.
_REFRESH_STEPS = {"cc:refresh": "cc_refresh", "rea:refresh": "rea_refresh"}


def _refresh_fields(steps: list[dict]) -> dict[str, object]:
    """Every keep-up refresh step's outcome as pulse-row fields (``cc_refresh``,
    ``rea_refresh``, plus ``<name>_error`` on failure and ``<name>_stderr`` on
    a success that warned); nothing for a refresh that did not run."""
    fields: dict[str, object] = {}
    for step in steps:
        key = _REFRESH_STEPS.get(step.get("name"))
        if key is None:
            continue
        fields[key] = "ok" if step.get("ok") else "error"
        if not step.get("ok") and step.get("error"):
            fields[f"{key}_error"] = step["error"]
        elif step.get("ok") and step.get("stderr"):
            fields[f"{key}_stderr"] = step["stderr"]
    return fields


def _blocked_wake(reasons: list[str]) -> None:
    """Record a blocked wake. The release refreshes still run, unless the heartbeat is paused.

    The release lists must stay fresh whatever the session and staleness gates
    say (measured 2026-10-10: with the refresh inside the gated pipeline, 114
    wakes in 12 days ran it 0 times). Both keep-up loops run here, Claude
    Code's and REA's -- the REA step first sat inside the gated pipeline only,
    which would have repeated that failure (Codex review of GRIP#7997). A pause
    means no background work at all, so a PAUSED wake skips both. Each result
    goes into the pulse row, so a blocked wake still shows whether a refresh
    worked.
    """
    for r in reasons:
        _say(r)
    fields: dict[str, object] = {"reasons": reasons}
    if not any(r.startswith("PAUSED:") for r in reasons):
        python, _ = pipeline_python()
        lib_dir = CLAUDE_DIR / "lib"
        fields.update(_refresh_fields(_run_cc_harness(lib_dir, python) + _run_rea_harness(lib_dir, python)))
    record_pulse("blocked", **fields)


def run_scheduled(force: bool = False) -> None:
    """The scheduled pulse. EVERY exit path records a dated pulse row."""
    passed, reasons = check_gates(force)
    if not passed:
        _blocked_wake(reasons)
        return

    _say("Gates passed. Running learning pipeline...")
    result = run_pipeline()
    log_result(result)
    update_stamp()

    ok, total = result["steps_ok"], result["steps_total"]
    _say(f"Complete: {ok}/{total} steps succeeded.")
    failed = [s["name"] for s in result["steps"] if not s.get("ok")]
    if failed:
        _say(f"Failed: {', '.join(failed)}")
    record_pulse("ran", steps_ok=ok, steps_total=total, failed=failed,
                 **_refresh_fields(result["steps"]))


def main() -> int:
    """Dispatch. Operator flags are one-shot and deliberately record NO pulse -- only
    the scheduled path does, so a manual --status can never fake liveness."""
    args = sys.argv[1:]

    if "--health" in args:
        return show_health()
    if "--trend" in args:
        show_trend(_parse_trend_n(args))
        return 0
    if "--cc-staleness-check" in args:
        msg = cc_releases_staleness_check()
        if msg:
            print(msg)
            return 1
        return 0

    handlers = {"--status": show_status, "--install": install_scheduler,
                "--pause": _cmd_pause, "--resume": _cmd_resume}
    for flag, handler in handlers.items():
        if flag in args:
            handler()
            return 0

    run_scheduled(force="--force" in args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 -- deliberate catch-all; re-raised below
        # A scheduled job that exits non-zero MUST leave a dated trace in its own error
        # log. The dated summary lands first so the log is greppable by time; `raise`
        # then emits the native traceback and exits 1, losing no detail.
        # (SystemExit is a BaseException, so --health's exit 1 is not caught here.)
        _say(f"UNHANDLED EXCEPTION: {type(exc).__name__}: {exc}", err=True)
        record_pulse("error", error=f"{type(exc).__name__}: {exc}"[:300])
        raise
