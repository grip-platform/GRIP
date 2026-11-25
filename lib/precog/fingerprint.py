"""Fingerprint — deterministic sha256 of (prompt + kernel_digest + routing_hint).

Pure stdlib. No I/O. Case-preserving normalisation (code identifiers matter).
Separator '|' prevents hash collisions from concatenation ambiguity.
"""
from __future__ import annotations

import hashlib
import unicodedata
import re


def normalise_prompt(prompt: str) -> str:
    """Normalise prompt for fingerprinting.

    Strips leading/trailing whitespace, collapses interior whitespace runs
    to a single space, applies Unicode NFC. Case is PRESERVED (code matters).
    """
    normalised = unicodedata.normalize("NFC", prompt)
    normalised = normalised.strip()
    normalised = re.sub(r"\s+", " ", normalised)
    return normalised


def compute_fingerprint(
    prompt: str,
    kernel_digest: str,
    routing_hint: str | None = None,
) -> str:
    """Compute sha256 fingerprint over (normalised_prompt | kernel_digest | routing_hint_hash).

    Args:
        prompt: Raw prompt text (will be normalised).
        kernel_digest: sha256 hex of the current HOLISTIC kernel content.
        routing_hint: Optional routing-hint string or None.
            None and the empty string produce DIFFERENT fingerprints by design.

    Returns:
        64-char lowercase hex sha256 digest.
    """
    norm = normalise_prompt(prompt)
    # Hash routing_hint independently so None != ""
    hint_hash = hashlib.sha256(
        (routing_hint if routing_hint is not None else "\x00NULL\x00").encode()
    ).hexdigest()
    payload = f"{norm}|{kernel_digest}|{hint_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()
