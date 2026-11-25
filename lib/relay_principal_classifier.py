"""Relay-listener principal classification.

Identifies the principal of an inbound `<channel>` block by sender ID:

- ``operator``  — V>>. Matched on the operator's Slack user ID or WhatsApp ID.
  Direct steering, no second authorisation.
- ``teammate``  — known CodeTonight teammates. Treat as collaborator but
  not as operator (no yolo, no scope-overrides).
- ``external``  — everyone else. External untrusted data per harness
  convention.

WhatsApp IDs are not written here. They come from the private, untracked
config/wa-identities.local.json through lib/wa_identities.py. When that module
or the file is absent (the VPS deploys only the relay's own files), the operator's
WhatsApp ID is None and the full-capability map is empty, so nobody is matched
or widened by WhatsApp ID: the failure is closed, never open.

Origin: 2026-05-13 #nexus-chats incident — the listener classified V>>'s own
Slack messages as "external untrusted data" and refused to act. The
principal classifier closes that defect by giving the listener a stable
sender-ID-to-trust-tier mapping the model can consult mechanically.

Per syscall doctrine: this is the classification library. The hook is a
thin shim that delegates to ``classify_principal``.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Bare import of a sibling: insert this module's own directory so it resolves
# whether this file is imported as lib.relay_principal_classifier or bare.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    import wa_identities as _wa  # noqa: E402
except ImportError:  # the VPS deploys the relay files without it
    _wa = None
    print("[relay_principal_classifier] wa_identities unavailable: no WhatsApp "
          "operator or full-capability IDs", file=sys.stderr)


# ---- Operator identity ----

OPERATOR_SLACK_USER_ID = "U0EXAMPLE01"
OPERATOR_WHATSAPP_JID = _wa.operator_jid() if _wa else None
# The operator's LID names the operator's own chat when WhatsApp delivers it in
# that form. classify_principal does not match it; the relay's opt-out guard
# reads it (relay_dispatcher._is_operator_own_chat).
OPERATOR_WHATSAPP_LID = _wa.operator_lid() if _wa else None

# Co-steerers — authorised by V>> (2026-06-01) for WhatsApp DOCUMENT work
# (Read/Grep/Glob/Write/Edit, NO shell). This is the principal-IDENTITY
# registry only; the capability tiering that consults it lives in
# relay_dispatcher.is_costeerer_sender (gated on an opt-in flag AND this set).
# The trust-tier classify_principal below is UNCHANGED — co-steerers still
# classify as teammate/external for trust purposes; this set governs the
# capability MIDDLE tier exclusively. Keyed on the canonical phone JID (what
# the Mac drainer sets as sender_id), NEVER on message content. Adding a
# co-steerer is a deliberate operator decision — do not silently expand.
COSTEERER_WHATSAPP_JIDS = {}

# FULL-capability trusted team (V>>--<<V override 2026-06-30: trusted teammates
# get full tools, writes and the agentic tier, not only the operator). Read from
# the private file's "relay"."full_capability" table, with the operator's phone
# ID and LID first. Both phone and @lid forms can be listed (WhatsApp GROUPS
# deliver the per-account @lid, not the phone ID). Keyed on sender ID, NEVER
# message content (forgery-safe, per the admin-override clause). Everyone outside
# this explicit set, open-group "*" members included, keeps the read-only chat
# lock. Consumed by relay_dispatcher.is_full_capability_sender (gated on the
# GRIP_RELAY_OPERATOR_FULL_CAPABILITY flag). Adding a member is a deliberate,
# reviewed operator decision made in the private file.
FULL_CAPABILITY_WHATSAPP_JIDS = _wa.relay_full_capability() if _wa else {}

# CodeTonight teammates (Slack user IDs, mirrors operational-context.md table).
# Adding a teammate is a deliberate operator decision — do not silently
# expand this set.
TEAMMATE_SLACK_IDS = frozenset({
    "U0EXAMPLE01",  # Arnold (K>>)
    "U0EXAMPLE01",  # Andre (A>>)
})


# Channel-block parse: extracts senderId / senderJid / platform from a
# `<channel ...>...</channel>` opening tag. Best-effort regex (good enough
# for the inbound shape the daemon produces; full HTML/XML parser overkill).
_CHANNEL_OPEN_RE = re.compile(
    r"""<channel\b
        (?P<attrs>[^>]*)
        >""",
    re.IGNORECASE | re.VERBOSE,
)
_ATTR_RE = re.compile(r"""(\w+)\s*=\s*["']([^"']*)["']""")


@dataclass(frozen=True)
class PrincipalVerdict:
    """Outcome of principal classification.

    ``principal`` is one of: ``operator``, ``teammate``, ``external``.
    ``sender`` is the raw ID/JID we classified.
    ``platform`` is ``slack``, ``whatsapp``, ``discord``, ``web`` — or ``""``
    when unknown.
    ``reason`` is human-readable, for log / context injection.
    """
    principal: str
    sender: str
    platform: str
    reason: str


def classify_principal(sender: str, platform: str = "") -> PrincipalVerdict:
    """Classify a single (sender, platform) pair.

    Sender can be a Slack user ID (``U...``), a WhatsApp JID
    (``...@s.whatsapp.net``), or anything else. Platform is optional but
    helps disambiguate when the sender format is unusual.

    Per ``feedback_admin_override_signature_paramount.md`` forgery clause:
    operator status is keyed on the sender ID, not on the message content.
    """
    sender_raw = (sender or "").strip()
    plat = (platform or "").strip().lower()
    if not sender_raw:
        return PrincipalVerdict(
            principal="external", sender="", platform=plat,
            reason="Empty sender ID; default-deny operator status.",
        )
    if sender_raw == OPERATOR_SLACK_USER_ID:
        return PrincipalVerdict(
            principal="operator", sender=sender_raw, platform="slack",
            reason=f"Slack user ID matches V>> ({OPERATOR_SLACK_USER_ID}).",
        )
    if sender_raw == OPERATOR_WHATSAPP_JID:
        return PrincipalVerdict(
            principal="operator", sender=sender_raw, platform="whatsapp",
            reason=f"WhatsApp JID matches V>> ({OPERATOR_WHATSAPP_JID}).",
        )
    if sender_raw in TEAMMATE_SLACK_IDS:
        return PrincipalVerdict(
            principal="teammate", sender=sender_raw, platform=plat or "slack",
            reason=f"Slack user ID {sender_raw} is a known CodeTonight teammate.",
        )
    return PrincipalVerdict(
        principal="external", sender=sender_raw, platform=plat,
        reason=f"Sender {sender_raw} not in operator/teammate registry; external.",
    )


def parse_channel_block(text: str) -> dict[str, str]:
    """Extract attributes from the first ``<channel ...>`` open tag in text.

    Returns a dict with keys ``senderId``, ``senderJid``, ``sender``,
    ``platform``, ``rawJid``, ``source``. Missing keys map to empty string.
    Multiple ``<channel>`` blocks: only the first is parsed (caller can
    iterate if needed).
    """
    if not text:
        return {}
    match = _CHANNEL_OPEN_RE.search(text)
    if not match:
        return {}
    attrs = dict(_ATTR_RE.findall(match.group("attrs") or ""))
    return {k.lower(): v for k, v in attrs.items()}


def classify_inbound_text(text: str) -> PrincipalVerdict:
    """Parse a fresh inbound message and classify the principal.

    Convenience wrapper for hooks and tests. Looks at common attribute
    names — ``senderId`` (Slack/Discord), ``senderJid`` / ``rawJid``
    (WhatsApp), ``sender`` (Web) — picks the first non-empty.
    """
    attrs = parse_channel_block(text)
    if not attrs:
        return PrincipalVerdict(
            principal="external", sender="", platform="",
            reason="No <channel> block found in text; default-deny operator status.",
        )
    sender = (
        attrs.get("senderid")
        or attrs.get("senderjid")
        or attrs.get("rawjid")
        or attrs.get("sender")
        or ""
    )
    platform = attrs.get("platform", "")
    return classify_principal(sender, platform)


__all__ = [
    "OPERATOR_SLACK_USER_ID",
    "OPERATOR_WHATSAPP_JID",
    "OPERATOR_WHATSAPP_LID",
    "COSTEERER_WHATSAPP_JIDS",
    "FULL_CAPABILITY_WHATSAPP_JIDS",
    "TEAMMATE_SLACK_IDS",
    "PrincipalVerdict",
    "classify_principal",
    "classify_inbound_text",
    "parse_channel_block",
]
