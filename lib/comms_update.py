"""DFI-FIB Track A — platform-agnostic comms update library.

Three-layer architecture (lib / skill / MCP):
- This module is the lib layer: composes payloads, hands to adapters.
- SlackAdapter is the reference implementation (Block Kit JSON).
- Stub adapters (Discord, Telegram, Web, WhatsApp) raise NotImplementedError
  with a redirect to the relevant plan doc.

Usage::

    from lib.comms_update import CommsUpdate, SlackAdapter

    update = CommsUpdate(
        headline="GRIP Gen 511 — features shipped",
        body="Three tracks, one sprint.",
        fields={"PRs": "3", "Tests": "21"},
        footer="GRIP v6.0.0",
        severity="info",
        origin="rsi-optimal",
        template_type="features",
    )
    adapter = SlackAdapter()
    blocks = adapter.render(update)   # list of Slack Block Kit dicts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# PII shield at the compose boundary (rules/visual-artifact-leak-prevention.md,
# lib/redaction_shield.py — the ONE canonical shield for every public surface)
# ---------------------------------------------------------------------------

def _redact_free_text(text: str):
    """Route ONE free-text field through the canonical public PII shield.

    FAIL-OPEN by construction: any shield-unusable state (import failure, a
    git-crypt-locked denylist that raises, a runtime error) returns the input
    UNCHANGED so the outbound compose path can never crash on redaction. Only
    free text is passed here (headline / body / field VALUES) — never
    structural scaffolding (block types, field KEYS, emoji, footer, origin).
    The public profile leaves non-PII text (counts, enums, a bare ``#196``,
    a normal PR title) intact; it only opaques denylisted identities + the
    generic PII floor (emails / phones / IPs / JIDs / key shapes)."""
    if not isinstance(text, str) or not text:
        return text
    try:
        from redaction_shield import Shield, load_denylist
        return Shield(load_denylist(), profile="public").redact(text).text
    except Exception:  # noqa: BLE001 — shield-unusable must NEVER break the post
        return text


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------

@dataclass
class CommsUpdate:
    """Platform-agnostic update payload."""
    headline: str
    body: str
    fields: dict[str, str]
    footer: str
    severity: Literal["info", "warning", "error"]
    origin: str
    template_type: Literal["features", "heartbeat"]
    # Optional: extra items that overflow into thread (> 5 items)
    overflow: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CommsAdapter(Protocol):
    """Protocol for platform-specific renderers."""
    supports_blocks: bool

    def render(self, update: CommsUpdate) -> object:
        """Render update to platform-native payload."""
        ...

    def send(self, update: CommsUpdate, recipient: str) -> None:
        """Send update to recipient channel/room."""
        ...


# ---------------------------------------------------------------------------
# Block Kit helpers (Slack)
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI = {"info": ":information_source:", "warning": ":warning:", "error": ":red_circle:"}


def _header_block(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _section_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _fields_block(fields: dict[str, str]) -> dict:
    # Thread overflow rule: <= 5 items in main block
    items = list(fields.items())[:5]
    return {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
            for k, v in items
        ],
    }


def _divider() -> dict:
    return {"type": "divider"}


def _context_block(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


# ---------------------------------------------------------------------------
# SlackAdapter — reference implementation
# ---------------------------------------------------------------------------

class SlackAdapter:
    """Concrete CommsAdapter for Slack Block Kit.

    render() returns a list of Block Kit block dicts.
    send() uses the grip-channel MCP tool or direct Slack API.
    """
    supports_blocks: bool = True

    def render(self, update: CommsUpdate) -> list[dict]:
        """Translate CommsUpdate → Block Kit block list.

        Free-text fields (headline, body, field VALUES) are routed through the
        canonical public PII shield at THIS compose boundary — the last point
        before the text becomes a Block Kit payload bound for a channel. Field
        KEYS, the severity emoji, footer, and origin are structural scaffolding
        and are left unredacted (a field key is a label, not operator PII).
        The shield fails open: a shield-unusable state yields the text
        unchanged, never a crash on the outbound path."""
        emoji = _SEVERITY_EMOJI.get(update.severity, ":information_source:")
        blocks: list[dict] = [
            _header_block(f"{emoji} {_redact_free_text(update.headline)}"),
            _section_block(_redact_free_text(update.body)),
        ]
        if update.fields:
            safe_fields = {k: _redact_free_text(v) for k, v in update.fields.items()}
            blocks.append(_divider())
            blocks.append(_fields_block(safe_fields))
        blocks.append(_divider())
        blocks.append(_context_block(f"{update.footer} | origin: {update.origin}"))
        return blocks

    def send(self, update: CommsUpdate, recipient: str) -> None:
        """Post to Slack channel. Requires grip-channel MCP or direct API token.

        For operator use — not called in tests (integration concern, not unit).
        """
        raise NotImplementedError(
            "send() requires grip-channel MCP tool or SLACK_BOT_TOKEN env var. "
            "See skills/slack-update/SKILL.md for usage."
        )


# ---------------------------------------------------------------------------
# Signed provenance (Cryptographic-Causation — the outward-claim record)
# ---------------------------------------------------------------------------

def record_update_provenance(
    update: CommsUpdate,
    *,
    path=None,
    idr_path=None,
    head_pointer=None,
) -> dict:
    """Attach signed, replayable provenance to a POSTED /slack-update claim.

    ADDITIVE + FAIL-OPEN: call this AFTER the Block Kit post has succeeded. The
    update's structured ``fields`` (e.g. ``PRs merged: 3``) are the verbatim
    factual claims; the recorder cites each against the post's own canonical
    record, then emits a signed IDR leaf + a memory-chain node (via
    ``lib.channel_claim_provenance``). NEVER raises — a recording failure can
    never break the (already-sent) announce. ``path`` / ``idr_path`` /
    ``head_pointer`` are injectable for hermetic tests + the recording audit.
    """
    try:
        from lib.channel_claim_provenance import record_claim_post
    except Exception as exc:  # noqa: BLE001 — import failure must not break the post
        print(f"[comms-update] provenance unavailable (post unaffected): {exc}",
              file=__import__("sys").stderr)
        return {"ok": False, "reason": "import-failed"}
    claim_lines = [f"{k}: {v}" for k, v in update.fields.items()]
    claim_lines.extend(str(x) for x in update.overflow)
    return record_claim_post(
        update.headline, claim_lines,
        path=path, idr_path=idr_path, head_pointer=head_pointer,
    )
