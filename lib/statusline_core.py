#!/usr/bin/env python3
"""GRIP statusline core — harness-agnostic status-state reader.

Single source of truth for the status-line state shared by:

  * ``statusline-command.sh``  (Claude Code) — reference, still shell-native
  * ``grip-status``            (any harness terminal) — human + ``--json``
  * the OpenCode TUI plugin    (reads ``--json``, renders natively)

It reads the same state files the CC statusline reads — the active-LLM
marker, active modes, wave position, git — plus harness-supplied values
(selected model id, cost, context %) and emits normalised JSON so every
harness renders the SAME truth.

The crown jewel is :func:`resolve_model` — marker-first, fail-closed. It is
the exact semantics of ``statusline-command.sh`` ``row2_model()``, so the
shell and every other harness cannot disagree about which model is
*actually* executing (the harness may have selected DeepSeek v4 Pro while
HAL routed to Astra, and the status line must say so).

Colours are emitted as *semantic names* (``green``, ``bold_red``, …), never
ANSI escapes, so each renderer maps them to its native palette (terminal
ANSI, OpenCode theme tokens, …).

Only stdlib. Run from any CWD: the bootstrap below makes the GRIP repo root
importable so ``from lib import active_llm`` resolves via the namespace
package ``lib`` (there is deliberately no ``lib/__init__.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Bootstrap: make the GRIP repo root importable regardless of CWD / invocation.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lib import active_llm  # noqa: E402
from lib.state_paths import wave_position_path  # noqa: E402

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# ---------------------------------------------------------------------------
# Semantic colour names — each renderer maps these to its own palette.
# ---------------------------------------------------------------------------
TIER_COLOUR: dict[str, str] = {
    "subscription": "magenta",
    "frontier": "bold_red",
    "premium": "red",
    "mid": "yellow",
    "cheap": "green",
    "local": "blue",
    "free": "cyan",
}
TIER_COLOUR_DEFAULT = "cyan"

# Family colours for the harness fallback (mirrors the shell's stdin mapping).
_FAMILY_COLOUR: dict[str, str] = {
    "Fable": "bold_magenta",
    "Mythos": "bold_magenta",
    "Opus": "magenta",
    "Sonnet": "cyan",
    "Haiku": "dim",
    "Claude": "cyan",
}

_GLOBAL_TTL_CAP = 21600  # 6h — the global marker is a cold-start default only


def colour_for_tier(tier: str) -> str:
    """Semantic colour for a HAL model tier (unknown → cyan)."""
    return TIER_COLOUR.get(tier, TIER_COLOUR_DEFAULT)


def fmt_window(n: int) -> str:
    """Render a context window in plain units: 1M / 256K / 64K / 8K."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n // 1_000_000}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def family_display(model_id: str, default: str = "Claude") -> tuple[str, str]:
    """Map a harness model id to a plain display name + semantic colour.

    Mirrors ``statusline-command.sh`` ``row2_model()``'s stdin fallback: the
    Claude-family guard runs first (a proxy id merely *containing* "sonnet"/
    "fable" is never asserted as that family), non-Claude ids render verbatim.
    """
    if not model_id:
        return default, _FAMILY_COLOUR.get(default, "cyan")
    low = model_id.lower()
    if "claude" in low or "anthropic/" in low:
        for key in ("fable", "mythos", "opus", "sonnet", "haiku"):
            if key in low:
                display = key.capitalize()
                return display, _FAMILY_COLOUR[display]
        return "Claude", "cyan"
    display = model_id[:16] + ("…" if len(model_id) > 16 else "")
    return display, "cyan"


def _marker_fresh(payload: dict, source_is_global: bool, now: Optional[int] = None) -> bool:
    """Fail-closed freshness — the shell's ``row2_model`` TTL/ts discipline.

    A malformed or future timestamp, or a malformed TTL, invalidates the whole
    marker (→ stale → harness fallback), never clamps-to-now.  The global
    marker's TTL is capped at 6h so a session-lifetime pin leaked into it
    cannot lie indefinitely.
    """
    now = int(time.time()) if now is None else now
    ts = payload.get("ts", 0)
    ttl = payload.get("ttl_seconds", 0)
    if not isinstance(ttl, int) or ttl < 0:
        ttl = 0
    if source_is_global and ttl > _GLOBAL_TTL_CAP:
        ttl = _GLOBAL_TTL_CAP
    if not isinstance(ts, int) or ts == 0 or ts > now + 60:
        return False
    return ttl > 0 and (now - ts) < ttl


def _render_solo(payload: dict) -> Optional[dict[str, Any]]:
    """Render a solo / solo-once marker: ``Grok 3`` / ``Opus 1M``."""
    display = payload.get("display") or ""
    if not display:
        return None
    tier = payload.get("tier", "unknown")
    colour = colour_for_tier(tier)
    # Frontier generation keeps ONE colour regardless of source (AGORA R2 c2):
    # a marker-supplied Fable must not change colour by data path.
    if display.startswith(("Fable", "Mythos")):
        colour = "bold_magenta"
    ctx = int(payload.get("context_window", 0) or 0)
    window = fmt_window(ctx) if ctx > 0 else ""
    return {
        "display": display,
        "window": window,
        "tier": tier,
        "colour": colour,
        "reason": payload.get("reason") or "",
    }


def _render_council(payload: dict, mode: str) -> Optional[dict[str, Any]]:
    """Render a council / MoE / fallback marker: up to 3 members, then a tail."""
    count = int(payload.get("count", 0) or 0)
    if count < 1:
        return None
    members = payload.get("members", []) or []
    names = [m.get("display", "?") for m in members]
    prefix = {"council": "Council", "moe": "MoE", "fallback": "Fallback"}.get(mode, "Council")
    if count <= 3:
        label = f"{prefix}: " + " · ".join(names)
    else:
        label = f"{prefix} ({count}): " + " · ".join(names[:2]) + f" +{count - 2}"
    return {
        "display": label,
        "window": "",
        "tier": "unknown",
        "colour": "cyan",
        "reason": payload.get("reason") or "",
    }


def resolve_model(
    harness_model_id: str = "",
    harness_window: Optional[int] = None,
) -> dict[str, Any]:
    """Resolve the *actually* executing model — marker-first, fail-closed.

    Returns ``{display, window, tier, colour, mode, source, reason}`` where
    ``source`` is ``"marker"`` when a fresh HAL marker wins, else ``"harness"``
    (the harness-selected model, the fallback truth). ``reason`` is the
    short human "why" from the marker (``""`` when none / harness fallback).
    """
    # Single resolution — reuse the writer's own session-key logic so reader
    # and writer can never disagree (GRIP #2931: the shell once reimplemented
    # it and silently fell back to the clobbered global marker).
    sf: Path = active_llm._state_file(None)  # noqa: SLF001 — same-repo reuse
    payload = active_llm._read_state(sf)  # noqa: SLF001
    source_is_global = sf == active_llm.GLOBAL_STATE_FILE

    if payload and _marker_fresh(payload, source_is_global):
        mode = payload.get("mode", "solo") or "solo"
        if mode in ("council", "moe", "fallback"):
            rendered = _render_council(payload, mode)
        else:  # solo, solo-once, or unknown → solo
            rendered = _render_solo(payload)
        if rendered:
            return {**rendered, "mode": mode, "source": "marker"}

    # Harness fallback — the selected model, honestly labelled, never a family
    # we cannot verify.
    display, colour = family_display(harness_model_id)
    window = fmt_window(harness_window or 0)
    return {
        "display": display,
        "window": window,
        "tier": "unknown",
        "colour": colour,
        "mode": "harness",
        "source": "harness",
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Git state
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str) -> str:
    try:
        res = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2
        )
        return res.stdout.strip()
    except Exception:
        return ""


def read_git(cwd: str) -> dict[str, Any]:
    """Branch, ahead/behind, dirty flag, and a plain-English hint."""
    if not _git(["rev-parse", "--git-dir"], cwd):
        return {"in_repo": False, "branch": "", "ahead": 0, "behind": 0, "dirty": False, "hint": ""}
    branch = _git(["symbolic-ref", "--short", "HEAD"], cwd) or "detached"
    if len(branch) > 18:
        branch = branch[:18] + "…"
    ahead = behind = 0
    upstream = _git(["rev-parse", "--abbrev-ref", "@{u}"], cwd)
    if upstream and not upstream.startswith("fatal"):
        try:
            ahead = int(_git(["rev-list", "--count", "@{u}..HEAD"], cwd) or 0)
            behind = int(_git(["rev-list", "--count", "HEAD..@{u}"], cwd) or 0)
        except ValueError:
            ahead = behind = 0
    dirty = bool(_git(["status", "--porcelain"], cwd))
    hint = "uncommitted" if dirty else ("ready to push" if ahead > 0 else "")
    return {
        "in_repo": True,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
        "hint": hint,
    }


# ---------------------------------------------------------------------------
# Modes + wave
# ---------------------------------------------------------------------------

def read_modes(claude_dir: Path = CLAUDE_DIR) -> list[str]:
    """Active modes (``.active-modes`` multi-line, else ``.active-mode`` single)."""
    modes: list[str] = []
    multi = claude_dir / ".active-modes"
    single = claude_dir / ".active-mode"
    try:
        if multi.exists():
            modes = [ln.strip() for ln in multi.read_text().splitlines() if ln.strip()]
        elif single.exists():
            first = single.read_text().splitlines()[0:1]
            if first and first[0].strip():
                modes = [first[0].strip()]
    except OSError:
        return []
    return modes


def mode_text(modes: list[str]) -> str:
    """``code`` / ``code · analysis`` / ``research +4 more``."""
    n = len(modes)
    if n == 0:
        return ""
    if n <= 3:
        return " · ".join(modes)
    return f"{modes[0]} +{n - 1} more"


def read_wave(claude_dir: Path = CLAUDE_DIR) -> str:
    """Active wave label, hidden when stale (>24h) or phase unknown/complete."""
    wave_file = wave_position_path(claude_dir)
    if not wave_file.exists():
        return ""
    try:
        age = int(time.time() - wave_file.stat().st_mtime)
    except OSError:
        return ""
    if age > 86400:
        return ""
    try:
        data = json.loads(wave_file.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    pos = data.get("position") or ""
    phase = data.get("phase") or ""
    if not pos or phase not in ("ascent", "descent", "rest"):
        return ""
    cycle = pos.lstrip("W").split("-", 1)[0]
    label = {"ascent": "building", "descent": "winding down", "rest": "resting"}[phase]
    return f"cycle {cycle} {label}"


# ---------------------------------------------------------------------------
# Collection + render
# ---------------------------------------------------------------------------

def collect(harness: Optional[dict[str, Any]] = None, cwd: Optional[str] = None) -> dict[str, Any]:
    """Gather every segment into one normalised dict.

    ``harness`` supplies harness-native values the state files cannot::

        {"model_id", "window", "cost_usd", "duration_ms",
         "context_pct", "context_used", "context_window", "session_id"}
    """
    harness = harness or {}
    cwd = cwd or os.getcwd()
    model = resolve_model(
        harness_model_id=harness.get("model_id", ""),
        harness_window=harness.get("window"),
    )
    modes = read_modes()
    return {
        "model": model,
        "git": read_git(cwd),
        "modes": modes,
        "mode_text": mode_text(modes),
        "wave": read_wave(),
        "session": {"id": harness.get("session_id", "") or ""},
        "cost": {
            "usd": harness.get("cost_usd"),
            "duration_ms": harness.get("duration_ms"),
        },
        "context": {
            "pct": harness.get("context_pct"),
            "used": harness.get("context_used"),
            "window": harness.get("context_window"),
        },
    }


# ANSI map for the human renderer only (the JSON path never emits escapes).
_ANSI: dict[str, str] = {
    "green": "\033[1;32m",
    "cyan": "\033[36m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "blue": "\033[1;34m",
    "magenta": "\033[35m",
    "bold_magenta": "\033[1;35m",
    "dim": "\033[2m",
    "bold_red": "\033[1;31m",
}
_RESET = "\033[0m"


def _c(colour: str, text: str) -> str:
    return f"{_ANSI.get(colour, '')}{text}{_RESET}"


def render_human(d: dict[str, Any]) -> str:
    """Two-row, glance-able human rendering (mirrors the shell's layout)."""
    model = d["model"]
    git = d["git"]

    row1: list[str] = [_c("green", "GRIP")]
    if git.get("branch"):
        row1.append(_c("blue", git["branch"]))
    if git.get("ahead"):
        row1.append(_c("yellow", f"↑{git['ahead']}"))
    if git.get("behind"):
        row1.append(_c("yellow", f"↓{git['behind']}"))
    if git.get("hint"):
        row1.append(_c("yellow" if git["hint"] == "uncommitted" else "cyan", git["hint"]))

    row2: list[str] = []
    cost = d.get("cost", {})
    if cost.get("usd") is not None:
        dur_min = int(cost.get("duration_ms") or 0) // 60000
        row2.append(_c("green", f"${cost['usd']:.2f}") + _c("dim", f" {dur_min}m"))
    ctx = d.get("context", {})
    if ctx.get("pct") is not None:
        pct = int(ctx["pct"])
        fill = min(pct // 10, 10)
        bar = "█" * fill + "░" * (10 - fill)
        colour = "bold_red" if pct >= 80 else "red" if pct >= 70 else "yellow" if pct >= 50 else "green"
        row2.append(_c(colour, f"{bar} {pct}% full"))
    model_txt = _c(model["colour"], model["display"])
    if model.get("window"):
        model_txt += _c("dim", f" {model['window']}")
    if model.get("reason"):
        model_txt += _c("dim", f" · {model['reason']}")
    row2.append(model_txt)
    if d.get("mode_text"):
        row2.append(_c("cyan", d["mode_text"]))
    if d.get("wave"):
        row2.append(_c("magenta", d["wave"]))

    return " · ".join(row1) + "\n" + " · ".join(row2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grip-status", description=__doc__)
    p.add_argument("--json", action="store_true", help="Emit normalised JSON (machine/plugin)")
    p.add_argument("--model", default="", help="Harness-selected model id (fallback)")
    p.add_argument("--window", type=int, default=None, help="Harness-selected model context window")
    p.add_argument("--cost-usd", type=float, default=None)
    p.add_argument("--duration-ms", type=int, default=None)
    p.add_argument("--context-pct", type=int, default=None)
    p.add_argument("--context-used", type=int, default=None)
    p.add_argument("--context-window", type=int, default=None)
    p.add_argument("--session-id", default="")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    harness = {
        "model_id": args.model,
        "window": args.window,
        "cost_usd": args.cost_usd,
        "duration_ms": args.duration_ms,
        "context_pct": args.context_pct,
        "context_used": args.context_used,
        "context_window": args.context_window,
        "session_id": args.session_id,
    }
    d = collect(harness)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        print(render_human(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
