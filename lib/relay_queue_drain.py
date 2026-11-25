#!/usr/bin/env python3
"""relay_queue_drain — Mac-side consumer for the durable inbound queue.

Closes the inbound-relay gap diagnosed 2026-06-01 (last-500 split:
757 ``notified`` / 455 ``queued``). ``notified`` = a live MCP-subscribed CC
session received the inbound at that instant; ``queued`` = no live subscriber
then, so the daemon buffered it to ``data/inbound-queue.jsonl`` and **never
retroactively actioned it**. The VPS owns Slack/Discord via the webhook
one-shot dispatcher; WhatsApp lives on the Mac (Hetzner IP blocked at the
Baileys handshake) and had **no analogue consumer**. This is it.

Design (verify-canonical 2026-06-01 — supersedes the preplan's "just enable
``GRIP_RELAY_WEBHOOK_URL``" path, which emits ``channel_id=''`` for WhatsApp
→ 400 at the dispatcher's REQUIRED_FIELDS check):

  Tail the durable JSONL the daemon already writes (read-only on its output —
  NEVER touches the live WhatsApp bridge):
      data/inbound-queue.jsonl
        → rows newer than a persisted floor, exactly-once via fingerprint
        → require an EXPLICIT GRIP-address (the Mac daemon captures the
          operator's whole un-scoped stream; ambient chatter must not action)
        → resolve the reply recipient from the daemon-RESOLVED ``meta.sender``
          NAME via a FAIL-CLOSED canonical-phone-JID allowlist — NEVER the
          inbound ``@lid`` (which resolves to the WRONG contact)
        → RelayDispatcher.process(event) — REUSES the tested decide()-gate +
          capability lock (read-only unless operator + opt-in) + 3-rung ack +
          reply transport (defaults to the Mac daemon 127.0.0.1:3101).

Storm-proofing (origin: 2026-06-01 launchd-PATH incident — one row re-acked
12× because ``claude`` was off launchd's minimal PATH, so run_claude raised
FileNotFoundError AFTER the rung-1 ack, and the old break-without-mark retried
it every poll forever):
  * PRE-FLIGHT — if the ``claude`` binary is unresolvable, IDLE (process
    nothing, ack nothing) instead of ack-storming.
  * RETRY CAP — a row that fails process() MAX_PROCESS_RETRIES times is
    POISONED (logged + marked done), never retried. Bounds acks to the cap.

Sole actioning path (preplan D-3): run WITHOUT a channels-enabled CC session.
Read-only by default (``GRIP_RELAY_OPERATOR_FULL_CAPABILITY`` unset ⇒ locked,
preplan D-1). Stdlib only (syscall-doctrine + KISS).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

# relay_dispatcher is co-located in lib/. resolve() collapses symlinks
# (grip-worktree.sh symlinks git-crypt keys). Importing it gives us the whole
# tested actioning pipeline (decide-gate, capability lock, 3-rung ack, reply).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# relay_decision is stdlib-only (regex + dataclasses) — safe to import eagerly,
# unlike relay_dispatcher (heavy dep tree, imported lazily in _default_process_fn).
# We reuse its GRIP-address detector to scope the UN-scoped WhatsApp stream.
import relay_decision  # noqa: E402

# The reply map's real IDs live in the private, untracked
# config/wa-identities.local.json, read through wa_identities. Without the
# module (or the file) the map is empty, and the drain IDLES rather than consume
# rows it cannot answer (see _operator_reply_jid).
try:
    import wa_identities as _wa  # noqa: E402
except ImportError:
    _wa = None
    print("[relay-queue-drain] wa_identities unavailable: empty reply map, "
          "the drain will idle", file=sys.stderr)

CLAUDE_DIR = Path(
    os.environ.get("GRIP_RELAY_CLAUDE_CWD", os.path.expanduser("~/.claude"))
)
QUEUE_PATH = Path(
    os.environ.get("GRIP_INBOUND_QUEUE_PATH", str(CLAUDE_DIR / "data" / "inbound-queue.jsonl"))
)
STATE_PATH = Path(
    os.environ.get(
        "GRIP_INBOUND_DRAIN_STATE",
        str(CLAUDE_DIR / "state" / "relay" / "inbound-drain.state.json"),
    )
)
def _env_int(name: str, default: int) -> int:
    """Parse an int env var, fail-SAFE to ``default`` on a missing/non-numeric
    value (never raise at import). A daemon must not hard-crash on an operator
    typo in a numeric env (e.g. GRIP_INBOUND_DRAIN_POLL=abc) — it falls back to
    the safe default and runs (AGORA #3407/#3408 crash-safety contract). ALL of
    this module's operator-facing numeric config goes through here, so no typo
    in any GRIP_INBOUND_* setting can crash the drainer at import."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


POLL_INTERVAL = _env_int("GRIP_INBOUND_DRAIN_POLL", 5)
# Keep the last N fingerprints — bounds the state file. A row older than the
# floor is already excluded by timestamp, so this only needs to cover the
# window of rows sharing the boundary ms; 5000 is generous.
_FP_KEEP = 5000
# Max times one row may fail process() before it is POISONED (logged + marked
# done, never retried). The storm-proofing cap — see module docstring.
MAX_PROCESS_RETRIES = _env_int("GRIP_INBOUND_DRAIN_MAX_RETRIES", 2)
# Content-dedup window (ms). The daemon can RE-ENQUEUE the same inbound WhatsApp
# message with a fresh enqueuedAt — Baileys messages.upsert fires for both
# 'notify' and 'append', and a reconnect can replay recent messages. A fresh
# enqueuedAt yields a different _fingerprint, so the row-fp exactly-once misses
# the duplicate → a SECOND dispatch + a SECOND ack burst (2026-06-01: one
# "GRIP:" command was acked twice). Deduping on STABLE content (sender + chat +
# body, NO timestamp) within this window kills the re-delivery while still
# allowing a genuinely-repeated identical command once the window elapses.
DEDUP_WINDOW_MS = _env_int("GRIP_INBOUND_DEDUP_WINDOW_MS", 600000)
# Safe-backlog-drain bounds (origin: 2026-06-02 — the durable queue reached 103
# rows; a naive --drain-backlog would blind-replay ALL of them, flooding the
# operator with stale replies). lifecycle-closed-or-expired: a backlog row is
# only CURRENT work if it is recent AND within the replay budget; anything older
# / past the budget is EXPIRED → tombstoned (marked done, floor advanced), never
# actioned. These bounds apply ONLY to a --drain-backlog replay; the steady-state
# new-arrival drain is unbounded (those rows ARE current work).
#   DRAIN_MAX_AGE_MS  — a backlog row older than this (relative to now) is expired
#                       and dropped. Default 24h.
#   DRAIN_BACKLOG_CAP — at most this many (the NEWEST) within-age rows are
#                       replayed in one boot. The rest are tombstoned. Default 25.
DRAIN_MAX_AGE_MS = _env_int("GRIP_INBOUND_DRAIN_MAX_AGE_MS", 24 * 3600 * 1000)
DRAIN_BACKLOG_CAP = _env_int("GRIP_INBOUND_DRAIN_BACKLOG_CAP", 25)
# Idle heartbeat cadence (s). A healthy drainer with nothing to do prints a
# heartbeat at least this often so it is OBVIOUSLY alive — never looks hung
# (origin: 2026-06-02 — a foreground poll loop that printed nothing read as a
# hang). 0 ⇒ heartbeat every poll (used by tests for determinism).
HEARTBEAT_SEC = _env_int("GRIP_INBOUND_DRAIN_HEARTBEAT_SEC", 60)
# Mirror of relay_dispatcher.CLAUDE_BIN (duplicated, not imported, so the
# pre-flight stays decoupled from the heavy relay_dispatcher import).
_CLAUDE_BIN = os.environ.get("GRIP_RELAY_CLAUDE_BIN", "claude")

# Canonical reply-JID allowlist — FAIL-CLOSED on any unlisted sender.
# Keyed on the daemon-RESOLVED display name (meta.sender), which the
# grip-channel allowlist authenticated — NEVER the user-controlled body and
# NEVER the inbound @lid (which resolves to the wrong contact). An unresolved
# sender → None → the row is skipped (never actioned), so an unknown party can
# never trigger a spawn or a reply.
#
# The map comes from the private file (wa_identities.relay_reply_map): the
# operator under "V>>" first, then the "relay"."reply" table. Admission here is
# by principal NAME; the capability TIER (full tools incl. shell, agentic) is
# decided ID-side in relay_dispatcher.is_full_capability_sender, so a name
# admitted here but absent from the full-capability map keeps the read-only
# chat lock. Replies route to each principal's canonical phone ID.
OPERATOR_REPLY_NAME = "V>>"
_DEFAULT_REPLY_JIDS = _wa.relay_reply_map() if _wa else {}


def _reply_jids() -> dict[str, str]:
    """Canonical reply-JID map. Env override (JSON) replaces the map read from
    the private file; a malformed override fails closed to that map (never to a
    wildcard)."""
    raw = os.environ.get("GRIP_RELAY_WA_REPLY_JIDS", "").strip()
    if not raw:
        return dict(_DEFAULT_REPLY_JIDS)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            return parsed
    except (ValueError, TypeError):
        pass
    return dict(_DEFAULT_REPLY_JIDS)


def _reply_to_chat_enabled() -> bool:
    """Reply INTO the originating chat (the inbound rawJid) — default ON
    (V>>-directed 2026-06-01, CBC).

    ON  → channel_id = the chat rawJid, so GRIP's reply AND the per-chat session
          both land in that chat: a shared chat (V>> and a co-steerer) sees
          the reply in context and shares ONE GRIP memory. The explicit "GRIP:"
          address IS the operator's consent to reply there.
    OFF (``GRIP_RELAY_WA_REPLY_TO_CHAT`` in 0/false/off/no) → reply to the
          sender's canonical DM + per-sender session (the pre-2026-06-01 safe
          behaviour; the one-env-var escape hatch if @lid chat-routing ever
          misbehaves).
    The rawJid is an echo-back-to-origin CHAT target, NEVER a person-identity
    lookup (identity stays meta.sender + canonical sender_id), so the
    @lid-untrust rule (route-to-canonical for a PERSON) is honoured.
    """
    return os.environ.get("GRIP_RELAY_WA_REPLY_TO_CHAT", "1").strip().lower() \
        not in ("0", "false", "off", "no", "")


def resolve_recipient(sender_name: str) -> str | None:
    """Resolve a daemon-resolved sender NAME → canonical phone JID, or None.

    Fail-closed: an unknown/empty name returns None, so the caller skips the
    row rather than reply to an unverified recipient. The load-bearing
    @lid-safety + unknown-party control.
    """
    return _reply_jids().get((sender_name or "").strip())


def _operator_reply_jid() -> str | None:
    """The operator's reply ID, or None when the reply map has no "V>>" entry.

    None means the private file (or the override) is missing the operator. The
    drain then IDLES: it marks no row and holds the floor, so nothing in the
    queue is consumed for want of configuration (see drain_once and run).
    """
    return _reply_jids().get(OPERATOR_REPLY_NAME)


def _claude_resolved() -> str | None:
    """Resolve the ``claude`` binary the dispatcher will spawn — absolute path
    or PATH lookup. None if unresolvable.

    The pre-flight uses this to IDLE rather than ack-storm: under launchd the
    PATH is minimal (/usr/bin:/bin), so a bare ``claude`` is not found and
    every actionable row would fail post-ack. Resolving here lets the daemon
    refuse to process until GRIP_RELAY_CLAUDE_BIN / PATH is fixed.
    """
    if os.path.isabs(_CLAUDE_BIN):
        return _CLAUDE_BIN if os.path.exists(_CLAUDE_BIN) else None
    return shutil.which(_CLAUDE_BIN)


def _is_grip_addressed(text: str) -> bool:
    """True iff the message EXPLICITLY addresses GRIP (vocative
    "GRIP:"/"@grip"/"hey grip", or name+imperative "grip do X").

    Reuses relay_decision's exact detector so the drainer's address semantics
    match the relay's. This is the load-bearing blast-radius control for the
    UN-scoped WhatsApp stream — see row_to_event.
    """
    low = relay_decision._strip_mrkdwn(text).lower().strip()
    return relay_decision._signal_grip_addressed(low)


def _fingerprint(row: dict) -> str:
    """Stable sha256 of the row — exactly-once key, robust to file rewrite."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content_fingerprint(row: dict) -> str:
    """sha256 of the STABLE message content — sender + chat + body + type,
    EXCLUDING the volatile enqueuedAt. Two re-enqueues of the SAME inbound
    message share this fp (unlike _fingerprint, which differs per enqueuedAt),
    so a daemon re-delivery / reconnect-replay is detectable as a duplicate
    (see DEDUP_WINDOW_MS). No WhatsApp message-id is present in the row, so
    content identity is the only stable dedup key available."""
    params = row.get("params", {}) or {}
    meta = params.get("meta", {}) or {}
    stable = {
        "method": row.get("method"),
        "content": params.get("content"),
        "sender": meta.get("sender"),
        "rawJid": meta.get("rawJid"),
        "type": meta.get("type"),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_redelivery(cfp: str, ts: int, content_seen: dict) -> bool:
    """True iff this content was already actioned within DEDUP_WINDOW_MS — a
    daemon re-delivery / reconnect-replay, not a new command. Measured from the
    first-actioned occurrence so a replay storm re-acks at most once per window."""
    prior = content_seen.get(cfp)
    return prior is not None and 0 <= (ts - prior) <= DEDUP_WINDOW_MS


def _read_rows(path: Path) -> list[dict]:
    """Read JSONL rows, tolerating a partial/garbled trailing line (the daemon
    may be mid-write). Un-parseable lines are skipped, never fatal."""
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _sender_name(row: dict) -> str:
    """Daemon-resolved sender display name from the row meta."""
    return ((row.get("params", {}) or {}).get("meta", {}) or {}).get("sender", "")


def _raw_jid(row: dict) -> str:
    """Inbound chat JID (rawJid) from the row meta — the CHAT, not the sender.

    In a 1:1 WhatsApp chat both participants' messages carry the SAME rawJid
    (live-verified on a one-to-one thread with the operator), so it is
    BOTH the per-chat session key AND the reply target — replying to it echoes
    GRIP's answer back INTO the originating chat (V>>-directed 2026-06-01) so a
    shared chat sees the reply in context. Echo-back-to-origin ONLY: never a
    person-identity lookup (identity stays meta.sender + canonical sender_id),
    so the @lid-untrust rule — which governs identifying a PERSON, not echoing
    to a CHAT — is honoured.
    """
    return ((row.get("params", {}) or {}).get("meta", {}) or {}).get("rawJid", "")


def row_to_event(row: dict, recipient: str | None) -> dict | None:
    """Map a queued notification row → a relay_dispatcher event, or None.

    Skips (None) when: sender not allowlisted (recipient None → fail-closed),
    non-WhatsApp platform, empty body, or NOT explicitly GRIP-addressed.

    Routing (V>>-directed 2026-06-01, CBC):
    * ``channel_id`` = the originating chat (rawJid) so GRIP's reply AND the
      per-chat session BOTH land in that chat — a shared chat (V>> and a
      co-steerer) sees the reply and shares ONE memory. The explicit "GRIP:"
      address IS consent to reply there. Falls back to the sender's canonical
      DM if there is no rawJid or GRIP_RELAY_WA_REPLY_TO_CHAT=0.
    * ``sender_id`` = canonical JID (resolve_recipient) — used ONLY for the
      operator full-capability gate (is_operator_sender), never as the reply
      target. Decoupled from channel_id on purpose.
    * ``sender_name`` = daemon-authenticated display name → the prompt's speaker
      label (mis-attribution / role-hallucination fix). Trusted closed vocab.
    """
    if recipient is None:
        return None
    params = row.get("params", {}) or {}
    meta = params.get("meta", {}) or {}
    if meta.get("platform") != "whatsapp":
        return None
    text = params.get("content")
    if not isinstance(text, str) or not text.strip():
        return None
    if not _is_grip_addressed(text):  # explicit-address blast-radius control
        return None
    return _assemble_event(row, recipient, text)


def _assemble_event(row: dict, recipient: str, text: str) -> dict:
    """Build the relay_dispatcher event dict from a validated row (the guards
    already passed in row_to_event). Kept separate so row_to_event stays a thin
    guard-coordinator under the KISS line cap.

    CONTRACT (module-private; sole caller is row_to_event): ``recipient`` is
    GUARANTEED non-None — row_to_event returns early on ``recipient is None``
    BEFORE calling this, so ``recipient: str`` is sound, not a latent None.
    Anchored by test_assemble_event_never_called_with_none_recipient (AGORA
    #3407 concern 1). Do not call this directly with a possibly-None recipient.

    * ``channel_id`` = the chat rawJid (reply target + per-chat session) when
      reply-to-chat is on, else the sender's canonical DM.
    * ``session_key`` = the chat rawJid: the dispatcher's _session_channel groups
      a shared 1:1 chat's BOTH participants (V>> and a co-steerer) into ONE claude
      session, DECOUPLED from the reply target. Echo-back-to-origin CHAT key only
      — NEVER a person-identity lookup (identity stays sender_id + sender_name),
      so the @lid-untrust rule is honoured. Absent rawJid ⇒ no key ⇒ the
      dispatcher falls back to channel_id (byte-identical prior behaviour).
    """
    raw_jid = _raw_jid(row)
    reply_to = raw_jid if (_reply_to_chat_enabled() and raw_jid) else recipient
    event = {
        "platform": "whatsapp",
        "sender_id": recipient,    # canonical JID — operator-capability gate only
        "channel_id": reply_to,    # the CHAT: reply target + per-chat session key
        "sender_name": _sender_name(row),
        "text": text,
        "ts": str(row.get("enqueuedAt", "")),
    }
    if raw_jid:
        event["session_key"] = raw_jid
    return _translate_inbound_if_configured(event)


def _translate_inbound_if_configured(event: dict) -> dict:
    """Best-effort inbound translation for senders in
    low_resource_translate.RELAY_LANGUAGE_BY_SENDER (V>>-directed 2026-09-24
    /goal: a sender who writes in a low-resource language). Lazily imported -- the relay
    daemon has no other dependency on transformers/torch, and this must
    never be the reason it fails to start.

    FAIL-OPEN, not fail-closed: a translation error returns the event
    UNCHANGED rather than raising. The relay's job is to get some response
    to the person; a failed translation should degrade to "GRIP sees the
    original text" (which force-actionable dispatch still handles, since
    _is_grip_addressed and the allowlist gate never depended on language),
    not to drop the message. Errors are printed to stderr, matching this
    module's existing WARN-and-continue convention (_notify_poison), rather
    than raised or silently swallowed.

    The import below uses the explicit `lib.` prefix for consistency with
    every other cross-module import in this codebase, not because the bare
    form fails here -- it doesn't: this module inserts its own directory
    onto sys.path at import time (see _LIB above), so a bare
    `from low_resource_translate import ...` also resolves in this specific
    file's execution context. An AGORA council (2026-09-24) flagged the
    bare form as a runtime ImportError; that specific consequence was
    checked and is FALSE for this file (proven via a stash/sed mutation
    test), but the underlying style point stands on its own -- relying on
    a caller's sys.path side effect for your own import is fragile even
    when it happens to work, so the explicit form is kept regardless.
    """
    try:
        from lib.low_resource_translate import translate_relay_event_inbound
    except ImportError:
        return event
    try:
        return translate_relay_event_inbound(event)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        print(f"[relay-queue-drain] inbound translation failed, using original text: {exc}",
              file=sys.stderr)
        return event


def initial_floor(path: Path, *, drain_backlog: bool) -> int:
    """First-boot floor: newest enqueuedAt currently in the file (process only
    NEW arrivals henceforth) — UNLESS --drain-backlog, then 0 (replay all).

    New-only is the safe default: it avoids a burst of stale replies to
    hours-old buffered messages on first start."""
    if drain_backlog:
        return 0
    rows = _read_rows(path)
    # +1 so the newest EXISTING row (ts == max) is excluded by the ``ts < floor``
    # filter — new-only means strictly-after-startup. Same-ms new arrivals are
    # still caught later by the fingerprint set, not the floor.
    return max((int(r.get("enqueuedAt", 0) or 0) for r in rows), default=0) + 1


def load_state(path: Path, queue_path: Path, *, drain_backlog: bool) -> dict:
    """Load persisted drain state, or initialise it (new-only floor)."""
    if path.exists():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and "floor_ms" in obj:
                obj.setdefault("processed_fps", [])
                obj.setdefault("failures", {})
                return obj
        except (ValueError, TypeError, OSError):
            pass
    return {"floor_ms": initial_floor(queue_path, drain_backlog=drain_backlog),
            "processed_fps": [], "failures": {}}


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def save_state(path: Path, state: dict, emit=None) -> bool:
    """Atomically persist drain state (temp + os.replace).

    An OSError is reported through ``emit`` (default: stderr) and returns False,
    never raised. On 2026-10-27 the shared install tree was in the Trash for nine
    minutes: ``mkdir`` raised FileExistsError on the dangling ``~/.claude`` symlink,
    this process died, and launchd restarted it into a state it could not read. A
    drainer that keeps its state in memory and retries the write on the next poll
    loses nothing; one that crashes re-actions whatever its last saved floor missed.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        (emit or _stderr)(f"[relay-queue-drain] state not saved to {path} ({exc}) -- "
                          "state directory unavailable; kept in memory, retried next poll")
        return False


# Honest-poison terminal (steerer directive 2026-06-11: going quiet is
# unacceptable; the sender must be told). When a row exhausts its retries
# the sender previously got "Heard — on it." + "Still working…" then ETERNAL
# SILENCE (171 poisoned rows 2026-06-01→11, ~17/day — every one a dropped ask).
# This is the drain-layer last line of defence for ANY exception class; the
# dispatcher's own rung-3 honesty (#2164/#3534) covers controlled failures.
POISON_NOTICE = (
    "GRIP here — I tried to handle your last message twice and hit an "
    "internal error both times. I'm not silently dropping it: the failure "
    "is logged for V>> to investigate. Please resend if it's urgent."
)


def _notify_poison(notify_fn, event: dict) -> None:
    """Best-effort honest terminal to the originating channel on poison.

    Never raises: a transport failure here must not crash the drain loop or
    block the poison-mark (the row is dropped either way; this is the courtesy
    layer, telemetry via _log_poison stays the record of truth)."""
    if notify_fn is None:
        return
    try:
        notify_fn(event)
    except Exception:  # noqa: BLE001 — best-effort by contract
        pass


def _default_notify_fn():
    """Production poison-notifier: post POISON_NOTICE back to the row's own
    channel via the dispatcher's proven ack transport (the same _default_ack
    that delivers rungs 1-3 — DRY over a parallel HTTP path). Lazy import,
    mirroring _default_process_fn, so tests never pull the heavy dep tree."""
    from relay_dispatcher import RelayDispatcher
    dispatcher = RelayDispatcher()
    return lambda event: dispatcher._default_ack(
        event.get("platform", "whatsapp"), event["channel_id"], POISON_NOTICE
    )


def _log_poison(event: dict, attempts: int) -> None:
    """Record a poisoned (permanently-failing) row to drain telemetry."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "event": "drain_poison_row", "attempts": attempts,
           "platform": event.get("platform"),
           "channel_id": event.get("channel_id"),
           "text_chars": len(event.get("text", ""))}
    p = CLAUDE_DIR / "logs" / "relay-queue-drain.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def _mark(st: dict, fp: str, ts: int) -> None:
    """Mark a row's fingerprint done + advance the floor high-water (mutates st).
    The triple that repeated 4× across the drain branches — DRY'd here."""
    st["seen"].add(fp)
    st["fps"].append(fp)
    st["max_ts"] = max(st["max_ts"], ts)


def _now_ms() -> int:
    """Wall-clock ms — wrapped so tests can monkeypatch a fixed clock."""
    return int(time.time() * 1000)


def _backlog_replay_fps(rows: list[dict], *, now_ms: int) -> set:
    """The fingerprints of backlog rows that are ELIGIBLE TO REPLAY, given the
    age window + the cap. Everything NOT in this set (in a backlog drain) is
    tombstoned, never actioned (lifecycle-closed-or-expired: live vs expired).

    Eligible = enqueuedAt within DRAIN_MAX_AGE_MS of now, then the NEWEST
    DRAIN_BACKLOG_CAP of those (a deep backlog's most-recent messages matter
    most; the operator's latest command must not be starved by ancient ones).
    Pure (no I/O, no mutation) so it is trivially testable + side-effect-free.
    """
    fresh = [r for r in rows
             if (now_ms - int(r.get("enqueuedAt", 0) or 0)) <= DRAIN_MAX_AGE_MS]
    fresh.sort(key=lambda r: int(r.get("enqueuedAt", 0) or 0))
    return {_fingerprint(r) for r in fresh[-DRAIN_BACKLOG_CAP:]}


def _drain_one_row(row: dict, st: dict, process_fn, resolver,
                   notify_fn=None) -> str:
    """Process ONE queue row against mutable drain-state ``st`` (keys: floor,
    seen, fps, failures, content_seen, max_ts). Returns the loop control signal:
    ``"skip"`` (below floor / already-seen / non-actionable / re-delivery),
    ``"process"`` (actioned), or ``"stop"`` (transient failure — retry next pass).
    Extracted so drain_once stays a thin coordinator under the KISS line cap."""
    ts = int(row.get("enqueuedAt", 0) or 0)
    if ts < st["floor"]:
        return "skip"
    fp = _fingerprint(row)
    if fp in st["seen"]:
        return "skip"
    # Backlog safety (ONLY when drain_backlog): a row outside the eligible
    # replay set (too old, or beyond the newest-N cap) is EXPIRED — tombstone it
    # (mark done + advance floor) so it is never actioned now AND never resurfaces
    # on a later normal pass. st["backlog_replay"] is None on a new-arrival drain,
    # so this branch is inert there (the cap/age never throttle current work).
    if st["backlog_replay"] is not None and fp not in st["backlog_replay"]:
        _mark(st, fp, ts)
        return "skip"
    event = row_to_event(row, resolver(_sender_name(row)))
    if event is None:
        _mark(st, fp, ts)
        return "skip"
    # Content-window dedup: a re-enqueue of the SAME message (fresh enqueuedAt →
    # different _fingerprint) within DEDUP_WINDOW_MS is a daemon re-delivery /
    # reconnect-replay, NOT a new command — skip silently (the 2026-06-01
    # double-ack fix). Mark the row-fp so the duplicate stops being re-evaluated.
    cfp = _content_fingerprint(row)
    if _is_redelivery(cfp, ts, st["content_seen"]):
        _mark(st, fp, ts)
        return "skip"
    try:
        process_fn(event)
    except Exception:  # noqa: BLE001
        st["failures"][fp] = st["failures"].get(fp, 0) + 1
        if st["failures"][fp] < MAX_PROCESS_RETRIES:
            return "stop"  # transient — retry next poll, bounded by the cap
        _log_poison(event, st["failures"][fp])
        _notify_poison(notify_fn, event)  # honest terminal — never silent-drop
        st["failures"].pop(fp, None)
        _mark(st, fp, ts)  # poisoned — skip permanently, do NOT block newer rows
        return "skip"
    st["failures"].pop(fp, None)
    st["content_seen"][cfp] = ts  # record AFTER a successful dispatch
    _mark(st, fp, ts)
    return "process"


def drain_once(*, path: Path, state: dict, process_fn, resolver=resolve_recipient,
               drain_backlog: bool = False, notify_fn=None) -> tuple[int, dict]:
    """Process queue rows newer than the floor, exactly-once, with a bounded
    retry cap + a content-window dedup for daemon re-deliveries. Thin coordinator
    over ``_drain_one_row`` (cc-enforcement Coordinator pattern). ``process_fn``
    is DI'd (default = RelayDispatcher.process) so tests drive a recording sink
    with no network. Returns (processed_count, new_state).

    ``drain_backlog`` gates the SAFE-replay bounds: when True, only the
    age-fresh + newest-``DRAIN_BACKLOG_CAP`` rows are eligible to action; the
    rest are tombstoned (never a 103-row flood). When False (steady state) every
    row newer than the floor is current work and is actioned unbounded.

    With no reply ID for the operator it returns ``(0, state)`` untouched: a
    missing private file must never consume the queue.
    """
    if _operator_reply_jid() is None:
        return 0, state  # IDLE: no row marked, floor held (see _operator_reply_jid)
    rows = sorted(_read_rows(path), key=lambda r: int(r.get("enqueuedAt", 0) or 0))
    st = {
        "floor": int(state.get("floor_ms", 0) or 0),
        "seen": set(state.get("processed_fps", [])),
        "fps": list(state.get("processed_fps", [])),
        "failures": dict(state.get("failures", {})),
        # content_seen: {content_fp: enqueuedAt of the actioned occurrence}.
        # Defensive .get so an older state file (no key) loads cleanly.
        "content_seen": {str(k): int(v)
                         for k, v in (state.get("content_seen", {}) or {}).items()},
        # backlog_replay: the fingerprints eligible to action on a backlog drain
        # (age-fresh ∩ newest-N). None ⇒ NOT a backlog drain ⇒ no throttling.
        "backlog_replay": (_backlog_replay_fps(rows, now_ms=_now_ms())
                           if drain_backlog else None),
    }
    st["max_ts"] = st["floor"]
    processed = 0
    for row in rows:
        action = _drain_one_row(row, st, process_fn, resolver, notify_fn)
        if action == "stop":
            break
        processed += action == "process"
    content_seen = st["content_seen"]
    if len(content_seen) > _FP_KEEP:  # bound to most-recent by ts (mirrors _FP_KEEP)
        content_seen = dict(sorted(content_seen.items(), key=lambda kv: kv[1])[-_FP_KEEP:])
    return processed, {"floor_ms": st["max_ts"], "processed_fps": st["fps"][-_FP_KEEP:],
                       "failures": st["failures"], "content_seen": content_seen}


def _default_process_fn():
    """Build the real actioning callable: RelayDispatcher.process with
    force_actionable=True.

    The drainer has ALREADY filtered to explicitly GRIP-addressed messages from
    an allowlisted steerer on the operator's UN-scoped WhatsApp stream, so
    decide()'s dedicated-channel confidence gate is bypassed — it would
    otherwise silently drop a "GRIP: <imperative>" command on a cold channel
    (0.25 < 0.50), which is exactly how a co-steerer drives GRIP.
    Imported lazily so the pure functions above stay importable in tests
    without the heavy relay_dispatcher dep tree."""
    from relay_dispatcher import RelayDispatcher
    dispatcher = RelayDispatcher()
    return lambda event: dispatcher.process(event, force_actionable=True)


def _startup_banner(*, drain_backlog: bool, poll: int, queue_path: Path,
                    state_path: Path, floor_ms: int) -> str:
    """One-line 'I am alive + here is my config' banner, printed on entry so the
    operator NEVER sees a silent process and assumes it hung (2026-06-02)."""
    return ("[relay-queue-drain] STARTED — Mac WhatsApp inbound consumer. "
            f"mode={'backlog-replay' if drain_backlog else 'new-arrivals'} "
            f"poll={poll}s floor_ms={floor_ms} queue={queue_path} "
            f"state={state_path}. Idle = healthy (heartbeat every "
            f"{HEARTBEAT_SEC}s); ^C to stop.")


def _prepare_run(*, process_fn, emit, state_path: Path, queue_path: Path,
                 drain_backlog: bool, poll: int):
    """Resolve run()'s DI seams, load state, emit the STARTED banner.

    Extracted to keep run() under the KISS line cap. Production path (no
    injected ``process_fn``) gets a real ``notify_fn``; an injected
    ``process_fn`` (tests) never implicitly gains a channel-posting notifier.
    Returns ``(process_fn, notify_fn, emit, state)``.
    """
    notify_fn = None
    if process_fn is None:
        process_fn = _default_process_fn()
        notify_fn = _default_notify_fn()
    if emit is None:
        emit = lambda msg: print(msg, flush=True)  # noqa: E731 — tiny DI seam
    state = load_state(state_path, queue_path, drain_backlog=drain_backlog)
    emit(_startup_banner(drain_backlog=drain_backlog, poll=poll,
                         queue_path=queue_path, state_path=state_path,
                         floor_ms=int(state.get("floor_ms", 0) or 0)))
    return process_fn, notify_fn, emit, state


def run(*, once: bool, drain_backlog: bool, poll: int,
        queue_path: Path = QUEUE_PATH, state_path: Path = STATE_PATH,
        process_fn=None, emit=None) -> bool:
    """Drain loop. ``once`` = single pass (test/cron); else poll forever.
    Returns True when the pass ended with its state saved (or idle), False when a
    one-shot pass could not save its state after retries (main exits 2 on that).

    Visibility fix (2026-06-02 'looks hung'): STARTED banner on entry +
    idle HEARTBEAT every ``HEARTBEAT_SEC`` (see _maybe_heartbeat), so a healthy
    drainer is never silent. ``emit`` is DI'd (default print) for tests.
    Backlog fix: ``drain_backlog`` bounds the replay (age ∩ newest-N) on the
    FIRST pass ONLY; later polls are unbounded new-arrival drains.
    Pre-flight: claude unresolvable ⇒ IDLE, never ack-storm (_emit_idle_preflight).
    Pre-flight: no reply ID for the operator ⇒ IDLE, mark nothing, logged once
    per start (_emit_idle_no_operator).
    """
    process_fn, notify_fn, emit, state = _prepare_run(
        process_fn=process_fn, emit=emit, state_path=state_path,
        queue_path=queue_path, drain_backlog=drain_backlog, poll=poll)
    warned = op_warned = False
    first_pass = True
    last_beat = time.monotonic()
    while True:
        if _operator_reply_jid() is None:
            op_warned = _emit_idle_no_operator(emit, op_warned)
            if once:
                return True
            time.sleep(max(1, poll))
            continue
        if _claude_resolved() is None:
            warned = _emit_idle_preflight(emit, warned)
            if once:
                return True
            time.sleep(max(1, poll))
            continue
        warned = False
        # Backlog replay is a ONE-BOOT event: only the first pass is bounded;
        # thereafter it is a normal unbounded new-arrival drain.
        n, state = drain_once(path=queue_path, state=state, process_fn=process_fn,
                              drain_backlog=drain_backlog and first_pass,
                              notify_fn=notify_fn)
        first_pass = False
        saved = save_state(state_path, state, emit=emit)
        if n:
            emit(f"[relay-queue-drain] actioned {n} inbound row(s) "
                 f"(floor_ms={state['floor_ms']})")
            last_beat = time.monotonic()  # a real action resets the idle timer
        if once:
            return saved or _retry_save(state_path, state, emit)
        last_beat = _maybe_heartbeat(emit, n, last_beat, queue_path,
                                     int(state.get("floor_ms", 0) or 0))
        time.sleep(max(1, poll))


def _retry_save(path: Path, state: dict, emit=None, *, attempts: int = 3, pause: float = 1.0) -> bool:
    """One-shot mode has no next poll to retry a failed state write, so retry here.
    A write that still fails makes the single pass report failure (main exits 2):
    the next invocation would reload the stale file and could action rows again.
    ``emit`` may be None (stderr then), like save_state's."""
    report = emit or _stderr
    for _ in range(attempts):
        time.sleep(pause)
        if save_state(path, state, emit=report):
            return True
    report("[relay-queue-drain] one-shot pass ended with its state unsaved after "
           f"{attempts} retries -- exit 2; the next run would re-action what this one did")
    return False


def _emit_idle_preflight(emit, warned: bool) -> bool:
    """Emit the claude-unresolvable IDLING notice once (deduped via ``warned``).
    Returns the new ``warned`` flag. Extracted to keep run() under the KISS cap."""
    if not warned:
        emit("[relay-queue-drain] FATAL: claude binary unresolvable "
             f"(GRIP_RELAY_CLAUDE_BIN={_CLAUDE_BIN!r} / PATH) — IDLING, "
             "processing NOTHING (refuses to ack-storm). Set "
             "GRIP_RELAY_CLAUDE_BIN to the absolute path and reload.")
    return True


def _emit_idle_no_operator(emit, warned: bool) -> bool:
    """Emit the no-operator IDLING notice once per start (deduped via ``warned``,
    which is never reset). Returns the new ``warned`` flag."""
    if not warned:
        emit("[relay-queue-drain] FATAL: no reply ID for the operator "
             f"({OPERATOR_REPLY_NAME}) — IDLING, marking NOTHING and holding the "
             "floor. Add \"operator\" to config/wa-identities.local.json (or "
             f"{OPERATOR_REPLY_NAME} to GRIP_RELAY_WA_REPLY_JIDS) and restart.")
    return True


def _maybe_heartbeat(emit, n: int, last_beat: float, queue_path: Path,
                     floor_ms: int) -> float:
    """Emit an idle heartbeat iff nothing was actioned AND HEARTBEAT_SEC elapsed,
    so a do-nothing drainer is visibly alive (never looks hung). Returns the
    (possibly-reset) last-beat timer. HEARTBEAT_SEC=0 ⇒ beat every idle poll."""
    if n == 0 and (time.monotonic() - last_beat) >= HEARTBEAT_SEC:
        emit(f"[relay-queue-drain] heartbeat — idle, alive, watching "
             f"{queue_path} (floor_ms={floor_ms})")
        return time.monotonic()
    return last_beat


def main() -> None:
    ap = argparse.ArgumentParser(description="Mac inbound-queue drainer")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--drain-backlog", action="store_true",
                    help="replay ALL buffered rows (default: new arrivals only)")
    ap.add_argument("--poll", type=int, default=POLL_INTERVAL,
                    help=f"poll interval seconds (default {POLL_INTERVAL})")
    args = ap.parse_args()
    if not run(once=args.once, drain_backlog=args.drain_backlog, poll=args.poll):
        sys.exit(2)  # a one-shot pass whose state could not be saved; the daemon never returns False


if __name__ == "__main__":
    main()
