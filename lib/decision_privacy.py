"""May this text go to a decision model? The privacy floor in front of every advisory Jev call.

A decision model (TypeSafe's Jev, through HAL decide) receives internal and public
text only: GRIP and HAL code, docs and logs, and public legal sources. Never
personal matters, WhatsApp content, children's data, secrets or client documents
(the operator's limit, 2026-10-10). Three deterministic checks run before any such
call, and any one of them keeps the text at home:

1. **The work is in the private guardian repository**, named by
   ``guardian_repo`` in config/guardian.local.json, where personal matters live
   (rules/personal-matters-stay-private.md).
2. **The redaction shield would change the text.** Something in it is a listed
   name, a matter term or an identifier, so it is not the internal text this path
   is for. Scrubbing is not enough here: a scrubbed text can still carry a
   personal matter's substance, so a text the shield had to touch is not sent at
   all.
3. **The text already carries a shield marker** (``[PERSON-3]``, ``[EMAIL]``, a
   listed pseudonym, or review_redaction's WITHHELD text): a caller scrubbed it
   upstream, and check 2 alone would wave it through. Ordinary bracketed capitals
   in code (``[SIGNAL_AGO]``, ``[GRIP]``) are not markers.

The caller's existing method answers instead; nothing else changes. Fails closed:
when a check cannot run, the answer is no.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

#: A numbered stand-in: a session token (``[PERSON-3]``, ``[ID_NUMBER-1]``) or a
#: pinned pseudonym (``[ORG-1]``). Its class is open-ended (a config class, or one
#: a model proposed), so every numbered shape still counts, exactly as before.
_NUMBERED = r"\[[A-Z][A-Z_]*-\d+\]"
#: What GRIP's other scrubbers write. ``[REDACTED]`` is the mask of
#: lib/wa_redact.py (WhatsApp credentials), lib/secret_redactor.py and
#: lib/hooks_engine/audit.py, which also writes ``[REDACTED_BYTES]``.
#: lib/wa_pseudonym.py writes a person as ``PERSON_`` and six hex digits, unbracketed.
_OTHER_SCRUBBERS = r"\[REDACTED(?:_BYTES)?\]|\bPERSON_[0-9A-F]{6}\b"
#: Every bracketed-capitals shape, plus the other scrubbers' shapes. It decides
#: alone whenever the shields' own vocabulary cannot be read, so a missing module
#: can only make check 3 stricter, never weaker.
_BROAD_MARKER = re.compile(rf"\[[A-Z][A-Z_]*(?:-\d+)?\]|{_OTHER_SCRUBBERS}")


def _shield_marker() -> re.Pattern:
    """The stand-ins the two shields write, read from the shield modules themselves.

    Only the UNNUMBERED shapes are narrowed: to the shield's own category names
    (``[EMAIL]``, ``[ID_NUMBER]``, ``[ADDRESS]``), review_redaction's WITHHELD
    text and the other scrubbers' masks. Ordinary bracketed capitals, such as a
    bash array key ``[SIGNAL_AGO]`` or a log prefix ``[GRIP]``, no longer read as
    scrubbed text. A listed pseudonym of any other shape is checked from the
    loaded list instead (:func:`_already_scrubbed`).
    """
    try:
        import redaction_shield as rs
        import review_redaction as rr

        names = set(rs._GENERIC_RE.groupindex) | set(rs._NER_RE.groupindex)
        names.update((rs.FALLBACK_CATEGORY, rs.GENERIC_FALLBACK_CATEGORY))
        names.add(rs.ADDRESS_MARKER[1:-1].lower())
        categories = "|".join(re.escape(name.upper()) for name in sorted(names))
        return re.compile(rf"\[(?:{categories})\]|{_NUMBERED}|{re.escape(rr.WITHHELD)}"
                          rf"|{_OTHER_SCRUBBERS}")
    except Exception:  # noqa: BLE001 -- unavailable vocabulary must not weaken the floor
        return _BROAD_MARKER


_SHIELD_MARKER = _shield_marker()


def _already_scrubbed(text: str) -> bool:
    """True when ``text`` carries a stand-in that a shield writes (check 3).

    The public profile writes a listed entry's pseudonym exactly as the config
    spells it, so each loaded pseudonym counts verbatim, whatever its shape. When
    the list cannot be loaded, the broad shape decides, so the check fails closed.
    """
    if _SHIELD_MARKER.search(text):
        return True
    try:
        import review_redaction as rr
        entries = rr._shield().denylist.entries
    except Exception:  # noqa: BLE001 -- no list, so no listed pseudonym can be ruled out
        return bool(_BROAD_MARKER.search(text))
    return any(e.pseudonym and e.pseudonym in text for e in entries)


def may_send(text: str, *, cwd: Optional[str] = None) -> Tuple[bool, str]:
    """``(True, "")`` when ``text`` may go to a decision model; ``(False, why)`` otherwise."""
    if not isinstance(text, str):
        return False, "not_text"
    guardian = _in_guardian_repo(Path(cwd or os.getcwd()))
    if guardian is not False:
        return False, "guardian_repo" if guardian else "guardian_config_unreadable"
    if _already_scrubbed(text):
        return False, "already_scrubbed"
    return _unchanged_by_the_shield(text)


def _in_guardian_repo(cwd: Path) -> Optional[bool]:
    """True inside the guardian repository, False outside it, None when that cannot be told."""
    try:
        import redaction_shield as rs
    except Exception:  # noqa: BLE001 -- no shield module, no way to tell
        return None
    if not rs.GUARDIAN_LOCAL.exists():
        return False  # a machine with no guardian repository (CI, a teammate)
    repo = rs._guardian_repo()
    if not repo:
        return None
    root = Path(repo).expanduser().resolve()
    here = cwd.expanduser().resolve()
    return here == root or root in here.parents


def _unchanged_by_the_shield(text: str) -> Tuple[bool, str]:
    try:
        import review_redaction as rr
        scrubbed = rr.redact(text)
    except Exception:  # noqa: BLE001 -- a shield that cannot run cannot vouch for the text
        return False, "shield_unavailable"
    if scrubbed == rr.WITHHELD:
        return False, "shield_failed"
    return (True, "") if scrubbed == text else (False, "shield_would_change_text")
