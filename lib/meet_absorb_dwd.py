#!/usr/bin/env python3
"""Domain-Wide Delegation (DWD) core for Meet Absorb.

Impersonates any @codetonight.co.za Workspace user via a service account
to read Gemini Notes docs, export text, and inspect Calendar events.

Design principles (Rule 18 JIT, correct-by-construction):
- Single Responsibility: each function does one thing.
- Functions < 30 lines, cyclomatic complexity < 10.
- Dispatch tables over if/elif chains.
- Dependency injection for the credentials factory (testable).
- No hardcoded user emails; caller provides target_user.

External dependencies (declare via requirements, not imported here unless
the file is actually executed): google-api-python-client, google-auth,
tenacity. Import is lazy inside factory functions so tests can mock.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Protocol, Set, Tuple

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_KEY_PATH = CLAUDE_DIR / ".secrets" / "dwd-key.json"
PROCESSED_LOG = CLAUDE_DIR / "data" / "meet-transcripts" / ".processed.jsonl"

# Service account email for ADC-based impersonation mode.
# When set, lib uses Application Default Credentials + IAM Token Creator
# impersonation (no local key file needed). This is the modern path for
# Workspaces that enforce iam.disableServiceAccountKeyCreation.
DWD_SA_EMAIL = os.environ.get("DWD_SA_EMAIL", "")

SCOPES = (
    # Drive: full read + write + metadata
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.appdata",
    # Docs, Sheets, Slides, Forms
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    # Calendar: full read + write
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    # Gmail: full mailbox + send + settings
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    # Admin SDK Directory: user + group (read + write) — required for dynamic enumeration
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
    # Admin SDK Reports: audit + usage (requires admin auditor role in admin.google.com)
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.reports.usage.readonly",
    # Chat, Tasks, Keep, Meet
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/keep",
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    # People, Contacts, Directory
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.other.readonly",
    "https://www.googleapis.com/auth/directory.readonly",
    # Classroom
    "https://www.googleapis.com/auth/classroom.courses",
)

GEMINI_NOTES_QUERY = "name contains 'Notes by Gemini'"


class _CredentialsFactory(Protocol):
    """Protocol for loading + impersonating service account credentials."""

    def __call__(self, key_path: Path, target_user: str) -> Any: ...


@dataclass(frozen=True)
class GeminiDoc:
    """A Gemini Notes document discovered in a target user's Drive."""

    doc_id: str
    name: str
    modified_time: str
    owner: str
    created_time: str = ""


@dataclass(frozen=True)
class MeetingWindow:
    """A Calendar event representing a live/imminent meeting."""

    event_id: str
    summary: str
    start: str
    end: str
    attendees: Tuple[str, ...]
    organiser: str


@dataclass(frozen=True)
class GmailMessage:
    """A lightweight Gmail message summary suitable for inbox triage."""

    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipients: Tuple[str, ...]
    date_utc: str
    snippet: str
    permalink: str = ""


# ---------------------------------------------------------------------------
# Credentials + service builders
# ---------------------------------------------------------------------------


def _file_key_credentials_factory(key_path: Path, target_user: str) -> Any:
    """Legacy: load DWD credentials from a downloaded SA key file."""
    from google.oauth2 import service_account  # lazy import

    creds = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=list(SCOPES)
    )
    return creds.with_subject(target_user)


def _adc_impersonation_credentials_factory(
    key_path: Path,  # unused; kept to match the protocol
    target_user: str,
) -> Any:
    """Modern: ADC + IAM Signer + DWD subject (no local key file).

    Three-layer credential chain for DWD without a downloaded key:

    1. ADC source credentials (`gcloud auth application-default login`).
    2. `google.auth.iam.Signer` — signs JWTs on behalf of the SA by
       calling IAM `projects.serviceAccounts.signJwt`. The SA's private
       key stays inside Google; we only hold laurie@'s ADC token.
    3. `google.oauth2.service_account.Credentials` wrapping the IAM
       signer, with `subject=target_user` to trigger DWD at the OAuth
       token exchange.

    Requires:
    - `gcloud auth application-default login` as a principal holding
      `roles/iam.serviceAccountTokenCreator` on the SA.
    - DWD_SA_EMAIL env var set to the target service account email.
    - The SA's Client ID authorised in admin.google.com with the
      required scopes.

    Compatible with iam.disableServiceAccountKeyCreation org policies.
    """
    if not DWD_SA_EMAIL:
        raise RuntimeError(
            "DWD_SA_EMAIL env var must be set for ADC impersonation mode. "
            "Example: export DWD_SA_EMAIL=grip-meet-absorb-sa@<project>.iam.gserviceaccount.com"
        )

    from google.auth import default as auth_default  # lazy imports
    from google.auth import iam as google_iam
    from google.auth.transport.requests import Request as AuthRequest
    from google.oauth2 import service_account

    source_creds, _ = auth_default()
    request = AuthRequest()
    signer = google_iam.Signer(request, source_creds, DWD_SA_EMAIL)
    return service_account.Credentials(
        signer=signer,
        service_account_email=DWD_SA_EMAIL,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=list(SCOPES),
        subject=target_user,
    )


def _default_credentials_factory(key_path: Path, target_user: str) -> Any:
    """Dispatch to ADC impersonation (preferred) or file-key (legacy).

    ADC mode wins when DWD_SA_EMAIL is set in the environment. Otherwise
    falls back to the downloaded key file pattern for backward compatibility
    with existing installs.
    """
    if DWD_SA_EMAIL:
        return _adc_impersonation_credentials_factory(key_path, target_user)
    return _file_key_credentials_factory(key_path, target_user)


def _build_service(api: str, version: str, creds: Any) -> Any:
    """Build a Google API client service. Lazy import keeps tests light."""
    from googleapiclient.discovery import build  # lazy import

    return build(api, version, credentials=creds, cache_discovery=False)


def _client_for(
    target_user: str,
    api: str,
    version: str,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> Any:
    """Build an impersonated API client for target_user.

    ADC impersonation mode (DWD_SA_EMAIL set) does not require a key file.
    Legacy file-key mode requires key_path to exist.
    """
    if not DWD_SA_EMAIL and not key_path.exists():
        raise FileNotFoundError(
            f"DWD key missing: {key_path}. "
            f"Set DWD_SA_EMAIL env var to use ADC impersonation mode instead."
        )
    creds = credentials_factory(key_path, target_user)
    return _build_service(api, version, creds)


# ---------------------------------------------------------------------------
# Retry decorator (tenacity wrapper, no-op fallback if unavailable)
# ---------------------------------------------------------------------------


def _with_retry(func: Callable) -> Callable:
    """Apply tenacity exponential backoff if available, else passthrough."""
    try:
        from tenacity import (  # lazy import
            retry,
            stop_after_attempt,
            wait_exponential,
        )
    except ImportError:
        return func
    return retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )(func)


# ---------------------------------------------------------------------------
# Drive: list + export Gemini Notes
# ---------------------------------------------------------------------------


def _build_drive_query(since_date: Optional[str]) -> str:
    """Build a Drive query string, optionally bounded by modifiedTime."""
    parts = [GEMINI_NOTES_QUERY, "trashed = false"]
    if since_date:
        parts.append(f"modifiedTime >= '{since_date}T00:00:00'")
    return " and ".join(parts)


def _doc_from_api(raw: "dict") -> GeminiDoc:
    """Convert a Drive API file dict into a GeminiDoc."""
    owners = raw.get("owners") or [{}]
    return GeminiDoc(
        doc_id=raw.get("id", ""),
        name=raw.get("name", ""),
        modified_time=raw.get("modifiedTime", ""),
        owner=owners[0].get("emailAddress", ""),
        created_time=raw.get("createdTime", ""),
    )


@_with_retry
def list_gemini_docs(
    target_user: str,
    since_date: Optional[str] = None,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> List[GeminiDoc]:
    """List Gemini Notes docs in target_user's Drive.

    Args:
        target_user: Full email address to impersonate.
        since_date: ISO date (YYYY-MM-DD) lower bound on modifiedTime.
        key_path: Path to the DWD service account JSON key.
        credentials_factory: Injected for testability.
    """
    drive = _client_for(
        target_user, "drive", "v3", key_path, credentials_factory
    )
    resp = drive.files().list(
        q=_build_drive_query(since_date),
        fields="files(id,name,modifiedTime,createdTime,owners(emailAddress))",
        orderBy="modifiedTime desc",
        pageSize=50,
    ).execute()
    return [_doc_from_api(f) for f in resp.get("files", [])]


@_with_retry
def export_doc_as_text(
    target_user: str,
    doc_id: str,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> str:
    """Export a Google Doc as plain text (UTF-8)."""
    drive = _client_for(
        target_user, "drive", "v3", key_path, credentials_factory
    )
    data = drive.files().export(
        fileId=doc_id, mimeType="text/plain"
    ).execute()
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


# ---------------------------------------------------------------------------
# Calendar: find live meetings for a user
# ---------------------------------------------------------------------------


def _meeting_from_api(raw: "dict") -> MeetingWindow:
    """Convert a Calendar API event dict into a MeetingWindow."""
    attendees = tuple(
        a.get("email", "") for a in (raw.get("attendees") or []) if a.get("email")
    )
    return MeetingWindow(
        event_id=raw.get("id", ""),
        summary=raw.get("summary", ""),
        start=(raw.get("start") or {}).get("dateTime", ""),
        end=(raw.get("end") or {}).get("dateTime", ""),
        attendees=attendees,
        organiser=(raw.get("organizer") or {}).get("email", ""),
    )


# ---------------------------------------------------------------------------
# Gmail: list + fetch lightweight message summaries
# ---------------------------------------------------------------------------


def _header_map(raw: "dict") -> dict[str, str]:
    headers = (raw.get("payload") or {}).get("headers") or []
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def _date_to_utc(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _message_permalink(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{message_id}" if message_id else ""


def _gmail_message_from_api(raw: "dict") -> GmailMessage:
    """Convert a Gmail API message dict into a triage-safe summary."""
    headers = _header_map(raw)
    recipients = tuple(
        email for _, email in getaddresses([headers.get("to", "")]) if email
    )
    message_id = raw.get("id", "")
    return GmailMessage(
        message_id=message_id,
        thread_id=raw.get("threadId", ""),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        recipients=recipients,
        date_utc=_date_to_utc(headers.get("date", "")),
        snippet=raw.get("snippet", ""),
        permalink=_message_permalink(message_id),
    )


def _gmail_query(hours_back: int, labels: Iterable[str] | None) -> str:
    parts = [f"newer_than:{max(1, hours_back)}h"]
    for label in labels or ():
        if label.lower() == "unread":
            parts.append("is:unread")
        elif label.lower() == "inbox":
            parts.append("in:inbox")
        else:
            parts.append(f"label:{label}")
    return " ".join(parts)


@_with_retry
def _fetch_gmail_message(gmail: Any, message_id: str) -> GmailMessage:
    raw = gmail.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["From", "To", "Subject", "Date"],
    ).execute()
    return _gmail_message_from_api(raw)


@_with_retry
def get_gmail_message(
    target_user: str,
    message_id: str,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> GmailMessage:
    """Fetch one Gmail message summary via DWD impersonation."""
    gmail = _client_for(
        target_user, "gmail", "v1", key_path, credentials_factory
    )
    return _fetch_gmail_message(gmail, message_id)


@_with_retry
def list_gmail_messages(
    target_user: str,
    hours_back: int = 24,
    limit: int = 20,
    labels: Iterable[str] | None = ("INBOX", "UNREAD"),
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> List[GmailMessage]:
    """List recent Gmail message summaries via DWD impersonation."""
    gmail = _client_for(
        target_user, "gmail", "v1", key_path, credentials_factory
    )
    resp = gmail.users().messages().list(
        userId="me",
        q=_gmail_query(hours_back, labels),
        maxResults=limit,
    ).execute()
    refs = resp.get("messages") or []
    return [_fetch_gmail_message(gmail, ref["id"]) for ref in refs if ref.get("id")]


def _live_window(minutes_before: int, minutes_after: int) -> Tuple[str, str]:
    """Return (start, end) ISO timestamps bounding a live meeting window."""
    now = datetime.now(timezone.utc)
    lower = (now - timedelta(minutes=minutes_after)).isoformat()
    upper = (now + timedelta(minutes=minutes_before)).isoformat()
    return lower, upper


@_with_retry
def get_live_meeting_for(
    target_user: str,
    minutes_before: int = 5,
    minutes_after: int = 60,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> Optional[MeetingWindow]:
    """Return the target user's currently-live meeting, if any."""
    cal = _client_for(
        target_user, "calendar", "v3", key_path, credentials_factory
    )
    lower, upper = _live_window(minutes_before, minutes_after)
    resp = cal.events().list(
        calendarId="primary",
        timeMin=lower,
        timeMax=upper,
        singleEvents=True,
        orderBy="startTime",
        maxResults=10,
    ).execute()
    events = resp.get("items", [])
    return _meeting_from_api(events[0]) if events else None


# ---------------------------------------------------------------------------
# Dedupe log integration (shared with meet-absorb.sh)
# ---------------------------------------------------------------------------


def _read_processed_ids(log_path: Path = PROCESSED_LOG) -> Set[str]:
    """Read the set of already-absorbed doc IDs from the dedupe log."""
    if not log_path.exists():
        return set()
    ids: Set[str] = set()
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.add(json.loads(line).get("id", ""))
        except json.JSONDecodeError:
            continue
    return ids


def is_processed(doc_id: str, log_path: Path = PROCESSED_LOG) -> bool:
    """Return True if doc_id has already been absorbed."""
    return doc_id in _read_processed_ids(log_path)


def mark_processed(
    doc: GeminiDoc,
    dest_path: Path,
    log_path: Path = PROCESSED_LOG,
) -> None:
    """Append a dedupe log entry after a successful absorb."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": doc.doc_id,
        "name": doc.name,
        "owner": doc.owner,
        "absorbed_at": datetime.now(timezone.utc).isoformat(),
        "path": str(dest_path),
        "source": "dwd",
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Diff-tail helper for --watch mode
# ---------------------------------------------------------------------------


def diff_tail(previous: str, current: str) -> str:
    """Return the suffix of `current` that extends `previous`.

    If `current` is a superset that starts with `previous`, return the new
    suffix. Otherwise return the full `current` (treated as a reset).
    """
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous):]
    return current


# ---------------------------------------------------------------------------
# Cross-org gate
# ---------------------------------------------------------------------------

DEFAULT_HOME_DOMAIN = "codetonight.co.za"


def cross_org_attendees(
    attendees: Iterable[str],
    home_domain: str = DEFAULT_HOME_DOMAIN,
) -> List[str]:
    """Return attendees whose email is outside the home domain."""
    suffix = "@" + home_domain.lower()
    return [
        email for email in attendees
        if email and not email.lower().endswith(suffix)
    ]


# ---------------------------------------------------------------------------
# Admin SDK: dynamic domain user enumeration for --auto mode
# ---------------------------------------------------------------------------


def _iter_all_user_pages(admin: Any, domain: str) -> List[dict]:
    """Collect every Directory user page into a flat list.

    Pagination is sequential (each page needs the previous token) but
    flattening keeps the public API loop-depth = 1. Uses
    customer="my_customer" + viewType="domain_public" per
    plans/dwd-auto-403-debug.md (Gen 481 task #1052).
    """
    _ = domain  # Retained for back-compat signature.
    collected: List[dict] = []
    page_token = None
    while True:
        resp = admin.users().list(
            customer="my_customer",
            maxResults=500,
            orderBy="email",
            pageToken=page_token,
            viewType="domain_public",
        ).execute()
        collected.extend(resp.get("users") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return collected


def _is_active_user(user: dict) -> bool:
    """True if the Directory user is neither suspended nor archived."""
    return not (user.get("suspended") or user.get("archived"))


@_with_retry
def list_domain_users(
    admin_user: str,
    domain: str = DEFAULT_HOME_DOMAIN,
    key_path: Path = DEFAULT_KEY_PATH,
    credentials_factory: _CredentialsFactory = _default_credentials_factory,
) -> List[str]:
    """Enumerate all active user emails in the Workspace domain.

    Args:
        admin_user: Workspace admin email to impersonate. Must have
                    Directory API read privileges in admin.google.com.
        domain: Workspace domain to enumerate.
        key_path: Path to the DWD service account JSON key.
        credentials_factory: Injected for testability.

    Returns:
        Sorted list of active user email addresses in the domain.
    """
    admin = _client_for(
        admin_user, "admin", "directory_v1", key_path, credentials_factory
    )
    users = _iter_all_user_pages(admin, domain)
    emails = [u.get("primaryEmail", "") for u in users if _is_active_user(u)]
    return sorted(e for e in emails if e)
