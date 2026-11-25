#!/usr/bin/env python3
"""
E11: JSONL Tail-Watermarking Hook — PostToolUse Read
Returns only new lines since last read for GRIP append-only JSONL files.
Savings: ~400-600K tokens/month (95% reduction on subsequent reads).

settings.json PostToolUse wiring:
  {"matcher": "Read", "hooks": [{"type": "command",
   "command": "python3 $HOME/.claude/hooks/jsonl-watermark-hook.py", "timeout": 3000}]}
"""
import json, os, sys
from pathlib import Path

WATERMARK_FILE = Path.home() / ".claude/state/jsonl-watermarks.json"


def _is_targeted(path: str) -> bool:
    return any(d in path for d in (".claude/learning", ".claude/data")) and path.endswith(".jsonl")


def _load_wm() -> dict:
    try:
        return json.loads(WATERMARK_FILE.read_text()) if WATERMARK_FILE.exists() else {}
    except Exception:
        return {}


def _save_wm(wm: dict) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK_FILE.write_text(json.dumps(wm, indent=2))


def _read_delta(path: str, offset: int) -> tuple[list[str], int]:
    size = os.path.getsize(path)
    if size <= offset:
        return [], size
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read().decode("utf-8", errors="replace").strip()
    return [ln for ln in raw.split("\n") if ln.strip()], size


def _emit(ctx: str) -> None:
    print(json.dumps({"additionalContext": ctx}))


def main() -> None:
    try:
        inp = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if inp.get("tool_name") != "Read":
        sys.exit(0)
    path = inp.get("tool_input", {}).get("file_path", "")
    if not _is_targeted(path) or not (inp.get("tool_response") or {}).get("output"):
        sys.exit(0)

    wm = _load_wm()
    offset = wm.get(path, 0)
    known = wm.get(f"{path}:total", 0)
    if os.path.getsize(path) < offset:  # rotation
        offset, known = 0, 0

    new_lines, new_size = _read_delta(path, offset)
    wm[path] = new_size
    wm[f"{path}:total"] = known + len(new_lines)
    _save_wm(wm)

    name = Path(path).name
    if not new_lines and known > 0:
        _emit(f"[JSONL-WATERMARK] {name}: {known} entries, no new content since last read.")
    elif new_lines and known > 0:
        _emit(f"[JSONL-WATERMARK] {name}: {known} prior entries omitted. {len(new_lines)} new:\n"
              + "\n".join(new_lines))
    # first read: no injection — full content passes through


if __name__ == "__main__":
    main()
