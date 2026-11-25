#!/usr/bin/env python3
"""
rtk_sprint_dividend.py — RTK token-savings evidence for RSI/sprint loops.

Records a baseline RTK snapshot at sprint start, captures per-wave deltas,
then renders a "Sprint Dividend" report with real token savings + cost offset.

Usage:
    python3 lib/rtk_sprint_dividend.py snapshot [--sprint-id ID]
    python3 lib/rtk_sprint_dividend.py wave N   [--sprint-id ID]
    python3 lib/rtk_sprint_dividend.py report   [--sprint-id ID]
    python3 lib/rtk_sprint_dividend.py status   [--sprint-id ID]
    python3 lib/rtk_sprint_dividend.py milestone          # lifetime achievement check
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATE_DIR = _ROOT / "state" / "rtk"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

# Sonnet 4.6 input cost — $3 / million tokens
_COST_PER_MTOK = 3.0

# Milestone thresholds: (tokens, title)
_MILESTONES = [
    (5_000_000, "compression architect"),
    (1_000_000, "compression master"),
    (500_000, "compression journeyman"),
    (100_000, "compression apprentice"),
]


# ── RTK helpers ───────────────────────────────────────────────────────────────

def _rtk_gain() -> dict:
    try:
        r = subprocess.run(
            ["rtk", "gain", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def _summary(snap: dict) -> dict:
    return snap.get("summary", {})


def _compute_delta(baseline: dict, current: dict) -> dict:
    b, c = _summary(baseline), _summary(current)
    d_saved   = c.get("total_saved", 0)   - b.get("total_saved", 0)
    d_input   = c.get("total_input", 0)   - b.get("total_input", 0)
    d_cmds    = c.get("total_commands", 0) - b.get("total_commands", 0)
    would_be  = d_input + d_saved
    avg_pct   = (d_saved / would_be * 100) if would_be > 0 else 0.0
    cost_usd  = d_saved * _COST_PER_MTOK / 1_000_000
    return {
        "commands": d_cmds,
        "tokens_saved": d_saved,
        "avg_savings_pct": round(avg_pct, 1),
        "cost_saved_usd": round(cost_usd, 5),
    }


# ── Formatting ────────────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 20) -> str:
    filled = round(max(0, min(100, pct)) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _usd(v: float) -> str:
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.2f}"


# ── Sprint state ──────────────────────────────────────────────────────────────

def _active_sprint_id() -> str:
    lock = _ROOT / "state" / "rsi" / "rsi-active.lock"
    try:
        data = json.loads(lock.read_text())
        sid = data.get("session_id", "")
        if sid:
            return sid[:16]
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _sprint_path(sprint_id: str) -> Path:
    safe = sprint_id.replace("/", "-").replace(":", "-")
    return _STATE_DIR / f"sprint_{safe}.json"


def _load(sprint_id: str) -> dict:
    p = _sprint_path(sprint_id)
    return json.loads(p.read_text()) if p.exists() else {}


def _dump(sprint_id: str, data: dict) -> None:
    _sprint_path(sprint_id).write_text(json.dumps(data, indent=2))


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_snapshot(sprint_id: str) -> None:
    snap = _rtk_gain()
    if not snap:
        print("⚠  RTK unavailable — no baseline recorded.", file=sys.stderr)
        return

    data = {
        "sprint_id": sprint_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "baseline": snap,
        "waves": [],
        "completed_at": None,
    }
    _dump(sprint_id, data)

    s = _summary(snap)
    saved = s.get("total_saved", 0)
    cmds  = s.get("total_commands", 0)
    print(
        f"★ RTK baseline  ·  {_tok(saved)} saved lifetime  "
        f"·  {cmds:,} commands  ·  sprint {sprint_id[:8]}"
    )


def cmd_wave(wave_num: int, sprint_id: str) -> None:
    sprint = _load(sprint_id)
    if not sprint:
        print(f"⚠  No baseline for sprint {sprint_id[:8]} — run snapshot first.", file=sys.stderr)
        return

    snap = _rtk_gain()
    if not snap:
        print("⚠  RTK unavailable.", file=sys.stderr)
        return

    prev  = sprint["waves"][-1]["snapshot"] if sprint["waves"] else sprint["baseline"]
    delta = _compute_delta(prev, snap)

    sprint["waves"].append({
        "wave": wave_num,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snap,
        "delta": delta,
    })
    _dump(sprint_id, sprint)

    saved = delta["tokens_saved"]
    pct   = delta["avg_savings_pct"]
    usd   = delta["cost_saved_usd"]
    sign  = "+" if saved >= 0 else ""
    print(
        f"★ RTK wave {wave_num}  ·  {sign}{_tok(saved)} tokens  "
        f"·  {pct:.0f}%  ·  {_bar(pct, 16)}  ·  {_usd(usd)} offset"
    )


def cmd_report(sprint_id: str) -> None:
    sprint = _load(sprint_id)
    if not sprint:
        print(f"⚠  No sprint data for {sprint_id[:8]}.", file=sys.stderr)
        return

    current = _rtk_gain()
    if not current:
        print("⚠  RTK unavailable.", file=sys.stderr)
        return

    delta   = _compute_delta(sprint["baseline"], current)
    waves   = sprint["waves"]
    best    = max(waves, key=lambda w: w["delta"]["tokens_saved"]) if waves else None

    # Sprint figures
    saved   = delta["tokens_saved"]
    pct     = delta["avg_savings_pct"]
    usd     = delta["cost_saved_usd"]
    cmds    = delta["commands"]

    # Lifetime figures
    cur_s   = _summary(current)
    lt_saved = cur_s.get("total_saved", 0)
    lt_usd   = lt_saved * _COST_PER_MTOK / 1_000_000

    # Monthly projection: assume 8h/day, 20 working days
    # Use started_at → now to estimate hours elapsed
    try:
        started = datetime.fromisoformat(sprint["started_at"])
        elapsed_h = max(0.5, (datetime.now(timezone.utc) - started).total_seconds() / 3600)
        monthly_usd = (usd / elapsed_h) * 8 * 20
    except Exception:
        monthly_usd = 0.0

    W = 56
    line = "─" * W

    def row(label: str, value: str) -> str:
        lbl = f"  {label:<16}"
        return f"│{lbl}│  {value:<{W - 21}}│"

    print()
    print(f"┌{line}┐")
    print(f"│{'  RTK Sprint Dividend'.ljust(W)}│")
    print(f"├{line}┤")
    print(row("sprint", sprint_id[:18]))
    print(row("waves", f"{len(waves)}" if waves else "—  (no per-wave snapshots)"))
    print(row("commands", f"{cmds:,}  (this sprint)"))
    print(f"├{line}┤")
    print(row("sprint saved", f"{_tok(saved)} tokens  ({pct:.0f}% compression)"))
    print(row("sprint offset", f"{_usd(usd)}  (@  ${_COST_PER_MTOK}/MTok  Sonnet 4.6)"))
    if best:
        bw = best
        print(row("peak wave", f"wave {bw['wave']}  (+{_tok(bw['delta']['tokens_saved'])} tokens)"))
    print(f"├{line}┤")
    print(row("session total", f"{_tok(lt_saved)}  ·  {_usd(lt_usd)} offset"))
    if monthly_usd > 0.01:
        print(row("projected/mo", f"~{_usd(monthly_usd)}  (8h × 20 working days)"))
    print(f"├{line}┤")
    bar = _bar(pct, W - 9)
    print(f"│  {bar}  {pct:.0f}%   │")
    print(f"└{line}┘")
    print()

    sprint["completed_at"] = datetime.now(timezone.utc).isoformat()
    _dump(sprint_id, sprint)

    # Milestone check after report
    cmd_milestone(quiet=True, lt_saved=lt_saved)


def cmd_status(sprint_id: str) -> None:
    sprint = _load(sprint_id)
    if not sprint:
        snap = _rtk_gain()
        s    = _summary(snap) if snap else {}
        saved = s.get("total_saved", 0)
        pct   = s.get("avg_savings_pct", 0)
        print(f"RTK  {_tok(saved)} lifetime  ·  {pct:.0f}% avg  ·  no active sprint")
        return

    current = _rtk_gain()
    if not current:
        print("RTK unavailable", file=sys.stderr)
        return

    delta = _compute_delta(sprint["baseline"], current)
    saved = delta["tokens_saved"]
    pct   = delta["avg_savings_pct"]
    usd   = delta["cost_saved_usd"]
    wn    = len(sprint["waves"])
    print(
        f"RTK {sprint_id[:8]}  ·  {_tok(saved)} saved  "
        f"·  {_bar(pct, 14)} {pct:.0f}%  ·  {_usd(usd)}  ·  {wn} waves"
    )


def cmd_milestone(quiet: bool = False, lt_saved: int | None = None) -> None:
    """Print milestone banner when lifetime savings cross a threshold."""
    if lt_saved is None:
        snap     = _rtk_gain()
        lt_saved = _summary(snap).get("total_saved", 0) if snap else 0

    milestone_file = _STATE_DIR / "milestones_reached.json"
    reached: set[int] = set()
    try:
        reached = set(json.loads(milestone_file.read_text()))
    except Exception:
        pass

    fired = False
    for threshold, title in _MILESTONES:
        if lt_saved >= threshold and threshold not in reached:
            reached.add(threshold)
            W = 56
            line = "─" * W
            print()
            print(f"┌{line}┐")
            print(f"│{'  ★ Milestone Unlocked'.ljust(W)}│")
            print(f"│{''.ljust(W)}│")
            print(f"│{'  ' + title.upper().ljust(W - 2)}│")
            print(f"│{''.ljust(W)}│")
            l1 = f"  {_tok(lt_saved)} lifetime tokens saved via RTK compression"
            l2 = f"  = {_usd(lt_saved * _COST_PER_MTOK / 1_000_000)} LLM cost offset (Sonnet 4.6 input)"
            print(f"│{l1.ljust(W)}│")
            print(f"│{l2.ljust(W)}│")
            print(f"└{line}┘")
            print()
            fired = True
            break  # one milestone per call

    if not fired and not quiet:
        # Show current position toward next milestone
        for threshold, title in reversed(_MILESTONES):
            if lt_saved < threshold:
                gap = threshold - lt_saved
                print(f"RTK lifetime: {_tok(lt_saved)} saved  ·  {_tok(gap)} until '{title}'")
                return
        print(f"RTK lifetime: {_tok(lt_saved)} saved  ·  all milestones reached")

    try:
        milestone_file.write_text(json.dumps(sorted(reached)))
    except Exception:
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Shared parent so --sprint-id works both before and after the subcommand
    _sid = argparse.ArgumentParser(add_help=False)
    _sid.add_argument(
        "--sprint-id", default=None,
        help="Sprint identifier (default: active RSI session ID or ISO timestamp)",
    )

    parser = argparse.ArgumentParser(
        description="RTK Sprint Dividend — token savings evidence for RSI loops",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_sid],
    )

    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("snapshot",  parents=[_sid], add_help=False,
                   help="Record baseline RTK stats at sprint start")
    wp = sub.add_parser("wave", parents=[_sid], add_help=False,
                        help="Record wave-end RTK stats and show delta")
    wp.add_argument("n", type=int, help="Wave number (1, 2, 3…)")
    sub.add_parser("report",    parents=[_sid], add_help=False,
                   help="Render full Sprint Dividend report")
    sub.add_parser("status",    parents=[_sid], add_help=False,
                   help="One-liner current savings vs baseline")
    sub.add_parser("milestone", help="Check lifetime savings milestones")

    args      = parser.parse_args()
    sprint_id = getattr(args, "sprint_id", None) or _active_sprint_id()

    dispatch = {
        "snapshot":  lambda: cmd_snapshot(sprint_id),
        "wave":      lambda: cmd_wave(args.n, sprint_id),
        "report":    lambda: cmd_report(sprint_id),
        "status":    lambda: cmd_status(sprint_id),
        "milestone": lambda: cmd_milestone(),
    }

    fn = dispatch.get(args.cmd)
    if fn:
        fn()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
