"""Session-pinned facts shim — injects per-session pinned content as system message on Commander spawn.

Part of #2524 80/20 (council verdict 4103db49 §4).

The shim reads ``state/pinned/{sanitised_session_key}.md`` and returns its content
(truncated to ``MAX_BYTES``). Any caller — Commander spawn, Slack thread bootstrap,
Discord thread bootstrap — passes its session key string and receives a string of
pinned facts to prepend, or ``None`` if no pin exists.

Sanitisation rules: filesystem-unsafe characters in the session key are replaced
so the file lookup is platform-agnostic. ``@`` becomes ``-at-`` (preserves
WhatsApp JID readability), ``/`` and ``:`` become ``-``, and spaces become ``_``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

PINNED_DIR = Path.home() / ".claude" / "state" / "pinned"
MAX_BYTES = 2048  # 2KB cap per pinned file (council §4)
TRUNCATION_NOTE = "\n\n[truncated — see {path} for full]"


def get_pinned_facts(session_key: str) -> Optional[str]:
    """Return pinned facts text for *session_key*, or ``None`` if missing.

    *session_key* is any string the platform produces — a WhatsApp JID, a Slack
    composite thread key, a Discord thread id, or an arbitrary user id. The file
    lookup uses :func:`_sanitize` to map the key to a filesystem-safe name.

    Files larger than :data:`MAX_BYTES` are truncated and a marker is appended
    pointing at the on-disk path so the operator can see the source of truth.
    """
    if not session_key:
        return None
    path = PINNED_DIR / f"{_sanitize(session_key)}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > MAX_BYTES:
        return text[:MAX_BYTES] + TRUNCATION_NOTE.format(path=path)
    return text


def _sanitize(session_key: str) -> str:
    """Return a filesystem-safe filename stem derived from *session_key*.

    Idempotent: ``_sanitize(_sanitize(x)) == _sanitize(x)`` — the second pass
    has nothing to replace because the first pass removed every reserved char.
    """
    return (
        session_key.replace("/", "-")
        .replace(":", "-")
        .replace("@", "-at-")
        .replace(" ", "_")
    )


def list_pinned() -> list[str]:
    """Return all session_keys with pinned files (debugging helper)."""
    if not PINNED_DIR.exists():
        return []
    return [p.stem for p in PINNED_DIR.glob("*.md")]
