#!/usr/bin/env python3
"""relay_mac_selfdeploy — Mac analogue of the VPS relay-selfdeploy poller.

Closes the deploy gap behind the 2026-06-11 silent-drop incident (#2173,
PR #3536 RCA): a fix merged to ``lib/relay_dispatcher.py`` reached the running
``com.grip.relay-queue-drain`` daemon HOURS late because (a) the daemon imports
relay modules once at process start and nothing ever restarted it, and (b) a
stale staged copy in the shared working tree had silently REVERTED the merged
fix on disk. The VPS has ``scripts/relay-selfdeploy.sh`` (cron */3: pull,
import-gate, restart); the Mac had no analogue. This is it.

Design constraints (load-bearing, in priority order):

1. **NEVER writes the working tree.** Concurrent interactive sessions own it
   (git-crypt blocks worktrees here, so the shared tree is the norm). A cron
   poller that clobbers files fights live sessions and violates the
   "git checkout main -- <path> overwrites unsaved work" PARAMOUNT. Deploy
   therefore means RESTART-onto-already-canonical-content, never pull/copy.
2. **Divergence is surfaced, never resolved.** Worktree ≠ origin/main for a
   relay path ⇒ the daemon may be running stale OR local code — a human must
   reconcile (it may be another session's in-flight work). The poller logs
   loudly AND notifies V>> on WhatsApp once per divergence fingerprint
   (lifecycle rule: silent failure was the entire incident class).
3. **Import-gate before any restart.** A restart that loads broken code is
   worse than a stale daemon (the VPS selfdeploy's fail-safe, mirrored).
4. **Lock-free git only.** ``fetch`` / ``rev-parse`` / ``hash-object`` /
   ``log`` never take ``.git/index.lock`` — safe beside live sessions.

Scheduling is launchd's (``com.grip.relay-mac-selfdeploy.plist``,
StartInterval): each invocation is one ``--once`` pass, no internal loop.
Stdlib only (syscall-doctrine + KISS); the WA notify reuses
``lib.whatsapp_transport`` lazily and fails open.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wa_identities import operator_jid  # noqa: E402

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
# The canonical relay path set the drain daemon imports. Mirrors the VPS
# selfdeploy pathset; extend ONLY when the daemon gains a new import.
RELAY_PATHS = (
    "lib/relay_dispatcher.py",
    "lib/relay_queue_drain.py",
    "lib/relay_decision.py",
    # Transitive dep: relay_dispatcher does `from gitcrypt_preflight import
    # assert_decrypted` (added in beab94855). Omitting it here meant the poller
    # deployed relay_dispatcher.py without its module -> ModuleNotFoundError ->
    # the running drain daemon (which imports relay_dispatcher in-process) could
    # not load canonical code, and a hand-deletion of the import diverged disk
    # from origin/main, jamming the poller. List a dep here whenever the daemon
    # gains a new import.
    "lib/gitcrypt_preflight.py",
    # The drain and the dispatcher read the team's WhatsApp IDs through the
    # classifier, which imports wa_identities, which imports claude_dir.
    "lib/relay_principal_classifier.py",
    "lib/wa_identities.py",
    "lib/claude_dir.py",
)
DAEMON_LABEL = "com.grip.relay-queue-drain"
STATE_PATH = CLAUDE_DIR / "state" / "relay" / "mac-selfdeploy.state.json"
LOG_PATH = CLAUDE_DIR / "logs" / "relay-mac-selfdeploy.jsonl"
OPERATOR_JID = operator_jid("GRIP_SELFDEPLOY_NOTIFY_JID")  # None: nothing is sent

# Decision verdicts (pure decide() output — the tested core).
NOOP = "noop"            # daemon fresh, worktree canonical — nothing to do
RESTART = "restart"      # worktree canonical, daemon predates last relay commit
DIVERGED = "diverged"    # worktree ≠ canonical — surface, never touch


def decide(*, worktree_match: bool, daemon_start_epoch: float | None,
           latest_commit_epoch: float | None) -> str:
    """Pure deploy decision. The ONLY paths to RESTART require a canonical
    worktree — divergent content is never (re)started, and a missing daemon
    with canonical content is healed by a (re)start.

    daemon_start_epoch None ⇒ daemon not running (crashed / unloaded):
    canonical worktree ⇒ RESTART heals it; divergent ⇒ DIVERGED (starting
    divergent code silently would recreate the incident class).
    latest_commit_epoch None ⇒ git unavailable this pass ⇒ NOOP (fail idle,
    never restart on missing evidence).
    """
    if not worktree_match:
        return DIVERGED
    if latest_commit_epoch is None:
        return NOOP
    if daemon_start_epoch is None:
        return RESTART
    return RESTART if daemon_start_epoch < latest_commit_epoch else NOOP


def _run(argv: list[str], *, cwd: Path | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd or CLAUDE_DIR), capture_output=True,
                          text=True, timeout=timeout)


def worktree_matches_canonical() -> tuple[bool, str]:
    """(all relay paths byte-identical to origin/main, fingerprint).

    Lock-free: blob-SHA comparison via rev-parse (object db) + hash-object
    (reads the file, no index). The fingerprint keys divergence-notify dedup.
    """
    shas = []
    match = True
    for rel in RELAY_PATHS:
        canon = _run(["git", "rev-parse", f"origin/main:{rel}"])
        local = _run(["git", "hash-object", rel])
        c, l = canon.stdout.strip(), local.stdout.strip()
        shas.append(l or "absent")
        if canon.returncode != 0 or local.returncode != 0 or c != l:
            match = False
    return match, ":".join(shas)


def latest_relay_commit_epoch() -> float | None:
    """Unix epoch of the newest origin/main commit touching RELAY_PATHS."""
    r = _run(["git", "log", "-1", "--format=%ct", "origin/main", "--",
              *RELAY_PATHS])
    out = r.stdout.strip()
    return float(out) if r.returncode == 0 and out else None


def daemon_pid() -> int | None:
    r = _run(["launchctl", "print", f"gui/{os.getuid()}/{DAEMON_LABEL}"])
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("pid = "):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def parse_etime(etime: str) -> float | None:
    """ps etime ([[dd-]hh:]mm:ss) → seconds. Locale-free by format."""
    etime = etime.strip()
    if not etime:
        return None
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d)
    parts = etime.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, (m, s) = 0, nums
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + s


def daemon_start_epoch() -> float | None:
    pid = daemon_pid()
    if pid is None:
        return None
    r = _run(["ps", "-p", str(pid), "-o", "etime="])
    elapsed = parse_etime(r.stdout) if r.returncode == 0 else None
    return time.time() - elapsed if elapsed is not None else None


def import_gate() -> bool:
    """True iff the worktree relay modules import cleanly. cwd=lib so the
    modules' bare co-located imports resolve via sys.path[0] (the '' entry).
    Uses THIS interpreter (the venv python under launchd)."""
    r = _run([sys.executable, "-c",
              "import relay_dispatcher, relay_queue_drain, relay_decision"],
             cwd=CLAUDE_DIR / "lib", timeout=120)
    return r.returncode == 0


def restart_daemon() -> bool:
    r = _run(["launchctl", "kickstart", "-k",
              f"gui/{os.getuid()}/{DAEMON_LABEL}"])
    return r.returncode == 0


def _load_state() -> dict:
    try:
        obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _log(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


# At most one divergence WA note per 6h, even across episodes (anti-flap floor).
_NOTIFY_MIN_INTERVAL_SEC = 6 * 3600


def notify_divergence(fingerprint: str, state: dict, *, send_fn=None,
                      now: float | None = None,
                      min_interval_sec: int = _NOTIFY_MIN_INTERVAL_SEC) -> bool:
    """At most ONE WA note per divergence EPISODE — a continuous run of
    DIVERGED verdicts — regardless of content churn within it.

    The dedup key is the EPISODE (``divergence_notified``), NOT the content
    fingerprint. While a session actively edits the relay files the blob-SHA
    changes every pass, so the old fingerprint-keyed dedup re-fired on every
    edit and spammed the operator (2026-06-21 incident: 5 sends across 7
    fingerprints inside ONE continuous WIP episode). ``fingerprint`` is still
    logged, just not used to gate the notify. ``run_once`` re-arms (clears the
    flag) when the verdict leaves DIVERGED. A ``min_interval_sec`` floor
    throttles even pathological reconcile/diverge flapping. Best-effort: a
    failed send records nothing, so it retries next pass instead of going
    silent."""
    if state.get("divergence_notified"):
        return False
    now = time.time() if now is None else now
    last = state.get("last_divergence_notify_epoch")
    if last is not None and (now - last) < min_interval_sec:
        return False
    if send_fn is None:
        def send_fn(text: str) -> bool:  # lazy import — tests inject instead
            if not OPERATOR_JID:  # no operator configured on this machine
                return False
            sys.path.insert(0, str(CLAUDE_DIR))
            from lib.whatsapp_transport import post
            return bool(post(recipient=OPERATOR_JID, text=text).get("ok"))
    try:
        ok = send_fn(
            "GRIP here — relay self-deploy check: the relay code on disk no "
            "longer matches what's merged on origin/main, so the WhatsApp "
            "drain daemon may be running stale or local code. I won't touch "
            "the working tree (another session may own those edits) — please "
            "reconcile when convenient. Details: logs/relay-mac-selfdeploy.jsonl"
        )
    except Exception:  # noqa: BLE001 — notify must never crash the poller
        ok = False
    if ok:
        state["divergence_notified"] = True
        state["last_divergence_notify_epoch"] = now
    return ok


def run_once() -> str:
    """One poll pass. Returns the verdict acted on (for logging/tests)."""
    fetch = _run(["git", "fetch", "origin", "main", "--quiet"], timeout=120)
    match, fingerprint = worktree_matches_canonical()
    verdict = decide(
        worktree_match=match,
        daemon_start_epoch=daemon_start_epoch(),
        latest_commit_epoch=latest_relay_commit_epoch(),
    )
    state = _load_state()
    if verdict == RESTART:
        if not import_gate():
            # Fail-safe: broken worktree imports — do NOT restart onto them.
            _log({"event": "import_gate_failed", "fingerprint": fingerprint})
            return NOOP
        ok = restart_daemon()
        _log({"event": "daemon_restarted" if ok else "restart_failed",
              "fingerprint": fingerprint})
    elif verdict == DIVERGED:
        sent = notify_divergence(fingerprint, state)
        _log({"event": "divergence", "fingerprint": fingerprint,
              "notified": sent})
    if verdict != DIVERGED and state.get("divergence_notified"):
        state.pop("divergence_notified", None)  # re-arm after reconcile
        # keep last_divergence_notify_epoch as the cross-episode anti-spam floor
    state["last_verdict"] = verdict
    state["fetch_ok"] = fetch.returncode == 0
    _save_state(state)
    return verdict


def main() -> None:
    print(f"[relay-mac-selfdeploy] {run_once()}", flush=True)


if __name__ == "__main__":
    main()
