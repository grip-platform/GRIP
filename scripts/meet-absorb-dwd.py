#!/usr/bin/env python3
"""meet-absorb-dwd.py — DWD CLI wrapper for Meet Absorb.

Replaces scripts/meet-absorb.sh for the Domain-Wide Delegation path.
Impersonates any @codetonight.co.za Workspace user via a service account.

Usage:
    meet-absorb-dwd.py --user development@codetonight.co.za --latest
    meet-absorb-dwd.py --auto
    meet-absorb-dwd.py --watch 30 --user development@codetonight.co.za
    meet-absorb-dwd.py --list --user development@codetonight.co.za

Safety:
    --allow-cross-org is required when attendees include non-home-domain emails.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_CLAUDE_DIR_BOOTSTRAP = Path(
    os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude"))
)


def _bootstrap_env_file(keys=("DWD_SA_EMAIL", "DWD_ADMIN_USER")):
    """Load selected keys from ~/.claude/.env into os.environ if not set.

    Runs BEFORE importing lib.meet_absorb_dwd so that module-level constants
    read from os.environ get populated correctly. Uses targeted line parsing
    (not `source` / set -a) so unrelated secrets in .env never leak into
    the process environment.
    """
    env_file = _CLAUDE_DIR_BOOTSTRAP / ".env"
    if not env_file.exists():
        return
    wanted = set(keys)
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k not in wanted or os.environ.get(k):
            continue
        os.environ[k] = v.strip().strip('"').strip("'")


_bootstrap_env_file()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.meet_absorb_dwd import (  # noqa: E402
    DEFAULT_HOME_DOMAIN,
    GeminiDoc,
    MeetingWindow,
    cross_org_attendees,
    diff_tail,
    export_doc_as_text,
    get_live_meeting_for,
    is_processed,
    list_domain_users,
    list_gemini_docs,
    mark_processed,
)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
TRANSCRIPT_DIR = CLAUDE_DIR / "data" / "meet-transcripts"
DEFAULT_ADMIN_USER = os.environ.get("DWD_ADMIN_USER", "licensing@codetonight.co.za")
DEFAULT_USERS_FILE = CLAUDE_DIR / "data" / "workspace-users.txt"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _log(msg):
    print("[meet-absorb-dwd] " + msg)


def _warn(msg):
    print("[meet-absorb-dwd] WARN: " + msg, file=sys.stderr)


def _err(msg):
    print("[meet-absorb-dwd] ERROR: " + msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# File sanitisation
# ---------------------------------------------------------------------------


_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_name(raw):
    """Sanitise a Gemini doc name into a filename-safe slug."""
    stripped = raw.replace(" - Notes by Gemini", "")
    slug = _UNSAFE_CHARS.sub("_", stripped).strip("_")
    return slug or "untitled"


# ---------------------------------------------------------------------------
# Absorb core
# ---------------------------------------------------------------------------


def _absorb_doc(target_user, doc):
    """Export a single doc and write it to the transcript dir."""
    if is_processed(doc.doc_id):
        _log("Already processed: " + doc.name)
        return None
    text = export_doc_as_text(target_user, doc.doc_id)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    dest = TRANSCRIPT_DIR / (_safe_name(doc.name) + ".txt")
    dest.write_text(text, encoding="utf-8")
    mark_processed(doc, dest)
    _log("Saved: {} ({} chars) [owner={}]".format(dest, len(text), doc.owner))
    return dest


def _latest_for_user(target_user):
    """Return the most recently modified Gemini doc for target_user."""
    today = datetime.now(timezone.utc).date().isoformat()
    docs = list_gemini_docs(target_user, since_date=today)
    return docs[0] if docs else None


# ---------------------------------------------------------------------------
# Cross-org gate
# ---------------------------------------------------------------------------


def _gate_cross_org(meeting, allow_cross_org, home_domain=DEFAULT_HOME_DOMAIN):
    """Return True if the absorb may proceed, False if blocked."""
    external = cross_org_attendees(meeting.attendees, home_domain)
    if not external:
        return True
    if allow_cross_org:
        _warn("Cross-org attendees present: {} (allowed by flag)".format(external))
        return True
    _warn(
        "Cross-org attendees present: {}. Pass --allow-cross-org to absorb anyway.".format(external)
    )
    return False


# ---------------------------------------------------------------------------
# Domain user enumeration (for --auto) — dynamic via Admin SDK Directory API
# ---------------------------------------------------------------------------


def _read_users_file(path):
    """Parse one email per line, stripping `#` comments and blanks."""
    users = set()
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and "@" in line:
            users.add(line)
    return sorted(users)


def _load_domain_users(
    admin_user=DEFAULT_ADMIN_USER,
    domain=DEFAULT_HOME_DOMAIN,
    users_file=None,
):
    """Enumerate active domain users via file (preferred) or Admin SDK.

    Priority: explicit --users-file > DEFAULT_USERS_FILE if present > Admin SDK.
    File path bypasses Admin SDK entirely — use it when Workspace org policy
    blocks SA→Directory API (tracked in #1052).
    """
    path = users_file or (DEFAULT_USERS_FILE if DEFAULT_USERS_FILE.exists() else None)
    if path:
        try:
            return _read_users_file(path)
        except FileNotFoundError as exc:
            _err("Users file not found: {}".format(exc))
            return []
    try:
        return list_domain_users(admin_user, domain=domain)
    except FileNotFoundError as exc:
        _err(str(exc))
        return []
    except Exception as exc:  # noqa: BLE001 - surface API errors cleanly
        _err("Directory API failed: {}: {}".format(type(exc).__name__, exc))
        return []


# ---------------------------------------------------------------------------
# Command handlers (dispatch table below)
# ---------------------------------------------------------------------------


def cmd_list(args):
    docs = list_gemini_docs(args.user)
    for d in docs:
        print("{}  {}  {}".format(d.modified_time[:10], d.doc_id[:16], d.name))
    print("{} doc(s) for {}".format(len(docs), args.user))
    return 0


def cmd_latest(args):
    doc = _latest_for_user(args.user)
    if not doc:
        _warn("No Gemini docs found for " + args.user)
        return 0
    _absorb_doc(args.user, doc)
    return 0


def _auto_scan_users(users, allow_cross_org):
    """Scan each domain user for a live meeting and absorb organiser's doc."""
    absorbed = 0
    for user in users:
        meeting = get_live_meeting_for(user)
        if not meeting:
            continue
        if not _gate_cross_org(meeting, allow_cross_org):
            continue
        organiser = meeting.organiser or user
        _log("Live meeting: {} (organiser={})".format(meeting.summary, organiser))
        doc = _latest_for_user(organiser)
        if doc and _absorb_doc(organiser, doc):
            absorbed += 1
    _log("Absorbed {} doc(s) in --auto pass".format(absorbed))
    return 0


def cmd_auto(args):
    admin_user = args.admin_user or DEFAULT_ADMIN_USER
    users = _load_domain_users(admin_user=admin_user, users_file=args.users_file)
    if not users:
        return 1
    using_file = bool(args.users_file) or DEFAULT_USERS_FILE.exists()
    source = "users-file" if using_file else "Admin SDK"
    _log("Enumerated {} active domain user(s) via {}".format(len(users), source))
    return _auto_scan_users(users, args.allow_cross_org)


def _watch_once(target_user, previous):
    """Run one tick of --watch. Returns the current full text."""
    doc = _latest_for_user(target_user)
    if not doc:
        return previous
    text = export_doc_as_text(target_user, doc.doc_id)
    new_suffix = diff_tail(previous, text)
    if new_suffix:
        print(new_suffix, end="" if new_suffix.endswith("\n") else "\n")
    return text


def cmd_watch(args):
    interval = args.watch
    previous = ""
    _log("Watching {} every {}s (Ctrl+C to stop)".format(args.user, interval))
    try:
        while True:
            previous = _watch_once(args.user, previous)
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("Stopped")
    return 0


# ---------------------------------------------------------------------------
# Dispatch + argparse
# ---------------------------------------------------------------------------


COMMANDS = {
    "list": cmd_list,
    "latest": cmd_latest,
    "auto": cmd_auto,
    "watch": cmd_watch,
}


def _select_command(args):
    """Resolve which command to run from mutually-exclusive flags."""
    if args.list:
        return "list"
    if args.auto:
        return "auto"
    if args.watch:
        return "watch"
    return "latest"


def _validate_args(args, command):
    """Return an error string if args are invalid for the selected command."""
    if command in ("list", "latest", "watch") and not args.user:
        return "--user is required for --" + command
    return None


def _build_parser():
    p = argparse.ArgumentParser(description="DWD Meet Absorb CLI")
    p.add_argument("--user", help="Target user email to impersonate")
    p.add_argument("--latest", action="store_true", help="Absorb latest doc")
    p.add_argument("--list", action="store_true", help="List docs for user")
    p.add_argument("--auto", action="store_true", help="Scan domain for live meetings")
    p.add_argument("--watch", type=int, metavar="SECONDS", help="Poll + diff-tail")
    p.add_argument(
        "--allow-cross-org",
        action="store_true",
        help="Allow absorb when attendees include non-codetonight.co.za emails",
    )
    p.add_argument(
        "--admin-user",
        default=None,
        help="Workspace admin to impersonate for Directory API in --auto mode",
    )
    p.add_argument(
        "--users-file",
        default=None,
        metavar="PATH",
        help="Read Workspace users from file (one email per line) instead of Admin SDK",
    )
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    command = _select_command(args)
    error = _validate_args(args, command)
    if error:
        _err(error)
        return 2
    try:
        return COMMANDS[command](args)
    except FileNotFoundError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - surface API errors cleanly
        _err("{}: {}".format(type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
