"""Dojo tip — daily-tip card renderer (stateless, ANSI-only).

Two stores, one renderer (design D4):

* **curated** — ``data/daily-tips/tips.json`` (30 hand-curated tips). Picked by
  day-of-year modulo the corpus size, so it is deterministic and stateless.
* **learned** — derived live from the capability inventory (skills, commands,
  modes, agents) whose file mtime is *newer than the last surfaced scan*. This
  is the autonomous-learning leg: as GRIP develops new capabilities, the tip
  source grows with them, without touching the curated corpus.

The stores are never merged. ``pick_tip`` prefers a fresh learned tip and falls
back to the curated tip of the day. The "since last run" cursor is a single
timestamp in ``cache/dojo-tip-scan.json`` — a scan marker, NOT dojo progress
state (design D3 forbids ``state/dojo/``; this is a bounded cache cursor).

No curses, no Slack token, no network. ``render_card`` is the one-line Swiss
card; ``tip_slack_text`` is the compact mrkdwn for the #daily-chat poster.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.dojo_theme import GLYPHS, wrap

_SCAN_MARKER = "dojo-tip-scan.json"

# Kinds of capability we derive learned tips from, and their scan pattern.
_CAPABILITY_SCANS = {
    "skill": ("skills", "*/SKILL.md"),
    "command": ("commands", "*.md"),
    "mode": ("modes/definitions", "*.yaml"),
    "agent": ("agents", "*.md"),
}


def _claude_dir(claude_dir: Optional[str] = None) -> Path:
    return Path(claude_dir or os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


# ── curated store ──────────────────────────────────────────────────────────
def load_curated_tips(claude_dir: Optional[str] = None) -> dict:
    """Load ``data/daily-tips/tips.json`` — the curated corpus (30 tips)."""
    cd = _claude_dir(claude_dir)
    path = cd / "data" / "daily-tips" / "tips.json"
    if not path.exists():
        return {"tips": [], "total_tips": 0}
    return json.loads(path.read_text(encoding="utf-8"))


def pick_curated_tip(claude_dir: Optional[str] = None, *, day_offset: int = 0) -> Optional[dict]:
    """Return today's curated tip (date modulo corpus size). Stateless."""
    data = load_curated_tips(claude_dir)
    tips = data.get("tips", [])
    total = data.get("total_tips") or len(tips)
    if not tips:
        return None
    idx = (datetime.now(timezone.utc).timetuple().tm_yday + day_offset) % total
    return tips[idx]


# ── learned store (capability scan) ────────────────────────────────────────
def _frontmatter_field(path: Path, field: str) -> str:
    """Best-effort single-line frontmatter field (``description:``, etc.)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(rf"^{field}:\s*(.*)$", text, re.MULTILINE)
    if not m:
        return ""
    value = m.group(1).strip().strip("'\"")
    # A block scalar (``description: >``) leaves an empty first line — pull the
    # next indented line as the one-liner.
    if not value:
        for line in text[m.end():].splitlines():
            if line.strip():
                return line.strip()
    return value


def _first_trigger(path: Path) -> str:
    """Return the first ``triggers:`` list entry from a skill's frontmatter."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"^triggers:\s*\n((?:^\s*-\s*.+$\n?)+)", text, re.MULTILINE)
    if not m:
        return ""
    first = re.search(r"^\s*-\s*[\"']?(.+?)[\"']?\s*$", m.group(1), re.MULTILINE)
    return first.group(1).strip() if first else ""


def _try_it(kind: str, name: str, path: Path) -> str:
    if kind == "skill":
        return _first_trigger(path) or f"/{name}"
    if kind == "command":
        return f"/{name}"
    if kind == "mode":
        return f"/mode {name}"
    return f"delegate: {name}"  # agents are delegated, not slash-invoked


def scan_capabilities(claude_dir: Optional[str] = None) -> list[dict]:
    """Scan skills/commands/modes/agents, newest mtime first.

    Returns ``{kind, name, path, mtime, try_it, description}`` dicts. The
    filesystem scan is the same signal ``scripts/generate-tips.py`` uses; here
    it feeds *learned* tips rather than rewriting the curated corpus.
    """
    cd = _claude_dir(claude_dir)
    found: list[dict] = []
    for kind, (rel_dir, pattern) in _CAPABILITY_SCANS.items():
        base = cd / rel_dir
        if not base.exists():
            continue
        for path in base.glob(pattern):
            if path.name in ("CLAUDE.md", "AGENTS.md"):
                continue
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            name = path.name.replace(".md", "").replace(".yaml", "")
            if kind == "skill":
                name = path.parent.name  # skills/<name>/SKILL.md
            found.append({
                "kind": kind,
                "name": name,
                "path": str(path),
                "mtime": mtime,
                "try_it": _try_it(kind, name, path),
                "description": _frontmatter_field(path, "description"),
            })
    found.sort(key=lambda c: -c["mtime"])
    return found


def _learned_tip(cap: dict) -> dict:
    msg = cap["description"] or f"A {cap['kind']} GRIP just grew."
    return {
        "day": None,
        "topic": f"New {cap['kind']}: {cap['name']}",
        "message": msg,
        "try_it": cap["try_it"],
        "docs": cap["path"].split("/lib/")[-1] if "/lib/" not in cap["path"] else cap["path"],
        "source": "learned",
        "mtime": cap["mtime"],
        "kind": cap["kind"],
    }


def learned_tips(claude_dir: Optional[str] = None, *, since: Optional[float] = None) -> list[dict]:
    """Return learned tips for capabilities newer than ``since`` (mtime).

    ``since=None`` returns nothing — a caller must supply a baseline so a bare
    first run cannot surface the entire history as "new".
    """
    if since is None:
        return []
    return [
        _learned_tip(c) for c in scan_capabilities(claude_dir) if c["mtime"] > since
    ]


# ── scan marker (cache cursor, not dojo progress) ──────────────────────────
def _marker_path(claude_dir: Optional[str] = None) -> Path:
    return _claude_dir(claude_dir) / "cache" / _SCAN_MARKER


def load_scan_marker(claude_dir: Optional[str] = None) -> Optional[float]:
    """Return the last-surfaced mtime cursor, or None on first run."""
    path = _marker_path(claude_dir)
    if not path.exists():
        return None
    try:
        return float(json.loads(path.read_text(encoding="utf-8")).get("last_surfaced_utc", 0.0))
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def save_scan_marker(claude_dir: Optional[str] = None, ts: Optional[float] = None) -> None:
    """Persist the last-surfaced cursor atomically into cache/.

    Uses ``os.replace`` on a mode-0600 temp file (explicit permissions, so the
    cache cursor is never world-readable), not ``Path.replace`` which inherits
    the default umask. A failed write is silent — the marker is a cursor, not
    progress state; losing it only re-shows a learned tip once.
    """
    import time as _time
    path = _marker_path(claude_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps({"last_surfaced_utc": ts if ts is not None else _time.time()})
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ── unified pick + render ──────────────────────────────────────────────────
def pick_tip(claude_dir: Optional[str] = None, *, advance: bool = False) -> tuple[Optional[dict], str]:
    """Return ``(tip, source)`` — learned first, curated fallback.

    ``advance=True`` (the daily #daily-chat poster) moves the scan cursor past
    whatever it surfaced, so the same learned tip is not re-shown every day.
    ``advance=False`` (the session-start card) only reads.
    """
    import time
    cd = _claude_dir(claude_dir)
    now = time.time()
    marker = load_scan_marker(cd)
    since = marker if marker is not None else now  # first run: baseline to now

    learned = learned_tips(cd, since=since)
    if learned:
        tip = learned[0]
        if advance:
            save_scan_marker(cd, tip["mtime"])
        return tip, "learned"

    if advance:
        save_scan_marker(cd, now)
    return pick_curated_tip(cd), "curated"


def _stdout_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def render_card(tip: Optional[dict], *, source: str = "curated", total: Optional[int] = None,
                colour: Optional[bool] = None) -> str:
    """Render the ONE-line Swiss tip card (delight kill-line: max one line).

    Shape: ``[TIP 12/30] topic  › try: `command``` — accent label + command,
    dim topic. A learned tip uses the ``NEW`` label. Stateless, ANSI-only.
    """
    if not tip:
        return ""
    use_colour = colour if colour is not None else _stdout_tty()

    if source == "learned":
        label = "NEW"
    else:
        day = tip.get("day")
        label = f"TIP {day}/{total}" if day and total else "TIP"

    topic = tip.get("topic", "") or ""
    try_it = tip.get("try_it", "") or ""

    if use_colour:
        label_part = wrap(f"[{label}]", "accent", bold=True)
        topic_part = wrap(topic, "gray")
        try_part = wrap(f"`{try_it}`", "accent")
    else:
        label_part, topic_part, try_part = f"[{label}]", topic, f"`{try_it}`"

    line = f"{label_part} {topic_part}"
    if try_it:
        line += f"  {GLYPHS['try']} try: {try_part}"
    return line


def tip_slack_text(tip: Optional[dict], *, source: str = "curated") -> str:
    """Compact mrkdwn for the #daily-chat poster (learned vs curated header)."""
    if not tip:
        return ""
    if source == "learned":
        header = f":sparkles: *GRIP learned something new* — *{tip.get('topic', '')}*"
    else:
        header = f":bulb: *GRIP Tip of the Day* (Day {tip.get('day', '?')}) — *{tip.get('topic', '')}*"
    lines = [header]
    if tip.get("message"):
        lines.append(tip["message"])
    lines.append(f":point_right: Try it: `{tip.get('try_it', '')}`")
    return "\n".join(lines)


if __name__ == "__main__":
    tip, source = pick_tip(advance=False)
    total = (load_curated_tips().get("total_tips") or 0) if source == "curated" else None
    print(render_card(tip, source=source, total=total, colour=True))
