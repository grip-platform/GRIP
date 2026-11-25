"""Memory projector operator for GRIP/HAL.

ℱ_mem(system_state) → system_state at fixed point (pressure == green, swap < threshold).
Follows syscall doctrine: thin shim, stable API, graceful degradation.

The Banach property:
  - ℱ_mem applied to an already-green state: returns state unchanged (noop)
  - ℱ_mem applied to a pressured state: takes minimum action toward green
  - Repeated application converges: red → yellow → green (fixed point)
  - Idempotent: ℱ_mem(ℱ_mem(s)) == ℱ_mem(s) by the projection property

Public API::

    from lib.hal_mem import probe, project, is_fixed_point, fmt_table

    state = probe()
    if not is_fixed_point(state):
        new_state, actions = project(state, dry_run=True)
        print(fmt_table(state))
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple

__all__ = [
    "MemoryState",
    "Action",
    "PressureLevel",
    "probe",
    "is_fixed_point",
    "project",
    "fmt_table",
    "fmt_summary",
]

PAGE_SIZE = 16_384          # bytes; Apple Silicon M-series default
GB = 1_073_741_824.0
MB = 1_048_576.0
SWAP_WARN_MB = 2_000        # yellow threshold for swap
SWAP_CRIT_MB = 6_000        # red threshold for swap
FREE_WARN_PCT = 20.0        # below this → yellow
FREE_CRIT_PCT = 10.0        # below this → red


class PressureLevel:
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class Action(NamedTuple):
    kind: str       # "purge" | "stop_process" | "quit_app" | "advisory"
    target: str     # process name, app name, or description
    estimated_mb: float
    command: list[str]  # empty for advisory


@dataclass
class MemoryState:
    total_gb: float
    free_pct: float           # 0-100; includes inactive (reclaimable)
    wired_gb: float
    active_gb: float
    inactive_gb: float        # reclaimable on demand
    compressed_gb: float
    swap_in_total: int        # cumulative (not delta) from vm_stat
    swap_out_total: int
    pressure_level: str
    top_procs: list[dict] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def _run(*args: str, timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.stdout if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _parse_vm_stat() -> dict[str, int]:
    out = _run("vm_stat")
    result: dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r"^(.+?):\s+([\d]+)\.", line)
        if m:
            result[m.group(1).strip()] = int(m.group(2))
    return result


def _parse_top_procs(n: int = 8) -> list[dict]:
    # `ps aux` has fixed 10 columns before COMMAND, so spaces in app names are safe.
    # Columns: USER PID %CPU %MEM VSZ RSS TT STAT STARTED TIME COMMAND
    out = _run("ps", "aux")
    procs = []
    for line in out.splitlines()[1:]:   # skip header
        parts = line.split(None, 10)    # split into exactly 11 fields
        if len(parts) < 11:
            continue
        try:
            raw_cmd = parts[10].strip()
            mem_pct = float(parts[3].replace(",", "."))
            rss_kb = int(parts[5])
            app_m = re.search(r"/([^/]+?)\.app/", raw_cmd)
            if app_m:
                name = app_m.group(1)[:22]          # "WhatsApp", "Brave Browser"
            else:
                name = raw_cmd.split("/")[-1].split()[0][:22]   # "node", "python3"
            procs.append({
                "pid": int(parts[1]),
                "name": name,
                "mem_pct": mem_pct,
                "rss_mb": round(rss_kb / 1024, 1),
            })
        except (ValueError, IndexError):
            continue
    return sorted(procs, key=lambda p: p["rss_mb"], reverse=True)[:n]


def _parse_memsize() -> float:
    out = _run("sysctl", "hw.memsize")
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) / GB if m else 0.0


def _classify(stats: dict[str, int], total_gb: float) -> tuple[str, float]:
    """Return (pressure_level, free_pct).

    Uses page counts from vm_stat. Free includes speculative (pre-fetched, reclaimable)
    and inactive (also reclaimable). Wired + active are the committed pages.
    """
    free = stats.get("Pages free", 0)
    inactive = stats.get("Pages inactive", 0)
    speculative = stats.get("Pages speculative", 0)
    total_pages = stats.get("Pages free", 0) + stats.get("Pages active", 0) + \
        stats.get("Pages inactive", 0) + stats.get("Pages speculative", 0) + \
        stats.get("Pages wired down", 0)
    reclaimable = free + inactive + speculative
    free_pct = (reclaimable / total_pages * 100) if total_pages else 0.0

    if free_pct < FREE_CRIT_PCT:
        return PressureLevel.RED, free_pct
    if free_pct < FREE_WARN_PCT:
        return PressureLevel.YELLOW, free_pct
    return PressureLevel.GREEN, free_pct


def probe() -> MemoryState:
    """Tier 1: read-only memory snapshot. ~80ms, no LLM, no permissions needed.

    Safe to call at any time. Returns a MemoryState capturing the current
    system memory configuration.
    """
    stats = _parse_vm_stat()
    total_gb = _parse_memsize()
    top_procs = _parse_top_procs()
    pressure_level, free_pct = _classify(stats, total_gb)

    return MemoryState(
        total_gb=total_gb,
        free_pct=round(free_pct, 1),
        wired_gb=round(stats.get("Pages wired down", 0) * PAGE_SIZE / GB, 2),
        active_gb=round(stats.get("Pages active", 0) * PAGE_SIZE / GB, 2),
        inactive_gb=round(stats.get("Pages inactive", 0) * PAGE_SIZE / GB, 2),
        compressed_gb=round(
            stats.get("Pages used by compressor", stats.get("Pages occupied by compressor", 0))
            * PAGE_SIZE / GB, 2
        ),
        swap_in_total=stats.get("Swapins", 0),
        swap_out_total=stats.get("Swapouts", 0),
        pressure_level=pressure_level,
        top_procs=top_procs,
    )


def is_fixed_point(state: MemoryState) -> bool:
    """True iff state is already at its fixed point.

    The Banach property: project(fixed_point_state) returns (state, []) — no actions.
    This is the idempotence condition: ℱ_mem(s) = s iff is_fixed_point(s).

    Falsified if: returning True here while the user observes spinning beachball
    or system slow-down — that means our GREEN threshold is too permissive.
    """
    return state.pressure_level == PressureLevel.GREEN


def _suggest_actions(state: MemoryState) -> list[Action]:
    """Tier 2: prescribe minimum actions to move toward the fixed point.

    Conservative by default — only advisory unless the caller opts into execution.
    Ordered by estimated memory recovery (largest first).
    """
    actions: list[Action] = []

    for proc in state.top_procs:
        name = proc["name"].lower()
        rss = proc["rss_mb"]
        if rss < 200:
            continue
        if "ollama" in name:
            actions.append(Action(
                kind="stop_process",
                target=proc["name"],
                estimated_mb=rss,
                command=["killall", "ollama"],
            ))
        elif any(kw in name for kw in ("chrome", "brave", "safari", "firefox", "arc")):
            actions.append(Action(
                kind="advisory",
                target=f"close unused {proc['name']} tabs",
                estimated_mb=rss * 0.3,
                command=[],
            ))
        elif rss > 500:
            actions.append(Action(
                kind="advisory",
                target=f"{proc['name']} ({rss:.0f} MB)",
                estimated_mb=rss,
                command=[],
            ))

    if state.pressure_level in (PressureLevel.YELLOW, PressureLevel.RED):
        actions.append(Action(
            kind="purge",
            target="inactive memory",
            estimated_mb=state.inactive_gb * 1024 * 0.8,
            command=["sudo", "purge"],
        ))

    return sorted(actions, key=lambda a: a.estimated_mb, reverse=True)


def project(
    state: MemoryState,
    *,
    dry_run: bool = True,
    auto_stop_ollama: bool = False,
) -> tuple[MemoryState, list[Action]]:
    """Apply one iteration of the projector operator ℱ_mem.

    The fixed-point property:
      - is_fixed_point(state) == True  →  returns (state, [])  [idempotent noop]
      - is_fixed_point(state) == False →  takes minimum action, re-probes

    Args:
        state: current system memory state
        dry_run: if True, suggest actions but do not execute them
        auto_stop_ollama: if True and not dry_run, stops ollama without user prompt

    Returns:
        (new_state, actions_taken)
        new_state == state if dry_run (no changes made)
    """
    if is_fixed_point(state):
        return state, []

    actions = _suggest_actions(state)

    if dry_run:
        return state, actions

    taken: list[Action] = []
    for action in actions:
        if not action.command:
            continue
        if action.kind == "stop_process" and "ollama" in action.target.lower():
            if not auto_stop_ollama:
                continue
        if action.kind == "purge" and action.command[0] == "sudo":
            continue
        try:
            subprocess.run(action.command, check=False, timeout=10.0, capture_output=True)
            taken.append(action)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    new_state = probe()
    return new_state, taken


def fmt_table(state: MemoryState) -> str:
    """Format a compact htop-style memory table for terminal or Slack output."""
    bar_len = 30
    used_pct = 100 - state.free_pct
    filled = round(bar_len * used_pct / 100)
    bar = "[" + "#" * filled + "-" * (bar_len - filled) + "]"

    level_label = {
        PressureLevel.GREEN: "GREEN  (ok)",
        PressureLevel.YELLOW: "YELLOW (watch)",
        PressureLevel.RED: "RED    (act now)",
    }.get(state.pressure_level, state.pressure_level)

    lines = [
        f"Memory  {bar} {used_pct:.1f}% used of {state.total_gb:.0f} GB",
        f"Pressure: {level_label}   Free: {state.free_pct:.1f}%   Inactive(reclaimable): {state.inactive_gb:.1f} GB",
        f"Wired: {state.wired_gb:.1f} GB   Active: {state.active_gb:.1f} GB   Compressed: {state.compressed_gb:.1f} GB",
        "",
        f"{'PID':>7}  {'Process':<22}  {'%MEM':>5}  {'RSS MB':>8}",
        "-" * 54,
    ]
    for p in state.top_procs[:8]:
        lines.append(f"{p['pid']:>7}  {p['name']:<22}  {p['mem_pct']:>5.1f}  {p['rss_mb']:>8.1f}")
    lines.append(f"\nFixed point: {'YES' if is_fixed_point(state) else 'NO — actions available via /mem project'}")
    return "\n".join(lines)


def fmt_summary(state: MemoryState) -> str:
    """One-line summary for HAL startup pre-flight and Monitor alerts."""
    icon = {"green": "[ok]", "yellow": "[!]", "red": "[!!]"}.get(state.pressure_level, "[?]")
    return (
        f"{icon} Memory {state.pressure_level.upper()}: "
        f"{state.free_pct:.0f}% free of {state.total_gb:.0f}GB | "
        f"active={state.active_gb:.1f}GB wired={state.wired_gb:.1f}GB "
        f"compressed={state.compressed_gb:.1f}GB"
    )
