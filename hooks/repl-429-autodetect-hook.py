#!/usr/bin/env python3
"""repl-429-autodetect-hook — arm auth-bucket failover on the interactive REPL's OWN 429s.

The gap this closes (rate-limit-never-blocks.md "HONEST LIMIT"): GRIP hooks fire on tool
calls / turn boundaries, NEVER on the interactive REPL's inference — so a transient Anthropic
429 ("Server is temporarily limiting requests · Rate limited") that throttles the live
session is invisible at the instant it happens, and `grip-auth-failover` never auto-arms
(its `--note-429` counter only ever saw GRIP's OWN LLM calls, not the harness's).

BUT the harness PERSISTS every API error to the session transcript as an
`isApiErrorMessage:true` record. This hook reads that transcript on Stop / UserPromptSubmit,
finds FRESH transient-429 records, and feeds `grip-auth-failover --note-429` (which owns the
windowed N=3 auto-engage). A usage-cap record routes to `--note-cap` instead, with its text,
from which the tool reads the reset time ("You've hit your session limit · resets 7:20am
(UTC)"); by default the tool then notifies and waits rather than engaging the paid key
(GRIP#7274). Any other api-error ("Not logged in", …) is ignored. When the harness recorded
an HTTP status, only a 429 can feed `--note-429`: a 5xx or network failure reaches both
buckets alike (GRIP#7274).
A record saying ONE model's allowance is used up ("You're out of usage credits ... keep using
Fable 5.1", with or without "or /model to switch models") routes to `--note-model-cap` at any
status, which only notifies: every other model still runs on the subscription, so it must
never count toward engaging the paid key (GRIP#7274).

The reverse signal: while an engagement marker exists and this session bills the
subscription (no ANTHROPIC_API_KEY in its environment), a newest turn that a real model
answered feeds `grip-auth-failover --note-ok`, which ends an AUTOMATIC engagement once the
last throttle is old enough. Without it an engagement lasted until someone ran --reset.

Honest scope (same as grip-auth-failover): this CANNOT rescue the running REPL — the harness
owns the live session's auth. It ARMS the marker so the NEXT `claude` spawn rides the
separate, unthrottled API-key bucket. The value is preventive: an intermittently-throttled
session auto-arms BEFORE a hard stall, so the eventual restart is already protected.

CRITICAL correctness guard: a record only counts if `isApiErrorMessage` is true. The mere
TEXT "temporarily limiting requests" appears in ordinary messages (this very docstring, the
rule file, an operator quoting the error) — matching text alone would false-arm on the
conversation ABOUT a 429. The `isApiErrorMessage` flag is what distinguishes a real harness
error record from prose.

Fail-open: any error is a silent no-op (exit 0). This hook never blocks a turn.
"""
from __future__ import annotations

import json
import os
import contextlib
import re
import subprocess
import sys
from pathlib import Path

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
_WATCH_DIR = _CLAUDE_DIR / "state" / "session"
# Overridable for tests; defaults to the real shim.
_AUTH_FAILOVER = Path(os.environ.get("GRIP_AUTH_FAILOVER_BIN")
                      or (_CLAUDE_DIR / "bin" / "grip-auth-failover"))
# The engagement marker, resolved exactly as lib/relay_dispatcher.py resolves it.
_MARKER = Path(os.environ.get("GRIP_AUTH_FAILOVER_MARKER")
               or (_WATCH_DIR / "auth-failover.json"))
_FAILOVER_ARGS = {
    "transient": ["--note-429", "interactive REPL transient 429 auto-detected from transcript"],
    "cap": ["--note-cap"],  # + the error text, so the tool can read the reset time
    "model-cap": ["--note-model-cap"],  # + the error text, so the notice can name the model
    "ok": ["--note-ok", "interactive REPL turn served by the subscription"],
}

# The transient-vs-usage-cap discrimination lives in ONE tested pure-stdlib module
# (lib/rate_limit_classifier.py), the L1 floor for rate-limit-never-blocks.md — the same
# function grip-auth-failover uses. Folding it in here (was: three inline regexes) upgrades
# coverage for free: it also catches "weekly limit", "credit balance is too low", and reset /
# Retry-After hints the old regexes mis-ignored. Resolve lib/ off THIS FILE (hooks/ sibling)
# so it works from the main tree or a worktree and regardless of a test-overridden CLAUDE_DIR;
# CLAUDE_DIR/lib is a secondary path for out-of-tree installs.
for _cand in (Path(__file__).resolve().parent.parent / "lib", _CLAUDE_DIR / "lib"):
    _p = str(_cand)
    if _p not in sys.path:
        sys.path.insert(0, _p)
# Import the kind CONSTANT (and its expected value) rather than hard-coding the "transient"
# literal, and ASSERT the value at module load. This is the actual drift guard: an import alone
# only fails if TRANSIENT is *deleted*, NOT if its value changes (AGORA #4019 Speaker C — a
# correct catch: a rename `TRANSIENT = "foobar"` imports fine). The assert breaks LOUDLY at load
# the moment the classifier's transient value diverges from what this hook's _feed_failover
# contract ("transient" -> --note-429) depends on. The cap branch uses the classifier's own
# `Verdict.is_cap` property, so it needs no literal at all.
from rate_limit_classifier import TRANSIENT, classify  # noqa: E402
from _compat import fcntl as _fcntl  # noqa: E402  -- POSIX fcntl, or the LockFileEx lock on Windows
from _compat import grip_python  # noqa: E402  -- the interpreter that starts the failover tool

# Load-time contract: the hook's fire-kind mapping and _feed_failover both assume the transient
# kind is exactly "transient". If the classifier ever changes that value, fail HERE (loud,
# immediate) instead of silently mis-routing a transient 429. Fail-open still applies at the
# call boundary (main() wraps process() in try/except → exit 0), so a broken contract disables
# the hook rather than breaking the turn.
assert TRANSIENT == "transient", (
    f"rate_limit_classifier.TRANSIENT changed to {TRANSIENT!r}; "
    "update this hook's fire-kind contract (_classify / _feed_failover)."
)

_MAX_TAIL_BYTES = 1_000_000  # bounded tail read (~2000 records). A fresh 429 is APPENDED at the
#                              end of the transcript, so it always lands well inside this window;
#                              records older than the window are out of scope by design (a stale
#                              429 is not the one to arm on, and unbounded reads are not free).


def _error_text(rec: dict) -> str:
    """The human-readable error text from an api-error record's message.content.

    The harness writes api-errors as text parts; a json.dumps fallback covers any VARIANT
    structure (a non-text content shape) so a differently-shaped error cannot slip past the
    classifier. This only ever WIDENS coverage on records already gated by isApiErrorMessage —
    it cannot false-arm on ordinary messages (those never reach here)."""
    msg = rec.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = " ".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and isinstance(p.get("text"), str))
        return texts if texts.strip() else json.dumps(content)
    if content is not None:
        return json.dumps(content)
    return ""


def _classify(text: str) -> str:
    """Return 'transient' | 'cap' | 'model-cap' | '' (ignore), via the shared classifier.

    Maps the classifier's Verdict to this hook's fire-kind contract:
      * a usage cap                 -> 'cap'   (feed --note-cap)
      * one model's allowance used  -> 'model-cap' (feed --note-model-cap: notify only)
      * a CONFIDENT transient       -> 'transient' (feed --note-429)
      * anything else (UNKNOWN)     -> ''      (ignore, no failover)

    UNKNOWN maps to ignore ON PURPOSE: the caller only reaches here for records already
    gated by isApiErrorMessage:true, so a non-429 error record ("Not logged in", a 500) that
    carries NO rate-limit signal must NOT arm the bucket — the exact behaviour of the old
    inline regexes. Only a decisive cap/transient phrase fires. 'not your usage limit' still
    forces transient (the classifier's cap-negation precedence)."""
    v = classify(text)
    if v.is_cap:                       # the classifier's own property — drift-proof
        return "cap"
    if v.is_model_cap:                 # likewise; is_cap is False for one model's limit
        return "model-cap"
    if v.kind == TRANSIENT:            # imported constant, not a copied literal — cannot drift
        return "transient"
    return ""


def _status_code(rec: dict) -> int | None:
    """The HTTP status the harness recorded for an api-error record, or None."""
    try:
        return int(rec["apiErrorStatus"])
    except (KeyError, TypeError, ValueError):
        return None


def _fire_kind(rec: dict) -> str:
    """Which failover call an api-error record warrants: 'transient', 'cap', 'model-cap' or ''.

    Wording alone over-fires. Claude Code appends "try again in a moment" to every
    server-side error, so on 2026-10-03 a gateway 502 for a DNS failure ("upstream
    unreachable") classified as transient, and outage records armed the failover
    (GRIP#7274). The API-key bucket reaches the same host, so it cannot route around
    an outage. When the harness recorded a status, the status decides: only a 429 is
    a throttle and may feed --note-429. A usage cap still routes by its wording at any
    status, because "credit balance is too low" arrives as a 400. One model's allowance
    running out routes by its wording at any status too: at 429 it used to fall through
    to 'transient' and count toward the three-in-five-minutes threshold that moves EVERY
    model onto the paid key, though the other models still ran on the subscription. A
    record with no status keeps the wording-only behaviour.
    """
    kind = _classify(_error_text(rec))
    status = _status_code(rec)
    if status is None or kind in ("cap", "model-cap"):
        return kind
    return "transient" if status == 429 else ""


def _subscription_billed() -> bool:
    """True when this session bills the subscription, judged by its environment.

    A hook inherits the harness's environment. A session started while the failover
    was engaged carries ANTHROPIC_API_KEY (~/.zshrc exports it), so its replies prove
    the API key serves, not the subscription, and must not end an engagement.
    """
    return not os.environ.get("ANTHROPIC_API_KEY")


def _newest_reply_is_real(lines: list, last_ts: str) -> bool:
    """True when the newest fresh assistant record is a real model reply.

    Walks backwards and stops at the first assistant record, so a long transcript
    costs one parse. An api-error record, a synthetic one, or nothing newer than the
    watermark all answer False.
    """
    for line in reversed(lines):
        if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        model = str((rec.get("message") or {}).get("model") or "")
        fresh = str(rec.get("timestamp", "")) > last_ts
        return fresh and rec.get("isApiErrorMessage") is not True and model[:1] not in ("", "<")
    return False


def _watch_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return _WATCH_DIR / f"repl-429-watch-{safe}.json"


def _load_watermark(p: Path) -> str:
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("last_ts", ""))
    except Exception:
        return ""


def _save_watermark(p: Path, ts: str) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"last_ts": ts}), encoding="utf-8")
        tmp.replace(p)  # POSIX-atomic
    except Exception:
        pass


def _tail_lines(path: Path) -> list:
    """Read only the last _MAX_TAIL_BYTES of the transcript; drop a possibly-partial head line."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - _MAX_TAIL_BYTES))
        data = fh.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > _MAX_TAIL_BYTES else lines


def _feed_failover(kind: str, text: str = "") -> None:
    if not _AUTH_FAILOVER.exists():
        return
    tool_args = [str(_AUTH_FAILOVER), *_FAILOVER_ARGS[kind]]  # an unknown kind fails loud
    if kind in ("cap", "model-cap"):
        tool_args.append(text)  # the error text names the cap's reset time, or the spent model
    try:
        # Started through the GRIP interpreter, never by path: Windows cannot start a script
        # that has no .exe (WinError 193), and never-system-python-for-grip.md asks every
        # spawn site to name its interpreter (GRIP#8806). Resolving it reads the venv
        # folder, which can raise, so it sits inside the try with the start.
        subprocess.run([grip_python(_CLAUDE_DIR), *tool_args],
                       capture_output=True, timeout=5, check=False)
    except Exception:
        pass


@contextlib.contextmanager
def _watch_lock(wp: Path):
    """Serialise the read-watermark -> classify -> feed -> write-watermark critical section
    across CONCURRENT hook invocations for one session (a Stop and a UserPromptSubmit can fire
    close together). Without it both could read the same watermark, see the same fresh 429, and
    each feed --note-429 — double-counting one event toward the N=3 threshold. flock from
    _compat on a per-session lock file: POSIX fcntl, or the LockFileEx lock on Windows, where
    the two hooks can fire together too (GRIP#8757).
    Fail-open: if the lock cannot be taken, proceed unlocked rather than block the turn."""
    try:
        wp.parent.mkdir(parents=True, exist_ok=True)
        fh = open(wp.with_name(wp.name + ".lock"), "w", encoding="utf-8")
    except Exception:
        yield
        return
    try:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
        yield
    finally:
        try:
            fh.close()  # releases the advisory lock
        except Exception:
            pass


def process(transcript_path: str, session_id: str) -> str | None:
    """Scan fresh api-error records, feed the failover shim, advance the watermark.

    Returns the last kind fired ('transient'/'cap'/'model-cap') or None. Pure except for the shim call and
    the watermark write — unit-testable by pointing GRIP_AUTH_FAILOVER_BIN at a Python stub. The
    critical section is flock-serialised so concurrent hook fires cannot double-count one 429."""
    tp = Path(transcript_path)
    if not tp.exists():
        return None
    wp = _watch_path(session_id)
    with _watch_lock(wp):
        return _scan_locked(tp, wp)


def _scan_locked(tp: Path, wp: Path) -> str | None:
    last_ts = _load_watermark(wp)
    newest, fired = last_ts, None
    try:
        lines = _tail_lines(tp)
    except Exception:
        return None
    for line in lines:
        if '"isApiErrorMessage":true' not in line and '"isApiErrorMessage": true' not in line:
            continue  # MUST be a real error record, not prose that mentions the error text
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("isApiErrorMessage") is not True:
            continue
        ts = str(rec.get("timestamp", ""))
        if not ts or ts <= last_ts:  # ISO-8601 with 'Z' sorts lexicographically
            continue
        kind = _fire_kind(rec)
        if kind:
            _feed_failover(kind, _error_text(rec))
            fired = kind
        if ts > newest:
            newest = ts
    if newest != last_ts:
        _save_watermark(wp, newest)
    _note_recovery(lines, last_ts)
    return fired


def _note_recovery(lines: list, last_ts: str) -> None:
    """Tell the failover the subscription served a turn (GRIP#7274).

    Only worth a subprocess while engaged, and only from a session that bills the
    subscription. The failover decides whether the throttle is old enough to end
    the engagement.
    """
    if _MARKER.exists() and _subscription_billed() and _newest_reply_is_real(lines, last_ts):
        _feed_failover("ok")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)
    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id", "")
    if not transcript_path:
        sys.exit(0)
    try:
        fired = process(transcript_path, session_id)
    except Exception:
        sys.exit(0)
    if fired == "model-cap":
        print("[repl-429-autodetect] one model's allowance is used up → grip-auth-failover "
              "notified (this does not arm the API-key bucket; /model switches model)",
              file=sys.stderr)
    elif fired == "cap":
        print("[repl-429-autodetect] usage cap detected in transcript → grip-auth-failover "
              "fed (by default it notifies with the reset time and waits; it arms the "
              "API-key bucket only under the cap policy \"engage\")", file=sys.stderr)
    elif fired:
        print(f"[repl-429-autodetect] {fired} 429 detected in transcript → grip-auth-failover fed "
              f"(arms the API-key bucket for the next spawn; cannot re-auth this live session)",
              file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
