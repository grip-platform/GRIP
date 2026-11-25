#!/usr/bin/env python3
"""GRASP Google syscall shim — sovereign, read-only-by-default, receipts always.

Single entry point for every Google operation GRIP performs, backed by the
installed ``gog`` CLI (gogcli 0.32.0). gog owns auth (tokens live in its own
keyring); this shim never touches tokens and never calls Google HTTP endpoints
directly — gog IS the external boundary (``rules/syscall-doctrine.md``).

Capability lock (``rules/untrusted-content-capability-lock.md`` pattern):
every argv this module builds passes through ONE builder that always injects
the agent-safety flags — ``--json --readonly --gmail-no-send --no-input`` and
a pinned ``--account <email>``. A send/mutate operation is excluded by
construction, not by call-site discipline. Verified against the real CLI
(``gog --help`` / ``gog schema``, 2026-07-05): all flag spellings below exist
in gogcli 0.32.0; exit code 4 = auth_required, 3 = empty_results, 10 = config
(no OAuth client credentials yet).

Public API::

    from lib.grasp_google import (
        resolve_account, gmail_search, gmail_thread, gmail_attachments,
        auth_status, brief, known_accounts,
    )

Every Gmail result is a GRASP receipt dict:
``{account, message_id, thread_id, date, from, subject, snippet}`` —
deterministic pointers a skeptic can replay against the mailbox.

Failure semantics (Rule 17 — plain language):
missing binary -> ``GogNotInstalledError("gog is not installed — brew install
gogcli")``; no stored token -> ``GogAuthError("No Google login stored for
<email>. Run: gog auth add <email>")``; unknown alias -> ``ValueError``
listing the known aliases.

Falsifier: this shim is wrong if gog renames the safety flags or changes its
exit-code contract — ``tests/test_grasp_google.py`` pins both (mutation
removing any safety flag fails the canary).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from lib.claude_dir import get_claude_dir

__all__ = [
    "GogAuthError",
    "GogError",
    "GogNotInstalledError",
    "auth_status",
    "brief",
    "gmail_attachments",
    "gmail_search",
    "gmail_thread",
    "known_accounts",
    "resolve_account",
]

# ---------------------------------------------------------------------------
# Account registry (constants + env-var accounts + optional override file)
# ---------------------------------------------------------------------------
# "work" is a business role address at the operator's own domain (allowed
# per rules/no-sensitive-info-in-grip.md's owner-identity carve-out) and
# stays a literal default. "personal"/"bliksem" have no safe default to
# ship in source — each is read from its own environment variable instead
# (set it in your own shell/profile; never commit a value). Unset means the
# alias is simply not available until configured, via the env var or the
# override file below.
_ACCOUNTS: dict[str, str] = {
    "work": "development@codetonight.co.za",
}
_ENV_ACCOUNT_VARS: dict[str, str] = {
    "personal": "GRASP_GOOGLE_PERSONAL_EMAIL",
    "bliksem": "GRASP_GOOGLE_BLIKSEM_EMAIL",
}
_ACCOUNTS_OVERRIDE: Path = get_claude_dir() / "state" / "grasp" / "accounts.json"

# ---------------------------------------------------------------------------
# gog contract (verified 2026-07-05 against gogcli 0.32.0 --help/schema)
# ---------------------------------------------------------------------------
_GOG_BIN = "gog"
_SAFETY_FLAGS: tuple[str, ...] = ("--json", "--readonly", "--gmail-no-send", "--no-input")
_EXIT_EMPTY = 3          # gog schema: empty_results
_EXIT_AUTH_REQUIRED = 4  # gog schema: auth_required
_EXIT_CONFIG = 10        # gog schema: config (no OAuth client credentials yet)
_TIMEOUT_S = 120
_AUTH_STDERR_HINTS = ("auth add", "no stored token", "not authenticated", "no account")
_CONFIG_STDERR_HINTS = ("auth credentials",)
_NOT_INSTALLED_MSG = "gog is not installed — brew install gogcli"

# Receipt normalisation dispatch table: receipt field -> candidate source keys,
# first non-empty wins. Covers gog's flattened rows (lowercase keys) AND raw
# Gmail API payload headers (capitalised names). O(n) over rows.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "message_id": ("messageId", "message_id", "id"),
    "thread_id": ("threadId", "thread_id", "id"),
    "date": ("date", "Date", "internalDate"),
    "from": ("from", "From", "sender"),
    "subject": ("subject", "Subject"),
    "snippet": ("snippet",),
}


class GogError(RuntimeError):
    """A gog invocation failed; message is plain language."""


class GogNotInstalledError(GogError):
    """The gog binary is missing from PATH."""


class GogAuthError(GogError):
    """gog has no stored Google login for the requested account."""


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def known_accounts() -> dict[str, str]:
    """Alias -> email registry: constants, then each environment-variable
    account whose variable is set (``personal``/``bliksem`` — see
    ``_ENV_ACCOUNT_VARS``), then the optional override file
    ``state/grasp/accounts.json`` (override wins on alias collision)."""
    registry = dict(_ACCOUNTS)
    for alias, env_name in _ENV_ACCOUNT_VARS.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            registry[alias] = value
    try:
        override = json.loads(_ACCOUNTS_OVERRIDE.read_text(encoding="utf-8"))
        if isinstance(override, dict):
            registry.update({k: v for k, v in override.items() if isinstance(v, str) and "@" in v})
    except (OSError, ValueError):
        pass  # optional file: absent/unreadable/invalid -> constants only
    return registry


def resolve_account(name: str) -> str:
    """Resolve an account alias to its email. Any string containing '@'
    passes through as a literal account email."""
    if "@" in name:
        return name
    registry = known_accounts()
    email = registry.get(name)
    if email is None:
        aliases = ", ".join(f"{a} ({registry[a]})" for a in sorted(registry))
        raise ValueError(
            f"Unknown Google account alias {name!r}. Known aliases: {aliases}. "
            "Use one of those, or pass a full email address."
        )
    return email


# ---------------------------------------------------------------------------
# The single argv builder + runner (the capability lock)
# ---------------------------------------------------------------------------
def _build_argv(subcommand: tuple[str, ...], account_email: str, *extra: str) -> list[str]:
    """The ONE argv chokepoint: read-only + no-send + JSON + pinned account,
    injected unconditionally. All public ops route through here."""
    return [_GOG_BIN, *subcommand, *extra, *_SAFETY_FLAGS, "--account", account_email]


def _run_gog(argv: list[str], account_email: str) -> Any:
    """Run gog, return parsed JSON. Plain-language errors on every failure."""
    if shutil.which(_GOG_BIN) is None:
        raise GogNotInstalledError(_NOT_INSTALLED_MSG)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except FileNotFoundError as exc:  # binary vanished between which() and run()
        raise GogNotInstalledError(_NOT_INSTALLED_MSG) from exc
    return _interpret(proc, account_email)


def _looks_auth_error(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(hint in low for hint in _AUTH_STDERR_HINTS)


def _looks_config_error(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(hint in low for hint in _CONFIG_STDERR_HINTS)


def _interpret(proc: "subprocess.CompletedProcess[str]", account_email: str) -> Any:
    """Map gog's exit-code contract to values / plain-language errors.

    The config branch runs before the auth branch: a pre-auth machine's
    stderr ("run gog auth credentials ... then gog auth add ...") can match
    both hint sets, and the OAuth client is the earlier missing step.
    """
    if proc.returncode == _EXIT_CONFIG or (
        proc.returncode != 0 and _looks_config_error(proc.stderr)
    ):
        raise GogAuthError(
            "gog has no Google OAuth client configured yet. "
            "Run: gog auth credentials <client.json>, "
            f"then: gog auth add {account_email}"
        )
    if proc.returncode == _EXIT_AUTH_REQUIRED or (
        proc.returncode != 0 and _looks_auth_error(proc.stderr)
    ):
        raise GogAuthError(
            f"No Google login stored for {account_email}. "
            f"Run: gog auth add {account_email}"
        )
    if proc.returncode == _EXIT_EMPTY:
        return {}
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = lines[-1] if lines else f"exit code {proc.returncode}"
        raise GogError(f"gog command failed: {tail}")
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise GogError(f"gog returned output that is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Receipt normalisation (O(n), dispatch-table driven)
# ---------------------------------------------------------------------------
def _headers_map(row: dict) -> dict[str, str]:
    """Flatten Gmail API ``payload.headers`` [{name, value}] into a dict."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    headers = payload.get("headers") or row.get("headers") or []
    return {
        h["name"]: h.get("value", "")
        for h in headers
        if isinstance(h, dict) and h.get("name")
    }


def _receipt(account: str, row: dict) -> dict:
    """Normalise one gog row (flattened or raw Gmail shape) into a receipt."""
    scalars = {k: v for k, v in row.items() if not isinstance(v, (dict, list))}
    merged = {**_headers_map(row), **scalars}
    out: dict[str, Any] = {"account": account}
    for field, candidates in _FIELD_CANDIDATES.items():
        out[field] = next(
            (merged[k] for k in candidates if merged.get(k) not in (None, "")), ""
        )
    return out


def _rows(data: Any, keys: tuple[str, ...]) -> list[dict]:
    """Extract the row list from a gog JSON body (bare list or envelope)."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


# ---------------------------------------------------------------------------
# Public Gmail ops (all read-only by construction)
# ---------------------------------------------------------------------------
def gmail_search(account: str, query: str, max_results: int = 20) -> list[dict]:
    """Search Gmail (thread-level, Gmail query syntax) -> receipt list."""
    email = resolve_account(account)
    argv = _build_argv(("gmail", "search"), email, query, "--max", str(max_results))
    data = _run_gog(argv, email)
    return [_receipt(email, r) for r in _rows(data, ("threads", "messages", "results"))]


def gmail_thread(account: str, thread_id: str) -> dict:
    """Fetch one thread with per-message receipts."""
    email = resolve_account(account)
    data = _run_gog(_build_argv(("gmail", "thread", "get"), email, thread_id), email)
    body = data if isinstance(data, dict) else {}
    thread = body["thread"] if isinstance(body.get("thread"), dict) else body
    return {
        "account": email,
        "thread_id": thread.get("id") or thread_id,
        "messages": [_receipt(email, m) for m in _rows(thread, ("messages",))],
    }


def _attachment_parts(payload: dict) -> list[dict]:
    """Walk a Gmail payload tree, collecting attachment {id, filename} pairs."""
    found: list[dict] = []
    stack: list[Any] = [payload]
    while stack:
        part = stack.pop()
        if not isinstance(part, dict):
            continue
        att_id = (part.get("body") or {}).get("attachmentId")
        if att_id:
            found.append({"attachment_id": att_id, "filename": part.get("filename") or str(att_id)})
        stack.extend(part.get("parts") or [])
    return found


def _attachments_of(message: dict) -> list[dict]:
    """All attachments of a message: gog-flattened list + payload walk, deduped."""
    flat = [
        {"attachment_id": a.get("attachmentId") or a.get("id"),
         "filename": a.get("filename") or ""}
        for a in message.get("attachments", []) if isinstance(a, dict)
    ]
    candidates = [a for a in flat if a["attachment_id"]]
    candidates += _attachment_parts(message.get("payload") or {})
    seen: set = set()
    unique = []
    for att in candidates:
        if att["attachment_id"] not in seen:
            seen.add(att["attachment_id"])
            att["filename"] = att["filename"] or str(att["attachment_id"])
            unique.append(att)
    return unique


def gmail_attachments(account: str, message_id: str, out_dir: str) -> list[str]:
    """Download every attachment of a message into ``out_dir`` -> file paths.

    Two verified gog calls: ``gmail get <id> --format full`` to discover
    attachment ids, then ``gmail attachment <messageId> <attachmentId>
    --out <dir> --name <filename>`` per attachment.
    """
    email = resolve_account(account)
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    data = _run_gog(
        _build_argv(("gmail", "get"), email, message_id, "--format", "full"), email
    )
    message = data["message"] if isinstance(data, dict) and isinstance(data.get("message"), dict) else data
    paths: list[str] = []
    for att in _attachments_of(message if isinstance(message, dict) else {}):
        argv = _build_argv(
            ("gmail", "attachment"), email, message_id, att["attachment_id"],
            "--out", str(out), "--name", att["filename"],
        )
        _run_gog(argv, email)
        paths.append(str(out / att["filename"]))
    return paths


def auth_status() -> list[dict]:
    """Accounts gog holds stored logins for, from ``gog auth list`` JSON.

    Uses account ``auto`` (a documented gog value) so even this local, no-API
    listing rides the same locked argv builder as every other op.
    """
    data = _run_gog(_build_argv(("auth", "list"), "auto"), "auto")
    entries = _rows(data, ("accounts",))
    return [{**e, "email": e.get("email") or e.get("account") or ""} for e in entries]


# ---------------------------------------------------------------------------
# brief() — search + sovereign HAL summary, deterministic-digest fail-soft
# ---------------------------------------------------------------------------
def _hal_summarise(question: str, receipts: list[dict]) -> str:
    """Summarise receipts via GRIP's canonical AI syscall (lib/hal_llm_adapter
    — sovereign multi-provider ladder, never a direct provider SDK call).
    Lazy import keeps this shim import-light when HAL is not needed."""
    from lib.hal_llm_adapter import HALLLMAdapter

    lines = [
        f"- {r['date']} | from {r['from']} | {r['subject']} | {r['snippet']}"
        for r in receipts
    ]
    prompt = (
        f"{question}\n\nEmail receipts (metadata only, newest first):\n"
        + "\n".join(lines)
        + "\n\nAnswer in plain language, citing senders and dates from the receipts."
    )
    return (HALLLMAdapter().generate(prompt) or "").strip()


def _deterministic_digest(receipts: list[dict]) -> str:
    """Zero-LLM fallback digest: counts + top subjects + date span."""
    if not receipts:
        return "deterministic digest (HAL offline): 0 matching emails."
    dates = sorted(str(r["date"]) for r in receipts if r.get("date"))
    span = f" spanning {dates[0]} to {dates[-1]}" if dates else ""
    subjects = "; ".join(str(r["subject"]) for r in receipts if r.get("subject"))[:400]
    return (
        f"deterministic digest (HAL offline): {len(receipts)} matching emails{span}. "
        f"Top subjects: {subjects or '(no subjects)'}."
    )


def brief(account: str, query: str, question: Optional[str] = None) -> dict:
    """Search + summarise -> ``{answer, receipts}``. FAIL-SOFT: any HAL
    error/unreachability degrades to the deterministic digest — receipts are
    identical either way, so the evidence never depends on the LLM."""
    email = resolve_account(account)
    receipts = gmail_search(email, query)
    ask = question or f"Summarise these emails matching {query!r} for the operator."
    try:
        answer = _hal_summarise(ask, receipts)
        if not answer:
            raise GogError("HAL returned an empty answer")
    except Exception:  # HAL offline/erroring must never break the brief
        answer = _deterministic_digest(receipts)
    return {"answer": answer, "receipts": receipts}
