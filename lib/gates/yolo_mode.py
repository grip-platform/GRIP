"""Yolo mode — unified autonomous loop bypass for /ultrado and /rsi-optimal.

Complements ``hooks/autonomous_mode.py`` with a lighter activation path that
requires no secret key or legal consent phrase — the operator's explicit
invocation of /ultrado or /rsi-optimal is the standing consent. A marker
file under ``~/.claude/state/session/`` carries the active bypass set, TTL,
and activator metadata; the gate consults the marker at fire-time.

The marker is SESSION-SCOPED (GRIP#5740): ``yolo-<sid16>-mode.json``, keyed by
``CLAUDE_CODE_SESSION_ID``. It used to be one global ``yolo-mode.json`` shared
by every concurrent session, so one session's bypass silently applied to every
other. Measured 2026-09-14: a peer armed ``yolo_quality`` and an unrelated
session's edits landed under it while that session's own ``grip-yolo status``
had just printed INACTIVE. That is the same pile-on the AFK marker fixed in
2026-07, and ``bin/grip-afk`` is the naming precedent this follows.

Reads fall back to the legacy global marker, deliberately: a bypass armed
before this change — or by a caller with no session id — must keep working
rather than be revoked mid-edit, which is the inverse half of the same bug.

Design invariants
-----------------

1. The bypass set covers *halting* gates only — gates whose enforcement is
   "prompt a human". Quality, design-principle, and optimisation gates
   (DRY/KISS/BIG-O/CC at PreToolUse + PostToolUse, knowledge-work-scorer,
   JIT correct-by-construction) are NOT in the bypass set. RSI loops rely
   on those to converge to optimal code.

2. Force push, secrets detection, git-crypt corruption guards, and
   confidence-gate-on-external-calls stay on the hard-blocked path.
   YOLO_L2_BYPASS is defined with an empty delta over L1 for this reason:
   there is no yolo-level that allows force push. Force push still needs
   ``GRIP_AUTONOMOUS=<key>:2`` or manual approval.

3. TTL auto-invalidation is mechanical — a marker past its deadline is
   treated as inactive. The next session-start hook sweeps stale markers.

4. Marker parse failures fail-closed. A malformed JSON file means
   ``is_yolo_active() = (False, "")``. A typo cannot accidentally yolo
   the world.

5. Atomic write via tempfile + rename so a crashed activate call never
   leaves a half-parsed marker the gate misreads as valid.

Usage from a skill / entry-point
--------------------------------

```python
from lib.gates.yolo_mode import activate_yolo, deactivate_yolo

marker = activate_yolo("/ultrado", ttl_seconds=14400, reason="Gen 510 sprint")
try:
    run_loop()
finally:
    deactivate_yolo("/ultrado")
```

Usage from a bash entry
-----------------------

```bash
python3 -c "from lib.gates.yolo_mode import activate_yolo; activate_yolo('/ultrado', ttl_seconds=14400)"
# ... loop ...
python3 -c "from lib.gates.yolo_mode import deactivate_yolo; deactivate_yolo('/ultrado')"
```
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# yolo_mode is DUAL-IMPORTED: hooks use `from gates.yolo_mode import ...`
# (sys.path carries lib/), while bin + tests use `from lib.gates.yolo_mode
# import ...` (sys.path carries CLAUDE_DIR). Neither `from lib.claude_dir`
# nor a package-relative import resolves in BOTH contexts. Ensuring lib/ is on
# sys.path makes the top-level `from claude_dir import` work either way — this
# is the canonical path-portability idiom (rules/path-portability.md), not an
# avoidable smell; a "clean" package import would break the 6 hook callers.
_LIB = Path(__file__).resolve().parent.parent  # lib/gates/ -> lib
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
YOLO_MARKER_PATH = CLAUDE_DIR / "state" / "session" / "yolo-mode.json"
YOLO_LOG_PATH = CLAUDE_DIR / "logs" / "yolo-mode.log"


def session_key() -> str:
    """The 16-char session key, or "" when this process has no session id.

    Mirrors ``bin/grip-afk``'s ``_scoped_afk_name`` deliberately: prefer the var
    the harness actually exports to Bash subprocesses, keep the older name as a
    compat fallback, lowercase it, strip to the ``[a-z0-9]`` a path segment may
    safely carry, and take the first 16 (64-bit — collision-negligible for any
    realistic concurrent-session count). An empty or non-conforming id yields
    "", which routes the caller back to the global marker unchanged.
    """
    raw = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
    return "".join(c for c in raw.lower() if c.isascii() and c.isalnum())[:16]


def scoped_marker_path() -> Path:
    """This session's marker path; the global one when there is no session id.

    DERIVED FROM ``YOLO_MARKER_PATH``, not from ``CLAUDE_DIR``, and that is
    load-bearing rather than incidental: three existing test files redirect
    state by monkeypatching that constant, so deriving from it means the scoped
    path follows them into the tmp dir for free. Deriving from CLAUDE_DIR
    instead would leave those tests writing into the real state directory.
    """
    sid = session_key()
    if not sid:
        return YOLO_MARKER_PATH
    return YOLO_MARKER_PATH.parent / f"yolo-{sid}-mode.json"


# Maximum TTL any activator can request. Prevents an infinite yolo.
MAX_TTL_SECONDS = 12 * 3600  # 12 hours

# Bypass set: halting gates only. DO NOT add quality / optimisation /
# correct-by-construction gates here — RSI loops need those to keep firing
# so code auto-optimises through the dual-gate JIT refactoring mechanism.
#
# Sourced from AUTONOMOUS_L1_BYPASS in hooks/autonomous_mode.py plus three
# gates that currently lack bypass IDs (approach-verification,
# pre-check-dispatch, confidence-gate) — wave W3 adds those IDs at the
# enforcement points.
YOLO_L1_BYPASS = frozenset({
    # Existing GRIP_AUTONOMOUS L1 set (mirrored, not inherited, to keep
    # yolo and autonomous-mode defensively independent)
    # reset --hard, checkout --, restore.
    # NOT reflog-recoverable, despite what this comment said until 2026-08-15:
    # the reflog records ref movements, never the working tree. A never-staged
    # edit to a TRACKED file is lost outright; staged content survives only as a
    # dangling blob via `git fsck --lost-found`. Measured in a scratch repo:
    #     never-staged 'UNCOMMITTED WORK' -> GONE (absent from reflog AND fsck)
    #     staged       'STAGED WORK'      -> fsck only, never reflog
    # Real incident 2026-08-10: `git reset --hard HEAD~3` on the shared tree
    # destroyed local edits to ~17 tracked files; the reflog recovered none.
    # The bypass is UNCHANGED -- only this rationale is corrected, so nobody
    # widens it further on a premise that is false. See local task #651.
    "destructive-git-nonforce",
    "git-push-protected",         # push to main/master (yolo-push equivalent, covers PR #2217)
    "code-write",                 # write to code files — core AFK need
    "temp-script",                # create temp scripts (low risk, auto-cleaned)
    "stash-safety",               # cross-branch stash pop (recoverable via stash list)
    "plan-review",                # auto-execute cached plans
    "context-gate-cleanup",       # graceful shutdown ops during context gate
    # "sensitive_file" was HERE until 2026-08-18 and is deliberately absent.
    # It contradicted design invariant 2 above ("secrets detection ... stays on
    # the hard-blocked path"): with a marker armed, the Gate 3 secrets check at
    # lib/gates/bash_orchestrator.py:92 was skipped, so an autonomous loop could
    # stage and commit a dotenv/PEM/private key unchallenged. Measured before
    # removal, driving the real _run_secrets_gate with a live marker: no marker
    # -> DENY "[GUARDRAIL: SECRETS DETECTION]"; marker armed -> None.
    #
    # The removed entry justified itself with "GRIP manages encrypted files via
    # git-crypt (safe to commit)". That premise is FALSE, and falsely in a
    # specific direction worth recording: git-crypt encrypts by PATH, while this
    # gate matches by FILENAME, so the two sets barely intersect. Measured with
    # `git check-attr filter` over every entry in patterns.SENSITIVE_FILE_PATTERNS
    # at a realistic staging path: 0 of 22 resolved to a git-crypt filter
    # (certs/server.pem, id_rsa, credentials.json, .netrc, ... all "unspecified"
    # = committed in PLAINTEXT). Only 3 of 22 were even gitignored, and gitignore
    # is not a confidentiality control -- a forced add (-f) walks past it. The
    # control in the same run (drafts/x.pem, docs/restricted/x.key -> git-crypt)
    # proves the measurement was live rather than a harness returning a
    # confident zero.
    #
    # FALSIFIER: re-run that check-attr sweep. If .gitattributes ever grows
    # name-based rules ("*.pem filter=git-crypt", and so on) covering the
    # pattern set, the original rationale becomes true for the covered subset
    # and a scoped bypass could be argued again -- but only for the patterns the
    # sweep actually shows ENCRYPTED, never for the set as a whole.
    # New halting gates this plan adds bypass IDs for (W3 task)
    "approach-verification",      # AskUserQuestion on fresh approach
    "pre-check-dispatch",         # subagent dispatch clarity check
    "confidence-gate",            # < 99.9999999% confidence blocker
})

# L2 delta over L1 is INTENTIONALLY EMPTY for yolo mode. Force push
# requires GRIP_AUTONOMOUS=<key>:2 with stored secret + consent. There
# is no yolo-level that rewrites remote history. This is the single most
# important safety invariant.
YOLO_L2_BYPASS = YOLO_L1_BYPASS  # intentionally identical

# Quality-only preset: ONLY the two JIT correct-by-construction checks
# (quality-pretool covers DRY/KISS/BIG-O/YAGNI/ISP; the other covers
# cyclomatic complexity). Opt-in only — default activators (/ultrado,
# /rsi-optimal, /sneak, autonomous-rsi-sprint) stay on yolo_l1 so RSI
# loops retain their auto-optimisation feedback.
#
# GRIP#9697 (2026-11-26, V>>'s decision D): this preset is now a
# narrower ALTERNATIVE to the default one, never a superset of it.
# Before this change, activating it under the --quality flag also
# silently relaxed push-to-main, hard reset, and the other halting
# checks the default preset covers -- every quality window was as wide
# as the default one, and neither the help text (bin/grip-yolo) nor the
# docs (rules/efficiency-rules.md, docs/SAFETY-GATES.md) said so. A
# session wanting both kinds of relief now activates the default preset
# instead (one marker holds one preset per session).
#
# Trade-off: enabling this preset lets unrelated quality violations
# anywhere in the touched file ship without correction. Reach for it
# when the noise blocks legitimate edits — e.g. landing a one-line fix
# in a long-standing models.py that already trips KISS on an unrelated
# 80-line method, or applying a vendor patch you can't refactor. Force
# push is still NEVER bypassed.
YOLO_QUALITY_BYPASS = frozenset({
    "quality-pretool",   # DRY/KISS/BIG-O/YAGNI/ISP deny tier
    "cc-gate",           # cyclomatic complexity deny tier
})

# Named preset -> bypass set lookup. Activators choose the preset by name
# so future presets (yolo_docs, yolo_tests, yolo_sandbox) are additive.
#
# GRIP#9697: two other checks have their own ids too -- the channel-scope
# one (lib/channel_scope_gate.py) and the repo-protection one
# (lib/grip_hal_repo_protection.py) -- but those ids are DELIBERATELY
# absent from every preset below. Before this change both libraries asked
# only "is ANY preset active?" (ignoring which one), so turning this on
# for any reason -- including --quality -- silently relaxed them too.
# Both now ask specifically for their own id, so turning any preset here
# on leaves them fully enforced; only a preset that NAMES one of those
# ids on purpose would relax it, and none does today. The channel-scope
# and repo-protection tests each pin this as a structural anchor, the
# same way the main secrets check pins itself absent from every preset.
_PRESETS = {
    "yolo_l1": YOLO_L1_BYPASS,
    "yolo_l2": YOLO_L2_BYPASS,
    "yolo_quality": YOLO_QUALITY_BYPASS,
}


def _now() -> datetime:
    """Wall-clock helper so tests can monkey-patch it."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """RFC3339-ish timestamp with zone."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def _parse_iso(raw: str) -> datetime | None:
    """Parse an RFC3339-ish timestamp; return None on failure."""
    if not raw:
        return None
    try:
        # Python's fromisoformat before 3.11 rejects tz suffixes like +0200
        # without a colon; this repo requires 3.11+ so it's fine.
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _log_yolo(event: str, activator: str, detail: str = "") -> None:
    """Append one tab-separated line to the yolo audit log.

    Best-effort: disk errors are swallowed so a missing log directory or
    permission problem never blocks gate decisions. Missing audit lines
    are a signal that something is wrong with the log path, not a silent
    security bypass.
    """
    try:
        YOLO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = f"{_iso(_now())}\t{event}\t{activator}\t{detail}\n"
        with YOLO_LOG_PATH.open("a") as fp:
            fp.write(line)
    except OSError:
        pass


def _log_yolo_bypass(gate: str, command: str = "") -> None:
    """Record a single BYPASS fire. Called by is_autonomous_bypass when
    the yolo marker is consulted and a gate is allowed through."""
    detail = f"cmd={command.strip()[:160]!r}" if command else ""
    _log_yolo("BYPASS", gate, detail)


def activate_yolo(
    activator: str,
    *,
    ttl_seconds: int = 14400,
    bypass_set: str = "yolo_l1",
    reason: str = "",
) -> Path:
    """Create the yolo marker file and return its path.

    Called by /ultrado, /rsi-optimal, /auto-rsi, autonomous-rsi-sprint,
    and the manual ``grip-yolo`` shell wrapper.

    Args
    ----
    activator: string identifying who flipped the switch. Shows up in
        every audit line for this activation. Examples: ``/ultrado``,
        ``/rsi-optimal``, ``autonomous-rsi-sprint``, ``grip-yolo-manual``.
    ttl_seconds: deadline offset from now. Clamped to MAX_TTL_SECONDS.
    bypass_set: named preset in ``_PRESETS``. Defaults to ``yolo_l1``.
    reason: free-text explanation written to the marker and the log.

    Returns the path to the written marker file. Raises only on filesystem
    errors during the atomic write — callers that need to be yolo-resilient
    should catch ``OSError`` at their entry point and fall back to the
    current manual-approval path.
    """
    ttl_seconds = max(1, min(ttl_seconds, MAX_TTL_SECONDS))
    if bypass_set not in _PRESETS:
        bypass_set = "yolo_l1"

    now = _now()
    deadline = now + timedelta(seconds=ttl_seconds)
    payload = {
        "activator": activator,
        "pid": os.getpid(),
        "started_at": _iso(now),
        "ttl_seconds": ttl_seconds,
        "deadline": _iso(deadline),
        "bypass_set": bypass_set,
        "reason": reason or activator,
        # Recorded so `grip-yolo status` can say WHOSE bypass is active. The
        # marker already carried `pid`, which shows the design anticipated
        # ownership; only the resolution ignored it (GRIP#5740).
        "session": session_key(),
    }

    target = scoped_marker_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic rename: write a temp file in the same dir, then rename over
    # the target. A crash mid-write leaves only the .tmp, which the gate
    # never reads; the target is either the old valid marker or the new
    # valid marker, never a half-written blob.
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(target.parent),
        prefix="yolo-mode.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(payload, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.rename(target)

    _log_yolo("ACTIVATE", activator, f"ttl={ttl_seconds}s deadline={_iso(deadline)}")
    return target


def deactivate_yolo(activator: str = "") -> bool:
    """Remove THIS SESSION's yolo marker. True if one was present.

    Idempotent: calling on an empty state is a no-op and returns False.
    Always logs a DEACTIVATE event so the audit trail shows the end of
    each yolo session even if the marker was already expired by TTL.

    Removes only the caller's own marker (GRIP#5740). A session holding a
    scoped marker never deletes the legacy global one, because that file may be
    another session's in-flight bypass — silently revoking it mid-edit is the
    inverse half of the bug this scoping fixes. A caller with no session id
    owns the global marker and does remove it, unchanged from before.
    """
    target = scoped_marker_path()
    existed = target.exists()
    target.unlink(missing_ok=True)
    _log_yolo("DEACTIVATE", activator or "?", "" if existed else "(no marker)")
    return existed


def _marker_verdict(marker: Path) -> tuple[bool, str]:
    """(active, preset) for ONE marker file. Fail-closed on anything unclear.

    Extracted so liveness can be judged per-file: the sweep needs to ask that
    question of a marker it is not otherwise reading from.
    """
    try:
        data = json.loads(marker.read_text())
    except (json.JSONDecodeError, OSError):
        return (False, "")
    if not isinstance(data, dict):
        return (False, "")

    deadline = _parse_iso(data.get("deadline", ""))
    if deadline is None:
        return (False, "")
    if _now() > deadline:
        _log_yolo("EXPIRED", data.get("activator", "?"), f"deadline={data.get('deadline', '')}")
        return (False, "")

    preset_name = data.get("bypass_set", "yolo_l1")
    if preset_name not in _PRESETS:
        return (False, "")
    return (True, preset_name)


def active_marker_path() -> Path | None:
    """The marker a read actually consults, or None when neither file exists.

    Scoped first, then the legacy global. Exposed because `grip-yolo status`
    previously read ``YOLO_MARKER_PATH`` directly and so could report another
    session's bypass as though it were yours (GRIP#5740).
    """
    scoped = scoped_marker_path()
    if scoped.exists():
        return scoped
    if YOLO_MARKER_PATH.exists():
        return YOLO_MARKER_PATH
    return None


def is_yolo_active() -> tuple[bool, str]:
    """Return (active, bypass_set_name) by consulting the marker file.

    Reads this session's scoped marker first, falling back to the legacy global
    one so a bypass armed before GRIP#5740 — or by a caller with no session id
    — is still honoured rather than silently revoked.

    Fail-closed: any parse error, missing field, or expired deadline
    produces (False, ""). The gate never treats unclear state as permissive.
    """
    marker = active_marker_path()
    if marker is None:
        return (False, "")
    return _marker_verdict(marker)


def is_yolo_bypass(gate: str) -> bool:
    """Return True if the yolo marker is active AND ``gate`` is in the
    active preset's bypass set.

    Convenience wrapper for call sites that already know the gate name.
    Logs a BYPASS audit line as a side effect when the answer is True.
    """
    active, preset_name = is_yolo_active()
    if not active:
        return False
    preset = _PRESETS.get(preset_name, YOLO_L1_BYPASS)
    if gate in preset:
        _log_yolo_bypass(gate)
        return True
    return False


def sweep_expired_markers() -> bool:
    """Delete an expired marker if present. Called by session-start hooks.

    Returns True if any marker was swept. Never touches a still-valid marker —
    use ``deactivate_yolo`` for explicit end-of-loop cleanup.

    Sweeps BOTH this session's scoped marker and the legacy global one. The
    global one needs sweeping here because ``deactivate_yolo`` deliberately
    will not touch it from a scoped session, so a stale pre-GRIP#5740 marker
    would otherwise linger indefinitely. Acting on EXPIRY is safe across
    sessions in a way deletion is not: an expired bypass grants nobody
    anything, so removing it cannot revoke a live one.
    """
    swept = False
    seen = set()
    for marker in (scoped_marker_path(), YOLO_MARKER_PATH):
        if marker in seen or not marker.exists():
            continue
        seen.add(marker)
        if _marker_verdict(marker)[0]:
            continue
        marker.unlink(missing_ok=True)
        _log_yolo("SWEPT", "session-start", f"(expired marker swept: {marker.name})")
        swept = True
    return swept
