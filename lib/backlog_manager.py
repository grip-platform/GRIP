#!/usr/bin/env python3
"""GRIP Backlog Manager — personal knowledge staging with autonomous processing.

A "second brain inbox" where users drop notes, URLs, ideas, documents, and
todos. GRIP autonomously scans, categorises, analyses, and proposes actions
with critical reasoning gates to prevent over-inference.

Usage:
    python3 backlog_manager.py add "note text"           # Quick-add a note
    python3 backlog_manager.py add --url "https://..."   # Save a URL for later
    python3 backlog_manager.py add --file path/to/file   # Stage a file
    python3 backlog_manager.py list [--status STATUS]    # List backlog items
    python3 backlog_manager.py process [--limit N]       # Process inbox items
    python3 backlog_manager.py search <query>            # Search across all items
    python3 backlog_manager.py import-whatsapp <file>    # Import WhatsApp export
    python3 backlog_manager.py status                    # Show backlog statistics
    python3 backlog_manager.py triage <item-id>          # Interactive triage
    python3 backlog_manager.py remind                    # Show items needing attention

Directory structure:
    ~/.claude/backlog/
        inbox/          # New, unprocessed items
        processed/      # Categorised and summarised items
        archived/       # Completed or dismissed items

Cross-platform: macOS, Windows (Git Bash), Linux.
"""

import contextlib
import hashlib
import json
import locale
import os
import re
import stat
from itertools import chain
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ── Config ──────────────────────────────────────────────────────────────────

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
BACKLOG_DIR = CLAUDE_DIR / "backlog"
INBOX_DIR = BACKLOG_DIR / "inbox"
PROCESSED_DIR = BACKLOG_DIR / "processed"
ARCHIVED_DIR = BACKLOG_DIR / "archived"
# Where a dropped .json file is kept once scan_inbox_folder finds it is not a
# backlog item (GRIP#9812): the same folder scripts/backlog-scanner.py's own
# inbox/dropped/ drop path keeps its files in (GRIP#9809), outside every
# folder _iter_item_paths reads as *.json items.
KEPT_DIR = BACKLOG_DIR / "kept-files"

for d in (INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR, KEPT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Item Schema ─────────────────────────────────────────────────────────────

# Status lifecycle: inbox -> processing -> processed -> archived
VALID_STATUSES = {"inbox", "processing", "processed", "archived", "actionable", "deferred"}

# Content type detection
CONTENT_TYPES = {
    "url": "A saved URL for later reading/investigation",
    "note": "A text note, thought, or idea",
    "todo": "An actionable task or reminder",
    "file": "A staged file (PDF, image, code, document)",
    "snippet": "A code snippet or technical reference",
    "quote": "A saved quote or passage",
    "research": "A research finding, paper, or article reference",
    "idea": "A creative idea or proposal for future work",
}


def _generate_id(content: str) -> str:
    """Generate a short, unique ID for a backlog item."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:6]
    return f"bl-{ts}-{content_hash}"


_SECRET_MARKERS = (
    "password", "api_key", "api key", "apikey", "secret_key", "secret key",
    "token:", "bearer ", "never lose", "credential",
    "private key", "-----begin",
)
# A key prefix counts only when no letter sits right before it and a run of
# key characters follows: the bare "sk-" matched words such as "task-1"
# (AGORA on GRIP#9809), and a word boundary missed "my_sk-..." (AGORA on
# GRIP#9831). "ssh-" is not a marker: it named tools such as ssh-agent, and
# private keys are caught by "private key" and "-----begin".
_KEY_SHAPE = re.compile(r"(?<![a-z])[sp]k-[a-z0-9_-]{16,}")


def contains_credential(text: str) -> bool:
    """True when ``text`` looks like it holds a password, key or token. The one
    secret check: the manager uses it to call an item a credential, and the
    pipeline uses it before a dropped file's text can reach a model (Codex on
    GRIP#9809)."""
    lowered = text.lower()
    return any(m in lowered for m in _SECRET_MARKERS) or bool(_KEY_SHAPE.search(lowered))


def _detect_content_type(content: str) -> str:
    """Auto-detect the type of content being added."""
    content_lower = content.lower().strip()

    # Credential/secret detection comes FIRST — security gate. It used to run
    # after the URL and to-do checks, so "TODO: rotate password ..." or a URL
    # carrying a token was stored in plain text as a to-do or a link.
    if contains_credential(content_lower):
        return "credential"

    # URL detection
    if re.match(r"https?://", content_lower):
        return "url"

    # Todo detection
    todo_markers = ["todo:", "todo -", "[ ]", "- [ ]", "remind me", "don't forget",
                    "need to", "must", "should", "action:"]
    if any(content_lower.startswith(m) or m in content_lower[:50] for m in todo_markers):
        return "todo"

    # Research detection (before quote — "Research:" starts like attribution)
    research_markers = ["paper:", "research:", "study:", "arxiv", "doi:", "abstract:"]
    if any(m in content_lower for m in research_markers):
        return "research"

    # Idea detection
    idea_markers = ["idea:", "what if", "we could", "imagine", "concept:", "proposal:"]
    if any(m in content_lower for m in idea_markers):
        return "idea"

    # Code snippet detection
    code_markers = ["```", "def ", "function ", "class ", "import ", "const ", "let ", "var "]
    if any(m in content for m in code_markers):
        return "snippet"

    # Quote detection (after more specific patterns to avoid false positives)
    if content.startswith('"') or content.startswith("'"):
        return "quote"

    # Default: note
    return "note"


def _extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    return re.findall(r"https?://[^\s<>\"')\]]+", text)


def _extract_domain(url: str) -> str:
    """Extract the simplified domain from a URL."""
    try:
        parsed = urlparse(url)
        parts = parsed.netloc.split(".")
        if len(parts) > 2 and parts[0] in ("www", "m", "mobile"):
            parts = parts[1:]
        return ".".join(parts[-2:]) if len(parts) >= 2 else parsed.netloc
    except Exception:
        return "unknown"


# ── Item CRUD ───────────────────────────────────────────────────────────────

def _claim_item_id(content: str) -> tuple[str, Path]:
    """Mint a unique id for ``content`` and atomically claim its inbox file.

    _generate_id's timestamp has 1-second resolution, so two creates of
    identically-describable content within the same second -- two
    same-named file drops, for instance -- can compute the same id.
    Checking across every backlog directory first keeps the id globally
    unique against an item long since moved to processed/ or archived/;
    claiming the inbox/ file with O_CREAT | O_EXCL and retrying on the
    next suffix when that loses the race closes the window a plain
    check-then-write would leave open for two calls landing at the same
    instant, not just one strictly after the other -- the same
    claim-a-free-name discipline move_into() already uses for filenames
    (Codex + AGORA speaker B on GRIP#10117).
    """
    item_id = base_id = _generate_id(content)
    suffix = 1
    dirs = _all_backlog_dirs()
    while True:
        # Every candidate, the retried ones included, is checked against every
        # directory, so losing the race never mints an id already in processed/.
        if any((directory / f"{item_id}.json").exists() for directory in dirs):
            suffix += 1
            item_id = f"{base_id}-{suffix}"
            continue
        path = INBOX_DIR / f"{item_id}.json"
        try:
            # 0o666 is what write_text gives, so the claim adds no exec bit.
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))
            return item_id, path
        except FileExistsError:
            suffix += 1
            item_id = f"{base_id}-{suffix}"


def create_item(
    content: str,
    content_type: Optional[str] = None,
    source: str = "cli",
    tags: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Create a new backlog item in the inbox."""
    if content_type is None:
        content_type = _detect_content_type(content)

    # Security gate: redact credentials before storing
    if content_type == "credential":
        content = "[REDACTED — credential detected. Original not stored. Review WhatsApp source.]"
        print(f"  WARNING: Credential detected and redacted. Not stored in plaintext.",
              file=sys.stderr)

    item_id, path = _claim_item_id(content)
    urls = _extract_urls(content)

    item = {
        "id": item_id,
        "content": content,
        "content_type": content_type,
        "status": "inbox",
        "source": source,
        "tags": tags or [],
        "urls": urls,
        "domains": [_extract_domain(u) for u in urls],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "processed_at": None,
        "processing": {
            "summary": None,
            "category": None,
            "priority": None,  # 1-5 (1=urgent, 5=someday)
            "actionable": None,
            "proposed_actions": [],
            "critical_notes": [],  # Anti-sycophancy notes
        },
        "metadata": metadata or {},
    }

    # path is already claimed by _claim_item_id, so this only ever fills a
    # NEW file, never silently overwrites one; clean up the empty claim on
    # a write failure rather than leave it behind (same discipline
    # move_into() uses for its own claim-then-replace).
    try:
        path.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):  # json.dumps raises TypeError on unserialisable metadata
        with contextlib.suppress(OSError):
            path.unlink()
        raise

    return item


def _read_item_text(path: Path) -> str:
    """An item file's text. Items are saved as UTF-8. One saved before GRIP#9336
    on a system whose default encoding is not UTF-8, such as Windows, is in
    that encoding, so it is read that way; a byte neither can decode becomes
    U+FFFD. Reading never raises a decode error, and an encoding name Python
    does not know falls back to UTF-8, so one old file cannot stop a listing
    (AGORA on GRIP#9799). Before any such lossy read, the exact bytes are kept
    beside the file, because a later save writes the U+FFFD marks back."""
    raw = path.read_bytes()
    text, lossy_from = _decode_item(raw)
    if lossy_from is not None:
        _keep_original_bytes(path, raw, lossy_from)
    return text


def _decode_item(raw: bytes) -> tuple[str, Optional[str]]:
    """An item file's text, and the legacy encoding's name when the decode had
    to replace bytes (None when it was exact)."""
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        pass
    legacy = _legacy_encoding()
    try:
        return raw.decode(legacy), None
    except LookupError:
        used = "utf-8"
    except UnicodeDecodeError:
        used = legacy
    return raw.decode(used, errors="replace"), legacy


def _refuse_to_lose(path: Path) -> None:
    """Before a save replaces or removes an item file that reads only lossily,
    make sure its exact bytes are kept, and refuse the save if they cannot be:
    the copy made during a read is best effort, the save must not be (AGORA on
    GRIP#9825)."""
    raw = path.read_bytes()
    lossy_from = _decode_item(raw)[1]
    if lossy_from is not None and not _keep_original_bytes(path, raw, lossy_from):
        raise OSError(f"{path.name} holds bytes that are neither UTF-8 nor {lossy_from}, and "
                      "its original could not be kept, so it is not saved over")


def _keep_original_bytes(path: Path, raw: bytes, legacy: str) -> bool:
    """Keep ``path``'s exact bytes beside it as ``<name>.<hash>.original-bytes``,
    which the ``*.json`` listing never reads, and say so on stderr; True when
    the copy exists. The name carries a hash of the bytes, so a copy can only
    exist for these very bytes: a copy of an earlier version, or a stray file
    of that name, can no longer pass for one (AGORA on GRIP#9825). The copy is
    written under a temporary name and renamed, so it is never left half
    written, and a failure to write it is reported, never raised: this runs
    inside a read, and a listing must not break (AGORA on GRIP#9825)."""
    copy = path.with_name(f"{path.name}.{hashlib.sha256(raw).hexdigest()[:12]}.original-bytes")
    if copy.exists():
        return True  # these bytes are kept and reported already: no warning on every listing
    temp = copy.with_name(f"{copy.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(raw)
        os.replace(temp, copy)
        kept = f"the original is kept as {copy.name}"
    except OSError as exc:
        with contextlib.suppress(OSError):
            temp.unlink()
        kept = f"its original bytes could not be kept ({exc.strerror or exc})"
    print(f"[backlog] {path.name} is neither UTF-8 nor {legacy}; unreadable bytes show "
          f"as U+FFFD, and {kept}", file=sys.stderr)
    return copy.exists()


def _legacy_encoding() -> str:
    """The encoding an item was saved in before GRIP#9336: the locale's own,
    which on Windows is the ANSI code page. locale.getencoding() reports it
    even in UTF-8 mode (PYTHONUTF8=1, and the default from Python 3.15), where
    getpreferredencoding() says UTF-8 and the fallback would decode nothing
    (Codex on GRIP#9799)."""
    getencoding = getattr(locale, "getencoding", None)  # Python 3.11 and later
    if getencoding:
        return getencoding()
    # Python 3.10, which GRIP still supports: getpreferredencoding() also says
    # UTF-8 in UTF-8 mode, so ask for the code page or the locale's codeset
    # directly (Codex on GRIP#9799).
    if sys.platform == "win32":
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetACP()}"
    return locale.nl_langinfo(locale.CODESET) or "utf-8"


def load_item(item_id: str) -> Optional[dict]:
    """Load a backlog item by ID from any status directory."""
    for directory in (INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR):
        path = directory / f"{item_id}.json"
        if path.exists():
            return json.loads(_read_item_text(path))
    return None


def update_item(item: dict) -> Path:
    """Save an updated item to the appropriate directory."""
    item["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Determine directory from status
    status = item.get("status", "inbox")
    if status in ("inbox", "processing"):
        directory = INBOX_DIR
    elif status == "archived":
        directory = ARCHIVED_DIR
    else:
        directory = PROCESSED_DIR

    # Remove from old locations
    item_id = item["id"]
    existing = [p for p in (d / f"{item_id}.json" for d in (INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR))
                if p.exists()]
    # Every copy about to be replaced or removed is checked before any is
    # removed, so a refusal leaves all of them in place (AGORA on GRIP#9825).
    for old_path in existing:
        _refuse_to_lose(old_path)
    for old_path in existing:
        if old_path.parent != directory:
            old_path.unlink()

    # Write to new location
    path = directory / f"{item_id}.json"
    path.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _iter_item_paths(directories):
    """Flatten directory list into a single stream of backlog JSON paths.

    Accepts both the current ``bl-*.json`` naming convention and the
    legacy ``YYYY-MM-DD-{type}-{hash}.json`` / ``backlog-*.json`` formats
    inherited from earlier versions of the ingestion tooling.
    """
    return chain.from_iterable(
        (p for p in d.glob("*.json") if p.name != ".gitattributes")
        for d in directories
    )


def _iter_parsed_items(directories):
    """Yield parsed items from the given directories, skipping malformed files."""
    for path in _iter_item_paths(directories):
        try:
            yield json.loads(_read_item_text(path))
        except (json.JSONDecodeError, KeyError):
            continue


def _all_backlog_dirs() -> tuple:
    """Return the three backlog directories, resolved at call time.

    Resolved dynamically so pytest fixtures can monkeypatch the module
    constants between tests.
    """
    return (INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR)


def list_items(
    status: Optional[str] = None,
    content_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """List backlog items with optional filtering.

    Iterates every directory and filters by the JSON ``status`` field.
    The physical directory is advisory only — historical data drift
    means items can live in ``archived/`` while still carrying
    ``status: inbox``. The authoritative source of truth is the JSON
    payload, not the folder layout.
    """
    items = [
        item for item in _iter_parsed_items(_all_backlog_dirs())
        if (status is None or item.get("status", "inbox") == status)
        and (content_type is None or item.get("content_type") == content_type)
    ]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items[:limit]


def list_items_with_total(
    status: Optional[str] = None,
    content_type: Optional[str] = None,
    limit: int = 50,
) -> "tuple[list[dict], int]":
    """``(page, total)`` from ONE scan of the backlog directories.

    :func:`list_items` returns a PAGE. Any caller printing ``len()`` of it as
    a count is claiming a page is a total, which the CLI did: a backlog of
    200 rendered "Backlog (50 items)". Pairing two calls would scan the
    directories twice and open an inconsistency window, so the total is taken
    from the same scan that produced the page.
    """
    items = [
        item for item in _iter_parsed_items(_all_backlog_dirs())
        if (status is None or item.get("status", "inbox") == status)
        and (content_type is None or item.get("content_type") == content_type)
    ]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items[:limit], len(items)


def _matches_query(item: dict, query_lower: str) -> bool:
    """True if the query substring appears in content, summary, or tags."""
    content = (item.get("content") or "").lower()
    summary = (item.get("processing", {}).get("summary") or "").lower()
    tags = " ".join(item.get("tags", [])).lower()
    return query_lower in content or query_lower in summary or query_lower in tags


def search_items(query: str) -> list[dict]:
    """Search across all backlog items."""
    query_lower = query.lower()
    results = [
        item for item in _iter_parsed_items((INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR))
        if _matches_query(item, query_lower)
    ]
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


def _update_bounds(bounds: dict, created: str) -> None:
    """Track oldest/newest created_at timestamps in a mutable bounds dict."""
    if bounds["oldest"] is None or created < bounds["oldest"]:
        bounds["oldest"] = created
    if bounds["newest"] is None or created > bounds["newest"]:
        bounds["newest"] = created


def backlog_status() -> dict:
    """Get backlog statistics."""
    counts = {"inbox": 0, "processed": 0, "archived": 0, "actionable": 0, "deferred": 0}
    type_counts: dict[str, int] = {}
    bounds = {"oldest": None, "newest": None}

    for item in _iter_parsed_items((INBOX_DIR, PROCESSED_DIR, ARCHIVED_DIR)):
        status = item.get("status", "inbox")
        counts[status] = counts.get(status, 0) + 1
        ct = item.get("content_type", "unknown")
        type_counts[ct] = type_counts.get(ct, 0) + 1
        _update_bounds(bounds, item.get("created_at", ""))

    oldest = bounds["oldest"]
    newest = bounds["newest"]

    total = sum(counts.values())
    return {
        "total": total,
        "by_status": counts,
        "by_type": type_counts,
        "oldest": oldest,
        "newest": newest,
    }


# ── WhatsApp Import ─────────────────────────────────────────────────────────

def _parse_whatsapp_line(line: str) -> Optional[dict]:
    """Parse a single WhatsApp chat line into structured data."""
    # Format: [YYYY/MM/DD, HH:MM:SS] Sender: Message
    match = re.match(
        r"\[(\d{4}/\d{2}/\d{2}),\s*(\d{2}:\d{2}:\d{2})\]\s*([^:]+):\s*(.*)",
        line,
    )
    if not match:
        return None

    date_str, time_str, sender, message = match.groups()

    # Skip system messages, media placeholders, game results
    if sender.strip() in ("", "You"):
        return None
    if "‎<attached:" in message:
        return None  # Media attachment — skip text, handle separately
    if message.strip().startswith("‎"):
        return None  # System message (pinned, etc.)

    # Skip game results (Qwartel, Wordle, etc.)
    game_patterns = [
        r"🐦.*Qwartel",
        r"Wordle\s+\d+",
        r"🟩|🟥|🟨|⬜|⬛",  # Game grid squares
    ]
    for pattern in game_patterns:
        if re.search(pattern, message):
            return None

    # Skip very short messages (likely reactions or single emoji)
    if len(message.strip()) < 5:
        return None

    return {
        "date": date_str.replace("/", "-"),
        "time": time_str,
        "sender": sender.strip(),
        "message": message.strip(),
    }


_WHATSAPP_DATE_PREFIX = re.compile(r"\[\d{4}/")


def _merge_whatsapp_lines(lines: list) -> list:
    """Merge continuation lines into their parent message."""
    merged = []
    for line in lines:
        if _WHATSAPP_DATE_PREFIX.match(line):
            merged.append(line)
        elif merged:
            merged[-1] += " " + line.strip()
    return merged


def _parse_whatsapp_lines(merged: list) -> tuple:
    """Parse merged lines into structured messages, returning (parsed, skipped)."""
    parsed = []
    skipped = 0
    for line in merged:
        result = _parse_whatsapp_line(line)
        if result:
            parsed.append(result)
        else:
            skipped += 1
    return parsed, skipped


def _dedupe_messages(parsed: list) -> list:
    """Remove duplicate messages by first 100 chars."""
    seen = set()
    unique = []
    for msg in parsed:
        key = msg["message"][:100]
        if key not in seen:
            seen.add(key)
            unique.append(msg)
    return unique


def _create_whatsapp_items(unique: list) -> int:
    """Persist unique WhatsApp messages as backlog items."""
    created = 0
    for msg in unique:
        create_item(
            content=msg["message"],
            source=f"whatsapp-{msg['date']}",
            metadata={
                "whatsapp_date": f"{msg['date']} {msg['time']}",
                "whatsapp_sender": msg["sender"],
            },
        )
        created += 1
    return created


def import_whatsapp(chat_file: str, dry_run: bool = False) -> dict:
    """Import a WhatsApp chat export into the backlog."""
    path = Path(chat_file)
    if not path.exists():
        return {"error": f"File not found: {chat_file}"}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    merged = _merge_whatsapp_lines(lines)
    parsed, skipped = _parse_whatsapp_lines(merged)
    unique = _dedupe_messages(parsed)
    created = _create_whatsapp_items(unique) if not dry_run else 0

    return {
        "total_lines": len(lines),
        "merged_lines": len(merged),
        "parsed": len(parsed),
        "unique": len(unique),
        "skipped": skipped,
        "created": created,
        "dry_run": dry_run,
        "date_range": {
            "first": parsed[0]["date"] if parsed else None,
            "last": parsed[-1]["date"] if parsed else None,
        },
    }


# ── File Drop Detection ────────────────────────────────────────────────────

def _describe_dropped_file(path: Path) -> tuple[str, str]:
    """The content and content type a dropped file's item holds: its text, or a reference."""
    if path.suffix in (".txt", ".md", ".markdown"):
        content = path.read_text(encoding="utf-8", errors="replace")[:5000]
        return content, _detect_content_type(content)
    if path.suffix in (".pdf",):
        return f"PDF file: {path.name} ({path.stat().st_size / 1024:.0f} KB)", "file"
    if path.suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return f"Image: {path.name} ({path.stat().st_size / 1024:.0f} KB)", "file"
    if path.suffix in (".py", ".js", ".ts", ".rs", ".go", ".rb", ".sh"):
        return path.read_text(encoding="utf-8", errors="replace")[:3000], "snippet"
    return f"File: {path.name} ({path.stat().st_size / 1024:.0f} KB)", "file"


def _move_to_processed(path: Path) -> Path:
    """Move a dropped file into PROCESSED_DIR under a free name; see move_into."""
    return move_into(path, PROCESSED_DIR)


def move_into(path: Path, folder: Path, name: Optional[str] = None) -> Path:
    """Move a file into ``folder`` under a name no other file holds; return it.

    The scanner keeps files dropped into inbox/dropped/ this way too
    (GRIP#9337). The dropped name is tried first, then ``<stem>-2<suffix>``, ``<stem>-3<suffix>`` and so
    on. Each candidate is claimed by creating it with O_CREAT | O_EXCL, which is atomic on a
    local filesystem on every platform, so the move overwrites neither an earlier processed file (as
    ``Path.rename`` does on Linux and macOS) nor another scan's claim; the dropped file then
    replaces its own empty claim. A candidate that exists in any form is taken: Windows
    refuses an existing folder with PermissionError rather than FileExistsError. If the
    replace fails, the claim is removed and the error raised, with the dropped file left
    where it was (GRIP#8776).
    """
    target = Path(name or path.name)  # the name to try first, ``path``'s own by default
    n = 1
    while True:
        claim = folder / (target.name if n == 1 else f"{target.stem}-{n}{target.suffix}")
        try:
            os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except OSError:
            if not os.path.lexists(claim):
                raise
            n += 1
            continue
        try:
            os.replace(path, claim)
        except OSError:
            with contextlib.suppress(OSError):
                claim.unlink()  # an empty claim left behind holds no data either way
            raise
        return claim


def is_hidden(path: Path) -> bool:
    """True for a dot-file, and on Windows for a file marked hidden or system.
    Windows hides a file with an attribute, not by its name, and can write a
    desktop.ini into a folder whose look is customised, so a name check alone
    took such files for drops (GRIP#9339). The attribute exists only on
    Windows; elsewhere only the name counts. A file that cannot be examined
    counts as not hidden, and its move then reports it."""
    if path.name.startswith("."):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


# The three fields create_item() always writes, so a .json file carrying
# every one of them is a genuine backlog item rather than something that was
# merely dropped into inbox/ (GRIP#9812).
_ITEM_FIELDS = {"id", "status", "created_at"}


def _is_backlog_item(path: Path) -> bool:
    """True when ``path`` holds the backlog item schema this module writes.

    A parse failure, a value that is not a JSON object, or an object missing
    any of ``_ITEM_FIELDS`` means ``path`` is not an item — it is a .json
    document someone dropped into inbox/, such as a stray settings.json, and
    scan_inbox_folder keeps it instead of leaving it there to be misread by
    list_items() as a phantom item with none of the fields real code expects
    (GRIP#9812).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and _ITEM_FIELDS <= data.keys()


def scan_inbox_folder() -> list[dict]:
    """Detect regular files dropped into the inbox folder and ingest them.

    Each file is moved into processed/ first, under a name no other file holds, and its item
    is saved only once the move has succeeded, so a file is never ingested twice and never
    overwrites an earlier processed file; the item's ``file_path`` says where the file went.
    A file that cannot be read or moved, such as one another program still holds open, is
    reported on stderr and left for the next scan. A file whose item then cannot be saved,
    for example text create_item cannot encode in the locale's encoding, stays in
    processed/ and its location is reported, so it is not ingested again and the scan goes
    on. Folders (such as the hourly scanner's inbox/dropped/) and hidden files are skipped
    (GRIP#8776). A .json file already holding the item schema is left alone as an existing
    item; one that does not is kept in KEPT_DIR instead of processed/, so it is never
    misread by list_items() as a phantom item (GRIP#9812).
    """
    new_items = []
    for path in INBOX_DIR.iterdir():
        if path.suffix == ".json" and _is_backlog_item(path):
            continue  # Already a backlog item
        if is_hidden(path):
            continue  # A dot-file, or one Windows marks hidden or system
        if not path.is_file():
            continue  # A folder, such as scripts/backlog-scanner.py's inbox/dropped/

        try:
            content, content_type = _describe_dropped_file(path)
            dest = (move_into(path, KEPT_DIR) if path.suffix == ".json"
                    else _move_to_processed(path))
        except OSError as exc:
            print(f"  Skipped {path.name}: {exc.strerror or exc}. "
                  "A later scan retries it if it is still in the inbox.", file=sys.stderr)
            continue

        try:
            item = create_item(
                content=content,
                content_type=content_type,
                source=f"file-drop:{path.name}",
                metadata={"original_filename": path.name, "file_path": str(dest)},
            )
        except (OSError, UnicodeError) as exc:
            print(f"  Kept {path.name} as {dest}, but could not save its item: {exc}",
                  file=sys.stderr)
            continue
        new_items.append(item)

    return new_items


# ── CLI ─────────────────────────────────────────────────────────────────────

def _format_item(item: dict, verbose: bool = False) -> str:
    """Format a backlog item for terminal display."""
    status_icons = {
        "inbox": "📥",
        "processing": "⏳",
        "processed": "✅",
        "actionable": "🎯",
        "deferred": "⏸️",
        "archived": "📦",
    }
    icon = status_icons.get(item.get("status", "inbox"), "❓")
    ct = item.get("content_type", "note")
    content = item.get("content", "")

    # Truncate for display
    if len(content) > 80:
        display = content[:77] + "..."
    else:
        display = content

    line = f"  {icon} [{ct:8s}] {display}"

    if verbose:
        line += f"\n    ID: {item['id']}  Created: {item.get('created_at', '?')[:10]}"
        if item.get("tags"):
            line += f"  Tags: {', '.join(item['tags'])}"
        summary = item.get("processing", {}).get("summary")
        if summary:
            line += f"\n    Summary: {summary[:60]}"

    return line


def _cmd_add():
    """Add an item to the backlog."""
    if len(sys.argv) < 3:
        print("  Usage: backlog_manager.py add 'note text'")
        print("         backlog_manager.py add --url 'https://...'")
        print("         backlog_manager.py add --file path/to/file")
        return

    if sys.argv[2] == "--url" and len(sys.argv) > 3:
        content = sys.argv[3]
        item = create_item(content, content_type="url")
    elif sys.argv[2] == "--file" and len(sys.argv) > 3:
        file_path = sys.argv[3]
        if not Path(file_path).exists():
            print(f"  File not found: {file_path}")
            return
        content = f"File: {file_path}"
        item = create_item(content, content_type="file",
                          metadata={"file_path": file_path})
    else:
        content = " ".join(sys.argv[2:])
        item = create_item(content)

    print(f"  Added to backlog: {item['id']}")
    print(f"  Type: {item['content_type']}")
    print(f"  Content: {content[:60]}{'...' if len(content) > 60 else ''}")


def _cmd_list():
    """List backlog items."""
    status = None
    for i, arg in enumerate(sys.argv):
        if arg == "--status" and i + 1 < len(sys.argv):
            status = sys.argv[i + 1]

    items, total = list_items_with_total(status=status)
    if not items:
        print("  Backlog is empty." if status is None else f"  No items with status '{status}'.")
        return

    # "50 of 213" when paged, never a bare "50 items" -- printing a page as a
    # total is the same defect that had /recall reporting 8 memories against
    # 379 matches and the ultraprompt picker reporting 6 briefs against 11.
    shown = f"{len(items)} of {total} items" if len(items) < total else f"{len(items)} items"
    print(f"\n  Backlog ({shown}):")
    for item in items:
        print(_format_item(item))


def _cmd_search():
    """Search the backlog."""
    if len(sys.argv) < 3:
        print("  Usage: backlog_manager.py search <query>")
        return

    query = " ".join(sys.argv[2:])
    results = search_items(query)
    print(f"\n  Found {len(results)} items matching '{query}':")
    for item in results[:20]:
        print(_format_item(item, verbose=True))


def _cmd_status():
    """Show backlog statistics."""
    stats = backlog_status()
    print(f"\n  Backlog Status")
    print(f"  {'─' * 40}")
    print(f"  Total items: {stats['total']}")

    if stats["by_status"]:
        print(f"\n  By status:")
        for status, count in sorted(stats["by_status"].items()):
            if count > 0:
                print(f"    {status:12s}  {count:>4}")

    if stats["by_type"]:
        print(f"\n  By type:")
        for ct, count in sorted(stats["by_type"].items(), key=lambda x: -x[1]):
            print(f"    {ct:12s}  {count:>4}")

    if stats["oldest"]:
        print(f"\n  Date range: {stats['oldest'][:10]} to {stats['newest'][:10]}")


def _cmd_import_whatsapp():
    """Import WhatsApp chat export."""
    if len(sys.argv) < 3:
        print("  Usage: backlog_manager.py import-whatsapp <chat-file.txt>")
        return

    chat_file = sys.argv[2]
    dry = "--dry-run" in sys.argv

    result = import_whatsapp(chat_file, dry_run=dry)
    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    print(f"\n  WhatsApp Import {'(DRY RUN)' if dry else ''}")
    print(f"  {'─' * 40}")
    print(f"  Total lines: {result['total_lines']}")
    print(f"  Parsed messages: {result['parsed']}")
    print(f"  Unique (deduplicated): {result['unique']}")
    print(f"  Skipped (games, media, system): {result['skipped']}")
    print(f"  Date range: {result['date_range']['first']} to {result['date_range']['last']}")

    if not dry:
        print(f"  Items created: {result['created']}")
    else:
        print(f"\n  Run without --dry-run to import {result['unique']} items.")


def _cmd_scan():
    """Scan inbox folder for dropped files."""
    new_items = scan_inbox_folder()
    if new_items:
        print(f"  Ingested {len(new_items)} new files:")
        for item in new_items:
            print(f"    {item['id']} [{item['content_type']}] {item['content'][:50]}")
    else:
        print("  No new files in inbox.")


def _cmd_remind():
    """Show items needing attention."""
    items = list_items(status="actionable", limit=10)
    items += list_items(status="inbox", limit=5)

    if not items:
        print("  Nothing needs attention right now.")
        return

    print(f"\n  Items needing attention ({len(items)}):")
    for item in items:
        print(_format_item(item, verbose=True))


_ACTION_VERBS = frozenset({
    "todo", "fix", "do", "add", "remove", "check", "build", "write",
    "call", "email", "ping", "ask", "reply", "send", "review", "read",
    "test", "deploy", "ship", "investigate", "debug", "merge", "close",
})


# (category, priority, actionable, terminal status) for each content type with a fixed rule.
_CLASSIFICATION_RULES = {
    "credential": ("security", 1, False, "archived"),
    "todo":       ("todo",     2, True,  "actionable"),
    "idea":       ("idea",     3, True,  "actionable"),
    "research":   ("research", 3, False, "deferred"),
    "quote":      ("quote",    5, False, "deferred"),
    "url":        ("reference",4, False, "deferred"),
    "snippet":    ("code_reference", 4, False, "deferred"),
    "file":       ("file",     4, False, "deferred"),
}


def _item_text(item: dict) -> str:
    """The item's content as stripped text, whatever type it was stored as."""
    raw_content = item.get("content") or ""
    if isinstance(raw_content, dict):
        raw_content = json.dumps(raw_content, ensure_ascii=False)
    elif not isinstance(raw_content, str):
        raw_content = str(raw_content)
    return raw_content.strip()


def _classification(ctype: str, content: str) -> tuple:
    """(category, priority, actionable, status): the content type's rule, else the note heuristic."""
    if ctype in _CLASSIFICATION_RULES:
        return _CLASSIFICATION_RULES[ctype]
    actionable = bool(set(content.lower().split()) & _ACTION_VERBS)
    return "note", 3 if actionable else 4, actionable, "actionable" if actionable else "deferred"


def _classify_item(item: dict) -> None:
    """Rule-based auto-classification.

    Fills the ``processing`` block and assigns a terminal status
    (``actionable``/``deferred``/``archived``) based on the item's
    ``content_type`` and a small keyword heuristic for notes. This is
    deliberately mechanical — no LLM call per item — so drain is O(n)
    file I/O rather than O(n) API round-trips. Items that need deeper
    analysis can be re-promoted via ``triage`` later.
    """
    content = _item_text(item)
    ctype = item.get("content_type") or "note"
    proc = item.setdefault("processing", {})

    summary = (content.split("\n", 1)[0] if content else "")[:120]
    proc["summary"] = summary or proc.get("summary")
    proc.setdefault("proposed_actions", [])
    proc.setdefault("critical_notes", [])

    category, priority, actionable, status = _classification(ctype, content)
    proc["category"] = category
    proc["priority"] = priority
    proc["actionable"] = actionable
    item["status"] = status
    item["processed_at"] = datetime.now(timezone.utc).isoformat()


def _cmd_process():
    """Drain inbox items via rule-based classification.

    Usage: process [--limit N]  (default 9999, i.e. everything)
    """
    limit = 9999
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[i + 1])
            except ValueError:
                print(f"  Invalid --limit value: {sys.argv[i + 1]}")
                return

    items = list_items(status="inbox", limit=limit)
    if not items:
        print("  Inbox empty. Nothing to process.")
        return

    stats = {"actionable": 0, "deferred": 0, "archived": 0}
    skipped = 0
    for item in items:
        try:
            _classify_item(item)
            update_item(item)
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        stats[item["status"]] = stats.get(item["status"], 0) + 1

    total = sum(stats.values())
    print(f"\n  Processed {total} inbox items")
    print(f"  {'─' * 40}")
    for status in ("actionable", "deferred", "archived"):
        if stats.get(status):
            print(f"    {status}: {stats[status]}")
    if skipped:
        print(f"    skipped (malformed): {skipped}")
    remaining = len(list_items(status="inbox", limit=100000))
    print(f"\n  Inbox remaining: {remaining}")


def _cmd_triage():
    """Re-promote a processed/deferred item back to inbox for deeper review.

    Usage: triage <item-id>
    """
    if len(sys.argv) < 3:
        print("  Usage: triage <item-id>")
        return
    item_id = sys.argv[2]
    item = load_item(item_id)
    if item is None:
        print(f"  Item not found: {item_id}")
        return
    item["status"] = "inbox"
    item["processed_at"] = None
    update_item(item)
    print(f"  Re-triaged {item_id} to inbox for deeper review.")


_COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "search": _cmd_search,
    "status": _cmd_status,
    "import-whatsapp": _cmd_import_whatsapp,
    "scan": _cmd_scan,
    "remind": _cmd_remind,
    "process": _cmd_process,
    "triage": _cmd_triage,
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    handler = _COMMANDS.get(cmd)
    if handler is None:
        print(f"  Unknown command: {cmd}")
        print(__doc__)
        return
    handler()


if __name__ == "__main__":
    main()
