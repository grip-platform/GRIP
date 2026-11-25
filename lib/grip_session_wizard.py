"""GRIP session-isolation wizard — pre-launch TUI for the worktree trade-off.

Why this exists
---------------
``~/.claude`` IS the git-crypt GRIP repo. Concurrent ``claude`` sessions share
ONE working tree, therefore ONE ``.git/HEAD``. Any ``git checkout`` by any
session flips HEAD for EVERY session in that tree → branch contamination
(logged Gen 348 / Gen 509; ``rules/efficiency-rules.md`` Rule 13). This is the
#1 git-friction source.

The fix is a PRE-LAUNCH shell wizard at a real TTY (sibling to ``grip
channels`` / ``lib/grip_channels_wizard.py``). A SessionStart *hook* cannot do
this job: hooks are scripts, ``AskUserQuestion`` is model-only and is
suppressed when grip-channel inbound is loaded, and a hook fires AFTER claude
has already booted in the shared tree. Only a shell that runs BEFORE ``claude``
exec can ``cd`` into an isolated worktree.

The choice is BINARY (the load-bearing correction)
--------------------------------------------------
"main" vs "fresh branch" BOTH share one HEAD → NEITHER prevents contamination.
ONLY an isolated worktree (separate ``.git/HEAD``) isolates. So the wizard
offers exactly two outcomes:

* ``main``     — stay on the shared main tree (operational sessions: memory,
                 channels, daemons all resolve to ``$HOME/.claude``).
* ``worktree`` — spawn a git-crypt-safe isolated worktree via
                 ``lib/grip-worktree.sh`` (isolated FEATURE / PR work).

It does NOT offer "fresh branch" as if it were isolation.

Proportional cost (PARAMOUNT Execution Bias)
--------------------------------------------
Solo launches are silent ``main`` — zero friction. The launcher runs BEFORE
``claude`` spawns, so the Gate O counter (which counts from INSIDE a session
and floors at 1) cannot be used raw: with exactly one other live session it
returns 1 and looks solo — the classic 2-session collision case. The launcher
counts OTHER live sessions and adds itself (``_count_other_sessions() + 1``).
On Windows, where Git Bash has no ``pgrep``, the count reads harness processes
through psutil, leaves out GUI programs such as the Claude desktop app, and
judges launch claims with Git's ``ps -W`` (GRIP#8280).

Concurrent default = ISOLATE (2026-07-12 flip)
----------------------------------------------
With ≥2 sessions and NO standing choice, the launch AUTO-ISOLATES into a
fresh git-crypt-safe worktree based on ``origin/main`` (one banner line, no
blocking wizard). The first/solo session keeps the shared main tree — the
natural owner of memory, channels, and daemons; later concurrent launches
get their own ``.git/HEAD``. Origin incident: four gg++ sessions on shared
``main`` (2026-07-12) — the old default (main + optional wizard) meant the
gate never actually isolated anyone. An explicit standing choice (env or
config ``main``/``worktree``) is always honoured; ``ask`` keeps the wizard.

Resolution precedence (mirrors the channels wizard)
---------------------------------------------------
1. ``GRIP_SESSION_ISOLATION`` env var (main/worktree/ask → directive; else none)
2. ``state/session-isolation-config.json`` ``choice`` field
3. No standing choice + ≥2 sessions → AUTO-WORKTREE (TTY) / main + note (no TTY)

``bin/grip`` calls ``resolve_or_signal()`` and the directive helpers; it never
reaches into the JSON itself (``rules/syscall-doctrine.md``).

Per ``rules/efficiency-rules.md`` Rule 17, every user-facing string is plain
English.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Single-key input is platform-specific, and importing the POSIX modules at
# module scope crashed every Windows launch (A>> 2026-08-17):
#
#     File "C:\Users\<user>\.grip\lib\grip_session_wizard.py", line 70
#       import termios
#     ModuleNotFoundError: No module named 'termios'
#
# termios and tty do not exist on Windows; msvcrt does, and does not exist on
# POSIX. Both imports are therefore optional, and _select_key_reader() below
# picks the one that is actually present. There is a third, always-available
# fallback so a platform with neither still gets a working prompt rather than
# an import-time crash.
# ---------------------------------------------------------------------------

try:
    import termios
    import tty
except ModuleNotFoundError:  # Windows
    termios = None
    tty = None

try:
    import msvcrt
except ModuleNotFoundError:  # POSIX
    msvcrt = None

# ---------------------------------------------------------------------------
# claude_dir shim (rules/path-portability.md)
#
# NOTE: this module deliberately does NOT reuse Gate O's
# ``gates.session_isolation.count_claude_sessions`` — that counter runs
# INSIDE a claude session and floors at 1 (self). At launcher time claude
# has not spawned yet, so the floor made one existing session look like
# solo and the gate under-fired on the classic 2-session collision case
# (found 2026-07-12). ``_count_other_sessions`` below is the launcher-
# correct counter.
# ---------------------------------------------------------------------------

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _claude_dir() -> Path:
    raw = os.environ.get("CLAUDE_DIR", "").strip()
    return Path(raw) if raw else (Path.home() / ".claude")


# ---------------------------------------------------------------------------
# Constants — single source of truth (DRY)
# ---------------------------------------------------------------------------

CONFIG_VERSION = 1

CHOICE_MAIN = "main"
CHOICE_WORKTREE = "worktree"
CHOICE_ASK = "ask"
WIZARD_SIGNAL = "WIZARD"  # caller runs the interactive flow

VALID_CHOICES = (CHOICE_MAIN, CHOICE_WORKTREE, CHOICE_ASK)

# Env var → effective choice (lowercase). Anything else = no opinion.
ENV_TO_CHOICE = {
    "main": CHOICE_MAIN,
    "stay": CHOICE_MAIN,
    "worktree": CHOICE_WORKTREE,
    "isolated": CHOICE_WORKTREE,
    "ask": CHOICE_ASK,
}

# Worktree spawn defaults
_WORKTREE_BRANCH_PREFIX = "feat/session-"
_REAP_LIMIT = 3  # max stale worktrees reclaimed per launch (bounded, cheap)


def _worktree_home() -> str:
    """Durable home for session worktrees (survives reboot, unlike /tmp).

    A session worktree can hold dirty/unpushed work when its session dies;
    parking it under /tmp silently destroys that work on the next reboot.
    Override with ``GRIP_WORKTREE_HOME``.
    """
    raw = os.environ.get("GRIP_WORKTREE_HOME", "").strip()
    return raw or str(Path.home() / ".grip-worktrees")


# Launch claims close the simultaneous-launch TOCTOU (AGORA PR #3939
# review, Speaker C item 1): two launchers that both pgrep BEFORE either
# claude spawns would each see zero others and both stay on main. Each
# launcher therefore atomically CLAIMS its launch (O_EXCL file keyed by the
# bin/grip shell PID — that exact PID becomes claude via exec) BEFORE
# counting, so at least one racer always sees the other; in the worst case
# both isolate, which is safe (no shared-HEAD collision is possible).
_CLAIM_TTL_SECONDS = 600  # stale-claim horizon (launcher crashed pre-exec)


def _claims_dir() -> Path:
    return _claude_dir() / "state" / "session" / "launch-claims"


def _launcher_pid() -> int:
    """The bin/grip shell PID (becomes the claude PID via exec).

    bin/grip exports ``GRIP_LAUNCHER_PID=$$`` around the wizard call; the
    ppid fallback covers direct invocation.
    """
    raw = os.environ.get("GRIP_LAUNCHER_PID", "").strip()
    try:
        return int(raw) if raw else os.getppid()
    except ValueError:
        return os.getppid()


def _claim_launch() -> None:
    """Atomically record this launch (O_EXCL — idempotent, best-effort)."""
    try:
        _claims_dir().mkdir(parents=True, exist_ok=True)
        target = _claims_dir() / f"{_launcher_pid()}.json"
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "pid": _launcher_pid(),
                "claimed_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
            }) + "\n")
    except FileExistsError:
        pass  # already claimed this launch — idempotent
    except OSError:
        pass  # never block a launch on claim bookkeeping


def _live_claim_pids() -> set[int]:
    """PIDs of live, fresh launch claims; dead/stale claims are reaped."""
    pids: set[int] = set()
    try:
        entries = list(_claims_dir().glob("*.json"))
    except OSError:
        return pids
    now = datetime.now(timezone.utc).timestamp()
    for entry in entries:
        try:
            pid = int(entry.stem)
            fresh = (now - entry.stat().st_mtime) < _CLAIM_TTL_SECONDS
            if fresh and _pid_alive(pid):
                pids.add(pid)
            elif not fresh or not _pid_alive(pid):
                entry.unlink(missing_ok=True)  # guaranteed closer (TTL+PID)
        except (OSError, ValueError):
            continue
    return pids


def _harness_binaries() -> tuple[str, ...]:
    """Process names that constitute a live GRIP session.

    Sourced from the picker's HARNESS_REGISTRY so the two cannot drift: a
    harness ``gg++ <name>`` can launch is, by construction, one whose process
    must be COUNTED (isolation trigger) and PROTECTED (reaper). Before this,
    both were ``pgrep -x claude`` — exact-name-matched and therefore
    structurally blind to codex/opencode/aider/hermes, so a foreign harness
    could neither trigger isolation nor survive the reaper. Falls back to the
    historical claude-only set if the registry cannot be imported.

    The derivation itself now lives on the registry's own module as
    ``grip_harness_picker.harness_binaries``, because three callers need it —
    this wizard, the closeout advisory, and the gg++ concurrency counter — and
    three private copies of one derivation is how the drift this fixes began.
    """
    try:
        from grip_harness_picker import harness_binaries
    except ImportError:
        return ("claude",)
    return harness_binaries()


def _live_harness_pids(runner=None) -> set[int] | None:
    """PIDs of live GRIP-harness processes (``pgrep -x 'claude|codex|...'``).

    ``pgrep -x`` anchors the pattern to the whole process name and the pattern
    is an ERE, so one alternation enumerates every launchable harness in a
    single call (verified on macOS: ``pgrep -x 'claude|codex|zsh'`` returns the
    zsh PIDs too).

    Returns the PID set on a clean enumeration, an EMPTY set when pgrep cleanly
    reports no match (exit 1), and ``None`` when enumeration FAILS — a raised
    error (incl. ``TimeoutExpired``, a ``SubprocessError`` subclass) or a pgrep
    error exit (>=2). ``None`` means "live sessions undeterminable", which the
    reaper must treat as protect-all, never as "no sessions" (AGORA #4021 r2
    C1 — a pgrep timeout must not let live worktrees be reaped). ``runner`` is
    injectable; it defaults to ``subprocess.run`` resolved at CALL time so the
    existing ``mock.patch.object(wiz.subprocess, "run")`` tests keep working.
    """
    run = runner or subprocess.run
    try:
        res = run(["pgrep", "-x", "|".join(_harness_binaries())],
                  capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None  # enumeration failed (incl. TimeoutExpired) → undeterminable
    rc = getattr(res, "returncode", 2)
    if rc == 1:
        return set()  # pgrep: no matching process → determinable "none live"
    if rc != 0:
        return None   # pgrep error exit → undeterminable
    pids: set[int] = set()
    for line in getattr(res, "stdout", "").strip().splitlines():
        try:
            pids.add(int(line.strip()))
        except ValueError:
            continue
    return pids


# Back-compat alias. Call sites and tests reference ``_live_claude_pids`` (and
# patch it via ``mock.patch.object(wiz, "_live_claude_pids", ...)``, which
# rebinds this module global — the call sites resolve it at call time, so the
# patch still lands). The name is historical; the behaviour is now harness-wide.
_live_claude_pids = _live_harness_pids


# Windows (GRIP#8280). Git Bash has no pgrep, and os.kill(pid, 0) raises OSError
# there, which _pid_alive reads as alive, so the count above saw no running session
# and kept every dead launch for its claim's lifetime. On Windows the count asks two
# other sources instead. psutil finds harness processes by program name, as pgrep -x
# does, and reports Windows PIDs. A launch claim is named by the launcher's Git Bash
# PID; exec hands that PID on to the harness, and Git's `ps -W` maps it to the
# harness's Windows PID. So both sources meet in one PID space, and a session found
# both ways counts once. Both the psutil scan and the `ps -W` reader live in
# lib/windows_processes.py, shared with the other Windows session counters and the
# session coordinator's lock check (GRIP#8835).


def _windows_processes():
    """``lib/windows_processes.py``, or None when it cannot be imported."""
    try:
        import windows_processes
    except ImportError:
        return None
    return windows_processes


def _msys_pid_table(runner=None) -> dict[int, int] | None:
    """Git Bash's live processes as ``{PID: WINPID}``, or None when they cannot be read.

    None as well when this launcher's Git Bash PID is not in the PID column: the
    listing then came from another Cygwin-family runtime, or there is no Git Bash
    launcher, and it cannot judge a claim. ``git_bash_pid_table`` in
    ``lib/windows_processes.py`` reads the listing.
    """
    processes = _windows_processes()
    table = processes.git_bash_pid_table(runner) if processes else None
    if table is None or _launcher_pid() not in table:
        return None
    return table


def _windows_harness_pids() -> set[int] | None:
    """Windows PIDs of live harness processes, or None without psutil.

    The scan is ``console_pids`` in ``lib/windows_processes.py`` (GRIP#8835): a
    process counts when its name is ``<harness>.exe`` and its program is not a
    Windows GUI program, so the Claude desktop app's ``claude.exe`` processes never
    count. None as well when that module cannot be imported.
    """
    processes = _windows_processes()
    return processes.console_pids(_harness_binaries()) if processes else None


def _fresh_claims(own: int) -> dict[int, Path]:
    """Every claim younger than the claim lifetime except ``own``; older claims are removed."""
    fresh: dict[int, Path] = {}
    try:
        entries = list(_claims_dir().glob("*.json"))
    except OSError:
        return fresh
    now = datetime.now(timezone.utc).timestamp()
    for entry in entries:
        try:
            pid = int(entry.stem)
            if now - entry.stat().st_mtime >= _CLAIM_TTL_SECONDS:
                entry.unlink(missing_ok=True)
            elif pid != own:
                fresh[pid] = entry
        except (OSError, ValueError):
            continue
    return fresh


def _windows_live_claims(strict: bool = False) -> set[int] | None:
    """Windows PIDs of the other live, fresh launch claims; dead claims are removed.

    ``ps -W`` is read only when another fresh claim exists, so a solo launch runs
    no ps. A claim whose PID is in the PID column is returned as that row's Windows
    PID; one in the WINPID column was written with a Windows PID by
    ``_launcher_pid()``'s fallback and is returned as it is. With no listing to
    judge by, every fresh claim counts, as it did before. Under ``strict``, which
    the reaper asks for, that case answers None instead: an unjudged claim is a
    session whose working folder cannot be known.
    """
    fresh = _fresh_claims(_launcher_pid())
    if not fresh:
        return set()
    table = _msys_pid_table()
    if table is None:
        return None if strict else set(fresh)
    winpids = set(table.values())
    live: set[int] = set()
    for pid, entry in fresh.items():
        if pid in table:
            live.add(table[pid])
        elif pid in winpids:
            live.add(pid)
        else:
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass
    return live


def _count_other_sessions() -> int:
    """Live ``claude`` sessions BEFORE this launch spawns its own.

    Gate O's ``count_claude_sessions()`` floors at 1 because it runs inside a
    session and counts itself. At launcher time claude has NOT spawned yet,
    so that floor makes one existing session indistinguishable from none —
    exactly the 2-session collision case this gate exists for. Fail-safe to 0
    (never isolate on an unobservable process table).

    Claim-then-count: this launch is claimed FIRST, then the count is the
    union of the live ``claude`` process table and other live launch claims
    (a racing launcher that has not exec'd claude yet), minus self.

    On Windows (GRIP#8280) the process table comes from psutil and the claims
    are judged by Git's ``ps -W``; see ``_windows_live_claims``.
    """
    _claim_launch()
    if sys.platform == "win32":
        return len((_windows_harness_pids() or set()) | _windows_live_claims())
    pids = (_live_claude_pids() or set()) | _live_claim_pids()
    pids.discard(_launcher_pid())
    return len(pids)

# ANSI — degrade gracefully via env detection
_NO_COLOR = bool(os.environ.get("NO_COLOR"))
_TERM = os.environ.get("TERM", "")
_ANSI_OK = (not _NO_COLOR) and _TERM not in ("", "dumb")

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "fg_orange": "\033[38;5;208m",
    "fg_green": "\033[38;5;42m",
    "fg_red": "\033[38;5;203m",
}


def _style(text: str, *names: str) -> str:
    if not _ANSI_OK:
        return text
    codes = "".join(_ANSI.get(n, "") for n in names)
    return f"{codes}{text}{_ANSI['reset']}"


# ---------------------------------------------------------------------------
# Paths (lazy so mock.patch / CLAUDE_DIR override works)
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    return _claude_dir() / "state" / "session-isolation-config.json"


def _presence_path() -> Path:
    return _claude_dir() / "state" / "session" / "main-claim.json"


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Directive:
    """What bin/grip should do with the launch.

    ``kind`` is ``"main"`` (no cd) or ``"worktree"`` (cd into ``path`` first).
    ``path`` is empty for ``main``.
    """

    kind: str
    path: str = ""


# ---------------------------------------------------------------------------
# Resolution (called from bin/grip)
# ---------------------------------------------------------------------------


def _env_choice() -> str | None:
    raw = os.environ.get("GRIP_SESSION_ISOLATION", "").strip().lower()
    return ENV_TO_CHOICE.get(raw)


def _persisted_choice(path: Path | None = None) -> str | None:
    target = path if path is not None else _config_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    choice = data.get("choice")
    return choice if choice in VALID_CHOICES else None


def resolve_or_signal(path: Path | None = None) -> str:
    """Return ``main`` / ``worktree`` / ``WIZARD`` per the resolution order.

    A persisted ``ask`` maps to ``WIZARD`` (caller runs the wizard each time).
    Note: this does NOT apply the ≥2-session gate — that lives in
    ``should_prompt()`` so the env/config layers stay pure and testable.
    """
    env = _env_choice()
    if env == CHOICE_ASK:
        return WIZARD_SIGNAL
    if env in (CHOICE_MAIN, CHOICE_WORKTREE):
        return env
    persisted = _persisted_choice(path)
    if persisted == CHOICE_ASK:
        return WIZARD_SIGNAL
    if persisted in (CHOICE_MAIN, CHOICE_WORKTREE):
        return persisted
    return WIZARD_SIGNAL


def should_prompt(sessions: int | None = None) -> bool:
    """True only when proportional cost is justified: ≥2 concurrent sessions.

    ``sessions`` means the TOTAL once this launch lands (self + others); it is
    injectable for hermetic tests. When unspecified, the launcher-time count
    is ``_count_other_sessions() + 1`` — NOT the Gate O counter, whose
    floor-at-1 made one existing session look like solo (see module
    docstring). Solo sessions never see the gate (PARAMOUNT Execution Bias).
    """
    n = sessions if sessions is not None else (_count_other_sessions() + 1)
    return n >= 2


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_config(choice: str, set_by: str = "wizard",
                 path: Path | None = None) -> None:
    """Write the choice to ``state/session-isolation-config.json`` atomically."""
    if choice not in VALID_CHOICES:
        raise ValueError(f"invalid choice: {choice!r}")
    target = path if path is not None else _config_path()
    payload = {
        "choice": choice,
        "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "set_by": set_by,
        "version": CONFIG_VERSION,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def claim_main(path: Path | None = None) -> None:
    """Record that this session is taking the shared main tree.

    A lightweight presence stamp — NOT a hard lock. The wizard only prompts
    when ≥2 sessions exist, so the stamp's role is observability (who took
    main), not mutual exclusion. Best-effort; never raises.
    """
    target = path if path is not None else _presence_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "pid": os.getpid(),
            "claimed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Worktree spawn (delegates to lib/grip-worktree.sh — git-crypt-safe)

#: Cap on the pre-base fetch. Short by design: a fresher base is worth a couple
#: of seconds, and is never worth blocking a launch, so this expires fail-open.
_FETCH_TIMEOUT_S = 12
# ---------------------------------------------------------------------------


def _worktree_script() -> Path:
    return _claude_dir() / "lib" / "grip-worktree.sh"


def _new_worktree_target() -> tuple[str, str]:
    """Return (worktree_path, branch_name) keyed by pid+epoch — collision-free."""
    tag = f"{os.getpid()}-{int(datetime.now(timezone.utc).timestamp())}"
    return (f"{_worktree_home()}/grip-session-{tag}",
            f"{_WORKTREE_BRANCH_PREFIX}{tag}")


def _origin_main_base_args(runner=subprocess.run) -> list[str]:
    """``["--base", "origin/main"]`` when the ref resolves, else ``[]``.

    A SHORT, fail-open ``git fetch`` runs first. This reverses AGORA #3939
    round 2, which reasoned that "freshness of the base is not load-bearing"
    and skipped the fetch to save launch latency.

    That premise was refuted on 2026-08-19 (V>>: *"Before starting ANY
    ISOLATED WORKTREE, the WORKTREE must pull local MAIN"*). ``origin/main``
    is a local remote-tracking ref that moves ONLY on fetch, so without one
    ``--base origin/main`` is correct in shape and arbitrarily stale in fact.
    That session's worktree was born 2 commits behind and drifted to 7, and
    the staleness was not harmless: the shared tree and the worktree
    disagreed about module contents, so tests imported one copy while
    assertions read the other and a genuinely failing test was masked.

    The latency objection is answered rather than dismissed. The fetch is
    capped at ``_FETCH_TIMEOUT_S`` and is FAIL-OPEN: a timeout, an offline
    machine, or any git error falls through to the last-fetched ref exactly
    as before. A launch is never blocked on the network — it just starts
    from a fresher base whenever the network allows, which is strictly
    better than never trying.
    """
    repo = str(_claude_dir())
    # Refresh origin/main first, best-effort. Errors are swallowed on purpose:
    # this must never be the reason a session fails to launch.
    try:
        runner(
            ["git", "-C", repo, "fetch", "--quiet", "origin", "main"],
            capture_output=True, text=True, timeout=_FETCH_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        probe = runner(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet",
             "origin/main^{commit}"],
            capture_output=True, text=True, timeout=8,
        )
        if getattr(probe, "returncode", 1) == 0:
            return ["--base", "origin/main"]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def spawn_worktree(runner=subprocess.run) -> Directive:
    """Create a git-crypt-safe isolated worktree off origin/main.

    Returns a ``worktree`` Directive on success, or falls back to ``main`` on
    any failure (fail-safe: never block the launch — a failed worktree must
    not strand the operator). ``runner`` is injectable for tests.

    The add runs with ``--lock-mutex`` (concurrent launches are exactly the
    TOCTOU window the mutex closes) and ``--base origin/main`` when the ref
    resolves (see ``_origin_main_base_args``).
    """
    script = _worktree_script()
    if not script.exists():
        sys.stderr.write(
            "[grip session] grip-worktree.sh not found — staying on main\n")
        return Directive(CHOICE_MAIN)
    wt_path, branch = _new_worktree_target()
    try:
        home = Path(_worktree_home())
        home.mkdir(parents=True, exist_ok=True)
        os.chmod(home, 0o700)  # worktrees hold uncommitted code — owner-only
    except OSError:
        pass
    base_args = _origin_main_base_args(runner)
    try:
        result = runner(
            ["bash", str(script), "add", wt_path, branch,
             "--lock-mutex", "--owner-pid", str(_launcher_pid()), *base_args],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(
            f"[grip session] worktree spawn failed ({exc}) — staying on main\n")
        return Directive(CHOICE_MAIN)
    if getattr(result, "returncode", 1) != 0:
        sys.stderr.write(
            "[grip session] worktree spawn failed — staying on main\n")
        if getattr(result, "stderr", ""):
            sys.stderr.write(result.stderr)
        return Directive(CHOICE_MAIN)
    sys.stderr.write(
        f"[grip session] isolated worktree ready: {wt_path} (branch {branch})\n")
    return Directive(CHOICE_WORKTREE, wt_path)


# ---------------------------------------------------------------------------
# Stale-worktree reaper (rules/lifecycle-closed-or-expired.md: a durable
# worktree home needs a guaranteed closer with an AUTONOMOUS trigger — this
# runs on every concurrent launch, bounded and fail-soft)
# ---------------------------------------------------------------------------


def _lock_pid_for_branch(branch: str) -> int | None:
    """PID recorded in the branch-lock file, or None when absent/unreadable."""
    lock = (_claude_dir() / "state" / "branch-locks"
            / f"{branch.replace('/', '_')}.lock")
    try:
        for line in lock.read_text(encoding="utf-8").splitlines():
            if line.startswith("PID="):
                return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def _pid_alive(pid: int) -> bool:
    """True unless the PID provably does not exist (conservative)."""
    if sys.platform == "win32":
        return _msys_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but not ours — treat as alive
    return True


def _msys_pid_alive(pid: int) -> bool:
    """A branch lock's owner on Windows is alive while Git's ``ps -W`` lists it.

    The owner is the launcher's Git Bash PID, which psutil cannot see. The listing
    is read fresh for every lock and never kept: a launcher that locked its worktree
    after an earlier read would otherwise look dead, and its new worktree could be
    reaped. A PID in the WINPID column, written through ``_launcher_pid()``'s
    fallback, counts too. With no listing to judge by, or without
    ``lib/windows_processes.py``, the owner counts as alive.
    """
    processes = _windows_processes()
    table = _msys_pid_table()
    if processes is None or table is None:
        return True
    return processes.pid_listed(table, pid)


def _session_worktrees(runner=subprocess.run) -> list[tuple[str, str]]:
    """(path, branch) pairs for session worktrees under the worktree home."""
    try:
        res = runner(
            ["git", "-C", str(_claude_dir()), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(res, "returncode", 1) != 0:
        return []
    home = str(Path(_worktree_home()).resolve())
    pairs: list[tuple[str, str]] = []
    path = ""
    for line in getattr(res, "stdout", "").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("branch refs/heads/") and path:
            branch = line[len("branch refs/heads/"):].strip()
            if (_path_under(path, home)
                    and branch.startswith(_WORKTREE_BRANCH_PREFIX)):
                pairs.append((path, branch))
            path = ""
    return pairs


def _path_under(path: str, home: str) -> bool:
    try:
        return str(Path(path).resolve()).startswith(home + os.sep)
    except OSError:
        return False


def _cwd_within(cwd: str, path: str) -> bool:
    """True if resolved ``cwd`` IS ``path`` or is nested under it.

    An empty cwd is "no cwd" (False). But if EITHER operand cannot be RESOLVED
    (``OSError`` — a live cwd OR the worktree ``path`` on an unreadable mount)
    it fails SAFE to True: an unresolvable live cwd must never read as "not
    here", and an unresolvable worktree over-protects rather than risking a
    reap of an unreadable-mount worktree (AGORA #4021 r2 C3 + r3 — fail-safe,
    never fail-open).
    """
    if not cwd:
        return False
    try:
        c = str(Path(cwd).resolve())
        p = str(Path(path).resolve())
    except OSError:
        return True  # unresolvable → cannot prove "not within" → keep (fail-safe)
    return c == p or c.startswith(p + os.sep)


def _pid_cwd(pid: int, runner=None) -> str | None:
    """Resolved cwd of a live PID via ``lsof -d cwd``.

    Returns the resolved cwd on success, else ``None`` — liveness is
    UNDETERMINABLE and the caller must fail-safe (protect, never reap). ``None``
    covers: lsof absent, a transient probe error, a NON-ZERO exit whose partial
    output must not be trusted, a reported cwd that cannot be RESOLVED, AND a
    clean exit with NO cwd field. That last case is the pgrep→lsof RACE: a
    genuinely live PID always has a cwd, so "no cwd field" means the PID exited
    between the pgrep and lsof probes (or lsof glitched). Treating it as "no
    live cwd here" (``''``) would be fail-OPEN and let the reaper delete the
    worktree of a session alive moments ago, so it too yields ``None`` (AGORA
    #4021 C1+C2, r3 unresolvable-cwd, r4 DEFER pgrep→lsof race). ``''`` is
    never returned.
    """
    run = runner or subprocess.run
    try:
        res = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                  capture_output=True, text=True, timeout=2)  # bounded per-pid
    except FileNotFoundError:
        return None  # lsof not installed → liveness undeterminable
    except (OSError, subprocess.SubprocessError):
        return None  # transient probe failure → undeterminable (fail-safe)
    if getattr(res, "returncode", 1) != 0:
        return None  # non-zero exit may be a partial/failed read → fail-safe
    for line in getattr(res, "stdout", "").splitlines():
        if line.startswith("n"):
            raw = line[1:].strip()
            if not raw:
                # Empty NAME field: Path("").resolve() would yield the
                # LAUNCHER's cwd — a bogus non-None value. No verifiable cwd →
                # undeterminable → protect (AGORA #4024 judge falsifier).
                return None
            try:
                return str(Path(raw).resolve())
            except OSError:
                return None  # unresolvable cwd → undeterminable, protect-all
    return None  # no cwd field for a live PID → vanished mid-probe → protect


def _live_session_cwds(runner=None):
    """Resolved cwds of every live session (claude processes + launch claims).

    The reaper must never remove a worktree that IS a live session's cwd — the
    removal yanks that session's working directory out from under it
    (feedback_worktree_removal_live_session_cwd_collision). The branch-lock is
    a fragile liveness proxy: a ``gg++`` isolation worktree may never write
    one, so the process cwd is the ground-truth signal. Returns ``None`` when
    live sessions exist but their cwds cannot be read (lsof absent), telling
    the caller to reap nothing that launch (fail-safe keep over a yanked cwd).
    On Windows, which has no lsof, psutil reads the working folders instead
    (``_windows_live_session_cwds``, GRIP#8280).
    """
    if sys.platform == "win32":
        return _windows_live_session_cwds()
    claude = _live_claude_pids(runner)
    if claude is None:
        return None  # live-session enumeration failed → protect everything
    pids = claude | _live_claim_pids()
    pids.discard(_launcher_pid())
    if not pids:
        return frozenset()
    cwds: set[str] = set()
    for pid in pids:
        cwd = _pid_cwd(pid, runner)
        if cwd is None:
            return None  # undeterminable → protect everything this launch
        if cwd:
            cwds.add(cwd)
    return frozenset(cwds)


def _windows_live_session_cwds():
    """The working folders of every other live session on Windows, or None.

    The sessions are the harness scan plus the live launch claims, all as Windows
    PIDs, and psutil reads each one's working folder. None, which keeps every
    worktree, when psutil is missing, when a claim cannot be judged, or when any
    folder cannot be read.
    """
    harness = _windows_harness_pids()
    if harness is None:
        return None
    claims = _windows_live_claims(strict=True)
    if claims is None:
        return None
    cwds: set[str] = set()
    for pid in harness | claims:
        cwd = _winpid_cwd(pid)
        if cwd is None:
            return None
        cwds.add(cwd)
    return frozenset(cwds)


def _winpid_cwd(pid: int) -> str | None:
    """The working folder of Windows process ``pid``, or None when it cannot be read.

    A process that has ended, one psutil may not read, and an empty answer all
    give None, as an lsof failure does.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        cwd = psutil.Process(pid).cwd()
    except (psutil.Error, OSError):
        return None
    return cwd or None


def _worktree_is_reapable(path: str, branch: str, runner=subprocess.run,
                          *, live_cwds) -> bool:
    """Dead owner + not a live cwd + clean tree + zero commits ahead of main.

    ``live_cwds`` is a REQUIRED keyword-only arg (the resolved cwds of every
    live session) — never defaulted, so a caller can never silently skip the
    cwd guard by forgetting it (AGORA #4021 r3 C4). Anything dirty, unpushed,
    owned by a live lock-PID, or serving as a live session's cwd is KEPT —
    reaping must never destroy work OR yank a live session's working
    directory. Any probe failure → not reapable (fail-safe).
    """
    pid = _lock_pid_for_branch(branch)
    if pid is not None and _pid_alive(pid):
        return False
    # A live session's cwd inside this worktree → removing it would yank that
    # session's working directory (the branch-lock above misses gg++ isolation
    # worktrees that never write one; the process cwd is ground truth).
    if any(_cwd_within(c, path) for c in live_cwds):
        return False
    try:
        st = runner(["git", "-C", path, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=10)
        if getattr(st, "returncode", 1) != 0 or getattr(st, "stdout", "x").strip():
            return False
        ahead = runner(
            ["git", "-C", path, "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if getattr(ahead, "returncode", 1) != 0:
            return False
        return getattr(ahead, "stdout", "1").strip() == "0"
    except (OSError, subprocess.SubprocessError):
        return False


def reap_stale_worktrees(runner=subprocess.run, limit: int = _REAP_LIMIT) -> int:
    """Remove session worktrees whose owner died with nothing left to lose.

    Returns the number reaped. Bounded per launch; silent unless something
    was actually reclaimed. The safe-delete ``git branch -d`` only removes
    fully-merged branches — an unmerged branch survives its worktree.
    """
    script = _worktree_script()
    if not script.exists():
        return 0
    live_cwds = _live_session_cwds(runner)
    if live_cwds is None:
        return 0  # liveness undeterminable (lsof absent) → reap nothing
    reaped = 0
    for path, branch in _session_worktrees(runner):
        if reaped >= limit:
            break
        if not _worktree_is_reapable(path, branch, runner, live_cwds=live_cwds):
            continue
        try:
            res = runner(["bash", str(script), "remove", path],
                         capture_output=True, text=True, timeout=30)
            if getattr(res, "returncode", 1) != 0:
                continue
            # -D, not -d: reapability already PROVED zero commits ahead of
            # origin/main, so force-delete loses nothing — while -d's own
            # merged-into-HEAD heuristic can refuse a provably-merged branch
            # when the main tree sits on an older commit (AGORA #3939 r2).
            bd = runner(["git", "-C", str(_claude_dir()),
                         "branch", "-D", branch],
                        capture_output=True, text=True, timeout=10)
            if getattr(bd, "returncode", 1) != 0:
                sys.stderr.write(
                    f"[grip session] worktree reaped but branch left: "
                    f"{branch}\n")
        except (OSError, subprocess.SubprocessError):
            continue
        reaped += 1
    if reaped:
        sys.stderr.write(
            f"[grip session] reaped {reaped} stale session worktree(s)\n")
    return reaped


# ---------------------------------------------------------------------------
# Renderers (pure — no I/O; testable). Each well under 30 lines (CC + KISS).
# ---------------------------------------------------------------------------


def render_header(sessions: int) -> list[str]:
    return [
        _style("GRIP SESSION ISOLATION", "bold"),
        _style("pre-launch / shared working-tree guard", "dim"),
        _style("─" * 60, "dim"),
        f"  {_style(str(sessions), 'fg_orange', 'bold')} concurrent "
        f"claude session(s) detected on this machine.",
        "",
    ]


_TRADE_OFF_LINES = (
    ("MAIN     ", "Stay on the shared main working tree. CORRECT for"),
    ("         ", "OPERATIONAL work: GRIP memory, channels, and daemons"),
    ("         ", "all resolve to $HOME/.claude (the main tree). Risk:"),
    ("         ", "another session's `git checkout` flips YOUR branch"),
    ("         ", "too — both sessions share one .git/HEAD."),
    ("WORKTREE ", "Spawn an ISOLATED working copy (own .git/HEAD) off"),
    ("         ", "origin/main via lib/grip-worktree.sh (git-crypt-safe;"),
    ("         ", "native worktrees break on this encrypted repo). CORRECT"),
    ("         ", "for ISOLATED FEATURE / PR work — no branch contamination."),
    ("         ", "CAVEAT: split-brain for operational work — memory,"),
    ("         ", "channels, and daemons still point at $HOME/.claude,"),
    ("         ", "NOT the worktree. Use it for code, not session ops."),
)


def render_trade_off() -> list[str]:
    out = [_style("TRADE-OFF", "bold")]
    for label, body in _TRADE_OFF_LINES:
        out.append(f"  {_style(label, 'fg_orange')}  {body}")
    out.append("")
    return out


_ACTION_PROMPT = (
    "[M]ain (shared)  [W]orktree (isolated)  [A]sk-each-launch  [Q]uit (main)"
)

_KEY_TO_ACTION = {
    "m": "main", "w": "worktree", "a": "ask", "q": "quit",
}


def render_full_screen(sessions: int) -> list[str]:
    lines: list[str] = []
    lines.extend(render_header(sessions))
    lines.extend(render_trade_off())
    return lines


# ---------------------------------------------------------------------------
# Single-key input + stdout
# ---------------------------------------------------------------------------


def _read_one_key_posix() -> str:
    """One keypress, no Return, via termios/tty (macOS, Linux)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_one_key_windows() -> str:
    """One keypress, no Return, via msvcrt (Windows console)."""
    return msvcrt.getwch()


def _read_one_key_line() -> str:
    """Fallback when neither module is present: first char of a whole line.

    Costs the operator a Return keypress, which is a far better outcome than
    the module-import crash this replaces.
    """
    return sys.stdin.readline()[:1]


def _select_key_reader():
    """Pick the reader whose platform module actually imported."""
    if msvcrt is not None:
        return _read_one_key_windows
    if termios is not None and tty is not None:
        return _read_one_key_posix
    return _read_one_key_line


_READ_KEY = _select_key_reader()


def _read_one_key() -> str:
    if not sys.stdin.isatty():
        return ""
    ch = _READ_KEY()
    if not ch or ord(ch) in (3, 4, 27):  # Ctrl+C / Ctrl+D / Esc
        raise KeyboardInterrupt()
    return ch.lower()


def _prompt_for_action() -> str:
    while True:
        sys.stdout.write(_ACTION_PROMPT + " ")
        sys.stdout.flush()
        try:
            key = _read_one_key()
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            return "quit"
        sys.stdout.write("\n")
        action = _KEY_TO_ACTION.get(key)
        if action is not None:
            return action
        sys.stdout.write(_style("  unknown key — try again\n", "dim"))


def _render_to_stdout(lines: list[str]) -> None:
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Directive emission — the contract bin/grip consumes on stdout
# ---------------------------------------------------------------------------


def emit_directive(directive: Directive) -> None:
    """Print the launch directive on the LAST stdout line for bin/grip.

    Format: ``DIRECTIVE main`` or ``DIRECTIVE worktree <path>``. bin/grip reads
    only the final ``DIRECTIVE`` line, so human-facing render text above it on
    stderr/stdout is harmless.
    """
    if directive.kind == CHOICE_WORKTREE and directive.path:
        sys.stdout.write(f"DIRECTIVE worktree {directive.path}\n")
    else:
        sys.stdout.write("DIRECTIVE main\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Wizard entry points
# ---------------------------------------------------------------------------


def _choice_to_directive(choice: str) -> Directive:
    """Map a resolved/selected choice to a launch directive.

    ``worktree`` triggers an actual worktree spawn (with fail-safe to main).
    """
    if choice == CHOICE_WORKTREE:
        return spawn_worktree()
    return Directive(CHOICE_MAIN)


def run_interactive() -> int:
    """Render, prompt, persist (if M/W/A chosen), emit directive."""
    sessions = _count_other_sessions() + 1
    # Render to stderr so stdout carries ONLY the DIRECTIVE line for bin/grip.
    for line in render_full_screen(sessions):
        sys.stderr.write(line + "\n")
    sys.stderr.write(_ACTION_PROMPT + "\n")
    sys.stderr.flush()
    action = _prompt_for_action()
    if action == "quit":
        sys.stderr.write(_style("no change — staying on main\n", "dim"))
        emit_directive(Directive(CHOICE_MAIN))
        return 0
    if action == "main":
        write_config(CHOICE_MAIN)
        claim_main()
        emit_directive(Directive(CHOICE_MAIN))
        return 0
    if action == "worktree":
        write_config(CHOICE_WORKTREE)
        emit_directive(spawn_worktree())
        return 0
    if action == "ask":
        write_config(CHOICE_ASK)
        emit_directive(Directive(CHOICE_MAIN))  # this launch stays on main
        return 0
    emit_directive(Directive(CHOICE_MAIN))
    return 0


def _has_controlling_terminal() -> bool:
    """True when an interactive operator terminal is present.

    Deliberately does NOT consult ``sys.stdout.isatty()``: ``bin/grip`` captures
    this wizard's stdout in a pipe (``$(… --directive | tail)``) to read the
    ``DIRECTIVE`` line back, so stdout is ALWAYS a pipe at launch. Gating
    auto-isolate on stdout being a TTY made the isolate branch UNREACHABLE in
    production — every concurrent launch fell through to the "no TTY → main"
    fallback, so gg++ never isolated anyone (2026-07-12, four-sessions-on-
    shared-main, round 2 of the #3939 fix). The operator-presence signal is
    stdin being a terminal, or a controlling ``/dev/tty`` existing.
    """
    try:
        if sys.stdin.isatty():
            return True
    except (ValueError, OSError):
        pass
    # /dev/tty and O_NOCTTY are both POSIX-only. Reading os.O_NOCTTY on
    # Windows raises AttributeError, which the OSError handler below would
    # not catch, so check for it rather than letting a piped Windows launch
    # crash where it should simply answer "no terminal".
    if not hasattr(os, "O_NOCTTY"):
        return False
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    os.close(fd)
    return True


def run_directive() -> int:
    """Non-interactive launch path for bin/grip.

    Solo → silent ``main``. With ≥2 concurrent sessions:

    * an explicit standing choice (env/config ``main``/``worktree``) wins;
    * an explicit ``ask`` (env/config) + TTY → the interactive wizard;
    * NO standing choice + TTY → AUTO-ISOLATE into a fresh worktree (the
      2026-07-12 default flip — the old default-main meant concurrent
      launches were never actually isolated);
    * no TTY → ``main`` + one stderr note (a piped/CI launch is never
      silently moved into a worktree).
    """
    # Count ONCE and thread it through — a second live count could disagree
    # with the one the decision was made on (AGORA #3939 round 2, item 4).
    total = _count_other_sessions() + 1
    if not should_prompt(total):
        emit_directive(Directive(CHOICE_MAIN))
        return 0
    signal = resolve_or_signal()
    if signal in (CHOICE_MAIN, CHOICE_WORKTREE):
        emit_directive(_choice_to_directive(signal))
        return 0
    # signal == WIZARD and ≥2 sessions.
    # The operator-presence check must NOT consult sys.stdout.isatty(): bin/grip
    # captures stdout in a pipe to read the DIRECTIVE line, so it is always a
    # pipe here. Gating on it made auto-isolate unreachable in prod (#3939 r2).
    tty = _has_controlling_terminal()
    if not tty:
        sys.stderr.write(
            "[grip session] >=2 claude sessions but no TTY; defaulting to main. "
            "Run `grip session` to choose isolation.\n")
        emit_directive(Directive(CHOICE_MAIN))
        return 0
    explicit_ask = (_env_choice() == CHOICE_ASK
                    or _persisted_choice() == CHOICE_ASK)
    if explicit_ask:
        return run_interactive()
    sys.stderr.write(
        f"[grip session] {total} concurrent claude sessions -> isolating this "
        "launch in a fresh worktree off origin/main (the shared main tree "
        "stays with the first session). Opt out: GRIP_SESSION_ISOLATION=main "
        "or run `grip session`.\n")
    reap_stale_worktrees()
    emit_directive(spawn_worktree())
    return 0


def run_accept(value: str) -> int:
    if value not in VALID_CHOICES:
        sys.stderr.write(f"--accept must be one of {VALID_CHOICES}\n")
        return 2
    write_config(value, set_by="manual")
    sys.stdout.write(f"choice persisted: {value}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip session",
        description=("Configure GRIP session isolation for concurrent claude "
                     "sessions (shared main tree vs isolated worktree)."),
    )
    parser.add_argument("--resolve", action="store_true",
                        help="print resolved choice or WIZARD signal and exit "
                             "(does NOT apply the >=2-session gate)")
    parser.add_argument("--directive", action="store_true",
                        help="full launch gate: emit `DIRECTIVE main|worktree "
                             "<path>` for bin/grip (used at launch)")
    parser.add_argument("--accept", choices=VALID_CHOICES,
                        help="non-interactive set (test/install)")
    args = parser.parse_args(argv)

    if args.resolve:
        sys.stdout.write(resolve_or_signal() + "\n")
        return 0
    if args.directive:
        return run_directive()
    if args.accept is not None:
        return run_accept(args.accept)
    return run_interactive()


if __name__ == "__main__":
    raise SystemExit(main())
