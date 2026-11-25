#!/usr/bin/env python3
"""Backlog autonomous scanner — processes inbox items through the pipeline.

Runs outside Claude Code sessions via launchd (macOS) or cron (Linux).
Uses Claude API directly for reasoning stages.

Process isolation: no context window consumption, no hook execution,
predictable bounded cost.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

# Add lib to path
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
sys.path.insert(0, str(CLAUDE_DIR))

from lib.backlog_item import (
    BacklogItem, BACKLOG_DIR, save_item, load_item,
    STAGE_DIRS, generate_id, now_iso
)
from lib.backlog_manager import is_hidden, move_into
from lib.backlog_pipeline import process_item

# Logging setup
log_path = BACKLOG_DIR / "scanner.log"
logging.basicConfig(
    filename=str(log_path),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backlog-scanner")

# Where dropped files are kept once an item points at them. Not a stage folder,
# so no item listing reads it (Codex on GRIP#9809).
KEPT_DIR = BACKLOG_DIR / "kept-files"


def create_item_from_file(file_path: Path) -> BacklogItem:
    """Create a backlog item from a dropped file."""
    return BacklogItem(
        source_method="file_drop",
        source_raw_input=str(file_path),
        source_file_path=str(file_path),
        content_type="note",
        content_body=f"[Dropped file: {file_path.name}]",
    )


def _move_back(kept: Path, dropped_file: Path, exc: Exception) -> None:
    """Return a kept file to inbox/dropped/ after its item failed to save, under
    its dropped name or the next free one, so it never replaces a file dropped
    there since. If even that fails, log where the file is, because no later
    cycle looks in KEPT_DIR (AGORA on GRIP#9809)."""
    try:
        back = move_into(kept, dropped_file.parent, name=dropped_file.name)
    except OSError as move_exc:
        log.error("Could not save an item for %s (%s) nor move it back (%s); it is in %s",
                  dropped_file.name, exc, move_exc, kept)
        return
    log.warning("Could not save an item for %s; moved back as %s for the next cycle: %s",
                dropped_file.name, back.name, exc)


def _keep_dropped(f: Path, inbox: Path) -> bool:
    """Move one dropped file into KEPT_DIR under a free name and save an item
    pointing at it; True when the item was saved. If either step fails, the
    file is left in, or moved back to, inbox/dropped/, so the next cycle tries
    again rather than stranding it without an item (Codex on GRIP#9809)."""
    try:
        kept = move_into(f, KEPT_DIR)
    except OSError as exc:
        log.warning("Could not keep dropped file %s: %s", f.name, exc)
        return False
    item = create_item_from_file(kept)
    try:
        save_item(item, inbox)
    except Exception as exc:  # noqa: BLE001 -- any failure must not strand the file
        _move_back(kept, f, exc)
        return False
    log.info("Ingested dropped file: %s -> %s, kept as %s", f.name, item.id, kept.name)
    return True


def ingest_dropped_files(inbox: Path) -> int:
    """Turn each regular file in inbox/dropped/ into an inbox item.

    The file is kept in KEPT_DIR and the item points at it, so the pipeline can
    still read it later in the same cycle. Deleting it here lost its content
    (GRIP#9337). KEPT_DIR is outside every stage folder, because both item
    listings read each stage's *.json files as items, and a dropped .json file
    there showed up as a phantom item or, malformed, broke the listing (Codex
    on GRIP#9809). A folder, or any other entry that is not a regular file, is
    skipped: it cannot be moved as a file, and unlink() raised on it. So is a
    symbolic link, because moving a relative link changes what it points at,
    and its item could read an unrelated kept file (Codex on GRIP#9809)."""
    dropped = inbox / "dropped"
    if not dropped.exists():
        return 0
    KEPT_DIR.mkdir(parents=True, exist_ok=True)
    return sum(_keep_dropped(f, inbox) for f in sorted(dropped.iterdir())
               if not is_hidden(f) and f.is_file() and not f.is_symlink())


def _ensure_dirs(inbox: Path) -> None:
    for d in STAGE_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    (inbox / "dropped").mkdir(exist_ok=True)


def _record_failure(item: BacklogItem, exc: Exception, retry_limit: int) -> None:
    """Return a failed item to the inbox for a retry, or file it as processed
    with its error once its retries are spent."""
    log.error("Failed to process %s: %s", item.id, exc)
    item.error = str(exc)
    item.retry_count += 1
    if item.retry_count >= retry_limit:
        item.stage = "processed"
        save_item(item, STAGE_DIRS["processed"])
        log.warning("Item %s exhausted retries, moved to processed with error", item.id)
    else:
        save_item(item, STAGE_DIRS["inbox"])
        log.info("Item %s returned to inbox for retry (%d/%d)",
                 item.id, item.retry_count, retry_limit)


def _release(lock_path: Path) -> None:
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass


def _process_one(item_path: Path, config: dict, retry_limit: int, stats: dict) -> None:
    """Process one inbox item under a lock: an atomic move into the processing
    folder, skipped if another cycle holds it, and released whatever happens."""
    lock_path = STAGE_DIRS["processing"] / item_path.name
    if lock_path.exists():
        return
    item = load_item(item_path)
    log.info("Processing item: %s (%s)", item.id, item.content_title or "untitled")
    try:
        os.replace(str(item_path), str(lock_path))
    except OSError as e:
        log.warning("Failed to acquire lock for %s: %s", item.id, e)
        return
    try:
        item = process_item(item, config)
        item.stage = "processed"
        save_item(item, STAGE_DIRS["processed"])
        stats["items_processed"] += 1
        log.info("Processed: %s -> category=%s, confidence=%.2f",
                 item.id, item.category, item.confidence or 0)
    except Exception as e:
        _record_failure(item, e, retry_limit)
        stats["items_failed"] += 1
    finally:
        _release(lock_path)


def scan_cycle(config: dict) -> dict:
    """One complete scanner cycle. Idempotent.

    Returns stats dict with items_processed, items_failed and dropped_ingested.
    """
    inbox = STAGE_DIRS["inbox"]
    stats = {"items_processed": 0, "items_failed": 0, "dropped_ingested": 0}
    _ensure_dirs(inbox)
    stats["dropped_ingested"] = ingest_dropped_files(inbox)

    scanner_config = config.get("scanner", {})
    max_items = scanner_config.get("max_items_per_cycle", 20)
    retry_limit = scanner_config.get("retry_limit", 3)
    items = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for item_path in items[:max_items]:
        _process_one(item_path, config, retry_limit, stats)
    return stats


def load_config() -> dict:
    """Load backlog configuration."""
    config_path = BACKLOG_DIR / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def main():
    """Main entry point for the scanner."""
    log.info("Scanner cycle starting")
    start = time.time()

    try:
        config = load_config()
        if not config.get("scanner", {}).get("enabled", True):
            log.info("Scanner disabled in config, exiting")
            return

        stats = scan_cycle(config)
        elapsed = time.time() - start

        log.info(
            "Scanner cycle complete: processed=%d, failed=%d, dropped=%d, "
            "elapsed=%.1fs",
            stats["items_processed"], stats["items_failed"],
            stats["dropped_ingested"], elapsed,
        )
    except Exception as e:
        log.error("Scanner cycle failed: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    main()
