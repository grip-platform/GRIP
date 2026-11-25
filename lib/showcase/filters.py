"""Showcase request filters — slug validation + bot detection."""
from __future__ import annotations

import re

# ^[a-z0-9][a-z0-9_-]{0,31}$ — same shape as ULTRALEAD pixel slug regex
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Known link-preview / scanner User-Agent fragments. Case-insensitive substring match.
BOT_UA_FRAGMENTS = (
    "slackbot",
    "twitterbot",
    "linkedinbot",
    "facebookexternalhit",
    "whatsapp",
    "telegrambot",
    "discordbot",
    "skypeuripreview",
    "google-inspectiontool",
    "googlebot",
    "bingbot",
    "applebot",
    "yandexbot",
    "msnbot",
)


def is_valid_slug(s: str) -> bool:
    """True iff s matches the allowed slug shape."""
    return bool(s and SLUG_RE.match(s))


def looks_like_bot(ua: str) -> bool:
    """Heuristic: True iff UA contains a known link-preview/scanner fragment."""
    if not ua:
        return False
    ua_l = ua.lower()
    return any(frag in ua_l for frag in BOT_UA_FRAGMENTS)


def is_genuine_view(events: list[dict]) -> bool:
    """A 'genuine' view = saw the page (or pixel) AND played the video.

    Bot link-previews fetch the pixel but never fire `play`; this filter
    distinguishes preview fetches from real human views.
    """
    seen = {e.get("event") for e in events}
    has_arrival = "page_view" in seen or "pixel" in seen
    return has_arrival and "play" in seen
