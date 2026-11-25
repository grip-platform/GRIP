"""Mask credentials shared in chat before they reach a durable artefact.

Twelve years of family WhatsApp carries WiFi passwords, card PINs, streaming
logins and meeting IDs. None of it is evidence in a legal matter, and all of
it would ride into a bundle handed to an attorney if a corpus were copied
across unfiltered.

**The label survives; only the secret goes.** ``Password: [REDACTED]`` keeps
the fact that a credential was shared on that date, which is occasionally
material, while removing the thing that must not be republished.

    from lib.wa_redact import redact, has_credential

    if has_credential(text):
        text = redact(text)

Why the lookahead is shaped the way it is
-----------------------------------------
The obvious pattern is ``(LABEL\\s*[:=]\\s*)(?!\\[REDACTED\\])(.+)``, and it is
wrong in a way that is invisible until measured. The trailing ``\\s*`` sits
INSIDE the captured group, so when the lookahead fails the engine simply
backtracks that ``\\s*`` to zero width, re-tests the lookahead against a SPACE
rather than against ``[``, and passes. The redactor therefore cannot recognise
its own output: ``Password: [REDACTED]`` matches again.

Measured on the live corpus, that defect reported 30 already-clean rows as
unredacted credentials — a detector that cannot tell a masked secret from an
exposed one, which is the worst direction for this particular error, because it
makes a clean artefact look dirty and a second pass look necessary forever.

The fix is to move the whitespace skip inside the lookahead
(``(?!\\s*\\[REDACTED\\])``), where it cannot be backtracked away.

Known gap: spoken credentials
-----------------------------
The pattern anchors on a ``:`` or ``=`` separator, and **speech has neither**.
A machine transcript of "die wifi password is oop123" carries a real credential
that this module will not catch. Widening the pattern to drop the separator was
considered and rejected: it would also match "ek het my password vergeet", and a
redactor that eats ordinary sentences is one that gets switched off, which
protects nothing. Voice-note transcripts therefore need a human read before any
bundle leaves, and this gap is pinned by a test rather than left to be found.

Falsifier
---------
Wrong if ``redact(redact(x)) != redact(x)`` for any input, or if
``has_credential(redact(x))`` is ever true. Verifier:
``tests/test_wa_redact.py``.
"""
from __future__ import annotations

import re

__all__ = ["CREDENTIAL_LABELS", "MARKER", "has_credential", "redact", "redact_row"]

MARKER = "[REDACTED]"

# Afrikaans and English, because this corpus code-switches inside single lines.
CREDENTIAL_LABELS = (
    r"password|wagwoord|pin(?:code)?|otp|passcode"
    r"|user\s*name|gebruikersnaam|log\s*in|login"
    r"|meeting\s*id|network\s*name|wifi\s*name"
)

# The whitespace skip lives INSIDE the lookahead. See the module docstring.
_GUARD = rf"(?!\s*{re.escape(MARKER)})"
_LINE = re.compile(rf"(?im)^(\s*(?:{CREDENTIAL_LABELS})\s*[:=]){_GUARD}(.+)$")
_INLINE = re.compile(rf"(?i)\b((?:{CREDENTIAL_LABELS})\s*[:=]){_GUARD}\s*(\S+)")

_FIELDS = ("text", "original", "english", "transcript", "transcript_en")


def has_credential(text: str) -> bool:
    """True only for an UNMASKED credential. Already-redacted text is clean."""
    if not isinstance(text, str) or not text:
        return False
    return bool(_LINE.search(text) or _INLINE.search(text))


def redact(text: str) -> str:
    """Mask every unmasked credential value, keeping its label. Idempotent."""
    if not isinstance(text, str) or not text:
        return text
    text = _LINE.sub(lambda m: f"{m.group(1)} {MARKER}", text)
    return _INLINE.sub(lambda m: f"{m.group(1)} {MARKER}", text)


def redact_row(row: dict) -> tuple[dict, bool]:
    """Redact every text-bearing field of a corpus row.

    Returns the row and whether anything changed. A changed row is stamped
    ``redacted="credential"`` so a later reader can tell a masked secret from a
    message that never had one — the two are indistinguishable from the text
    alone, and that distinction is what makes the artefact auditable.
    """
    touched = False
    for field in _FIELDS:
        value = row.get(field)
        if isinstance(value, str) and has_credential(value):
            row[field] = redact(value)
            touched = True
    if touched:
        row["redacted"] = "credential"
    return row, touched
