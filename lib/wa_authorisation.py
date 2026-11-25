"""Read-authorisation gate for the WhatsApp mirror.

``state/whatsapp-history/`` is the operator's ENTIRE private WhatsApp — 143
threads at the time of writing. Until this module existed, nothing enforced
which of those a GRIP process could read: ``bin/grip-wa-chats`` resolved any
name, digits or JID and printed the transcript. The "read only the threads he
authorises" rule was prose in a memory file, enforced by nobody.

This is the mechanism that makes that rule real, and it is deliberately
**fail-closed**: with no allowlist file, no thread is readable.

    from lib.wa_authorisation import is_authorised, require, authorised_jids

    if is_authorised("27000000000@s.whatsapp.net"):
        ...
    require(jid)              # raises NotAuthorised with the reason

Allowlist file: ``$CLAUDE_DIR/state/wa-read-authorised.json`` (gitignored)::

    {
      "version": 1,
      "authorise_all": false,
      "granted_by": "V>>",
      "authorised": [
        {"jid": "27000000000@s.whatsapp.net",
         "granted_at_iso": "2026-09-10T00:00:00+02:00",
         "purpose": "forensic chronology"}
      ]
    }

``authorise_all`` is the operator's own explicit widening — full power stays
available (``rules/full-power-stop-override.md``), but the widening is a
deliberate, auditable act recorded in a file he owns, never an assumption
made by a brief or by this code.

Why the corrupt-file direction is INVERTED from lib/wa_control_state
--------------------------------------------------------------------
``wa_control_state.load()`` fails **safe to ACTIVE** on a corrupt file, which
is right for the relay: a truncated byte should not silently mute a contact.
For a READ gate the safe direction is the opposite — a corrupt opt-out record
must deny reading, never permit it. A future reader who "fixes" this to match
the sibling module turns a privacy gate into a leak, so the asymmetry is
stated here rather than left to be inferred.

Falsifier
---------
Wrong if any of these hold: a missing or malformed allowlist ever authorises
a thread; an opted-out contact is ever authorised regardless of the
allowlist; or ``authorise_all`` is ever set by anything other than the
operator editing the file. Verifier: ``tests/test_wa_authorisation.py``.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
import wa_control_state as _control  # noqa: E402

__all__ = [
    "NotAuthorised",
    "Verdict",
    "allowlist_path",
    "authorised_jids",
    "check",
    "is_authorised",
    "require",
]

CLAUDE_DIR = get_claude_dir()
ALLOWLIST_FILE = CLAUDE_DIR / "state" / "wa-read-authorised.json"
SCOPES_DIR = CLAUDE_DIR / "state" / "wa-scopes"


class NotAuthorised(PermissionError):
    """Raised when a thread is read without authorisation."""


@dataclass(frozen=True)
class Verdict:
    """The gate's answer, with the reason it reached it."""
    allowed: bool
    reason: str

    def __bool__(self) -> bool:  # so `if check(jid):` reads naturally
        return self.allowed


def allowlist_path(root: Path | str | None = None) -> Path:
    """Where the allowlist lives (overridable so tests never touch real state)."""
    return Path(root) / "wa-read-authorised.json" if root else ALLOWLIST_FILE


def _scopes_dir(root: Path | str | None) -> Path:
    return Path(root) / "wa-scopes" if root else SCOPES_DIR


def _load_allowlist(root: Path | str | None) -> tuple[dict | None, str]:
    """Return (document, reason). ``None`` document means: deny, with reason."""
    path = allowlist_path(root)
    if not path.exists():
        return None, f"no allowlist at {path} — nothing is authorised"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        # Deliberately NOT a silent zero: a corrupt gate must be loud and shut.
        return None, f"allowlist at {path} is unreadable ({exc}) — denying all"
    if not isinstance(data, dict):
        return None, f"allowlist at {path} is not a JSON object — denying all"
    return data, ""


def _opt_out_reason(jid: str, root: Path | str | None) -> str:
    """Non-empty when the contact has opted out — a veto over any allowlist."""
    try:
        slug = _control.slug_for_jid(jid)
    except ValueError as exc:
        return f"malformed JID ({exc})"
    scope = _scopes_dir(root) / slug
    if (scope / "tombstone.json").exists():
        return "contact has opted out (tombstone present)"
    control = scope / "control.json"
    if control.exists():
        try:
            data = json.loads(control.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # Inverted from wa_control_state on purpose — see module docstring.
            return "control state is unreadable; denying rather than assuming active"
        if data.get("state") == _control.STATE_OPTED_OUT:
            return "contact has opted out (control state)"
    return ""


def check(jid: str, root: Path | str | None = None) -> Verdict:
    """Full verdict for one thread. Opt-out always beats the allowlist."""
    veto = _opt_out_reason(jid, root)
    if veto:
        return Verdict(False, veto)

    data, reason = _load_allowlist(root)
    if data is None:
        return Verdict(False, reason)

    if data.get("authorise_all") is True:
        who = data.get("granted_by") or "unknown"
        return Verdict(True, f"authorise_all granted by {who}")

    for entry in data.get("authorised") or []:
        if isinstance(entry, dict) and entry.get("jid") == jid:
            purpose = entry.get("purpose") or "no purpose recorded"
            return Verdict(True, f"explicitly authorised ({purpose})")
        if isinstance(entry, str) and entry == jid:
            return Verdict(True, "explicitly authorised")

    return Verdict(False, "not on the allowlist")


def is_authorised(jid: str, root: Path | str | None = None) -> bool:
    return check(jid, root).allowed


def require(jid: str, root: Path | str | None = None) -> None:
    """Raise ``NotAuthorised`` unless the thread is readable."""
    verdict = check(jid, root)
    if not verdict.allowed:
        raise NotAuthorised(f"{jid}: {verdict.reason}")


def authorised_jids(candidates: list[str], root: Path | str | None = None) -> list[str]:
    """Filter a candidate list down to the readable threads, order preserved."""
    return [j for j in candidates if is_authorised(j, root)]
