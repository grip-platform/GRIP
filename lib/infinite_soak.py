"""External, read-only InfiniteMachine T4 observation (Claude Code only).

Cohort: unique session IDs with an explicitly interactive GRIP launch receipt
in [since, until). Errors are transcript records in that same window, counted
once even when the API flag and several oracle strings match. A missing,
empty or damaged transcript is unknown, never an error-free session. The
error-free share is confirmed clean sessions / all eligible sessions (a lower
bound when coverage is incomplete); no denominator means null, not 100%.

Legacy receipts lack interactivity; resumed launches may lack session IDs.
Both gaps are counted, never guessed from filenames or transcript mtimes.
Other harnesses and historical spare capacity remain unmeasured. An observed
error is evidence for investigation, not proof of a capacity-contract breach.

Adapted from the fleet history lane's api_errors.py: stream JSONL records,
extract text blocks, then classify usage before rate before overload. Only
counts, session UUIDs, and opaque account IDs leave this module. Never retain
or emit content, input paths, exception messages, or ledger payloads.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_dir import get_claude_dir

CLASSES = ("rate-limit", "overload", "usage-limit", "other")
ORACLES = ("Retrying in", "Waiting for API response", "You've hit",
           "Server is temporarily limiting", "API Error:")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
MODES = ("proxy", "base-url", "direct")


def timestamp(value):
    """ISO timestamps, including legacy naive UTC; never use file mtimes."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def session_id(value):
    return value if isinstance(value, str) and UUID.fullmatch(value) else None


def account_id(value):
    # Receipts may use personal account labels: expose an opaque stable ID only.
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return "account-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def allowed(path):
    """Block the forbidden subtree even through a symlink or explicit CLI path."""
    try:
        parts = path.resolve().parts
        return not any(a == "state" and b == "whatsapp-history"
                       for a, b in zip(parts, parts[1:]))
    except (OSError, RuntimeError):
        return False


def rows(path, diagnostics, prefix):
    """Streaming read, with content-free counters for every lost observation."""
    if not allowed(path):
        diagnostics[prefix + "_blocked"] += 1
        return
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError
                except ValueError:
                    diagnostics[prefix + "_malformed"] += 1
                    continue
                yield row
    except (OSError, UnicodeError):
        diagnostics[prefix + "_unreadable"] += 1


def event_text(row):
    message = row.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content", row.get("content", ""))
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(block["text"] for block in content
                     if isinstance(block, dict) and block.get("type") == "text"
                     and isinstance(block.get("text"), str))


def error_class(row):
    """One class per event; tool/thinking blocks and user prose aren't oracles."""
    if row.get("isSidechain") is True:
        return None
    text = event_text(row)
    message = row.get("message")
    role = row.get("type") or (message.get("role") if isinstance(message, dict) else None)
    if row.get("isApiErrorMessage") is not True and not (
        role in ("assistant", "system") and any(needle in text for needle in ORACLES)
    ):
        return None
    low = text.lower()
    if any(word in low for word in ("usage limit", "usage_limit", "weekly limit", "session limit",
                                    "hit your limit", "you've hit")):
        return "usage-limit"
    if any(word in low for word in ("429", "rate_limit", "rate limit", "rate-limit", "temporarily limiting")):
        return "rate-limit"
    if any(word in low for word in ("529", "overload")):
        return "overload"
    return "other"


class _Discard:
    """Suppress optional adapter output without retaining its contents."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


def ledger_accounts(state_dir):
    """Optional HAL read-only session join; never instantiate a mutable ledger.

    The current HAL checkout has no account_ledger module. A future adapter
    may expose read_session_rows(state_dir, read_only=True), yielding mappings
    with session_id and account_id. Without that explicit read-only API, skip
    with a fixed reason. A current account snapshot cannot identify past calls.
    Only unambiguous session associations are accepted; no time-based guesses.
    """
    if not allowed(state_dir):
        return {}, "skipped: forbidden ledger path"
    # Do not let an optional integration's diagnostics leak payloads to output.
    with contextlib.redirect_stdout(_Discard()), contextlib.redirect_stderr(_Discard()):
        try:
            module = importlib.import_module("lib.hal.account_ledger")
        except ImportError:
            return {}, "skipped: lib.hal.account_ledger unavailable"
        except Exception:
            return {}, "skipped: ledger import failed"
        reader = getattr(module, "read_session_rows", None)
        if not callable(reader):
            return {}, "skipped: ledger has no read-only session join API"
        try:
            accounts = {}
            for row in reader(state_dir, read_only=True):
                if not isinstance(row, dict):
                    continue
                sid = session_id(row.get("session_id"))
                aid = account_id(row.get("account_id"))
                if sid and aid != "unknown":
                    accounts.setdefault(sid, set()).add(aid)
            return accounts, "read-only session associations loaded"
        except Exception:
            return {}, "skipped: ledger read failed"


def transcript_index(projects, wanted, diagnostics):
    """Only projects/*/<known UUID>.jsonl; never recurse or open unrelated files."""
    found = {}
    if not allowed(projects):
        diagnostics["projects_blocked"] += 1
        return found
    try:
        with os.scandir(projects) as entries:
            for entry in entries:
                project = Path(entry.path)
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if not allowed(project):
                        diagnostics["projects_blocked"] += 1
                        continue
                    with os.scandir(project) as files:
                        for file in files:
                            sid = file.name.removesuffix(".jsonl")
                            if (file.name.endswith(".jsonl") and sid in wanted
                                    and file.is_file(follow_symlinks=False)):
                                found.setdefault(sid, []).append(Path(file.path))
                except OSError:
                    diagnostics["projects_unreadable"] += 1
    except OSError:
        diagnostics["projects_unreadable"] += 1
    return found


def summary(sessions):
    counts = Counter({kind: 0 for kind in CLASSES})
    for session in sessions:
        counts.update(session["errors"])
    clean = sum(s["status"] == "observed" and not sum(s["errors"].values()) for s in sessions)
    return {"sessions": len(sessions), "observed_sessions": sum(s["status"] == "observed" for s in sessions),
            "error_free_sessions": clean, "error_free_share": clean / len(sessions) if sessions else None,
            "error_events": sum(counts.values()), "errors_by_class": dict(counts)}


def report(receipts, projects, state_dir, since, until):
    """Inputs are explicit paths, never written; the result contains no content."""
    diagnostics = Counter()
    cohort = {}
    for row in rows(receipts, diagnostics, "receipts"):
        stamp = timestamp(row.get("ts"))
        if stamp is None:
            diagnostics["receipts_undated"] += 1
            continue
        if not since <= stamp < until:
            continue
        if row.get("interactive") is not True:
            diagnostics["receipts_noninteractive" if row.get("interactive") is False
                        else "receipts_interactivity_unknown"] += 1
            continue
        sid = session_id(row.get("session_id"))
        if sid is None:
            diagnostics["receipts_session_id_unknown"] += 1
            continue
        session = cohort.setdefault(sid, {"session_id": sid, "modes": set(), "accounts": set(),
                                          "harnesses": set()})
        session["modes"].add(row.get("wire_mode") if row.get("wire_mode") in MODES else "unknown")
        session["accounts"].add(account_id(row.get("account_id", row.get("account"))))
        session["harnesses"].add("claude-code" if row.get("harness") == "claude-code" else "unsupported")
    accounts, ledger_status = ledger_accounts(state_dir)
    index = transcript_index(projects, cohort, diagnostics)
    sessions = []
    for sid, source in sorted(cohort.items()):
        modes = source["modes"]
        aids = (source["accounts"] | accounts.get(sid, set())) - {"unknown"}
        session = {"session_id": sid, "wire_mode": next(iter(modes)) if len(modes) == 1 else "mixed",
                   "account": next(iter(aids)) if len(aids) == 1 else "multiple" if aids else "unknown",
                   "errors": {kind: 0 for kind in CLASSES}, "status": "missing"}
        paths = index.get(sid, [])
        if source["harnesses"] != {"claude-code"}:
            session["status"] = "unsupported-harness"
        elif len(paths) > 1:
            session["status"] = "ambiguous-transcript"
        elif paths:
            local = Counter()
            events = 0
            for row in rows(paths[0], local, "transcript"):
                if row.get("isSidechain") is True:
                    continue
                if row.get("sessionId", sid) != sid:
                    local["transcript_session_mismatch"] += 1
                    continue
                stamp = timestamp(row.get("timestamp"))
                relevant = row.get("type") in ("user", "assistant", "system") or row.get("isApiErrorMessage") is True
                if stamp is None:
                    if relevant:
                        local["transcript_undated"] += 1
                    continue
                if not since <= stamp < until:
                    continue
                events += int(relevant)
                kind = error_class(row)
                if kind:
                    session["errors"][kind] += 1
            session["status"] = "incomplete" if local else "observed" if events else "empty"
            diagnostics.update(local)
        sessions.append(session)
    by_mode = {key: summary([s for s in sessions if s["wire_mode"] == key])
               for key in sorted({s["wire_mode"] for s in sessions})}
    by_account = {key: summary([s for s in sessions if s["account"] == key])
                  for key in sorted({s["account"] for s in sessions})}
    return {"window": {"since": since.isoformat(), "until": until.isoformat()},
            **summary(sessions), "by_wire_mode": by_mode, "by_account": by_account,
            "sessions_detail": sessions, "diagnostics": dict(sorted(diagnostics.items())),
            "ledger": ledger_status,
            "scope": "Claude Code transcripts; launch cohort; capacity at error time unmeasured"}


def render(result):
    share = result["error_free_share"]
    lines = [f"Window: [{result['window']['since']}, {result['window']['until']})",
             f"Sessions in window: {result['sessions']}",
             f"Observed sessions: {result['observed_sessions']}",
             "Error-free share (confirmed / all): " + (f"{share:.1%}" if share is not None else "unknown"),
             f"Errors by class: {json.dumps(result['errors_by_class'], sort_keys=True)}"]
    for title, field in (("Wire mode", "by_wire_mode"), ("Account", "by_account")):
        for key, value in result[field].items():
            lines.append(f"{title} {key}: sessions={value['sessions']} errors={value['error_events']} "
                         + json.dumps(value["errors_by_class"], sort_keys=True))
    for session in result["sessions_detail"]:
        lines.append(f"Session {session['session_id']}: {session['status']} "
                     f"wire={session['wire_mode']} account={session['account']} "
                     + json.dumps(session["errors"], sort_keys=True))
    lines.extend(["Diagnostics: " + json.dumps(result["diagnostics"], sort_keys=True),
                  "Ledger: " + result["ledger"], result["scope"]])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=7, help="launch window length (default: 7 days)")
    parser.add_argument("--since", help="inclusive ISO timestamp; overrides --days")
    parser.add_argument("--until", help="exclusive ISO timestamp (default: now)")
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--projects", type=Path)
    parser.add_argument("--hal-state", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    until = timestamp(args.until) if args.until else datetime.now(timezone.utc)
    if not math.isfinite(args.days) or not 0 < args.days <= 36500 or until is None:
        parser.error("invalid window: use a positive finite day count and ISO timestamps")
    try:
        since = timestamp(args.since) if args.since else until - timedelta(days=args.days)
    except OverflowError:
        since = None
    if since is None or since >= until:
        parser.error("invalid window: since must precede until")
    root = get_claude_dir()
    result = report(args.receipts or root / "state" / "hal-infinite" / "wire.jsonl",
                    args.projects or root / "projects", args.hal_state or Path.home() / ".hal" / "state",
                    since, until)
    print(json.dumps(result, sort_keys=True) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
