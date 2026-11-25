"""Stream-safe renderer for the "GRIP heals itself" activation narrative.

NO ANSI escape sequences (the `lib/activate_grip_signature.py:7` stream-safety
rule): only box-drawing + status glyphs, emitted as complete lines, so terminal
chunk-streaming can never split an escape sequence mid-byte. `render_json_receipt`
is the non-tty / CI path. Pure text — no side effects (the caller triggers the
all-green chime + writes the receipt).
"""
from __future__ import annotations

import json

_OK = "✔"  # ✔
_BAD = "✘"  # ✘
_BAR_FULL = "█"  # █
_BAR_EMPTY = "░"  # ░

CONNECTED = "connected"


def progress_bar(done: int, total: int, width: int = 20) -> str:
    """A plain text-emoji bar (no ANSI) — safe on every terminal and in logs."""
    if total <= 0:
        return _BAR_EMPTY * width
    done = max(0, min(done, total))
    filled = round(width * done / total)
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def render_heal(result, *, signature: str = "") -> str:
    """The activation narrative: per-server FAIL->OK / still-RED, then an honest
    all-green seal or a partial-failure card with the re-run hint."""
    total = len(result.servers)
    green = sum(1 for s in result.servers if s.status == CONNECTED)

    lines = ["", "  GRIP is healing itself —", ""]
    for s in result.servers:
        if s.status == CONNECTED:
            tag = "ONLINE" + (" (healed)" if s.healed else "")
            lines.append(f"  {_OK} {s.name} — {tag}")
        else:
            lines.append(f"  {_BAD} {s.name} — OFFLINE: {s.detail}")

    lines += ["", f"  {progress_bar(green, total)}  {green}/{total} live", ""]

    if result.all_green:
        seal = f"{signature}  " if signature else ""
        lines.append(f"  {seal}GRIP is whole. Every tool is live.")
    else:
        offline = ", ".join(s.name for s in result.servers if s.status != CONNECTED)
        lines.append(f"  {total - green} still need attention: {offline}")
        lines.append("  Re-run any time:  grip-doctor --fix")
    lines.append("")
    return "\n".join(lines)


def render_json_receipt(signed: dict) -> str:
    """Machine-readable path for non-tty / CI: the full signed receipt as JSON."""
    return json.dumps(signed, indent=2)
