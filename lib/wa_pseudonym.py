"""Strip the people out of a message before it leaves this machine.

Deliverable (B) — asking a model what a message MEANS rather than which
substrings it contains — cannot be built without this, because the standing
constraint on the matter is that nothing reaches an external model carrying
real people in it. ``wa_redact`` covers credentials; it does not know a name
from a noun. This does.

    from wa_pseudonym import Pseudonymiser

    p = Pseudonymiser([["Ada Example", "Ada"], ["Bo Sample", "Bo"]])
    p.safe("Ada said sorry to Bo")   # 'PERSON_08390F said sorry to PERSON_5F6251'

``safe()`` scrubs and then RE-READS its own output, raising if anything it
was told to remove is still there. That second read is the whole point. A
scrubber that reports success is making a claim about its own operation, and
this session has been a long argument that such a claim is not evidence
(``verify-canonical-not-claimed.md``). The only check worth having is the one
that looks at the text about to be sent.

Design decisions that are not arbitrary
---------------------------------------
**Longest name first.** Replacing "Ada" before "Ada Example" leaves the
surname standing in the output — the exact identifier that matters most. The
substitution order is by descending length, so the full form always wins.

**Stable tokens, derived from the name.** The same person is the same token
on every call and across runs, because a semantic layer caches on the text it
sends: a token that changed per call would make every repeat a fresh spend,
and would make two mentions of one person look like two people to the model.
The token is derived by hashing the name, so it is stable without any state
on disk holding the real names.

**The caller supplies the names, and they are never written down here.** A
name list in this file would be third-party PII inside a repo mirrored to a
client org, which ``no-sensitive-info-in-grip.md`` forbids outright. The list
arrives at runtime and lives only in the caller's memory.

**Nicknames must be supplied; they cannot be guessed.** "Addie" for Ada is
not derivable from "Ada", so an unsupplied nickname passes through untouched.
That is a real limit and it is stated rather than hidden — ``residue()``
cannot report a name it was never given. The mitigation is the generic PII
sweep below, which is name-independent.

Falsifier
---------
Wrong if ``safe()`` ever returns text containing a supplied name, or if a
phone number, email address or South African ID number survives it. Verifier:
``tests/test_wa_pseudonym.py``.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = ["CONNECTIVES", "Pseudonymiser", "Residue", "names_from_rows", "token_for"]

# Name PARTS that are ordinary words and must never be scrubbed on their own.
# "en" is Afrikaans for "and": scrubbing it standalone would destroy every
# sentence a human then has to read, and a corrupted transcript is its own
# harm. Each of these only ever appears inside a compound surname, which the
# full name covers anyway, so excluding the part costs nothing.
CONNECTIVES = frozenset({
    "de", "den", "der", "die", "du", "van", "von", "der", "ter", "te",
    "la", "le", "el", "di", "da", "dos", "en", "of", "the", "and", "sa",
})

# Generic identifiers, independent of any supplied name list. Each is a shape
# rather than a value, so it catches the person nobody thought to name.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Loose on purpose: any 9+ digit run with optional separators, which covers SA
# mobiles (0XX XXX XXXX), the +27 form, and account numbers.
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
# SA identity number: 13 digits, sometimes spaced.
_SAID = re.compile(r"(?<!\d)\d{6}\s?\d{4}\s?\d{3}(?!\d)")


class Residue(Exception):
    """Raised when scrubbed text still holds something that must not leave."""

    def __init__(self, found: list[str]) -> None:
        super().__init__("text still holds: " + ", ".join(sorted(set(found))))
        self.found = found


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def token_for(name: str) -> str:
    """A stable token for one name.

    Derived from the name so it survives across processes without storing the
    name anywhere, and truncated because a reader has to hold it in their head
    while reading a transcript.

    It is NOT anonymisation and must not be described as such. Six hex
    characters is twenty-four bits over a guessable input: anyone who suspects
    a name can hash it and confirm the match. What the token does is stop the
    name TRAVELLING — it is absent from the text that leaves the machine —
    which is the property the constraint actually asks for. Against a reader
    who already knows the cast, it hides nothing.
    """
    digest = hashlib.sha256(_nfc(name).strip().lower().encode("utf-8")).hexdigest()
    return f"PERSON_{digest[:6].upper()}"


class Pseudonymiser:
    """Replaces supplied names, then the generic identifier shapes."""

    def __init__(self, names: list, *, extra: dict[str, str] | None = None) -> None:
        """`names` holds a string per person, or a list of that person's aliases.

        The alias form is not a convenience. Hashing each spelling separately
        gives "Ada Example" and "Ada" two different tokens, so one person
        reads to the model as two people and every judgement that spans two
        mentions of her is made about strangers. Aliases in one group share
        the FIRST spelling's token, so she stays one person.
        """
        self._map: dict[str, str] = {}
        for entry in names:
            group = [entry] if isinstance(entry, str) else list(entry)
            group = [n for n in (_nfc(n).strip() for n in group) if n]
            if not group:
                continue
            shared = token_for(group[0])
            for alias in group:
                self._map.setdefault(alias, shared)
        self._map.update(extra or {})
        # Longest first: "Ada Example" must be consumed before "Ada".
        self._names = sorted(self._map, key=len, reverse=True)
        self._patterns = [
            (re.compile(rf"(?<!\w){re.escape(n)}(?!\w)", re.IGNORECASE), self._map[n])
            for n in self._names
        ]

    @property
    def mapping(self) -> dict[str, str]:
        """name -> token, for the caller to read a result back into names."""
        return dict(self._map)

    def scrub(self, text: str) -> str:
        """Replace every supplied name and every generic identifier shape."""
        out = _nfc(text)
        for pattern, token in self._patterns:
            out = pattern.sub(token, out)
        out = _EMAIL.sub("EMAIL", out)
        out = _SAID.sub("ID_NUMBER", out)
        out = _PHONE.sub("PHONE", out)
        return out

    def residue(self, text: str) -> list[str]:
        """What is still present that should not be. Empty means clean."""
        found: list[str] = []
        for pattern, _ in self._patterns:
            if pattern.search(text):
                found.append(pattern.pattern)
        for shape, label in ((_EMAIL, "email"), (_SAID, "id-number"), (_PHONE, "phone")):
            if shape.search(text):
                found.append(label)
        return found

    def safe(self, text: str) -> str:
        """Scrub, then re-read the OUTPUT and raise if anything survived.

        The re-read is not belt-and-braces. A substitution can fail for
        reasons the substituting code cannot see — a name that arrives with a
        combining accent where the pattern holds a precomposed one, a shape
        the sweep does not model — and the only place that is observable is
        the text itself, after the work is done.
        """
        out = self.scrub(text)
        left = self.residue(out)
        if left:
            raise Residue(left)
        return out


def names_from_rows(rows, *, min_count: int = 1) -> dict:
    """Draft a name list from the corpus's own pushName field.

    A list composed from memory is incomplete, and an incomplete list is not a
    smaller version of the same protection — it is a disclosure, because the
    names nobody remembered are the ones that leave in the clear. The corpus
    knows who is in it, so the list is derived rather than recalled.

    Returns ``{"people": [[canonical, alias, ...], ...], "review": [...]}``.

    Each person contributes their full display name plus its word parts, so a
    bare first name in a message is caught as well as the full form, sharing
    one token. Parts are included by DEFAULT, which is the safe direction: a
    name that should not have been scrubbed costs a reader one odd token,
    while a name that should have been and was not has already left.

    ``review`` names the short parts a human should look at — they are the
    ones most likely to collide with an ordinary word. They are included in
    ``people`` regardless; review means "check whether this over-scrubs",
    never "this was silently dropped".
    """
    import collections
    import re as _re

    seen = collections.Counter()
    for row in rows:
        name = str(row.get("pushName") or "").strip()
        if name:
            seen[name] += 1

    people, review = [], []
    for name, count in seen.most_common():
        if count < min_count:
            continue
        if _re.fullmatch(r"[+\d\s()\-]+", name):
            continue                       # a phone number used as a display name
        group = [name]
        if len(name) < 4:
            # A short FULL name collides with ordinary words as readily as a
            # short part does — "Bo" is Afrikaans for "above". Missing this
            # was the first draft's bug: only parts were reviewed.
            review.append(name)
        for part in _re.split(r"[\s._\-]+", name):
            part = part.strip()
            if not part or part.lower() in CONNECTIVES or part == name:
                continue
            group.append(part)
            if len(part) < 4:
                review.append(part)
        people.append(group)
    return {"people": people, "review": sorted(set(review))}
