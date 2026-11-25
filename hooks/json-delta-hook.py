#!/usr/bin/env python3
"""E12: JSON Delta Encoder — PostToolUse hook for large JSON state file re-reads.

On first Read of a target JSON file: cache the content and line count.
On subsequent reads: emit only changed keys with before/after values
as additionalContext, reducing large state-file re-reads from ~80KB to ~1KB.

Target files: data/genome-shared.json, state/sprint-handoff/*.json, state/session/*.json
Estimated savings: ~200K tokens/month on state file re-reads.

Wired at: PostToolUse[Read] — appended after jsonl-watermark-hook.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path.home() / ".claude"
_CACHE_DIR = _CLAUDE / "state" / "json-snapshot-cache"
_TARGET_PATTERNS = [
    "data/genome-shared.json",
    "state/sprint-handoff/",
    "state/session/",
    "state/ultraread/",
]
# Skip delta for small files (full read is fine)
_MIN_SIZE_BYTES = 4_096
_TELEMETRY_FILE = _CACHE_DIR / "telemetry.jsonl"


def _is_target(file_path: str) -> bool:
    p = Path(file_path)
    # Must be within ~/.claude
    try:
        p.relative_to(_CLAUDE)
    except ValueError:
        return False
    rel = str(p.relative_to(_CLAUDE))
    return any(rel.startswith(pat) or rel == pat for pat in _TARGET_PATTERNS)


def _cache_key(file_path: str) -> str:
    return file_path.replace("/", "__").replace(".", "_")


def _load_cache() -> dict:
    idx = _CACHE_DIR / "index.json"
    if idx.exists():
        try:
            return json.loads(idx.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / "index.json").write_text(json.dumps(cache, indent=2))


def _flat_keys(obj: object, prefix: str = "") -> dict:
    """Flatten nested JSON to dot-separated key→value pairs (depth ≤ 3)."""
    result: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)) and prefix.count(".") < 2:
                result.update(_flat_keys(v, full))
            else:
                result[full] = v
    elif isinstance(obj, list):
        result[prefix] = f"[{len(obj)} items]"
    else:
        result[prefix] = obj
    return result


def _compute_delta(old_flat: dict, new_flat: dict) -> list[str]:
    lines: list[str] = []
    all_keys = set(old_flat) | set(new_flat)
    for k in sorted(all_keys):
        old_v = old_flat.get(k, "__MISSING__")
        new_v = new_flat.get(k, "__MISSING__")
        if old_v == "__MISSING__":
            lines.append(f"  + {k}: {json.dumps(new_v)[:120]}")
        elif new_v == "__MISSING__":
            lines.append(f"  - {k}: {json.dumps(old_v)[:120]}")
        elif old_v != new_v:
            lines.append(
                f"  ~ {k}: {json.dumps(old_v)[:60]} → {json.dumps(new_v)[:60]}"
            )
    return lines


def _emit(msg: str) -> None:
    print(json.dumps({"additionalContext": msg}))


def _log_delta_telemetry(fname: str, raw_bytes: int, delta_bytes: int) -> None:
    entry = {"ts": int(time.time()), "file": fname, "raw_bytes": raw_bytes, "delta_bytes": delta_bytes}
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _TELEMETRY_FILE.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _on_first_read(fname: str, obj: object, size: int, cache: dict, key: str) -> None:
    flat = _flat_keys(obj)
    cache[key] = {"flat": flat, "size": size}
    _save_cache(cache)
    _emit(f"[JSON-DELTA] {fname}: {size:,} bytes cached ({len(flat)} paths indexed).")


def _on_delta_read(fname: str, obj: object, size: int, cache: dict, key: str) -> None:
    old_flat = cache[key].get("flat", {})
    new_flat = _flat_keys(obj)
    delta_lines = _compute_delta(old_flat, new_flat)
    cache[key] = {"flat": new_flat, "size": size}
    _save_cache(cache)
    delta_bytes = len("\n".join(delta_lines).encode())
    _log_delta_telemetry(fname, size, delta_bytes)
    if not delta_lines:
        _emit(
            f"[JSON-DELTA] {fname}: unchanged ({len(new_flat)} paths, "
            f"{size:,} bytes). Full content omitted."
        )
        return
    body = "\n".join(delta_lines[:50])
    tail = f"\n  ... {len(delta_lines) - 50} more" if len(delta_lines) > 50 else ""
    _emit(
        f"[JSON-DELTA] {fname}: {len(delta_lines)} changes "
        f"(+added -removed ~changed). Full {size:,}-byte file omitted:\n{body}{tail}"
    )


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if hook_input.get("tool_name") != "Read":
        sys.exit(0)

    file_path = hook_input.get("tool_input", {}).get("file_path", "")
    if not file_path or not _is_target(file_path):
        sys.exit(0)

    try:
        size = os.path.getsize(file_path)
    except OSError:
        sys.exit(0)

    if size < _MIN_SIZE_BYTES:
        sys.exit(0)

    try:
        current_obj = json.loads(Path(file_path).read_text(errors="replace"))
    except Exception:
        sys.exit(0)

    cache = _load_cache()
    key = _cache_key(file_path)
    fname = Path(file_path).name

    if key not in cache:
        _on_first_read(fname, current_obj, size, cache, key)
    else:
        _on_delta_read(fname, current_obj, size, cache, key)


if __name__ == "__main__":
    main()
