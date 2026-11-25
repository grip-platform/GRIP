#!/usr/bin/env python3
"""
KONO Session Counter Store and operational content filtering.

Extracted from kono_substrate.py (H013 SOLID decomposition, Phase 1).
SessionCounterStore manages cross-process counter persistence for KONO session metrics.
is_operational_content() filters transient implementation artifacts from recall.

V>> 2026-05-21 — ISP decomposition (H-ISP-COUNTER-1):
8 property accessors collapsed into `__getattr__`/`__setattr__` dispatch
keyed on `_COUNTER_KEYS`. Public method count: 2 (`to_dict`, `reset`) —
well under the SOLID-ISP gate threshold (7). Caller surface preserved
byte-for-byte: `store.stored += 1`, `store.recalled = N`, etc all still
work via attribute interception. File-lock semantics unchanged
(LOCK_SH on read, LOCK_EX+atomic-rename on write).
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional, Dict

from file_lock import FileLock, LockTimeoutError

logger = logging.getLogger(__name__)

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
SESSION_COUNTER_FILE = CLAUDE_DIR / "state" / "integrations" / "kono-session-counters.json"

# Patterns for operational/test content that should not pollute the recall index.
# These are transient implementation artifacts, not knowledge. (Fix for #770.)
#
# V>> 2026-05-21 R0 fix: changed `^X` anchors → `\bX` word boundaries. The
# `^`-anchored variants were defeated by upstream wrappers — `auto_memory_saver.
# store_content_in_kono` prepends `"[checkpoint] "` to its argument before
# calling `is_operational_content`, so the start-of-string anchor never matched
# and INTEGRATION_TEST_MARKER_<pid> rows leaked into the production recall
# index. Word-boundary semantics correctly identify the marker as an embedded
# identifier regardless of prefix. False-positive risk on prose is bounded:
# these are all magic identifiers (numeric-suffixed or colon-terminated) that
# do not appear naturally in narrative content.
OPERATIONAL_CONTENT_PATTERNS = [
    re.compile(r"\bINTEGRATION_TEST_MARKER_\d+"),
    re.compile(r"\bCHECKPOINT_\d+\b"),
    re.compile(r"\bSESSION_CHECKPOINT:"),
    re.compile(r"\bTEST_PROBE_\d+\b"),
]


def is_operational_content(content: str) -> bool:
    """Check if content is an operational/test artifact that should not be recalled.

    Returns True for transient markers (integration test probes, checkpoint entries)
    that pollute semantic recall when stored as regular memories.
    """
    for pattern in OPERATIONAL_CONTENT_PATTERNS:
        if pattern.search(content):
            return True
    return False


class SessionCounterStore:
    """
    Persistent session counter storage with atomic writes and file locking.

    Manages cross-process counter persistence for KONO session metrics.
    Counters track stored/recalled/attested/promoted counts per session and
    persist to a JSON file so concurrent processes (hooks, CLI) see consistent values.

    Lifecycle:
        - Counters are loaded from file on construction.
        - reset() zeros all counters (new session baseline).
        - Each store/recall/attest/consolidate operation increments the relevant counter.
        - Counters persist automatically via __setattr__ interception (no manual save calls needed).
        - File uses flock() for safe concurrent access across processes.

    Attribute dispatch (V>> 2026-05-21):
        Counter names in `_COUNTER_KEYS` are exposed as ordinary attributes via
        `__getattr__`/`__setattr__`, backed by `_stored`/`_recalled`/... slots.
        Writes to counter names trigger `_save()`; writes to internal `_`-prefixed
        names route directly to `object.__setattr__` (no save, no recursion).
    """

    _COUNTER_KEYS = ('stored', 'recalled', 'attested', 'promoted')

    def __init__(self, path: Optional[Path] = None):
        object.__setattr__(self, '_path', path or SESSION_COUNTER_FILE)
        for key in self._COUNTER_KEYS:
            object.__setattr__(self, '_' + key, 0)
        self._load()

    def __getattr__(self, name: str) -> int:
        # __getattr__ only fires for missing attributes, so this is the
        # public-name accessor for counter keys. Internal `_X` slots are
        # set in __init__ and bypass this path.
        if name in self._COUNTER_KEYS:
            return object.__getattribute__(self, '_' + name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def __setattr__(self, name: str, value: int) -> None:
        if name in self._COUNTER_KEYS:
            object.__setattr__(self, '_' + name, value)
            self._save()
        else:
            object.__setattr__(self, name, value)

    def _load(self) -> None:
        """Load counters from file with file locking.

        The shared lock sits on the same sidecar that ``_save`` locks, so a
        save waits for a reader in progress; on Windows the replace in
        ``_save`` fails while a reader holds the file open (GRIP#8758).
        """
        try:
            if not self._path.exists():
                return
            with FileLock(self._path, shared=True, timeout=5, sidecar=True):
                with open(self._path, 'r') as f:
                    data = json.load(f)
            for key in self._COUNTER_KEYS:
                object.__setattr__(self, '_' + key, data.get(key, 0))
            logger.debug("Loaded session counters: %s", self.to_dict())
        except (json.JSONDecodeError, OSError, LockTimeoutError) as e:
            logger.debug("Could not load session counters: %s", e)

    def _save(self) -> None:
        """Persist counters via atomic write with file locking."""
        try:
            tmp = self._path.with_suffix('.tmp')
            data = json.dumps(self.to_dict())
            with FileLock(self._path, timeout=5, sidecar=True):
                with open(tmp, 'w') as f:
                    f.write(data)
                    f.flush()
                # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8758)
                tmp.replace(self._path)
            logger.debug("Saved session counters: %s", data)
        except (OSError, LockTimeoutError) as e:
            logger.debug("Could not save session counters: %s", e)

    def to_dict(self) -> Dict[str, int]:
        return {key: object.__getattribute__(self, '_' + key) for key in self._COUNTER_KEYS}

    def reset(self) -> None:
        """Reset all counters to zero (called on session init)."""
        for key in self._COUNTER_KEYS:
            object.__setattr__(self, '_' + key, 0)
        self._save()
