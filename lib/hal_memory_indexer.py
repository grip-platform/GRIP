#!/usr/bin/env python3
"""HAL-memory -> KONO indexer: make HAL-absorbed knowledge /recall-able.

THE GAP THIS CLOSES (plain English)
-----------------------------------
HAL absorbs external knowledge (Discord/Slack/monitor feeds, URL fetches, and
on 2026-06-09 ~250 GRIP drafts) by writing ``absorbed_<source>_<hash>.md`` files
into HAL's own home (``~/.hal/memory/``) via
``lib.hal.knowledge_absorption.absorb_from_source``. But ``/recall`` queries
GRIP's KONO substrate, which is fed from ``~/.claude/learning/insights.jsonl``
(see ``lib/kono_auto_capture.py``). The two stores never touched — so every byte
HAL absorbed was invisible to ``/recall``.

This module is the bridge. ``--sync`` scans the HAL memory directory, transforms
each absorbed ``.md`` into a KONO-ingestible insight record, and appends it to
``learning/insights.jsonl``. ``kono_auto_capture`` then routes those records into
the KONO substrate on its next run, and the knowledge becomes ``/recall``-able.

IDEMPOTENCY (the core safety property)
--------------------------------------
Each emitted record carries a deterministic ``fingerprint`` = sha256 of the
absorbed file's basename + its body. Before emitting, ``--sync`` loads every
fingerprint already present in the target ``insights.jsonl`` AND in a sidecar
cache. Re-running ``--sync`` therefore appends exactly zero new records the
second time. Adding one new absorbed file and re-running appends exactly one.

DESIGN
------
- DIP: ``source_dir``, ``insights_jsonl`` and ``cache_path`` are all injectable
  (the tests drive entirely synthetic dirs — zero real-filesystem coupling).
- Record schema deliberately carries BOTH ``id`` and ``fingerprint`` set to the
  same content-hash, because ``kono_auto_capture`` dedupes on
  ``fingerprint or id`` and the two coexisting insights.jsonl shapes (auto-memory
  vs evo) use different keys. Carrying both makes the record ingestible by
  ``kono_auto_capture`` regardless of which key it reads.
- Best-effort, plain-language errors. No stubs: every function is fully wired.

Usage:
    python3 lib/hal_memory_indexer.py --sync          # index HAL -> insights.jsonl
    python3 lib/hal_memory_indexer.py --sync --dry-run  # report, write nothing
    python3 lib/hal_memory_indexer.py --count         # how many absorbed files
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

# Path-portability shim (rules/path-portability.md).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
# The shared decoder lives under lib/, not lib/hal/: importing anything in the
# hal package loads the provider registry, which this module avoids.
from frontmatter_scalar import unquote as _unquote  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# Default target: the KONO ingestion file kono_auto_capture reads.
DEFAULT_INSIGHTS = CLAUDE_DIR / "learning" / "insights.jsonl"
# Default sidecar cache of fingerprints already emitted by this indexer.
DEFAULT_CACHE = CLAUDE_DIR / "state" / "integrations" / "hal-memory-indexer-cache.json"
# Absorbed files are named absorbed_<source>_<hash8>.md (knowledge_absorption.py).
_ABSORBED_GLOB = "absorbed_*.md"
# Frontmatter is a leading --- ... --- block (simple key: value lines).
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Body provenance lines written by knowledge_absorption (source:, channel_id:, url:).
_BODY_KV_RE = re.compile(r"^(source|channel_id|url|user_id|thread_id):\s*(.*)$")


def _default_hal_memory_dir() -> Path:
    """Resolve HAL's memory directory without importing HAL (avoid lib shadow).

    Mirrors ``lib/hal/home.py`` resolution: ``HAL_HOME`` env -> ``~/.hal`` ->
    ``CLAUDE_DIR`` -> ``~/.claude``, then ``/memory``. Pure env+path, no I/O
    beyond the isdir probe.
    """
    hal_path = Path(os.path.expanduser("~/.hal"))
    claude_path = Path(os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude"))
    home_env = os.environ.get("HAL_HOME")
    if home_env:
        base = Path(home_env)
    elif hal_path.is_dir():
        base = hal_path
    else:
        base = claude_path
    return base / "memory"


@dataclass(frozen=True)
class AbsorbedEntry:
    """A parsed absorbed ``.md`` file (frontmatter + body provenance + text)."""

    filename: str
    name: str
    description: str
    memory_type: str
    source: str
    channel_id: str
    url: str
    body: str

    def fingerprint(self) -> str:
        """Deterministic content hash — basename + body. Stable across runs.

        Basename is included so two files with identical bodies but different
        absorbed-hashes remain distinct; body is included so an edited file
        re-indexes. This is the idempotency key.
        """
        digest = hashlib.sha256()
        digest.update(self.filename.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(self.body.encode("utf-8"))
        return digest.hexdigest()


@dataclass
class SyncResult:
    """Outcome of a ``--sync`` pass."""

    scanned: int = 0
    indexed: int = 0
    skipped_duplicate: int = 0
    skipped_empty: int = 0
    dry_run: bool = False
    fingerprints_written: list[str] = field(default_factory=list)


def _parse_simple_frontmatter(text: str) -> dict[str, str]:
    """Parse ``key: value`` frontmatter lines (no nested structures).

    Absorbed files are written by ``MemoryEngine.write_entry``, which emits
    ``name`` and ``description`` as JSON strings, so a double-quoted value is
    decoded back; otherwise every indexed title kept its quotes.
    """
    result: dict[str, str] = {}
    for line in text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = _unquote(val.strip())
    return result


def _split_provenance(rest: str) -> tuple[dict[str, str], str]:
    """Split the body's leading provenance preamble from its content text.

    ``knowledge_absorption`` prepends ``source:``/``channel_id:``/``url:`` lines
    to the body, terminated by a blank line. Returns ({provenance kv}, content).
    """
    prov: dict[str, str] = {}
    content_lines: list[str] = []
    consuming = True
    for line in rest.split("\n"):
        kv = _BODY_KV_RE.match(line) if consuming else None
        if kv:
            prov[kv.group(1)] = kv.group(2).strip()
            continue
        if consuming and line.strip() == "":
            consuming = False  # blank line ends the preamble
            continue
        consuming = False
        content_lines.append(line)
    return prov, "\n".join(content_lines).strip()


def parse_absorbed(filename: str, raw: str) -> AbsorbedEntry:
    """Parse one absorbed ``.md`` into a structured entry.

    Splits the YAML-ish frontmatter from the body, then extracts the provenance
    key/values (``source``, ``channel_id``, ``url``) the absorber prepended.
    """
    match = _FRONTMATTER_RE.match(raw)
    if match:
        fm = _parse_simple_frontmatter(match.group(1))
        rest = raw[match.end():]
    else:
        fm, rest = {}, raw

    prov, body = _split_provenance(rest)
    return AbsorbedEntry(
        filename=filename,
        name=fm.get("name", ""),
        description=fm.get("description", ""),
        memory_type=fm.get("type", "reference"),
        source=prov.get("source") or _source_from_filename(filename),
        channel_id=prov.get("channel_id", ""),
        url=prov.get("url", ""),
        body=body,
    )


def _source_from_filename(filename: str) -> str:
    """Recover the source token from ``absorbed_<source>_<hash>.md``."""
    stem = filename[len("absorbed_"):] if filename.startswith("absorbed_") else filename
    stem = stem[:-3] if stem.endswith(".md") else stem
    parts = stem.rsplit("_", 1)
    return parts[0] if len(parts) == 2 else "unknown"


def to_insight_record(entry: AbsorbedEntry) -> dict:
    """Transform an absorbed entry into a KONO-ingestible insight record.

    The field mapping is the load-bearing contract (a Goodhart-resistant test
    asserts each mapping). ``kono_auto_capture`` reads ``text``, ``context``,
    ``tags``, ``learning_score`` and dedupes on ``fingerprint or id`` — all
    present. ``id == fingerprint`` so dedup works under either key.
    """
    fp = entry.fingerprint()
    text = entry.description.strip()
    body = entry.body.strip()
    if body:
        text = f"{text}\n\n{body}" if text else body
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tags = ["hal-absorbed", f"source:{entry.source}", entry.memory_type]
    if entry.url:
        tags.append(f"url:{entry.url}")
    return {
        "id": fp,
        "fingerprint": fp,
        "timestamp": now,
        "created_at": now,
        "source": f"hal_memory:{entry.source}",
        "context": "hal_absorbed_knowledge",
        "text": text,
        "memory_type": entry.memory_type,
        "learning_score": 0.7,
        "tags": tags,
        "hal_filename": entry.filename,
        "url": entry.url,
    }


class HalMemoryIndexer:
    """Bridges HAL-absorbed ``.md`` files into the KONO ``insights.jsonl`` feed."""

    def __init__(
        self,
        source_dir: Optional[Path] = None,
        insights_jsonl: Optional[Path] = None,
        cache_path: Optional[Path] = None,
    ):
        self.source_dir = Path(source_dir) if source_dir else _default_hal_memory_dir()
        self.insights_jsonl = Path(insights_jsonl) if insights_jsonl else DEFAULT_INSIGHTS
        self.cache_path = Path(cache_path) if cache_path else DEFAULT_CACHE

    # -- scanning -----------------------------------------------------------

    def iter_absorbed(self) -> Iterator[AbsorbedEntry]:
        """Yield every parsed absorbed entry in ``source_dir`` (sorted, stable)."""
        if not self.source_dir.is_dir():
            return
        for path in sorted(self.source_dir.glob(_ABSORBED_GLOB)):
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            yield parse_absorbed(path.name, raw)

    def count_absorbed(self) -> int:
        """Number of absorbed files present (cheap, no parse of bodies)."""
        if not self.source_dir.is_dir():
            return 0
        return sum(1 for _ in self.source_dir.glob(_ABSORBED_GLOB))

    # -- dedup state --------------------------------------------------------

    def _existing_fingerprints(self) -> set[str]:
        """Every fingerprint already in the target insights.jsonl + the cache.

        Reading both is what guarantees idempotency even if the sidecar cache is
        deleted: the records themselves carry the fingerprint, so a second sync
        re-derives the same dedup set from the file alone.
        """
        seen: set[str] = set()
        if self.insights_jsonl.exists():
            try:
                for line in self.insights_jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fp = rec.get("fingerprint") or rec.get("id")
                    if fp:
                        seen.add(fp)
            except OSError:
                pass
        seen |= self._load_cache()
        return seen

    def _load_cache(self) -> set[str]:
        if not self.cache_path.exists():
            return set()
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return set(data.get("fingerprints", []))
        except (json.JSONDecodeError, OSError):
            return set()

    def _save_cache(self, fingerprints: Iterable[str]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"fingerprints": sorted(set(fingerprints))}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.cache_path)

    # -- sync ---------------------------------------------------------------

    def sync(self, dry_run: bool = False) -> SyncResult:
        """Index every not-yet-seen absorbed entry into insights.jsonl.

        Idempotent: records already present (by fingerprint) are skipped, so a
        second sync with no new absorbed files writes nothing.
        """
        seen = self._existing_fingerprints()
        result = SyncResult(dry_run=dry_run)
        new_records: list[dict] = []
        new_fps: list[str] = []

        for entry in self.iter_absorbed():
            result.scanned += 1
            fp = entry.fingerprint()
            if fp in seen:
                result.skipped_duplicate += 1
                continue
            if not entry.body and not entry.description.strip():
                result.skipped_empty += 1
                continue
            seen.add(fp)  # guard against intra-run duplicate filenames
            new_records.append(to_insight_record(entry))
            new_fps.append(fp)

        if not dry_run and new_records:
            self._append_records(new_records)
            self._save_cache(self._load_cache() | set(new_fps))

        result.indexed = len(new_records)
        result.fingerprints_written = new_fps
        return result

    def _append_records(self, records: list[dict]) -> None:
        """Append JSONL records to insights.jsonl (creates parent if needed)."""
        self.insights_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(self.insights_jsonl, "a", encoding="utf-8") as fp:
            for rec in records:
                fp.write(json.dumps(rec) + "\n")
            fp.flush()
            os.fsync(fp.fileno())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index HAL-absorbed knowledge into GRIP's KONO insights feed.",
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="Scan HAL memory and append new insight records to insights.jsonl.",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the number of absorbed files in the HAL memory dir.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --sync: report what would be indexed; write nothing.",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=None,
        help="Override the HAL memory directory (default: ~/.hal/memory).",
    )
    parser.add_argument(
        "--insights", type=Path, default=None,
        help="Override the target insights.jsonl path.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    indexer = HalMemoryIndexer(
        source_dir=args.source_dir,
        insights_jsonl=args.insights,
    )

    if args.count:
        print(f"absorbed files in {indexer.source_dir}: {indexer.count_absorbed()}")
        return 0

    if args.sync:
        result = indexer.sync(dry_run=args.dry_run)
        verb = "would index" if result.dry_run else "indexed"
        print(
            f"scanned {result.scanned} absorbed file(s) in {indexer.source_dir}; "
            f"{verb} {result.indexed} new; "
            f"skipped {result.skipped_duplicate} already-present, "
            f"{result.skipped_empty} empty."
        )
        if not result.dry_run and result.indexed:
            print(
                f"appended to {indexer.insights_jsonl} — run "
                f"`python3 lib/kono_auto_capture.py` to route into KONO, "
                f"then `/recall` will find them."
            )
        return 0

    _build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
