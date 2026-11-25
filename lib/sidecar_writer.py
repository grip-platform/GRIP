"""W10 — per-target sidecar writer for cross-session reconciliation.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W10. When the propagator
writes to a canonical file (e.g. ``rules/operational-context.md``), this
module ALSO appends a one-entry record to a sidecar file named
``<target>.md.insights``. Sidecars are git-tracked (``merge=union``) so
they survive across machines via ``git pull``, giving session B on a
different machine the insights captured by session A.

Key design properties:
- Atomic append under fcntl.flock — N concurrent writers can't interleave.
- Dedup by fingerprint — same fp written twice produces one entry, second
  is silently absorbed.
- Append-only — sidecars grow monotonically; pruning is a separate
  reconciliation pass.
- Plain stdlib + simple line-based YAML-like format. The reader hook
  doesn't need a YAML parser.

Sidecar format (one block per entry, separated by ``---``)::

    - fingerprint: c10d3bcadb02
      hook: "AGORA imports 4 syms from hal_council_dispatch"
      classification: design_pattern
      session_id: 61bf35c6-...
      captured_at: 2026-05-05T01:30:34Z
      status: propagated

The line-based parser tolerates quoted/unquoted hooks, ignores extra
whitespace, and skips malformed blocks.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl


SIDECAR_SUFFIX = ".insights"


@dataclass(frozen=True)
class SidecarEntry:
    fingerprint: str
    hook: str
    classification: str
    session_id: str
    captured_at: str  # ISO-8601 UTC
    status: str = "propagated"

    def to_yaml_block(self) -> str:
        """Serialise as a single YAML-like list-of-mappings block."""
        # Escape any embedded double quotes in the hook.
        safe_hook = self.hook.replace('"', '\\"')
        return (
            f"- fingerprint: {self.fingerprint}\n"
            f"  hook: \"{safe_hook}\"\n"
            f"  classification: {self.classification}\n"
            f"  session_id: {self.session_id}\n"
            f"  captured_at: {self.captured_at}\n"
            f"  status: {self.status}\n"
        )


def sidecar_path(target: Path | str) -> Path:
    """Return the sidecar path for a given canonical target."""
    return Path(str(target) + SIDECAR_SUFFIX)


# ---------------------------------------------------------------------------
# Parser (forgiving line-based YAML-like)
# ---------------------------------------------------------------------------


_KEY_RE = re.compile(r"^\s*([a-z_]+):\s*(.*)$")


def parse_sidecar(path: Path | str) -> list[SidecarEntry]:
    """Parse a sidecar file into SidecarEntry records. Empty/missing -> []."""
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8")
    return list(_parse_text(raw))


def _parse_text(text: str) -> Iterable[SidecarEntry]:
    """Yield SidecarEntry per parseable block in ``text``."""
    current: dict[str, str] = {}
    in_block = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if in_block:
                entry = _emit(current)
                if entry:
                    yield entry
            current = {}
            in_block = True
            line = stripped[2:]
            stripped = line
        if not in_block:
            continue
        match = _KEY_RE.match(stripped)
        if match:
            current[match.group(1)] = _unquote(match.group(2))
    if in_block:
        entry = _emit(current)
        if entry:
            yield entry


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _emit(record: dict[str, str]) -> SidecarEntry | None:
    """Build a SidecarEntry from a parsed record dict, if all required keys present."""
    required = {"fingerprint", "hook", "classification", "session_id", "captured_at"}
    if not required.issubset(record):
        return None
    return SidecarEntry(
        fingerprint=record["fingerprint"],
        hook=record["hook"],
        classification=record["classification"],
        session_id=record["session_id"],
        captured_at=record["captured_at"],
        status=record.get("status", "propagated"),
    )


# ---------------------------------------------------------------------------
# Writer (atomic, dedup-aware)
# ---------------------------------------------------------------------------


def write_sidecar_entry(target: Path | str, entry: SidecarEntry) -> bool:
    """Append ``entry`` to the sidecar for ``target``. Idempotent on fingerprint.

    Returns True if entry was actually written, False if dedup'd out.

    Atomic via fcntl.LOCK_EX. Concurrent writers serialise; line-based
    block format guarantees no interleaving at character level.
    """
    sidecar = sidecar_path(target)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            # Read current contents to check for dedup.
            fh.seek(0)
            existing = list(_parse_text(fh.read()))
            if any(e.fingerprint == entry.fingerprint for e in existing):
                return False
            fh.seek(0, 2)  # to end
            fh.write(entry.to_yaml_block())
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return True


def fresh_entries(
    target: Path | str, *, max_age_seconds: int = 30 * 86400
) -> list[SidecarEntry]:
    """Return entries newer than ``max_age_seconds``. Default: 30 days."""
    entries = parse_sidecar(sidecar_path(target))
    cutoff = time.time() - max_age_seconds
    out = []
    for e in entries:
        try:
            ts = _iso_to_unix(e.captured_at)
        except ValueError:
            continue
        if ts >= cutoff:
            out.append(e)
    return out


def _iso_to_unix(iso: str) -> float:
    """Parse a permissive ISO-8601 UTC timestamp into unix seconds."""
    iso = iso.strip()
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    import datetime as _dt
    return _dt.datetime.fromisoformat(iso).timestamp()


def now_iso() -> str:
    """Return current UTC time as ISO-8601 with Z suffix."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
