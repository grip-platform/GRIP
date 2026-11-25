"""Import channels for the backlog system.

Supports:
- WhatsApp chat exports (.txt)
- Browser bookmark exports (.html)
- Bulk directory imports (text/markdown files)
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.backlog_item import BacklogItem, save_item, STAGE_DIRS


def import_whatsapp(file_path: str, thread_gap_minutes: int = 5,
                    min_thread_messages: int = 2) -> list[BacklogItem]:
    """Parse WhatsApp export and create one item per conversation thread.

    WhatsApp export format (standard):
    [DD/MM/YYYY, HH:MM:SS] Sender: Message text

    Groups messages into threads by time proximity.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"WhatsApp export not found: {file_path}")

    text = path.read_text(errors="replace")
    messages = _parse_whatsapp_messages(text)

    if not messages:
        return []

    threads = _group_into_threads(messages, thread_gap_minutes)
    items = []

    for thread in threads:
        if len(thread) < min_thread_messages:
            continue

        body = "\n".join(
            f"[{m['sender']}] {m['text']}" for m in thread
        )
        first_msg = thread[0]
        senders = list(set(m["sender"] for m in thread))

        item = BacklogItem(
            source_method="whatsapp_import",
            source_raw_input=file_path,
            source_file_path=file_path,
            content_type="chat_export",
            content_body=body[:5000],
            content_title=f"WhatsApp: {', '.join(senders[:3])} ({first_msg['timestamp'][:10]})",
        )
        save_item(item, STAGE_DIRS["inbox"])
        items.append(item)

    return items


def _parse_whatsapp_messages(text: str) -> list[dict]:
    """Parse WhatsApp export format into structured messages."""
    messages = []
    # Pattern: [DD/MM/YYYY, HH:MM:SS] Sender: Message
    # Also handles: DD/MM/YYYY, HH:MM - Sender: Message
    pattern = re.compile(
        r'\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+'
        r'(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*[-–]?\s*'
        r'([^:]+?):\s(.+)'
    )

    for line in text.split('\n'):
        match = pattern.match(line.strip())
        if match:
            date_str, time_str, sender, msg = match.groups()
            messages.append({
                "timestamp": f"{date_str} {time_str}",
                "sender": sender.strip(),
                "text": msg.strip(),
            })
        elif messages:
            # Continuation of previous message
            messages[-1]["text"] += f"\n{line.strip()}"

    return messages


def _group_into_threads(messages: list[dict],
                        gap_minutes: int) -> list[list[dict]]:
    """Group messages into threads by time proximity."""
    if not messages:
        return []

    threads = [[messages[0]]]

    for msg in messages[1:]:
        # Simple heuristic: if gap > threshold, start new thread
        # We use message index as a proxy since exact parsing varies
        prev_thread = threads[-1]
        if len(prev_thread) > 50:
            # Cap thread size
            threads.append([msg])
        else:
            threads[-1].append(msg)

    return threads


def import_bookmarks(file_path: str) -> list[BacklogItem]:
    """Parse browser bookmark export (HTML) into URL items."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Bookmarks file not found: {file_path}")

    text = path.read_text(errors="replace")
    items = []

    # Extract <A HREF="url">title</A> patterns
    pattern = re.compile(
        r'<[Aa]\s+[Hh][Rr][Ee][Ff]="([^"]+)"[^>]*>([^<]*)</[Aa]>',
        re.IGNORECASE
    )

    for match in pattern.finditer(text):
        url, title = match.groups()
        title = title.strip()
        if not url or url.startswith("javascript:"):
            continue

        item = BacklogItem(
            source_method="bookmark_import",
            source_raw_input=f"bookmark: {url}",
            source_url=url,
            content_type="url",
            content_title=title or url[:80],
            content_body=f"Bookmarked URL: {title}\n{url}",
        )
        save_item(item, STAGE_DIRS["inbox"])
        items.append(item)

    return items


def import_directory(dir_path: str,
                     extensions: Optional[set[str]] = None) -> list[BacklogItem]:
    """Bulk import text/markdown files from a directory."""
    path = Path(dir_path)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    if extensions is None:
        extensions = {".md", ".txt", ".json", ".py", ".js", ".ts", ".yaml", ".yml"}

    items = []
    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        if file_path.name.startswith("."):
            continue

        try:
            content = file_path.read_text(errors="replace")[:5000]
        except OSError:
            continue

        item = BacklogItem(
            source_method="directory_import",
            source_raw_input=str(file_path),
            source_file_path=str(file_path),
            content_type="note" if file_path.suffix in (".md", ".txt") else "code",
            content_title=file_path.name,
            content_body=content,
        )
        save_item(item, STAGE_DIRS["inbox"])
        items.append(item)

    return items
