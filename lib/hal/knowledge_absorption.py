"""HAL Knowledge Absorption — route external source events into MemoryEngine.

Absorbs knowledge events from Discord, Slack, and external monitors (e.g.
the legal-tech LinkedIn/news ingest service) via the existing
MemoryEngine.write_entry path, tagging each entry with source metadata
in frontmatter. Provenance is never faked: a monitor feed is recorded as
``source='monitor'``, not mislabelled as a chat source.

Usage:
    from lib.hal.knowledge_absorption import absorb_from_source

    result = absorb_from_source('discord', {'text': 'hello', 'channel_id': '123'})
    # {'absorbed': True, 'filename': 'absorbed_discord_5d41402a.md', 'source': 'discord'}
"""
import hashlib
from typing import Optional

from lib.hal.memory_engine import MemoryEngine

# Sources are the CHANNEL a knowledge event arrived on. The channel set mirrors
# the TypeScript url-absorber's AbsorbChannel (whatsapp/slack/discord/web) plus
# 'monitor' (the legal-tech ingest feed) and 'content' (generic any-content
# absorption — not a channel, so never mislabelled as one). Extended
# 2026-05-29 (H791 fold) so
# URL-absorption provenance — grip:<channel> — can be written through this
# canonical sink and become /recall-able. Provenance is never faked.
_VALID_SOURCES = frozenset({"discord", "slack", "monitor", "whatsapp", "web", "content"})
# Closed schema (see skills/absorb-knowledge). 'url' added 2026-05-29 for the
# URL-absorption path — a deliberate, documented extension, not a **kwargs blob.
_OPTIONAL_KEYS = ("user_id", "thread_id", "timestamp", "url")


def absorb_from_source(
    source: str,
    payload: dict,
    engine: Optional[MemoryEngine] = None,
) -> dict:
    """Absorb a knowledge event from an external source (discord, slack, etc).

    Args:
        source: Source identifier — must be one of {'discord', 'slack'}.
        payload: Dict with at minimum 'text' (str) and 'channel_id' (str).
                 Optional keys: 'user_id', 'thread_id', 'timestamp'.
        engine: Optional MemoryEngine; defaults to a fresh instance.

    Returns:
        Dict with 'absorbed' (bool), 'filename' (str), 'source' (str).

    Raises:
        ValueError: if source is not in {'discord', 'slack'} or payload missing 'text'.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(f"Invalid source {source!r}. Must be one of {sorted(_VALID_SOURCES)}.")
    if "text" not in payload or not isinstance(payload["text"], str):
        raise ValueError("payload must contain a non-empty 'text' key (str).")

    text = payload["text"]
    hash8 = hashlib.md5(text.encode()).hexdigest()[:8]
    filename = f"absorbed_{source}_{hash8}.md"

    extra_fm = "".join(
        f"{key}: {payload[key]}\n"
        for key in _OPTIONAL_KEYS
        if key in payload
    )
    description = f"Absorbed from {source} channel {payload.get('channel_id', '')}"

    (engine or MemoryEngine()).write_entry(
        filename=filename,
        name=f"absorbed_{source}_{hash8}",
        description=description,
        memory_type="reference",
        content=f"source: {source}\nchannel_id: {payload.get('channel_id', '')}\n{extra_fm}\n{text}",
    )

    return {"absorbed": True, "filename": filename, "source": source}


def absorb_content(
    text: str,
    *,
    title: str = "",
    url: Optional[str] = None,
    source: str = "content",
    sender: Optional[str] = None,
    engine: Optional[MemoryEngine] = None,
) -> dict:
    """Absorb knowledge from ANY content — arbitrary text, an article body, a
    fetched URL's text — through the canonical sink.

    The generic any-content entry on top of ``absorb_from_source`` (extended
    2026-09-20 from the URL-only H791 fold). Provenance is never faked: the
    default ``source='content'`` records that the knowledge did not arrive on
    a chat channel; pass ``source='web'`` (or any ``_VALID_SOURCES`` member)
    when the content genuinely came from that channel. A ``url`` or ``title``
    is rendered into a header so the entry is self-describing for /recall.

    Args:
        text: The content body (required).
        title: Optional document/message title rendered into the header.
        url: Optional source URL rendered into the header and frontmatter.
        source: Provenance tag — must be one of _VALID_SOURCES.
        sender: Optional platform-native sender id.
        engine: Optional MemoryEngine; defaults to a fresh instance.

    Returns:
        Dict with 'absorbed' (bool), 'filename' (str), 'source' (str).

    Raises:
        ValueError: if source is invalid or text is empty.
    """
    header = "\n".join(
        bit for bit in ((f"URL: {url}" if url else ""), (f"Title: {title}" if title else ""))
        if bit
    )
    body = f"{header}\n\n{text}" if header else text
    payload = {"text": body, "channel_id": source}
    if url:
        payload["url"] = url
    if sender:
        payload["user_id"] = sender
    return absorb_from_source(source, payload, engine)


def absorb_url(
    url: str,
    title: str,
    text: str,
    channel: str,
    *,
    sender: Optional[str] = None,
    engine: Optional[MemoryEngine] = None,
) -> dict:
    """Absorb a fetched URL's content into memory so it becomes /recall-able.

    The KONO sink for the channel-agnostic URL absorber (H791 fold). Formats the
    URL + title + extracted text into one reference entry and writes it through
    the canonical absorb_from_source path, tagged with the channel as source
    (provenance: source=<channel>, never faked). This is what closes the gap
    where bot-fetched URLs lived only in data/absorbed-urls.jsonl and were never
    retrievable.

    Args:
        url: The absorbed URL.
        title: Extracted page/resource title (may be empty).
        text: Extracted text content.
        channel: One of whatsapp/slack/discord/web (the channel it arrived on).
        sender: Optional platform-native sender id.
        engine: Optional MemoryEngine; defaults to a fresh instance.

    Returns:
        Dict with 'absorbed' (bool), 'filename' (str), 'source' (str).

    Raises:
        ValueError: if channel is not a valid source or text is empty.
    """
    # Delegates to the generic any-content entry with channel as the provenance
    # source. Header order (URL then Title) and payload are constructed
    # identically, so the absorbed filename hash is unchanged (hash-stable:
    # same inputs -> same absorbed_<source>_<md5[:8]>.md).
    return absorb_content(
        text, title=title, url=url, source=channel, sender=sender, engine=engine
    )
