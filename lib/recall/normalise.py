"""Unicode normalisation and safely quoted lexical queries."""
from __future__ import annotations

import unicodedata

STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i if in into is it its me my no nor not of on or our so than that the their
them there these they this those to was we were what when where which who why
will with would you your
""".split())


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(_character(char) for char in text)
    return " ".join(token for word in cleaned.split() if (token := word.strip("-_./")))


def _character(char: str) -> str:
    if char in "-_./":
        return char
    if unicodedata.category(char)[0] in "PSCZ":
        return " "
    return char


def content_words(text: str) -> list[str]:
    return list(dict.fromkeys(t for t in normalise(text).split() if t not in STOPWORDS))


def fts_query(text: str) -> str | None:
    terms = content_words(text)
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms) or None
