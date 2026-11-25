"""AgentMail email API — syscall-doctrine shim.

Provider-stable surface over the AgentMail HTTP API. The shim hides API-key
retrieval inside its own process scope (macOS Keychain via `security`), so
call sites never reference the token directly. This matches the pattern of
`lib/slack_transport.py` and `lib/whatsapp_transport.py`.

Why no SDK: AgentMail ships an official `agentmail` PyPI package, but GRIP's
existing shims use stdlib `urllib.request` to keep the dependency surface flat
and the implementation hot-swappable. The function shape here mirrors what the
SDK exposes; if a future call site needs the SDK directly, the shim API
remains the boundary.

Errors are plain-language (Rule 17) — they tell the operator what to do, not
just what failed.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://api.agentmail.to"
_KEYCHAIN_SERVICE = "grip-agentmail"
_DEFAULT_TIMEOUT = 30
_INBOX_SAFE = "@."
_MESSAGE_SAFE = "@.<>"


class AgentMailError(Exception):
    """Plain-language error from the AgentMail shim."""


def _get_api_key() -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise AgentMailError(
            "macOS 'security' command not found — the AgentMail shim "
            "currently requires macOS Keychain for API-key storage."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise AgentMailError(
            f"AgentMail API key not found in macOS Keychain entry "
            f"'{_KEYCHAIN_SERVICE}'. Store it with:\n"
            f"  security add-generic-password -a grip -s {_KEYCHAIN_SERVICE} "
            f"-w <YOUR_KEY> -U"
        ) from exc

    key = result.stdout.strip()
    if not key:
        raise AgentMailError(
            f"Keychain entry '{_KEYCHAIN_SERVICE}' is empty. Re-store the key "
            f"with: security add-generic-password -a grip -s "
            f"{_KEYCHAIN_SERVICE} -w <YOUR_KEY> -U"
        )
    return key


def _drop_none(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if v is not None}


def _esc(value: str, safe: str = _INBOX_SAFE) -> str:
    return urllib.parse.quote(value, safe=safe)


def _encode_query(query: dict | None) -> str:
    if not query:
        return ""
    cleaned: dict = {}
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        else:
            cleaned[key] = value
    if not cleaned:
        return ""
    return "?" + urllib.parse.urlencode(cleaned, doseq=True)


def _humanise_http_error(method: str, path: str, exc: urllib.error.HTTPError) -> str:
    body_text = ""
    try:
        body_text = exc.read().decode("utf-8")
    except Exception:
        pass

    if exc.code == 401:
        return (
            "AgentMail rejected the API key (401 Unauthorized). Rotate it via "
            "the AgentMail dashboard, then re-store with: "
            f"security add-generic-password -a grip -s {_KEYCHAIN_SERVICE} "
            "-w <NEW_KEY> -U"
        )
    if exc.code == 403:
        return (
            f"AgentMail blocked the request (403 Forbidden) on {method} {path} "
            "— the API key lacks permission for this operation."
        )
    if exc.code == 404:
        return (
            f"AgentMail returned 404 Not Found for {path}. The inbox, message, "
            "or thread ID may be wrong, or the resource has been deleted."
        )
    if exc.code == 429:
        retry = exc.headers.get("Retry-After", "?") if exc.headers else "?"
        return f"AgentMail rate-limited the request (429). Retry after {retry} seconds."
    if 500 <= exc.code < 600:
        return (
            f"AgentMail server error ({exc.code}) on {method} {path}. "
            "Try again shortly."
        )
    return f"AgentMail error {exc.code} on {method} {path}: {body_text or exc.reason}"


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    url = _API_BASE + path + _encode_query(query)

    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Accept": "application/json",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise AgentMailError(_humanise_http_error(method, path, exc)) from exc
    except urllib.error.URLError as exc:
        raise AgentMailError(
            f"Could not reach AgentMail ({_API_BASE}): {exc.reason}. "
            "Check network connectivity."
        ) from exc

    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AgentMailError(
            f"AgentMail returned non-JSON on {method} {path}: {payload[:200]!r}"
        ) from exc


# ---- Inbox API ---------------------------------------------------------------

def list_inboxes(*, limit: int | None = None, page_token: str | None = None) -> dict:
    """List inboxes accessible to this API key."""
    return _request("GET", "/inboxes", query={"limit": limit, "page_token": page_token})


def create_inbox(
    *,
    username: str | None = None,
    domain: str | None = None,
    display_name: str | None = None,
    client_id: str | None = None,
) -> dict:
    """Create a new inbox. `client_id` enables idempotent retries."""
    return _request("POST", "/inboxes", body=_drop_none({
        "username": username,
        "domain": domain,
        "display_name": display_name,
        "client_id": client_id,
    }))


def get_inbox(inbox_id: str) -> dict:
    """Fetch a single inbox by ID (the email address)."""
    return _request("GET", f"/inboxes/{_esc(inbox_id)}")


# ---- Message API -------------------------------------------------------------

def list_messages(
    inbox_id: str,
    *,
    limit: int | None = None,
    labels: list[str] | None = None,
    before: str | None = None,
    after: str | None = None,
    ascending: bool | None = None,
    page_token: str | None = None,
) -> dict:
    """List messages in an inbox. `labels` filters; `before`/`after` paginate by time."""
    return _request(
        "GET",
        f"/inboxes/{_esc(inbox_id)}/messages",
        query={
            "limit": limit,
            "labels": labels,
            "before": before,
            "after": after,
            "ascending": ascending,
            "page_token": page_token,
        },
    )


def get_message(inbox_id: str, message_id: str) -> dict:
    """Fetch a single message including body, headers, attachments metadata."""
    return _request(
        "GET",
        f"/inboxes/{_esc(inbox_id)}/messages/{_esc(message_id, _MESSAGE_SAFE)}",
    )


def send_message(
    inbox_id: str,
    *,
    to: list[str],
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    labels: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Send an email from an inbox. Requires `to` and at least one of `text`/`html`."""
    if not to:
        raise AgentMailError("send_message requires at least one 'to' recipient.")
    if text is None and html is None:
        raise AgentMailError("send_message requires either 'text' or 'html' body.")
    return _request(
        "POST",
        f"/inboxes/{_esc(inbox_id)}/messages/send",
        body=_drop_none({
            "to": to,
            "subject": subject,
            "text": text,
            "html": html,
            "cc": cc,
            "bcc": bcc,
            "reply_to": reply_to,
            "labels": labels,
            "attachments": attachments,
        }),
    )


def reply_to_message(
    inbox_id: str,
    message_id: str,
    *,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Reply in-thread to a message. Subject and recipients are derived server-side."""
    if text is None and html is None:
        raise AgentMailError("reply_to_message requires either 'text' or 'html' body.")
    return _request(
        "POST",
        f"/inboxes/{_esc(inbox_id)}/messages/{_esc(message_id, _MESSAGE_SAFE)}/reply",
        body=_drop_none({
            "text": text,
            "html": html,
            "cc": cc,
            "bcc": bcc,
            "attachments": attachments,
        }),
    )


# ---- Thread API --------------------------------------------------------------

def list_threads(
    inbox_id: str | None = None,
    *,
    limit: int | None = None,
    labels: list[str] | None = None,
    page_token: str | None = None,
) -> dict:
    """List threads. With `inbox_id` -> per-inbox; without -> org-wide."""
    path = "/threads" if inbox_id is None else f"/inboxes/{_esc(inbox_id)}/threads"
    return _request("GET", path, query={
        "limit": limit, "labels": labels, "page_token": page_token,
    })


def get_thread(inbox_id: str, thread_id: str) -> dict:
    """Fetch a single thread with its message list."""
    return _request(
        "GET",
        f"/inboxes/{_esc(inbox_id)}/threads/{_esc(thread_id)}",
    )


# ---- Draft API ---------------------------------------------------------------

def list_drafts(
    inbox_id: str,
    *,
    limit: int | None = None,
    labels: list[str] | None = None,
    page_token: str | None = None,
) -> dict:
    """List drafts in an inbox."""
    return _request(
        "GET",
        f"/inboxes/{_esc(inbox_id)}/drafts",
        query={"limit": limit, "labels": labels, "page_token": page_token},
    )


def create_draft(
    inbox_id: str,
    *,
    to: list[str],
    subject: str | None = None,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    send_at: str | None = None,
) -> dict:
    """Create a draft. `send_at` (ISO-8601) schedules the draft for future delivery."""
    if not to:
        raise AgentMailError("create_draft requires at least one 'to' recipient.")
    if text is None and html is None:
        raise AgentMailError("create_draft requires either 'text' or 'html' body.")
    return _request(
        "POST",
        f"/inboxes/{_esc(inbox_id)}/drafts",
        body=_drop_none({
            "to": to,
            "subject": subject,
            "text": text,
            "html": html,
            "cc": cc,
            "bcc": bcc,
            "send_at": send_at,
        }),
    )


def send_draft(inbox_id: str, draft_id: str) -> dict:
    """Send a previously-created draft immediately."""
    return _request(
        "POST",
        f"/inboxes/{_esc(inbox_id)}/drafts/{_esc(draft_id)}/send",
    )


# ---- Webhook API -------------------------------------------------------------

def create_webhook(*, url: str, events: list[str]) -> dict:
    """Subscribe a URL to AgentMail events (e.g. 'message.received')."""
    if not url.startswith("https://"):
        raise AgentMailError("Webhook URL must use HTTPS.")
    if not events:
        raise AgentMailError("create_webhook requires at least one event subscription.")
    return _request("POST", "/webhooks", body={"url": url, "events": events})
