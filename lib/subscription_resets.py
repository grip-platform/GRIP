"""Subscription windows, reset availability and guarded automatic spending.

Only the small, explicit display schema leaves the app-server response.
Authentication material, account identifiers and credit identifiers are never
copied into the cache.

Automatic spending is on by default: create state/codex-auto-reset.off or set
GRIP_CODEX_AUTO_RESET=0 to stop it. Defaults: four attempts per rolling 24 hours;
avoid a seat resetting within 15 minutes when another qualifies. The published
decision leaves contain only the fields in RESET_LEAF_FIELDS.

A hand-run ``spend --auto`` does not check workspace pins. The ladder skips
spending if a pin mismatches. If any seat's limits cannot be read, automatic
spending stops without using a credit. A seat whose login the server rejects
with HTTP 401 is signed out instead: it cannot serve, so it holds no room for a
credit to protect, and it does not stop the spending (GRIP#9071).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows (GRIP#8757)
from claude_dir import get_claude_dir
from statusline_limits import _default_hal_ledger, _read_json_quiet, hal_ledger_windows
from infinite_cycle_seal import PRODUCT_CUSTODY, _demo_seal_refused, fingerprint

CACHE_AGE = 600
FETCH_TIMEOUT = 30


def _window(raw):
    if not isinstance(raw, dict):
        return None
    used = raw.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return None
    reset = raw.get("resetsAt")
    duration = raw.get("windowDurationMins")
    return {"used_percent": used,
            "resets_at": int(reset) if isinstance(reset, (int, float)) and not isinstance(reset, bool) else None,
            "duration_mins": int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None}


def _reset_credits(result):
    """(count held, nearest expiry) from the reset-credit block."""
    info = result.get("rateLimitResetCredits")
    info = info if isinstance(info, dict) else {}
    count = info.get("availableCount", 0)
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0
    rows = info.get("credits")
    rows = rows if isinstance(rows, list) else []
    expiries = [row["expiresAt"] for row in rows if isinstance(row, dict)
                and row.get("status") == "available" and isinstance(row.get("expiresAt"), int)
                and not isinstance(row["expiresAt"], bool)]
    return count, (min(expiries) if expiries else None)


def parse_rate_limits(result) -> dict:
    """Parse a single app-server result into safe display fields."""
    if not isinstance(result, dict):
        raise ValueError("invalid rate-limit result")
    limits = result.get("rateLimits")
    if not isinstance(limits, dict):
        raise ValueError("missing rate limits")
    windows = {"five_hour": _window(limits.get("primary")),
               "weekly": _window(limits.get("secondary"))}
    count, nearest = _reset_credits(result)
    credits = limits.get("credits")
    credits = credits if isinstance(credits, dict) else {}
    balance = str(credits.get("balance", ""))
    balance = balance if re.fullmatch(r"\d+(?:\.\d+)?", balance) else ""
    plan = str(limits.get("planType") or "unknown")
    plan = plan if re.fullmatch(r"[A-Za-z0-9_-]+", plan) else "unknown"
    full = result.get("ordinaryUsageAllowed") is False or any(
        window and window["used_percent"] >= 100 for window in windows.values())
    return {"windows": windows, "resets_available": count, "nearest_expiry": nearest,
            "has_credits": credits.get("hasCredits") is True,
            "credits_balance": balance, "plan": plan,
            "state": "reset_available" if full and count else "full" if full else "ready"}


def _safe_label(label, fallback):
    value = str(label or "").strip()
    if not value or "@" in value or any(ord(char) < 32 for char in value):
        return fallback
    return value[:48]


_AUTH_CODE = re.compile(r'"code":\s*"([a-z_]{1,48})"')


class SeatSignedOut(Exception):
    """The server rejected this seat's login with HTTP 401 (GRIP#9071).

    The seat cannot serve until someone signs it in again. ``code`` is the
    server's reason, such as ``token_revoked``. It holds only lower-case letters
    and underscores, so no account detail can reach the cache or the screen.
    """

    def __init__(self, code="unauthorized"):
        super().__init__(code)
        self.code = code


def _reply_error(error):
    """The exception for an app-server error reply: SeatSignedOut for a 401."""
    message = str(error.get("message", "")) if isinstance(error, dict) else ""
    if re.search(r"\b401\b", message) and "unauthorized" in message.lower():
        match = _AUTH_CODE.search(message)
        return SeatSignedOut(match.group(1) if match else "unauthorized")
    return ValueError("app-server returned no rate limits")


def _reply_from(lines):
    """The app-server's id-2 result among complete JSON lines, or None."""
    for line in lines:
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            if not isinstance(message.get("result"), dict):
                raise _reply_error(message.get("error"))
            return message["result"]
    return None


def _stop(process, deadline):
    process.stdin.close()
    process.terminate()
    try:
        process.wait(timeout=max(0, min(1, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    process.stdout.close()


def _read_app_reply(process, deadline):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = b""
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            *lines, pending = (pending + chunk).split(b"\n")
            reply = _reply_from(lines)
            if reply is not None:
                return reply
    raise TimeoutError("app-server response unavailable")


def _app_request(home, method, params):
    """Use the existing one-shot app-server client for one request."""
    env = dict(os.environ, CODEX_HOME=str(Path(home).expanduser()), OPENAI_API_KEY="", CODEX_API_KEY="")
    command = [os.environ.get("GRIP_CODEX_BIN", "codex"), "app-server"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env)
    deadline = time.monotonic() + FETCH_TIMEOUT
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "grip-resets", "version": "1"}}},
        {"method": "initialized"},
        {"id": 2, "method": method, "params": params},
    ]
    try:
        process.stdin.write(("\n".join(json.dumps(item) for item in requests) + "\n").encode())
        process.stdin.flush()
        return _read_app_reply(process, deadline)
    finally:
        _stop(process, deadline)


def fetch_rate_limits(home):
    """Read one seat through the shared app-server client."""
    return _app_request(home, "account/rateLimits/read", {})


def consume_reset(home, key):
    """Spend one credit; the backend chooses which credit."""
    return _app_request(home, "account/rateLimitResetCredit/consume", {"idempotencyKey": key})


_UNKNOWN_SEAT = {"windows": {"five_hour": None, "weekly": None},
                 "resets_available": None, "nearest_expiry": None,
                 "has_credits": False, "credits_balance": "",
                 "plan": "unknown", "state": "unknown"}


def collect(registry_rows, fetch=None) -> dict:
    """Collect each Codex seat independently. A failed read stays unknown. A seat
    the server has signed out is the unknown seat with ``state`` set to
    ``"signed_out"`` and ``auth_error`` set to the server's reason, such as
    ``"token_revoked"``, which holds only lower-case letters and underscores."""
    fetch = fetch or fetch_rate_limits
    seats = []
    for number, row in enumerate(registry_rows, 1):
        label = _safe_label(row.get("label"), f"Codex {number}")
        try:
            parsed = parse_rate_limits(fetch(row["codex_home"]))
        except SeatSignedOut as exc:
            parsed = {**_UNKNOWN_SEAT, "state": "signed_out", "auth_error": exc.code}
        except Exception:
            parsed = dict(_UNKNOWN_SEAT)
        seats.append({"label": label, "provider": "Codex", **parsed})
    return {"updated_at": int(time.time()), "seats": seats}


def read_accounts(path=None):
    """Read the ladder registry, falling back to the default Codex home."""
    path = Path(path or os.environ.get("GRIP_CODEX_ACCOUNTS") or
                get_claude_dir() / "config" / "codex-accounts.json").expanduser()
    try:
        accounts = json.loads(path.read_text())["accounts"]
        rows = [{"slug": str(row["slug"]).strip(),
                 "codex_home": str(Path(str(row["codex_home"])).expanduser()),
                 "label": str(row.get("label", row["slug"]))}
                for row in accounts if row.get("slug") and row.get("codex_home")]
        if rows:
            return rows
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return [{"slug": "default", "codex_home": str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()),
             "label": "default"}]


RESET_LEAF_FIELDS = ("event", "ts", "position", "windows",
                     "credits", "nearest_expiry", "outcome", "key_digest", "decision_id")
RESET_JITTER = 120
RESET_LEDGER = "codex-auto-reset-ledger.json"
RESET_OFF = "codex-auto-reset.off"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _same_window(a, b):
    return (isinstance(a, (int, float)) and not isinstance(a, bool)
            and isinstance(b, (int, float)) and not isinstance(b, bool)
            and abs(a - b) <= RESET_JITTER)


def _blocking_reset(seat, now):
    windows = [window for window in _full_windows(seat)
               if isinstance(window.get("resets_at"), (int, float)) and window["resets_at"] > now]
    return max((window["resets_at"] for window in windows), default=None)


def _seat_facts(row, seat, position, key, now):
    return {"ts": int(now), "position": position,
            "windows": _public_windows(seat), "credits": seat.get("resets_available"),
            "nearest_expiry": seat.get("nearest_expiry"), "key_digest": _digest(key)}


def _public_windows(seat):
    return {name: {"used_percent": value.get("used_percent"), "resets_at": value.get("resets_at")}
            for name, value in (seat.get("windows") or {}).items() if isinstance(value, dict)}


def _load_context():
    import lib.context_chain as context
    return context


def _pair_reset(context, address, path, head_pointer):
    context = context or _load_context()
    node = context.checkpoint(next_step=None, title="codex reset decision",
        tier="feedback", summary="Reset credit decision for subscription seat.",
        records_idr=address, path=path, head_pointer=head_pointer)
    if not getattr(node, "id", None):
        raise ValueError("belief checkpoint returned no node")


def _seal_reset(facts, event, outcome, decision_id, *, idr, idr_path,
                context=None, context_path=None, head_pointer=None):
    decision = {name: {**facts, "event": event, "outcome": outcome,
                       "decision_id": decision_id}[name] for name in RESET_LEAF_FIELDS}
    written = False
    try:
        if idr is None:
            import lib.precog.idr as idr
        with _demo_seal_refused():
            leaf = idr.build_idr(prompt="", fingerprint=fingerprint(decision),
                decision=decision, predecessor_idr=decision_id if event == "outcome" else None,
                depth=1 if event == "outcome" else 0,
                kind="grip/codex-auto-reset", inputs={"record": "1"})
        if event == "decision":
            if idr.audit_custody_tier(leaf.audit) != PRODUCT_CUSTODY:
                return None, False
            address = idr.content_addr(asdict(leaf))
            if not address:
                return None, False
        idr.append_idr(leaf, path=idr_path)
        written = True
        if event == "decision":
            _pair_reset(context, address, context_path, head_pointer)
        return leaf.id, True
    except Exception:
        return (leaf.id if written and event == "decision" else None), False


def _load_attempts(path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data["attempts"] if isinstance(data.get("attempts"), list) else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _save_attempts(path, attempts):
    write_cache(path, {"attempts": attempts})


def _has_attempt(attempts, slug, reset, statuses):
    return any(a.get("slug") == slug and _same_window(a.get("blocked_until"), reset)
               and a.get("status") in statuses for a in attempts)


def _candidate(row, position, fetch, now, seat_slug, attempts):
    try:
        seat = parse_rate_limits(fetch(row["codex_home"]))
    except SeatSignedOut as exc:
        # It cannot serve, so it holds no room for the credit to protect (GRIP#9071).
        return None, f"seat is signed out ({exc.code})" if seat_slug is not None else ""
    except Exception:
        return None, "seat limits unavailable"
    if seat_slug is None and live_state(seat, now) == "ready":
        return None, "a usable seat exists; rotation comes first"
    reset = _blocking_reset(seat, now)
    if reset is None or not seat.get("resets_available"):
        return None, ""
    if _has_attempt(attempts, row["slug"], reset,
                    ("nothingToReset", "noCredit", "reset", "alreadyRedeemed")):
        return None, ""
    return (row, seat, position, reset), ""


def _pick_reset(rows, fetch, now, seat_slug, attempts, short_wait):
    choices = []
    for position, row in enumerate(rows, 1):
        if seat_slug is not None and row["slug"] != seat_slug:
            continue
        choice, reason = _candidate(row, position, fetch, now, seat_slug, attempts)
        if reason:
            return None, reason
        if choice:
            choices.append(choice)
    if not choices:
        return None, "no full seat with an available reset credit"
    pending = [item for item in choices if _has_attempt(
        attempts, item[0]["slug"], item[3], ("pending",))]
    preferred = [item for item in choices if item[3] - now > short_wait]
    pool = pending or preferred or choices
    return sorted(pool, key=lambda item: (-item[3], item[1].get("nearest_expiry") or float("inf"), item[2]))[0], ""


def _finish_attempt(attempt, row, seat, facts, *, fetch, idr, idr_path, now):
    outcome = attempt["status"]
    verified = None
    if outcome == "reset":
        try:
            verified = parse_rate_limits(fetch(row["codex_home"]))
        except Exception:
            verified = None
    facts = {**facts, "windows": _public_windows(verified) if verified else facts["windows"]}
    if not _seal_reset(facts, "outcome", outcome, attempt["decision_id"], idr=idr, idr_path=idr_path)[1]:
        return {"status": "none", "reason": "outcome signing failed"}
    if outcome == "alreadyRedeemed" or (verified and live_state(verified, now) == "ready"):
        return {"status": "reset", "slug": row["slug"], "decision_id": attempt["decision_id"],
                "reason": "reset credit redeemed and limits checked"}
    return {"status": "none", "reason": f"{outcome}; seat remains unavailable"}


def _prepare_attempt(row, seat, position, reset, attempts, ledger, *, idr, idr_path,
                     context, context_path, head_pointer, now):
    pending = next((a for a in attempts if a.get("slug") == row["slug"] and
                    _same_window(a.get("blocked_until"), reset) and a.get("status") == "pending"), None)
    attempt = pending or {"ts": int(now), "slug": row["slug"], "blocked_until": reset,
                          "key": str(uuid.uuid4()), "status": "pending", "called": False}
    facts = _seat_facts(row, seat, position, attempt["key"], now)
    if pending is None:
        decision_id, paired = _seal_reset(facts, "decision", "planned", None,
            idr=idr, idr_path=idr_path, context=context,
            context_path=context_path, head_pointer=head_pointer)
        if not paired:
            if decision_id:
                _seal_reset(facts, "outcome", "notCalled", decision_id,
                    idr=idr, idr_path=idr_path)
            return None, None
        attempt["decision_id"] = decision_id
        attempt["facts"] = facts
        attempts.append(attempt)
    _save_attempts(ledger, attempts)
    return attempt, facts


def _call_consume(attempt, attempts, ledger, row, consume):
    try:
        attempt["called"] = True
        _save_attempts(ledger, attempts)
        outcome = consume(row["codex_home"], attempt["key"])["outcome"]
    except Exception:
        return False
    attempt["status"] = outcome if outcome in ("reset", "alreadyRedeemed", "nothingToReset", "noCredit") else "unknown"
    _save_attempts(ledger, attempts)
    return True


def _expired_facts(attempt, rows):
    if isinstance(attempt.get("facts"), dict):
        return attempt["facts"]
    position = next((n for n, row in enumerate(rows, 1)
                     if row["slug"] == attempt.get("slug")), None)
    return {"ts": attempt.get("ts"), "position": position, "windows": {},
            "credits": None, "nearest_expiry": None,
            "key_digest": _digest(attempt.get("key", ""))}


def _close_expired(attempts, rows, ledger, *, idr, idr_path, now):
    for attempt in attempts:
        reset = attempt.get("blocked_until")
        if (attempt.get("status") != "pending" or not isinstance(reset, (int, float))
                or isinstance(reset, bool) or now <= reset + RESET_JITTER):
            continue
        outcome = "unknown" if attempt.get("called") else "notCalled"
        if not _seal_reset(_expired_facts(attempt, rows), "outcome", outcome,
                           attempt.get("decision_id"), idr=idr, idr_path=idr_path)[1]:
            return False
        attempt["status"] = outcome
        _save_attempts(ledger, attempts)
    return True


def _spend_locked(rows, state, *, idr_path, idr, context, context_path, head_pointer,
                  fetch, consume, now, seat_slug, dry_run, daily_cap, short_wait):
    ledger = state / RESET_LEDGER
    attempts = _load_attempts(ledger)
    if attempts is None:
        return {"status": "none", "reason": "reset ledger unreadable"}
    if not _close_expired(attempts, rows, ledger, idr=idr, idr_path=idr_path, now=now):
        return {"status": "none", "reason": "expired attempt outcome signing failed"}
    choice, reason = _pick_reset(rows, fetch, now, seat_slug, attempts, 0 if seat_slug else short_wait)
    if choice is None:
        return {"status": "none", "reason": reason}
    row, seat, position, reset = choice
    pending = any(a.get("slug") == row["slug"] and _same_window(a.get("blocked_until"), reset)
                  and a.get("status") == "pending" for a in attempts)
    if not pending and sum(0 <= now - a.get("ts", 0) < 86400 for a in attempts if a.get("called")) >= daily_cap:
        return {"status": "none", "reason": "daily reset cap reached"}
    if dry_run:
        return {"status": "dry_run", "slug": row["slug"], "reason": "would spend one reset credit"}
    attempt, facts = _prepare_attempt(row, seat, position, reset, attempts, ledger,
        idr=idr, idr_path=idr_path, context=context, context_path=context_path,
        head_pointer=head_pointer, now=now)
    if attempt is None:
        return {"status": "none", "reason": "decision signing failed"}
    if not _call_consume(attempt, attempts, ledger, row, consume):
        return {"status": "none", "reason": "reset call uncertain; same key retained for retry"}
    return _finish_attempt(attempt, row, seat, facts, fetch=fetch, idr=idr, idr_path=idr_path, now=now)


def spend_reset(rows, *, state_dir=None, idr_path=None, idr=None, context=None,
                context_path=None, head_pointer=None, fetch=None,
                consume=None, now=None, seat_slug=None, dry_run=False,
                daily_cap=4, short_wait=900):
    """Decide under a nonblocking lock and spend at most one reset credit."""
    state = Path(state_dir or get_claude_dir() / "state")
    if os.environ.get("GRIP_CODEX_AUTO_RESET") == "0" or (state / RESET_OFF).exists():
        return {"status": "none", "reason": "automatic reset switch is off"}
    if seat_slug and not any(row["slug"] == seat_slug for row in rows):
        return {"status": "none", "reason": "seat is absent from registry"}
    try:
        state.mkdir(parents=True, exist_ok=True)
        with (state / "codex-auto-reset.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "none", "reason": "another reset attempt holds the lock"}
            try:
                return _spend_locked(rows, state, idr_path=idr_path, idr=idr,
                    context=context, context_path=context_path, head_pointer=head_pointer,
                    fetch=fetch or fetch_rate_limits, consume=consume or consume_reset,
                    now=time.time() if now is None else now, seat_slug=seat_slug,
                    dry_run=dry_run, daily_cap=daily_cap, short_wait=short_wait)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError:
        return {"status": "none", "reason": "reset state unavailable; outcome uncertain"}


def _full_windows(seat):
    windows = (seat.get("windows") or {}).values()
    return [window for window in windows if window and window.get("used_percent", 0) >= 100]


def live_state(seat, now):
    """A seat's state at ``now``: a full window whose reset has passed is no
    longer full, so a stale reading never claims a seat is still blocked."""
    state = seat.get("state", "unknown")
    full = _full_windows(seat)
    if state not in ("full", "reset_available") or not full:
        return state
    resets = [window.get("resets_at") for window in full]
    if all(isinstance(reset, (int, float)) and reset <= now for reset in resets):
        return "ready"
    return state


def _ledger_window(value):
    return {"used_percent": value[0], "resets_at": value[1], "duration_mins": None} if value else None


def _group_ledger(windows):
    """``{label: {window name: value}}``, skipping wire fallback targets (``w4:``)."""
    grouped = {}
    for key, window in windows.items():
        identity, kind = key.rsplit(":", 1)
        if not identity.startswith("w4:"):
            grouped.setdefault(identity, {})[kind] = window
    return grouped


def _claude_seat(number, identity, values, now):
    seat = {"label": _safe_label(f"Claude · {identity}", f"Claude {number}"), "provider": "Claude",
            "plan": "unknown", "resets_available": None, "nearest_expiry": None,
            "has_credits": False, "credits_balance": "", "state": "full",
            "windows": {"five_hour": _ledger_window(values.get("five_hour") or values.get("5h")),
                        "weekly": _ledger_window(values.get("seven_day") or values.get("7d"))}}
    seat["state"] = live_state(seat, now) if _full_windows(seat) else "ready"
    return seat


def collect_claude(ledger_path=None, now=None):
    """Claude accounts from HAL's ledger: windows only, named by ledger label.

    A ``w4:`` entry is a wire fallback target, not a Claude subscription, so it
    is skipped. A label that could identify a person falls back to a number.
    """
    now = time.time() if now is None else now
    windows = hal_ledger_windows(_read_json_quiet(Path(ledger_path) if ledger_path else _default_hal_ledger()))
    grouped = _group_ledger(windows)
    return [_claude_seat(number, identity, values, now)
            for number, (identity, values) in enumerate(grouped.items(), 1)]


def default_cache_path():
    return get_claude_dir() / "state" / "subscription-resets.json"


def write_cache(path, data):
    """Atomically replace the cache with its safe display schema."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=True)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def read_cache(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) and isinstance(data.get("seats"), list) else {}
    except (OSError, ValueError, TypeError):
        return {}


def is_stale(cache, now):
    updated = cache.get("updated_at") if isinstance(cache, dict) else None
    return not isinstance(updated, (int, float)) or now - updated >= CACHE_AGE


def render_segment(cache, now) -> str:
    seats = cache.get("seats", []) if isinstance(cache, dict) else []
    if not seats:
        return ""
    codex = [seat for seat in seats if seat.get("provider", "Codex") == "Codex"]
    states = [live_state(seat, now) for seat in codex]
    total = sum(seat.get("resets_available") or 0 for seat in codex)
    ready = states.count("ready")
    mark = " -- RESET AVAILABLE" if "reset_available" in states else ""
    if "signed_out" in states:
        mark += f" -- {states.count('signed_out')} SIGNED OUT"
    return f"Codex {total} resets held, {ready}/{len(codex)} ready{mark}"


def _window_text(window, now):
    if not window:
        return "unknown"
    reset = window.get("resets_at")
    if isinstance(reset, (int, float)) and reset <= now:
        return f"{window['used_percent']:g}% (reset since reading)"
    if isinstance(reset, (int, float)):
        remaining = max(0, int(reset - now))
        interval = f"{remaining // 86400}d" if remaining >= 86400 else f"{remaining // 3600}h {remaining % 3600 // 60}m"
    else:
        interval = "unknown"
    duration = window.get("duration_mins")
    name = f"{duration // 1440}d " if duration and duration >= 1440 and duration != 10080 else ""
    return f"{name}{window['used_percent']:g}% (resets in {interval})"


def _held_text(seat):
    count = seat.get("resets_available")
    expiry = seat.get("nearest_expiry")
    held = "unknown" if count is None else str(count)
    if isinstance(expiry, (int, float)):
        held += " (nearest expiry " + datetime.fromtimestamp(expiry, timezone.utc).strftime("%Y-%m-%d") + ")"
    return held


def _state_text(seat, now):
    """The State column: a signed-out seat names the server's reason."""
    state = live_state(seat, now)
    if state != "signed_out":
        return state
    code = str(seat.get("auth_error") or "")
    return f"signed out ({code if re.fullmatch(r'[a-z_]{1,48}', code) else 'unauthorized'})"


def _table_row(seat, now):
    windows = seat.get("windows") or {}
    credits = ("yes" + (f" ({seat.get('credits_balance')})" if seat.get("credits_balance") else "")) if seat.get("has_credits") else "no"
    state = _state_text(seat, now)
    if seat.get("provider") == "Claude":
        state += ' -- /limit-reset in Claude Code, answer "No, keep it"'
    return " | ".join((seat.get("label", "unknown"), seat.get("plan", "unknown"),
                       _window_text(windows.get("five_hour"), now),
                       _window_text(windows.get("weekly"), now), _held_text(seat), credits, state))


def render_table(cache, now) -> str:
    header = "Seat | Plan | 5-hour / primary | Weekly | Resets held | Credits | State"
    return "\n".join([header] + [_table_row(seat, now) for seat in cache.get("seats", [])])


def _switch_cli(argv, state):
    action = argv[1] if len(argv) > 1 else "status"
    marker = state / RESET_OFF
    if action not in ("on", "off", "status"):
        raise SystemExit("usage: grip-resets auto on|off|status")
    if action == "off":
        state.mkdir(parents=True, exist_ok=True)
        marker.touch()
    elif action == "on":
        marker.unlink(missing_ok=True)
    enabled = not marker.exists() and os.environ.get("GRIP_CODEX_AUTO_RESET") != "0"
    print("Automatic Codex resets are on." if enabled else "Automatic Codex resets are off.")
    return 0


def _spend_cli(argv, *, state, fetch, consume, idr, idr_path, context,
               context_path, head_pointer, accounts):
    parser = argparse.ArgumentParser(prog="grip-resets spend",
        description="Spend one signed reset credit with --auto or --seat. Defaults: 4 per 24 hours; "
        "prefer waits over 15 minutes.",
        epilog="Switch off with grip-resets auto off or GRIP_CODEX_AUTO_RESET=0. "
        "Set GRIP_CODEX_AUTO_RESET_DAILY_CAP and GRIP_CODEX_AUTO_RESET_SHORT_WAIT_MINUTES "
        "to change the defaults.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--auto", action="store_true", help="spend only when every seat is full")
    target.add_argument("--seat", help="operator-selected registry seat slug")
    parser.add_argument("--dry-run", action="store_true", help="decide without signing or spending")
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    cap = int(os.environ.get("GRIP_CODEX_AUTO_RESET_DAILY_CAP", "4"))
    short_wait = int(os.environ.get("GRIP_CODEX_AUTO_RESET_SHORT_WAIT_MINUTES", "15")) * 60
    result = spend_reset(accounts or read_accounts(), state_dir=state, idr_path=idr_path,
        idr=idr, context=context, context_path=context_path, head_pointer=head_pointer,
        fetch=fetch, consume=consume, seat_slug=args.seat, dry_run=args.dry_run,
        daily_cap=max(0, cap), short_wait=max(0, short_wait))
    if args.json:
        print(json.dumps(result))
    elif result["status"] == "reset":
        print(f"Spent one reset credit on {result['slug']}; its limits were checked.")
    elif result["status"] == "dry_run":
        print(f"Would spend one reset credit on {result['slug']}.")
    else:
        print(f"No reset credit spent: {result['reason']}.")
    return 0


PROBE_SIGNED_OUT = 4
PROBE_UNREADABLE = 3


def _probe_cli(argv, fetch):
    """Read one seat for the ladder and print its state. It exits 0 for an answer,
    4 when the server has signed the seat out, and 3, printing "unknown", when the
    seat could not be read at all, so the ladder can say the check did not
    complete (Codex review, GRIP#9121). For that case, stderr names the error's
    type, so a rerun shows why. It never shows the message, which could carry text
    from the server's reply."""
    if len(argv) != 1:
        raise SystemExit("usage: grip-resets probe CODEX_HOME")
    try:
        seat = parse_rate_limits((fetch or fetch_rate_limits)(argv[0]))
    except SeatSignedOut as exc:
        print(f"signed_out {exc.code}")
        return PROBE_SIGNED_OUT
    except Exception as exc:
        print(f"could not read the seat: {type(exc).__name__}", file=sys.stderr)
        print("unknown")
        return PROBE_UNREADABLE
    print(seat["state"])
    return 0


def _display_cli(argv, cache_path, fetch):
    parser = argparse.ArgumentParser(description="Show subscription windows and held resets. "
        "Use spend --auto, spend --seat SLUG, auto on|off|status, or probe CODEX_HOME. "
        f"probe prints one seat's state and exits {PROBE_SIGNED_OUT} only when the server "
        f"has signed that seat out, or {PROBE_UNREADABLE} when the seat could not be read. "
        "Defaults: four spends per 24 hours and a 15-minute short-wait preference. "
        "GRIP_CODEX_AUTO_RESET=0 also switches spending off.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh", action="store_true")
    group.add_argument("--json", action="store_true")
    group.add_argument("--segment", action="store_true")
    group.add_argument("--stale", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    path = Path(cache_path) if cache_path else default_cache_path()
    cache = read_cache(path)
    now = time.time()
    if args.stale:
        return 0 if is_stale(cache, now) else 1
    if args.segment:
        print(render_segment(cache, now))
        return 0
    if args.refresh or is_stale(cache, now):
        cache = collect(read_accounts(), fetch=fetch)
        cache["seats"].extend(collect_claude())
        write_cache(path, cache)
    print(json.dumps(cache, ensure_ascii=True) if args.json else render_table(cache, now))
    return 0


def cli_main(argv=None, *, cache_path=None, fetch=None, consume=None, idr=None,
             idr_path=None, context=None, context_path=None, head_pointer=None,
             state_dir=None, accounts=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    state = Path(state_dir or get_claude_dir() / "state")
    if argv and argv[0] == "auto":
        return _switch_cli(argv, state)
    if argv and argv[0] == "probe":
        return _probe_cli(argv[1:], fetch)
    if argv and argv[0] == "spend":
        return _spend_cli(argv[1:], state=state, fetch=fetch, consume=consume,
                          idr=idr, idr_path=idr_path, context=context,
                          context_path=context_path, head_pointer=head_pointer,
                          accounts=accounts)
    return _display_cli(argv, cache_path, fetch)


if __name__ == "__main__":
    raise SystemExit(cli_main())
