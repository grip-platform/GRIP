#!/usr/bin/env python3
"""
compaction_state_gatherer.py -- Pure-function library for reading GRIP working state.

Gathers all GRIP subsystem state (CUBE, Wave, Genome, KONO, Work, Mode)
plus novel signals (compaction counter, momentum, drift anchor, caffeinate,
genome trend) for pre-compaction persistence.

Used by: hooks/pre-compact-hook.py, potentially /save, /recall.

Every gather function is fail-safe: returns None on any error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure lib/ is on sys.path for sibling imports
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from json_utils import safe_load_json, safe_save_json

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))


# ---------------------------------------------------------------------------
# Core subsystem gatherers
# ---------------------------------------------------------------------------

def gather_cube_state() -> str | None:
    """Read cube-state.json, return compact summary line."""
    try:
        data = safe_load_json(CLAUDE_DIR / "cache" / "cube-state.json")
        if data is None:
            return None
        levels = data.get("levels", {})
        embodied = data.get("embodied_count", 0)
        if not levels:
            return None
        parts = []
        for name, level in sorted(levels.items()):
            parts.append(f"{name}={level}")
        return f"{' '.join(parts)} | Embodied: {embodied}"
    except Exception:
        return None


def gather_wave_state() -> str | None:
    """Read the wave-position file lib/wave_state.py writes, return compact summary."""
    try:
        data = safe_load_json(CLAUDE_DIR / "state" / "session" / "wave-position")
        if data is None:
            return None
        pos = data.get("position", "?")
        phase = data.get("phase", "?")
        cube_w = data.get("cube_w", 0)
        return f"W{pos} {phase} (CUBE W={cube_w})"
    except Exception:
        return None


def gather_genome_pulse() -> str | None:
    """Read genome.json header for generation, gene count, avg fitness."""
    try:
        data = safe_load_json(CLAUDE_DIR / "cache" / "genome.json")
        if data is None:
            return None
        gen = data.get("generation", "?")
        # Count genes if present
        genes = data.get("genes", {})
        gene_count = len(genes) if isinstance(genes, dict) else "?"
        # Get fitness from history
        history = data.get("fitness_history", [])
        fitness = f"{history[-1]:.2f}" if history else "?"
        return f"gen {gen}, {gene_count} genes, fitness {fitness}"
    except Exception:
        return None


def gather_kono_top(limit: int = 5) -> list[str]:
    """Read permanent high-fitness memories from KONO (file-based, not vector search)."""
    try:
        db_path = CLAUDE_DIR / "data" / "kono.db"
        if not db_path.exists():
            return []
        # Use sqlite3 directly for speed (no vector search)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT summary FROM memories "
                "WHERE type = 'permanent' "
                "ORDER BY fitness DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [row[0][:60] for row in rows if row[0]]
        finally:
            conn.close()
    except Exception:
        return []


def gather_active_work() -> str | None:
    """Read MEMORY.md GRIP-BRIEF section for generation, branch, remaining, blockers."""
    try:
        memory_path = CLAUDE_DIR / "projects"
        # Find the project memory
        cwd = os.getcwd()
        encoded = cwd.replace("/", "-").replace(".", "-")
        mem_file = memory_path / encoded / "memory" / "MEMORY.md"
        if not mem_file.exists():
            return None
        content = mem_file.read_text()
        # Extract GRIP-BRIEF section
        match = re.search(
            r"<!-- GRIP-BRIEF-START -->(.*?)<!-- GRIP-BRIEF-END -->",
            content, re.DOTALL
        )
        if not match:
            return None
        brief = match.group(1).strip()
        # Extract key lines
        lines = []
        for line in brief.split("\n"):
            line = line.strip()
            if line.startswith("Gen ") or line.startswith("**Remaining"):
                lines.append(line)
            elif line.startswith("**Blockers"):
                lines.append(line)
        return " | ".join(lines) if lines else None
    except Exception:
        return None


def _resolve_cc_pid() -> str:
    """Walk process tree to find Claude Code PID (grandparent).

    On Windows that walk fails, since Git's ps has no -o, and Claude Code passes its own
    PID as CLAUDE_PID, the key the loader and restore use there (GRIP#8743). A 1 is no
    key there, the same rule the walk below applies."""
    cc_pid = os.environ.get("GRIP_CC_PID", "")
    if cc_pid:
        return cc_pid
    if sys.platform == "win32":
        claude_pid = os.environ.get("CLAUDE_PID", "")
        if re.fullmatch(r"[0-9]+", claude_pid) and claude_pid != "1":
            return claude_pid
        return ""
    try:
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=2,
        )
        gppid = result.stdout.strip()
        if gppid and gppid != "1":
            return gppid
    except Exception:
        pass
    return ""


def gather_active_mode() -> str | None:
    """Read active mode, preferring per-session file keyed on CC PID."""
    try:
        cc_pid = _resolve_cc_pid()
        if cc_pid:
            grip_tmp = os.environ.get("GRIP_TMP_DIR", os.environ.get("TMPDIR", "/tmp"))
            session_file = Path(grip_tmp) / f"grip-session-mode-{cc_pid}"
            if session_file.exists():
                return session_file.read_text().strip() or None
        path = CLAUDE_DIR / ".active-mode"
        if not path.exists():
            return None
        return path.read_text().strip() or None
    except Exception:
        return None


def gather_sprint_focus() -> list[str]:
    """Read latest instance JSON next_up field for remaining items."""
    try:
        cwd = os.getcwd()
        encoded = cwd.replace("/", "-").replace(".", "-")
        grip_dir = CLAUDE_DIR / "projects" / encoded / "grip"
        index_file = grip_dir / "index.json"
        if not index_file.exists():
            return []
        index = safe_load_json(index_file)
        if index is None:
            return []
        instances = index.get("instances", [])
        if not instances:
            return []
        latest = instances[-1]
        inst_file = grip_dir / f"{latest['instance_id']}.json"
        inst = safe_load_json(inst_file)
        if inst is None:
            return []
        next_up = inst.get("next_up", {})
        if isinstance(next_up, dict):
            return next_up.get("remaining", [])[:3]
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Novel signal gatherers
# ---------------------------------------------------------------------------

def gather_compaction_count() -> int:
    """Read+increment state/session/compaction-count.json. Returns compaction number."""
    try:
        path = CLAUDE_DIR / "state" / "session" / "compaction-count.json"
        data = safe_load_json(path)
        count = (data.get("count", 0) if data else 0) + 1
        safe_save_json(path, {"count": count})
        return count
    except Exception:
        return 1


def _load_drift_state() -> dict | None:
    """The state file hooks/anti-drift-hook.py writes (state_paths.AUDIT_DRIFT)."""
    return safe_load_json(CLAUDE_DIR / "state" / "audit" / "drift-state.json")


def gather_momentum() -> str | None:
    """Read tool-call streak from drift-state.json op_count. Returns 'N ops' if >20."""
    try:
        data = _load_drift_state()
        if data is None:
            return None
        op_count = data.get("op_count", 0)
        if op_count > 20:
            return f"{op_count} ops (flow state)"
        return None
    except Exception:
        return None


def gather_drift_anchor() -> str | None:
    """Read last topic from drift-state.json recent_paths. Returns topic string."""
    try:
        data = _load_drift_state()
        if data is None:
            return None
        recent = data.get("recent_paths", [])
        if recent:
            # First entry is most recent topic (space-separated keywords)
            return recent[0][:80]
        return None
    except Exception:
        return None


def gather_caffeinate() -> bool:
    """Check pgrep caffeinate. Returns True if marathon session active."""
    try:
        result = subprocess.run(
            ["pgrep", "caffeinate"],
            capture_output=True, timeout=2
        )
        return result.returncode == 0
    except Exception:
        return False


def gather_genome_trend() -> str | None:
    """Read last 3 entries from genome-history.jsonl, compute trend arrow."""
    try:
        path = CLAUDE_DIR / "cache" / "genome-history.jsonl"
        if not path.exists():
            return None
        # Read last 3 lines efficiently
        lines = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
        if len(lines) < 2:
            return None
        recent = lines[-3:] if len(lines) >= 3 else lines[-2:]
        fitnesses = []
        for line in recent:
            entry = json.loads(line)
            fitness = entry.get("avg_fitness")
            if fitness is not None:
                fitnesses.append(float(fitness))
        if len(fitnesses) < 2:
            return None
        delta = fitnesses[-1] - fitnesses[0]
        if delta > 0.01:
            return "trending UP"
        elif delta < -0.01:
            return "trending DOWN"
        return "stable"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def gather_all() -> dict[str, Any]:
    """Read all GRIP subsystem state. Returns structured dict."""
    return {
        "cube": gather_cube_state(),
        "wave": gather_wave_state(),
        "genome": gather_genome_pulse(),
        "kono": gather_kono_top(5),
        "work": gather_active_work(),
        "mode": gather_active_mode(),
        "focus": gather_sprint_focus(),
        "compaction_num": gather_compaction_count(),
        "momentum": gather_momentum(),
        "drift_anchor": gather_drift_anchor(),
        "caffeinate": gather_caffeinate(),
        "genome_trend": gather_genome_trend(),
    }


# State keys printed as "<label>: <value>", in output order.
_LABELLED_FIELDS = (("drift_anchor", "Last topic"), ("cube", "CUBE"), ("wave", "Wave"))

# MEMORY.md brief segments carried into the summary, as (marker, label).
_WORK_MARKERS = (("**Remaining", "Remaining"), ("**Blockers", "Blockers"))


def _format_header(state: dict[str, Any]) -> str:
    """Header line with compaction count + caffeinate."""
    header = "[GRIP STATE -- preserve across compaction]"
    compaction_num = state.get("compaction_num", 0)
    if compaction_num:
        header += f" [Compaction #{compaction_num}]"
    if state.get("caffeinate"):
        header += " Marathon active"
    return header


def _work_context(work: str | None) -> list[str]:
    """Gen N and branch, extracted from the work summary."""
    if not work:
        return []
    found = []
    gen_match = re.search(r"Gen \d+", work)
    if gen_match:
        found.append(gen_match.group())
    branch_match = re.search(r"Branch: (\S+)", work)
    if branch_match:
        found.append(branch_match.group(1))
    return found


def _format_context_line(state: dict[str, Any]) -> str:
    """Context line: gen, branch, mode, momentum. Empty when none are known."""
    ctx_parts = _work_context(state.get("work"))
    mode = state.get("mode")
    if mode:
        ctx_parts.append(f"Mode: {mode}")
    momentum = state.get("momentum")
    if momentum:
        ctx_parts.append(f"Momentum: {momentum}")
    return " | ".join(ctx_parts)


def _format_state_lines(state: dict[str, Any]) -> list[str]:
    """Anti-drift anchor, CUBE, wave, genome with trend, then sprint focus."""
    lines = [f"{label}: {state[key]}" for key, label in _LABELLED_FIELDS if state.get(key)]
    genome = state.get("genome")
    if genome:
        trend = state.get("genome_trend")
        lines.append(f"Genome: {genome} ({trend})" if trend else f"Genome: {genome}")
    focus = state.get("focus", [])
    if focus:
        lines.append(f"Focus: {'; '.join(focus[:3])}")
    return lines


def _format_work_segment(segment: str) -> str | None:
    """'Remaining: ...' or 'Blockers: ...' for one brief segment, else None."""
    for marker, label in _WORK_MARKERS:
        if segment.startswith(marker):
            value = segment.replace(f"{marker}**:", "").replace(f"{marker}**", "").strip()
            return f"{label}: {value}" if value else None
    return None


def _format_work_lines(work: str | None) -> list[str]:
    """Work: remaining + blockers."""
    if not work:
        return []
    lines = (_format_work_segment(segment.strip()) for segment in work.split(" | "))
    return [line for line in lines if line]


def format_compact(state: dict[str, Any], max_chars: int = 800) -> str:
    """Format state into compact text for compaction summary.

    Output is raw text (NOT JSON) that becomes custom instructions
    injected into Claude's compaction summary.
    """
    parts: list[str] = [_format_header(state)]
    context_line = _format_context_line(state)
    if context_line:
        parts.append(context_line)
    parts.extend(_format_state_lines(state))
    parts.extend(_format_work_lines(state.get("work")))

    # KONO top memories (compact)
    kono = state.get("kono", [])
    if kono:
        kono_str = " | ".join(kono[:5])
        parts.append(f"KONO[{len(kono)}]: {kono_str}")

    # Join and truncate to max_chars
    output = "\n".join(parts)
    if len(output) > max_chars:
        output = output[:max_chars - 3] + "..."
    return output
