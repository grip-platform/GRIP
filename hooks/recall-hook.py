#!/usr/bin/env python3
"""
Recall Hook - UserPromptSubmit Integration

On every substantive user message, semantically match against KONO memories
and inject relevant ones into Claude's context.

INTEGRATION:
    Add to ~/.claude/settings.json:
    {
      "hooks": {
        "UserPromptSubmit": [{
          "hooks": [{
            "type": "command",
            "command": "python3 ~/.claude/hooks/recall-hook.py",
            "timeout": 10,
            "async": true
          }]
        }]
      }
    }

PERFORMANCE:
    - Target: < 2s execution
    - Only processes messages >= 80 chars
    - Skips / commands (they have own recall paths)
    - No spatial expansion (speed over breadth)

VERSION: 1.0.0
DATE: 2026-02-07
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure venv packages are available (KONO deps: apsw, sqlite_vec)
VENV_SITE = Path.home() / ".claude" / "venv" / "lib"
if VENV_SITE.exists():
    for sp in sorted(VENV_SITE.glob("python*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))

# Constants
MIN_MESSAGE_LENGTH = 80
CLAUDE_DIR = Path.home() / ".claude"
DB_PATH = CLAUDE_DIR / "embeddings.db"
CACHE_FILE = CLAUDE_DIR / "state" / "features" / "recall-session-cache.json"
LOG_FILE = CLAUDE_DIR / "logs" / "hooks.log"

RECALL_LIMIT = 3
MIN_FITNESS = 0.1

# Shadow mode. Injection is OFF unless the operator explicitly opts in.
#
# Reading the "prompt" key (see _extract_message) is what finally makes this
# hook fire on a live Claude Code session, and switching that on together with
# injection would put three unevaluated memories in front of every prompt. The
# first few irrelevant ones would spend the [KONO RECALL] block's credibility
# permanently, and credibility is the one thing that cannot be re-earned by a
# later fix. So the recall is computed and written to SHADOW_LOG instead, which
# is the production denominator that has never existed. Nothing reaches
# context until that log says it is worth reaching context.
INJECT_ENV = "GRIP_KONO_RECALL_INJECT"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
SHADOW_LOG = CLAUDE_DIR / "logs" / "kono-recall-shadow.jsonl"


def injection_enabled() -> bool:
    """True only on an explicit opt-in; absent or unrecognised value means OFF."""
    return os.environ.get(INJECT_ENV, "").strip().lower() in _TRUTHY


def _is_commander_session() -> bool:
    """Detect if running inside a grip-channel commander or MCP server session.
    Three-tier check: env vars -> parent command line -> process ancestry."""
    # Tier 1: env vars (fastest, set by commander-pool.ts)
    if os.environ.get("GRIP_GUILD_BOT") == "1":
        return True
    if os.environ.get("CLAUDE_NON_INTERACTIVE"):
        return True
    # Tier 2: parent process command line
    try:
        import subprocess
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=2,
        )
        cmd = result.stdout.strip()
        if "server:grip-channel" in cmd or "--no-session-persistence" in cmd:
            return True
    except Exception:
        pass
    # Tier 3: check any ancestor for grip-channel/PM2 (catches reparented processes)
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=2,
        )
        grandppid = result.stdout.strip()
        if grandppid and grandppid != "1":
            result2 = subprocess.run(
                ["ps", "-o", "command=", "-p", grandppid],
                capture_output=True, text=True, timeout=2,
            )
            gcmd = result2.stdout.strip()
            if "grip-channel" in gcmd or "pm2" in gcmd.lower():
                return True
    except Exception:
        pass
    return False


def should_skip(message: str) -> bool:
    """Check if message should be skipped."""
    # Skip entirely for commander sessions — avoids lembed SIGSEGV from
    # concurrent model loading and unnecessary recall overhead
    if _is_commander_session():
        return True

    stripped = message.strip()

    if len(stripped) < MIN_MESSAGE_LENGTH:
        return True

    # Skip slash commands
    if stripped.startswith("/"):
        return True

    return False


def load_cache() -> dict:
    """Load session-scoped deduplication cache."""
    if not CACHE_FILE.exists():
        return {"session_id": "", "recalled_ids": [], "last_updated": ""}

    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"session_id": "", "recalled_ids": [], "last_updated": ""}


def save_cache(cache: dict) -> None:
    """Save deduplication cache."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def get_session_id() -> str:
    """Derive session ID from environment or fallback."""
    return os.environ.get("CLAUDE_SESSION_ID", "unknown")


def _log(msg: str) -> None:
    """Append a line to the hooks log."""
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[RECALL-HOOK] {msg}\n")
    except OSError:
        pass


def _extract_message(hook_input: dict) -> str:
    """Extract the user message from hook input.

    "prompt" is the key Claude Code actually sends on UserPromptSubmit, the
    same one hooks/precog-orchestrate-hook.py reads. It was absent from this
    tuple, so every live prompt extracted to "" and the hook returned before
    it ever queried KONO. The other three keys are kept for callers that use
    them; a non-string value is ignored rather than crashing should_skip.
    """
    if not isinstance(hook_input, dict):
        return ""
    for key in ("prompt", "message", "content", "user_message"):
        value = hook_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _invoke_worker(message: str, shadow: bool) -> dict | None:
    """Run recall in a subprocess to isolate lembed SIGSEGV crashes.

    Returns the worker's structured payload, or None when it produced nothing
    usable. Output that cannot be parsed as the expected envelope returns None
    rather than being treated as text, so a worker/hook version mismatch can
    never inject an unrecognised blob into context.
    """
    worker_input = json.dumps({
        "message": message,
        "limit": RECALL_LIMIT,
        "min_fitness": MIN_FITNESS,
        "cache_file": str(CACHE_FILE),
        "db_path": str(DB_PATH),
        "session_id": get_session_id(),
        "shadow": shadow,
    })
    # run_text, not subprocess.run(text=True): the worker's JSON envelope is
    # emitted with ensure_ascii=False, so recalled memory text crosses this pipe
    # as raw non-ASCII and reaches Claude's context verbatim once injection is
    # on. A cp1252 read would put mojibake in the memories themselves.
    _lib = str(CLAUDE_DIR / "lib")
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    from _compat import run_text
    result = run_text(
        [sys.executable, str(CLAUDE_DIR / "hooks" / "recall-worker.py")],
        input=worker_input,
        capture_output=True, timeout=8,
    )
    if result.returncode < 0:
        _log(f"Worker killed by signal {-result.returncode} (SIGSEGV isolated)")
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        _log("Worker returned non-JSON output; injecting nothing")
        return None
    return payload if isinstance(payload, dict) else None


def build_shadow_record(message: str, payload: dict, injected: bool) -> dict:
    """One observation of what this prompt would have recalled.

    Carries no prompt content and no memory content -- only the length of the
    prompt and the per-result numbers, because the log is a denominator, not a
    transcript. ``distance`` is whatever the substrate supplied and is commonly
    null: the hook queries with expand_spatial=False, which takes the hybrid
    search path, and only the spatial retrieve() path sets a distance. ``score``
    is the nearness axis that does exist there, so both are recorded and
    neither is invented.
    """
    results = payload.get("results") or []
    fitness = [r.get("fitness") for r in results]
    above = [f for f in fitness if isinstance(f, (int, float)) and f >= MIN_FITNESS]
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt_chars": len(message),
        "n_results": len(results),
        "n_raw": payload.get("n_raw"),
        "deduped": bool(payload.get("deduped")),
        "ids": [r.get("id") for r in results],
        "fitness": fitness,
        "distance": [r.get("distance") for r in results],
        "score": [r.get("score") for r in results],
        "floor": MIN_FITNESS,
        "n_above_floor": len(above),
        "cleared_floor": bool(above),
        "injected": injected,
    }


def append_shadow_record(record: dict) -> None:
    """Append one JSON line. A log that cannot be written must not break a prompt."""
    try:
        SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SHADOW_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    """Process user message and recall relevant KONO memories."""
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    message = _extract_message(hook_input)
    if not message or should_skip(message) or not DB_PATH.exists():
        sys.exit(0)

    inject = injection_enabled()
    try:
        payload = _invoke_worker(message, shadow=not inject)
    except Exception as e:
        _log(f"Error: {e}")
        sys.exit(0)

    if payload is None:
        sys.exit(0)

    append_shadow_record(build_shadow_record(message, payload, injected=inject))

    if inject:
        text = (payload.get("text") or "").strip()
        if text:
            print(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
