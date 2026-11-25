#!/usr/bin/env python3
"""E14: Session bootstrap oracle — SessionStart hook.

Hashes GRIP rules files + CLAUDE.md at session start. If all are unchanged
since last session, injects a cache-hit notice as additionalContext so Claude
does not re-read them via Read tool calls. Saves ~16K tokens/session when
rules are stable (~80% of sessions).

Hook position: SessionStart — appended after grip-commander-settings-healer.
Estimated savings: ~800K tokens/month (80 sessions × 80% unchanged × 16K).

Falsification: wrong if rules churn > 10 commits/30 days (check via
`git log --since="30 days ago" rules/`). Track via state/session-bootstrap-cache.json.
"""
from __future__ import annotations

import hashlib
import json
import time
from itertools import chain
from pathlib import Path

_CLAUDE = Path.home() / ".claude"
_CACHE_FILE = _CLAUDE / "state" / "session-bootstrap-cache.json"
_HIT_LOG = _CLAUDE / "state" / "session-bootstrap-hits.jsonl"
_RULE_GLOBS = ["rules/*.md", "CLAUDE.md", ".claude/CLAUDE.md"]


def _hash_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _collect_hashes() -> dict[str, str]:
    all_files = chain.from_iterable(
        sorted(_CLAUDE.glob(pat)) for pat in _RULE_GLOBS
    )
    return {str(p.relative_to(_CLAUDE)): _hash_file(p) for p in all_files}


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(data: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data, indent=2))


def _emit(msg: str) -> None:
    print(json.dumps({"additionalContext": msg}))


def _log_hit(hit: bool, sha: str, file_count: int) -> None:
    entry = {"ts": int(time.time()), "hit": hit, "sha": sha, "files": file_count}
    _HIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _HIT_LOG.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _changed_files(old: dict, new: dict) -> list[str]:
    modified = [k for k, v in new.items() if old.get(k) != v]
    removed = [f"{k} (removed)" for k in set(old) - set(new)]
    return modified + removed


def main() -> None:
    current = _collect_hashes()
    cache = _load_cache()
    old_hashes = cache.get("hashes", {})

    changed = _changed_files(old_hashes, current)

    combined = hashlib.sha256(
        json.dumps(current, sort_keys=True).encode()
    ).hexdigest()[:12]

    _save_cache({"hashes": current, "combined_sha": combined})
    _log_hit(hit=not changed, sha=combined, file_count=len(current))

    if not changed:
        file_count = len(current)
        _emit(
            f"[BOOTSTRAP-CACHE-HIT] {file_count} rule files unchanged "
            f"(sha={combined}). No re-read needed — session context is current."
        )
    else:
        _emit(
            f"[BOOTSTRAP-CACHE-MISS] {len(changed)} rule file(s) changed: "
            + ", ".join(changed[:5])
            + (f" (+{len(changed)-5} more)" if len(changed) > 5 else "")
            + ". Re-read recommended for changed files."
        )


if __name__ == "__main__":
    main()
