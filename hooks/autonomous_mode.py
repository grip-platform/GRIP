#!/usr/bin/env python3
"""
Autonomous Mode - AFK execution bypass for GRIP.

Extracted from context-gate-hook.py (TODO #10) for SRP compliance.
Imported by context-gate-hook.py at module level (hooks/ directory co-location).
Provides key validation, consent checking, and gate bypass logic
for unattended autonomous execution via GRIP_AUTONOMOUS env var.

Security model:
  - Secret stored in .autonomous-state.json (chmod 600, gitignored, local-only)
  - Machine compromise = game over regardless; file perms are sufficient
  - Constant-time comparison via hmac.compare_digest (timing attack safe)
  - Legal consent required before first activation (one-time acknowledgment)
  - Env var scoping = natural session isolation (no cross-terminal leak)

Level 1: Productive autonomy (push, write code, create PRs, stash ops)
Level 2: Full autonomy (also deploy, force push)
Safety floor NEVER bypassed at any level: Gates 1,3,A,B,D,F,G,5,6,9
  -- Gate 3 (secrets detection) included: no level bypasses it. The
     "sensitive_file" entry that used to break this promise was
     removed 2026-08-18; see the note in AUTONOMOUS_L1_BYPASS.
Requires: valid secret key + legal consent acknowledgment
"""

import hmac
import json
import os
import tempfile
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"
AUTONOMOUS_STATE_FILE = CLAUDE_DIR / "state" / "session" / "autonomous-state.json"

AUTONOMOUS_L1_BYPASS = {
    # L1: reset --hard, checkout --, restore.
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
    "git-push-protected",        # L1: push to main/master (GitHub PR review still required)
    "code-write",                # L1: write to code files (core AFK need -- agent must edit code)
    "temp-script",               # L1: create temp scripts (low risk, auto-cleaned)
    "stash-safety",              # L1: cross-branch stash pop (recoverable via stash list)
    "plan-review",               # L1: auto-execute cached plans (AFK needs unattended plan execution)
    "context-gate-cleanup",      # L1: graceful shutdown ops during context gate (2min window)
    # "sensitive_file" was HERE until 2026-08-18 and is deliberately absent.
    # It contradicted this module's own docstring ("Safety floor NEVER bypassed
    # at any level"): Gate 3 at lib/gates/bash_orchestrator.py:92 skips the
    # secrets check whenever is_autonomous_bypass("sensitive_file") is true, so
    # an L1 autonomous run could stage and commit a dotenv/PEM/private key.
    #
    # Its stated rationale was "GRIP manages encrypted files via git-crypt (safe
    # to commit)". MEASURED FALSE: git-crypt encrypts by PATH (.gitattributes
    # globs such as drafts/**, facts/*.md), whereas this gate matches by
    # FILENAME. Running `git check-attr filter` over every entry in
    # patterns.SENSITIVE_FILE_PATTERNS at a realistic staging path returned 0
    # of 22 encrypted -- certs/server.pem, id_rsa, credentials.json, .netrc and
    # the rest all resolve "unspecified", i.e. committed in PLAINTEXT. The same
    # sweep's control paths (drafts/x.pem, docs/restricted/x.key) did resolve to
    # git-crypt, so the zero is a real result and not a broken probe.
    #
    # FALSIFIER: re-run that sweep. Should .gitattributes gain name-based rules
    # covering these patterns, the rationale becomes true for whatever subset
    # the sweep reports ENCRYPTED -- and a bypass may then be argued for that
    # subset alone, never for the whole set. See lib/gates/yolo_mode.py for the
    # mirrored removal; the two sets are deliberately kept in sync.
}

AUTONOMOUS_L2_BYPASS = AUTONOMOUS_L1_BYPASS | {
    "destructive-git-force",     # L2 ONLY: force push rewrites remote history (harder to recover)
    "deploy",                    # L2 ONLY: production deployment (external user-facing impact)
}

AUTONOMOUS_GATES = {1: AUTONOMOUS_L1_BYPASS, 2: AUTONOMOUS_L2_BYPASS}

# Explicit consent phrase -- exact match required (not fuzzy keyword detection)
AUTONOMOUS_CONSENT_PHRASE = "i accept autonomous mode responsibility"


def _load_autonomous_state() -> dict:
    """Load consolidated autonomous state from single JSON file."""
    if AUTONOMOUS_STATE_FILE.exists():
        try:
            return json.loads(AUTONOMOUS_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_autonomous_state(state: dict):
    """Atomic write to autonomous state file (chmod 600).

    Uses tempfile + rename pattern for crash safety.
    """
    dir_path = AUTONOMOUS_STATE_FILE.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w', dir=str(dir_path), suffix='.tmp', delete=False
    ) as tmp:
        json.dump(state, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.rename(AUTONOMOUS_STATE_FILE)
    os.chmod(str(AUTONOMOUS_STATE_FILE), 0o600)


def _ensure_autonomous_secret() -> str:
    """Auto-generate secret key if not exists. Returns the secret."""
    state = _load_autonomous_state()
    if state.get("secret"):
        return state["secret"]

    import secrets as _secrets
    key = f"grip-auto-{_secrets.token_hex(8)}"
    state["secret"] = key
    _save_autonomous_state(state)
    return key


def _parse_autonomous_value(value: str) -> tuple:
    """Parse GRIP_AUTONOMOUS value into (key, level).

    Formats: <key> -> level 1, <key>:2 -> level 2
    Malformed level string returns level 0 (safe default).
    """
    if not value:
        return ("", 0)
    if ":" in value:
        key, level_str = value.rsplit(":", 1)
        try:
            return (key, min(int(level_str), 2))
        except ValueError:
            # Malformed level -- return 0 (disabled), not 1
            return (key, 0)
    return (value, 1)


def _validate_autonomous_key(provided_key: str) -> bool:
    """Validate provided key against stored secret (constant-time comparison)."""
    if not provided_key:
        return False
    secret = _ensure_autonomous_secret()
    return hmac.compare_digest(provided_key.encode(), secret.encode())


def _check_autonomous_consent() -> bool:
    """Check if user has acknowledged autonomous mode legal caution."""
    state = _load_autonomous_state()
    return bool(state.get("consent_ts"))


def get_autonomous_level(env_value: str = "") -> int:
    """Get autonomous mode level (0 = disabled).

    Requires BOTH valid secret key AND consent acknowledgment.

    Args:
        env_value: The GRIP_AUTONOMOUS env var value. If empty, reads from os.environ.
    """
    value = env_value or os.environ.get("GRIP_AUTONOMOUS", "")
    if not value:
        return 0

    provided_key, level = _parse_autonomous_value(value)

    if not _validate_autonomous_key(provided_key):
        return 0

    if not _check_autonomous_consent():
        return 0

    return level


def is_autonomous_bypass(gate: str, level: int = 0) -> bool:
    """Check if autonomous mode bypasses this gate.

    Consults two independent bypass mechanisms in priority order:

    1. **GRIP_AUTONOMOUS** — the heavy path. Requires a stored secret key
       in ``state/session/autonomous-state.json`` + legal consent phrase.
       L1 covers productive autonomy (push, write code, stash ops). L2 adds
       force push and deploy. Used for long unattended AFK runs.

    2. **yolo marker** — the light path introduced for /ultrado and
       /rsi-optimal. Requires no secret or consent (the explicit slash
       command invocation IS the consent), activated by a file marker
       at ``state/session/yolo-mode.json`` with TTL auto-invalidation.
       Bypass set is a subset of L1 and NEVER includes force push.
       See ``lib/gates/yolo_mode.py`` for the full design.

    Either mechanism active for a given gate returns True. Both being
    inactive returns False. This preserves the existing GRIP_AUTONOMOUS
    contract while adding yolo as an orthogonal additional path.

    Args:
        gate: The gate name to check bypass for.
        level: Pre-computed autonomous level. If 0, computes from env.
    """
    # Path 1: GRIP_AUTONOMOUS (existing heavy path)
    if level == 0:
        level = get_autonomous_level()
    if level and gate in AUTONOMOUS_GATES.get(level, set()):
        return True

    # Path 2: yolo marker (new light path for /ultrado, /rsi-optimal)
    # Late-import to avoid pulling gates at module load time, since
    # this hook is imported by context-gate-hook at module top and we
    # want to keep that import tree fast and acyclic. Try both sys.path
    # roots so the function works from hooks (which put ~/.claude/lib on
    # sys.path) and from tests (conftest also puts lib/ on sys.path).
    try:
        from gates.yolo_mode import is_yolo_bypass
    except ImportError:
        try:
            from lib.gates.yolo_mode import is_yolo_bypass  # type: ignore[no-redef]
        except ImportError:
            return False
    return is_yolo_bypass(gate)
