#!/usr/bin/env python3
"""
grip_save.py — Unified state persistence orchestrator.

Saves ALL GRIP memory subsystems in one shot. Mid-session safe
(does NOT clear wave state, deregister, or stop caffeinate).

Usage:
    python3 lib/grip_save.py                        # Auto-detect achievement
    python3 lib/grip_save.py "completed auth refactor"  # Explicit achievement
    python3 lib/grip_save.py --quiet                # Minimal output
    python3 lib/grip_save.py --slack C0EXAMPLE01    # Post digest to Slack
    python3 lib/grip_save.py --mcp                  # Print MCP App digest JSON

Called by: /save command, soft checkpoints
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# The fallback is built lazily. Written as
# os.environ.get("CLAUDE_DIR", Path.home() / ".claude") the default is
# constructed on every call, so Path.home() runs even when CLAUDE_DIR is set --
# and it raises RuntimeError wherever no home directory can be named, which on
# Windows includes scheduled tasks and sandboxed subprocesses.
_CONFIGURED_CLAUDE_DIR = os.environ.get("CLAUDE_DIR")
CLAUDE_DIR = Path(_CONFIGURED_CLAUDE_DIR) if _CONFIGURED_CLAUDE_DIR else Path.home() / ".claude"
LIB_DIR = CLAUDE_DIR / "lib"
FAILURE_LOG = CLAUDE_DIR / "logs" / "grip-save-failures.jsonl"

# Instance-serialize wall-clock budget, DERIVED rather than picked.
#
# History: 30→60 (2026-06-17), blamed on sessions contending the index.json
# FileLock. That diagnosis was wrong, and measuring it on 2026-08-25 is what
# showed why the raise did not hold. The lock ladder in instance_serializer is
# BOUNDED at 20s (5 + 15) and then deliberately gives up, deferring the index
# entry — it cannot consume 60s. What actually costs the time is the baseline
# serialize work, measured at ~40s on a long session and growing with session
# length. Baseline + ladder = ~60s, so the cap sat exactly at the worst case
# with zero headroom and any variance tipped it.
#
# Why this matters more than a spurious "1 failed": subprocess.run KILLS the
# child on timeout. The ladder is engineered to never clobber a concurrent
# append — it defers instead — and an outer kill pre-empts that designed
# degradation mid-work. The budget must therefore always exceed
# baseline + ladder, or the safety property is unreachable under load.
#
# (No stale-lock deadlock is possible here: FileLock uses fcntl.flock, which
# the kernel releases when the holder dies, verified by controlled test on
# 2026-08-25 — kill the holder, the next acquire succeeds immediately.)
#
# Derived so the two numbers cannot drift apart again: if the ladder grows, this
# grows with it.
#
# Raised 2026-09-24: the 150 above (3.75x headroom over a ~40s UNCONTENDED
# measurement) was still timing out in production -- 309 TimeoutExpired records
# for this step alone in the 500 most recent grip-save-failures.jsonl lines,
# more than any other step. Root cause is NOT the lock ladder (bounded at 20s,
# already accounted for below) and NOT a slow algorithm -- a single uncontended
# run measured 25.86s real, matching the old baseline's ballpark. It is the
# shared index this step reads, appends to, and rewrites WHOLE on every save
# (_write_index_with_lock_ladder -> _load_index + atomic_write_json):
# ~/.claude/projects/-Users-void--grip/grip/index.json measured at 11MB / 13,143
# entries (368 distinct sessions, ~36 saves/session average, 85 days of
# accumulated history, 2026-06-22 to present) -- an O(index size) cost paid by
# EVERY session on EVERY save, growing every day this environment stays in use.
#
# Reproduced directly rather than guessed: 4 concurrent `instance_serializer.py
# auto` invocations against the real 11MB index, under synthetic CPU load
# matching this machine's OBSERVED sustained load average (~20 on 10 cores,
# `uptime` at the time: "9.91 19.15 20.01"), measured 74.75-78.90s real per run
# (27-29% cpu -- mostly waiting on the lock and on I/O for the file, not
# computing). This constant is set with real margin over that worst-observed
# run, not a multiplier applied to blind guesswork.
#
# This budget increase buys time, not immunity: the index has no eviction or
# archival, so this cost trends upward indefinitely regardless of what this
# constant is set to. That is filed separately (GRIP#6552 -- see
# docs/measurements/2026-09-24-instance-index-growth.md) as the fix that
# actually bounds the cost; this constant only needs to survive TODAY's
# realistic worst case, which it now does with margin, not tomorrow's.
_INSTANCE_SERIALIZE_BASELINE_S = 260  # ~78.90s worst 4-way-concurrent measurement, 3.3x headroom


# Add lib/ to path for bare-module imports (`from tui_links import ...`) AND
# CLAUDE_DIR (lib/'s parent) for the package-qualified imports this file also
# uses (`from lib.context_chain import ...`). The latter resolve ONLY when
# CLAUDE_DIR is on sys.path; without it they work by accident only when cwd
# happens to be CLAUDE_DIR (Python's implicit sys.path[0]=''). From any other
# cwd — e.g. the blackbook's ~/.grip — the signed context-chain step failed
# with `No module named 'lib'`. Canonical fix per rules/path-portability.md.
for _p in (str(LIB_DIR), str(CLAUDE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)



# Derived AFTER the sys.path bootstrap above, deliberately. It sat above it
# until AGORA #5070 measured the consequence: imported as a module without
# lib/ already on sys.path, the import failed, the fallback engaged on EVERY
# run, the budget became 210 instead of the derived 170, and the warning
# printed every time — safe, but the derivation the file exists to perform
# was inert. The tests missed it because they seed sys.path first, which is
# reasonable for a test and is exactly how the gap stayed invisible.
#: Fallback used only when the ladder cannot be READ. Two ways it cannot:
#: the module is missing (ImportError), or it is present but predates the
#: ladder constants (AttributeError — the class imports, the attribute does
#: not). Catching only ImportError let the second case abort `import
#: grip_save` outright with a traceback, which is a worse failure than the
#: timeout this file exists to fix. Both are the same 'ladder cannot be
#: measured' condition and take the same safe exit.
#:
#: Fallback value: It is the CEILING the
#: ladder is required to stay under (pinned by
#: tests/test_instance_serialize_budget.py::test_ladder_rungs_are_ordered_and_bounded),
#: never a copy of its current value. A copy is what re-opens the drift this
#: whole constant exists to close: if the ladder later grows to 30s, a
#: hardcoded 20 keeps under-budgeting and the mid-ladder kill returns with no
#: diagnostic. Over-budgeting cannot cause that failure, so the safe direction
#: is up.
_INSTANCE_LOCK_LADDER_CEILING_S = 60

try:
    from instance_serializer import InstanceSerializer as _IS  # noqa: E402
    _INSTANCE_LOCK_LADDER_S = int(_IS.INDEX_LOCK_LADDER_BUDGET_S)
except (ImportError, AttributeError) as _exc:
    # Loud, not silent: a swallowed import here is indistinguishable from a
    # correctly derived budget, and the failure it permits is silent too.
    _INSTANCE_LOCK_LADDER_S = _INSTANCE_LOCK_LADDER_CEILING_S
    sys.stderr.write(
        f"[grip-save] could not import the index lock ladder ({_exc}); "
        f"budgeting its {_INSTANCE_LOCK_LADDER_CEILING_S}s CEILING instead of "
        f"its actual value. The budget is still safe, but it is no longer "
        f"derived — fix the import rather than trusting this number.\n"
    )
_INSTANCE_SERIALIZE_TIMEOUT_S = _INSTANCE_SERIALIZE_BASELINE_S + _INSTANCE_LOCK_LADDER_S

# ~68.15s measured 2026-09-23 on a single, uncontended run (real 68.15s; user
# 5.62s, sys 23.33s -- I/O-bound, not compute). The prior hardcoded 45 killed
# every honest run: logs/grip-save-failures.jsonl holds 3,200 TimeoutExpired
# records for this step since 2026-07-11, peaking at 155 in one day.
#
# Single-flight locking (issue #4310, see genome_session_evolve.py's own
# docstring) already stops CONCURRENT /save runs from stacking on this step --
# that fix addressed contention, not the budget. This constant is the separate
# fix: even one uncontended run needs more than 45s.
#
# Kill-safety (read from the write path, not reproduced by truncating a real
# run): the module's only disk write is a single save_genome() call at the
# very end of its evolve pass, via converge_dna.save_genome()'s locked_write()
# (atomic temp+rename). Everything before that call is in-memory. A timeout
# here can therefore never leave a torn genome file -- only an untouched one,
# skipping this session's generation, which the module's own no-evidence guard
# already treats as the honest outcome for a run it could not complete.
# Raised 2026-09-24 alongside the instance-serialize budget above, same root
# cause class: the prior 150 (2.2x headroom over a ~68.15s UNCONTENDED
# baseline) still timed out in production (106 TimeoutExpired records for this
# step in the 500 most recent grip-save-failures.jsonl lines). This module's
# single-flight lock (issue #4310) means it never WAITS on contention -- it
# skips immediately -- so the slowdown is not lock queueing. It is the same
# machine-level contention instance-serialize hits: reproduced directly by
# timing a real, uncontended-by-design run under synthetic CPU load matching
# this machine's observed sustained load average (~20 on 10 cores), which
# measured 116.91s real (33% cpu -- mostly waiting, not computing), against a
# 37.25s baseline with no synthetic load. Set with real margin over that
# worst-observed run, not a multiplier applied to blind guesswork.
_GENOME_EVOLVE_TIMEOUT_S = 300  # 2.6x the worst contended measurement (116.91s)
from _compat import grip_python  # noqa: E402 -- needs LIB_DIR on sys.path first

# Import tui_links for OSC 8 hyperlinks (graceful degradation)
try:
    from tui_links import file_link, url_link
except ImportError:
    def file_link(path: str, display: str | None = None) -> str:
        return display or Path(path).name
    def url_link(url: str, display: str | None = None) -> str:
        return display or url

# Import shared digest data model
try:
    from digest_data import (
        parse_achievements as _parse_achievements,
        parse_active_state as _parse_active_state,
        read_genome_summary as _read_genome_summary,
    )
except ImportError:
    def _parse_achievements() -> list[str]:
        return []
    def _parse_active_state(encoded: str) -> list[str]:
        return []
    def _read_genome_summary() -> dict | None:
        return None
try:
    from plain_language import explain_genome as _explain_genome
except ImportError:
    def _explain_genome(*_a, **_k) -> str:  # fail-soft: digest still renders
        return ""
GRIP_PYTHON = grip_python(CLAUDE_DIR)
# True when the resolved interpreter is GRIP's venv rather than the fallback.
# KONO's dependencies live in the venv, so this is what "KONO is available"
# actually means -- previously probed as venv/bin/python3.is_file(), which is
# the POSIX layout and never exists on Windows (venv/Scripts/python.exe).
VENV_ACTIVE = "venv" in Path(GRIP_PYTHON).parts


#: Operator-facing sentinel for the empty-stream cascade in :func:`_run`.
#: Module-level constant so production and tests reference the same format;
#: a drift between the two is now a test failure rather than a regression
#: shipped silently (AGORA review of PR #3207, Speaker C suggestion #2).
NO_DIAGNOSTIC_SENTINEL_FMT = "<exit {rc}, no diagnostic>"


def _log_failure(label: str, *, cmd: list[str] | None = None,
                 returncode: int | None = None, stderr: str = "",
                 stdout: str = "", exc_class: str = "",
                 traceback_text: str = "") -> None:
    """Append a structured failure record to logs/grip-save-failures.jsonl.

    The 80-char console message is for human glanceability; this sidecar
    preserves the full diagnostic surface (full stderr, full stdout, full
    traceback, exact command) so that the next-recurrence triage doesn't
    have to reproduce the failure to see what actually went wrong.

    Best-effort: a logging failure NEVER raises into the save pipeline —
    the subsystem result the caller is computing is the load-bearing
    value, not this audit line.
    """
    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "subsystem": label,
            "cmd": cmd or [],
            "returncode": returncode,
            "stderr": stderr,
            "stdout": stdout,
            "exc_class": exc_class,
            "traceback": traceback_text,
        }
        with FAILURE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _run(cmd: list[str], label: str, timeout: int = 15) -> tuple[bool, str]:
    """Run a subprocess with error isolation. Returns (success, message).

    On failure, ALSO appends the full diagnostic record (untruncated stderr,
    stdout, traceback, exit code, cmd) to ``logs/grip-save-failures.jsonl``
    so that next-recurrence diagnosis isn't blocked by the 80-char terse
    console summary. See ``_log_failure``.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(CLAUDE_DIR))
        if r.returncode == 0:
            return True, label
        _log_failure(label, cmd=cmd, returncode=r.returncode,
                     stderr=r.stderr, stdout=r.stdout)
        # CBC #3201: cascade stderr → labelled-stdout → sentinel. capture_output=True
        # guarantees both streams are str (anchored by test_run_capture_output_
        # guarantees_str_streams — refutes HAL #3207 "stderr None" false-positive).
        # Stdout labelled as "(no stderr; stdout: ...)" for source-honesty (AGORA
        # #3207 round 3 Speaker C concern B).
        stderr_msg, stdout_msg = r.stderr.strip(), r.stdout.strip()
        diag = (
            stderr_msg
            or (f"(no stderr; stdout: {stdout_msg})" if stdout_msg else None)
            or NO_DIAGNOSTIC_SENTINEL_FMT.format(rc=r.returncode)
        )
        return False, f"{label}: {diag[:80]}"
    except subprocess.TimeoutExpired:
        _log_failure(label, cmd=cmd, exc_class="TimeoutExpired")
        return False, f"{label}: timeout ({timeout}s)"
    except FileNotFoundError:
        _log_failure(label, cmd=cmd, exc_class="FileNotFoundError")
        return False, f"{label}: script not found"
    except Exception as e:
        _log_failure(label, cmd=cmd, exc_class=type(e).__name__,
                     traceback_text=traceback.format_exc())
        return False, f"{label}: {e}"


def _detect_achievement() -> str:
    """Auto-detect achievement from recent git log."""
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-1", "--format=%s"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR))
        if r.returncode == 0 and r.stdout.strip():
            return f"Mid-session save (last: {r.stdout.strip()[:60]})"
    except Exception:
        pass
    return "Mid-session checkpoint"


def _get_project_info() -> tuple[str, str, str]:
    """Get CWD, encoded project path, and grip index path."""
    cwd = os.getcwd()
    encoded = cwd.replace("/", "-").replace(".", "-")
    grip_index = CLAUDE_DIR / "projects" / encoded / "grip" / "index.json"
    return cwd, encoded, str(grip_index)


def _check_memory_md(encoded: str) -> str:
    """Check MEMORY.md status. Returns formatted status line.

    Resilient to the Edit-tool atomic rename-replace race — briefly retries
    on a transient miss and widens the exception catch so a PermissionError
    or OSError doesn't get silently reported as "not found".
    """
    mem_path = CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"
    for attempt in range(3):
        if mem_path.is_file():
            break
        if attempt < 2:
            time.sleep(0.05)
    else:
        return f"  [ ] MEMORY.md (not found at {mem_path})"
    try:
        return f"  [x] MEMORY.md ({len(mem_path.read_text().splitlines())} lines)"
    except UnicodeDecodeError:
        return "  [x] MEMORY.md (git-crypt encrypted)"
    except OSError as e:
        return f"  [!] MEMORY.md (read error: {e.__class__.__name__})"


def _check_embeddings() -> str:
    """Check embeddings DB status. Returns formatted status line."""
    emb_path = CLAUDE_DIR / "embeddings.db"
    if not emb_path.exists():
        return "  [ ] Embeddings index (not found)"
    import datetime
    age = datetime.datetime.now() - datetime.datetime.fromtimestamp(emb_path.stat().st_mtime)
    age_str = f"{int(age.total_seconds() / 60)}m ago" if age.total_seconds() < 3600 else f"{int(age.total_seconds() / 3600)}h ago"
    return f"  [x] Embeddings index ({emb_path.stat().st_size / (1024 * 1024):.0f}MB, last sync: {age_str})"


def _check_session_jsonl(encoded: str) -> str:
    """Check session JSONL status. Returns formatted status line."""
    proj_dir = CLAUDE_DIR / "projects" / encoded
    if not proj_dir.exists():
        return "  [ ] Session JSONL (project dir missing)"
    jsonl_files = [f for f in sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if not f.name.startswith("agent-")]
    if not jsonl_files:
        return "  [ ] Session JSONL (no sessions)"
    return f"  [x] Session JSONL ({sum(1 for _ in open(jsonl_files[0]))} entries)"


def _report_subsystem_status(cwd: str, encoded: str) -> None:
    """Report status of additional memory subsystems (read-only checks)."""
    print(_check_memory_md(encoded))
    print(_check_embeddings())
    print(_check_session_jsonl(encoded))
    print("  [x] KONO substrate (active)" if VENV_ACTIVE else "  [ ] KONO substrate (not active)")


def _print_digest_section(title_link: str, items: list, suffix: bool = False) -> None:
    """Print a digest section with bullet items."""
    print(f"  {title_link}")
    for item in items:
        print(f"  \u00b7 {item}")
    if suffix:
        print()


def _format_genome_section(genome: dict) -> list[str]:
    """Format genome digest lines with skill links.

    Total over its input: every field is read with .get() + a default, so a
    minimal/partial genome dict can never KeyError the digest. The canonical
    producer (digest_data.read_genome_summary) always populates all keys; the
    defaults guard hypothetical/future callers (AGORA #3399 r3). fitness=None
    (no scored cycle) renders as "no-fitness" rather than a fabricated 0.000.
    """
    generation = genome.get("generation", 0)
    gene_count = genome.get("gene_count", 0)
    linked = []
    for g in genome.get("top_genes", []):
        skill_path = CLAUDE_DIR / "skills" / g / "SKILL.md"
        linked.append(f"\u25b8 {file_link(str(skill_path), g)}" if skill_path.exists() else f"\u25b8 {g}")
    genome_link = file_link(str(CLAUDE_DIR / "cache" / "genome.json"), "Genome")
    fit = genome.get("fitness")
    fit_str = f"{fit:.3f}" if isinstance(fit, (int, float)) else "no-fitness"
    header = f"  {genome_link}  gen {generation} \u00b7 {gene_count} genes \u00b7 {fit_str} {genome.get('trend', '')}"
    plain = _explain_genome(
        generation, gene_count, fit,
        trend=genome.get("trend"), concise=True,
    )
    lines = [header, f"  {'  '.join(linked)}"]
    if plain:
        lines.append(f"  {plain}")
    return lines


def _memory_digest(encoded: str) -> None:
    """Print a creative summary of WHAT was saved across memory systems."""
    achievements = _parse_achievements()
    active = _parse_active_state(encoded)
    genome = _read_genome_summary()

    if not achievements and not active and not genome:
        return

    W = 57
    print("\n\u2500\u2500 Memory Digest " + "\u2500" * (W - 16))

    if achievements:
        link = file_link(str(CLAUDE_DIR / "rules" / "session-context.md"), "Achievements")
        _print_digest_section(link, achievements, suffix=bool(active or genome))

    if active:
        mem_path = str(CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md")
        items = [file_link(mem_path, i) if "items tracked" in i else i for i in active]
        _print_digest_section(file_link(mem_path, "Active State"), items, suffix=bool(genome))

    if genome:
        for line in _format_genome_section(genome):
            print(line)

    print("\u2500" * W)


def _should_run_subsystem(subsystem: str, allowed: set | None) -> bool:
    """Check whether a subsystem is allowed to run under the current budget."""
    if allowed is None:
        return True
    return subsystem in allowed


def _save_session_sync(results: list, allowed: set | None) -> None:
    """Write session context to sync directory for cross-session sharing."""
    if _should_run_subsystem("session_sync", allowed) and (LIB_DIR / "grip_session_sync.py").exists():
        results.append(_run(
            [GRIP_PYTHON, str(LIB_DIR / "grip_session_sync.py"), "write"],
            "Session sync", timeout=10))


def _save_insights(results: list, allowed: set | None) -> None:
    """Flush insights and run pattern detection (dependency order from session-end.sh)."""
    if _should_run_subsystem("insights", allowed) and (LIB_DIR / "insight_collector.py").exists():
        results.append(_run(
            [GRIP_PYTHON, str(LIB_DIR / "insight_collector.py"), "flush", ""],
            "Insights flush"))

    if _should_run_subsystem("insights", allowed) and (LIB_DIR / "auto_skill_generator.py").exists():
        results.append(_run(
            [GRIP_PYTHON, str(LIB_DIR / "auto_skill_generator.py"), "detect"],
            "Insights detect", timeout=20))


def _save_dna(results: list, allowed: set | None) -> None:
    """Run DNA sync, decay, and kono-sync (individually gated)."""
    if not (LIB_DIR / "converge_dna.py").exists():
        return
    dna_ops = [
        ("sync", "dna_sync"),
        ("decay", "dna_decay"),
        ("kono-sync", "dna_kono_sync"),
    ]
    for sub, gate_name in dna_ops:
        if _should_run_subsystem(gate_name, allowed):
            results.append(_run(
                [GRIP_PYTHON, str(LIB_DIR / "converge_dna.py"), sub],
                f"DNA {sub}"))


def _extract_gen_instance(grip_index: str) -> tuple[str, str]:
    """Extract generation and instance ID from the grip index file."""
    gen, inst = "0", "unknown"
    try:
        if Path(grip_index).exists():
            data = json.loads(Path(grip_index).read_text())
            instances = data.get("instances", [])
            if instances:
                gen = str(instances[-1].get("lineage", {}).get("generation", 0))
                inst = str(instances[-1].get("id", "unknown"))[:8]
    except Exception:
        pass
    return gen, inst


def _save_kono_summary(
    results: list, allowed: set | None, grip_index: str,
    achievement: str, cwd: str, quiet: bool, budget_mode: str,
) -> None:
    """Save KONO session summary (requires venv, abundant budget only)."""
    if _should_run_subsystem("kono_summary", allowed) and VENV_ACTIVE and (LIB_DIR / "kono_substrate.py").exists():
        gen, inst = _extract_gen_instance(grip_index)
        results.append(_run(
            [GRIP_PYTHON, str(LIB_DIR / "kono_substrate.py"),
             "store-session-summary",
             "--gen", gen, "--instance", inst, "--msgs", "0",
             "--achievement", achievement, "--project", cwd],
            "KONO summary"))
    elif not _should_run_subsystem("kono_summary", allowed) and not quiet:
        results.append((True, f"KONO summary skipped (budget: {budget_mode})"))


def _save_context_delta(results: list, prev_hash: str) -> None:
    """Record context delta (what changed since last save)."""
    try:
        from lib.context_delta import compute_delta, append_delta
        delta = compute_delta(prev_hash)
        if delta["changed"]:
            append_delta(delta)
            results.append((True, "Context delta recorded"))
        else:
            results.append((True, "Context delta (no change)"))
    except ImportError:
        pass
    except Exception as e:
        results.append((False, f"Context delta: {e}"))


# Deterministic snapshot cadence for the signed context chain (design §4; NEVER
# wall-clock, so the same chain folds snapshots at the same points → replay-stable).
_SNAPSHOT_EVERY_N_DELTAS = 8

# P1 source-population: the chain's (tier, paramount) pin axis is classified by
# the shared lib.context_chain_classify.classify_checkpoint (DRY — also used by
# hooks/context-checkpoint-hook). Imported lazily in _save_context_chain.


def _save_context_chain(results: list, cwd: str) -> None:
    """Slice A — write ONE signed ``context-delta`` per /save (per-/save cadence,
    V>>-ratified 2026-06-07), carrying the latest ``next_step`` BY VALUE so the
    chain threads the read-from-previous mental model. The FIRST call seeds the
    chain (genesis delta). A ``context-snapshot`` is cut every
    ``_SNAPSHOT_EVERY_N_DELTAS`` deltas (deterministic, replay-stable).

    Boot-path-SAFE: ``kono_preload`` does not read the chain until P6 (human-merge
    gated) — this writes a NEW sidecar (``state/precog/context.jsonl``) with zero
    blast radius on warm-start. Distinct from ``_save_context_delta`` (the older
    UNSIGNED ``cache/`` telemetry tracker). Fail-soft: a chain error never aborts
    /save.
    """
    try:
        from pathlib import Path as _Path

        from lib.context_chain import checkpoint, read_context_chain
        from lib.context_chain_classify import classify_checkpoint
        from lib.context_snapshot import deltas_since_snapshot, snapshot_chain
        from lib.next_step_block import read_latest_next_up

        next_up = read_latest_next_up(_Path(cwd)) or {}
        summary = _detect_achievement()
        tier, paramount = classify_checkpoint(summary, next_up)  # P1 source population
        node = checkpoint(next_up, summary=summary, tier=tier, paramount=paramount)
        since = deltas_since_snapshot(read_context_chain())
        if since >= _SNAPSHOT_EVERY_N_DELTAS:
            from lib.context_gc import reap  # Slice C: guaranteed closer (lifecycle-closed-or-expired)

            snap = snapshot_chain()  # Slice B: carries the shadow completeness commitment
            rep = reap()             # archive the now-subsumed delta prefix (recoverable, fail-closed)
            results.append((True,
                            f"Context chain: snapshot {snap.id[:16]} · {rep.get('live', '?')} live"
                            f" · {rep.get('archived_collapsed', 0)} folded"))  # report-split: folded ≠ current
        else:
            results.append((True, f"Context chain: delta {node.id[:16]} ({since}/{_SNAPSHOT_EVERY_N_DELTAS} to snapshot)"))
    except ImportError as e:
        # Surface once (not silent) so an incomplete chain-module deploy is visible
        # in /save output instead of indistinguishable from a clean no-op (AGORA C3).
        results.append((True, f"Context chain: skipped (module unavailable: {e})"))
    except Exception as e:  # noqa: BLE001 — chain write is best-effort, never aborts /save
        results.append((False, f"Context chain: {e}"))


def _signal_heartbeat() -> None:
    """Signal session heartbeat if session ID is available."""
    try:
        from lib.session_heartbeat import heartbeat as _hb
        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
        if session_id:
            _hb(session_id)
    except ImportError:
        pass


def _hypothesis_status_line() -> str | None:
    """One-line hypothesis status from the engine, or None if unavailable.

    Issue #2557: surface reconciliation state in /save output so PENDING
    hypotheses past their deadline are visible at every save, not silent.
    """
    try:
        from hypothesis_engine import save_status_line
        return save_status_line()
    except Exception:
        return None


def _report_save_results(
    results: list, cwd: str, encoded: str, achievement: str, elapsed: float,
) -> None:
    """Print structured save results and memory digest."""
    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)

    print("\nMemory Systems Saved:")
    for ok, msg in results:
        mark = "x" if ok else " "
        print(f"  [{mark}] {msg}")

    _report_subsystem_status(cwd, encoded)
    line = _hypothesis_status_line()
    if line:
        print(line)
    _memory_digest(encoded)

    print(f"\n/save complete: {passed} passed, {failed} failed ({elapsed:.1f}s)")
    print(f"Achievement: {achievement}")


def _init_save_context(achievement, budget):
    """Prepare save context: resolve achievement, paths, budget gates, context hash."""
    if not achievement:
        achievement = _detect_achievement()
    cwd, encoded, grip_index = _get_project_info()
    prev_hash = ""
    try:
        from lib.context_delta import get_latest_hash
        prev_hash = get_latest_hash()
    except ImportError:
        pass
    allowed = set(budget.save_subsystems()) if budget else None
    budget_mode = budget.mode if budget else "abundant"
    return achievement, cwd, encoded, grip_index, allowed, budget_mode, prev_hash


def _run_save_subsystems(results, allowed, budget_mode, achievement, cwd, grip_index, quiet):
    """Execute all save subsystems in dependency order."""
    _save_insights(results, allowed)
    _save_dna(results, allowed)

    if _should_run_subsystem("genome_evolve", allowed) and (LIB_DIR / "genome_session_evolve.py").exists():
        results.append(_run([GRIP_PYTHON, str(LIB_DIR / "genome_session_evolve.py")], "Genome session evolve", timeout=_GENOME_EVOLVE_TIMEOUT_S))

    # Hyphen→underscore filename regression: the previous `instance-serializer.py`
    # + `unified-memory-brief.py` paths were deleted on 2026-03-06 (commit
    # dcc92e559, "remove 35 hyphenated duplicate files from lib/") but this
    # call site was never migrated. The `.exists()` guard silently failed
    # → /save skipped both subsystems for ~2.5 months → ZERO new instance
    # JSONs landed in projects/{enc}/grip/ since 2026-04-29 → the unified-
    # session-memory architecture (kono_preload, holistic_synthesiser,
    # next_step_block, nextup_mesh, NextupHandler) was correctly injecting
    # a 3-week-stale latest-instance — the falsifier of V>>'s "plain
    # 'continue' tells GRIP the exact next step" goal. Fix is a 2-line
    # underscore swap; the underscore modules accept the SAME CLI verbatim
    # (smoke-test 2026-05-19: `instance_serializer.py auto --achievement X`
    # wrote 2d0598ec-... + updated index.json on the live substrate).
    # The pre-existing test below enumerates every LIB_DIR/X.py path in
    # this file and asserts each resolves to an existing module on origin/
    # main — the prime anchor that catches the next drift before it ships.
    if _should_run_subsystem("instance", allowed) and (LIB_DIR / "instance_serializer.py").exists():
        results.append(_run([GRIP_PYTHON, str(LIB_DIR / "instance_serializer.py"), "auto", "--achievement", achievement], "Instance serialize", timeout=_INSTANCE_SERIALIZE_TIMEOUT_S))

    if _should_run_subsystem("memory_brief", allowed) and (LIB_DIR / "unified_memory_brief.py").exists():
        # --skip-if-no-instance: a /save run from a project that is not yet
        # GRIP-initialised has no instance to inject — a clean no-op, not a
        # save failure. Without it the memory-brief step hard-fails (rc 1) and
        # inflates the pass/fail tally with a spurious "1 failed".
        results.append(_run([GRIP_PYTHON, str(LIB_DIR / "unified_memory_brief.py"), "--inject-memory", "--skip-if-no-instance", cwd], "Memory brief"))

    _save_kono_summary(results, allowed, grip_index, achievement, cwd, quiet, budget_mode)
    _save_session_sync(results, allowed)


def save_all(achievement: str | None = None, quiet: bool = False,
             budget=None) -> dict:
    """Orchestrate all subsystem saves. Returns status dict."""
    achievement, cwd, encoded, grip_index, allowed, budget_mode, prev_hash = _init_save_context(achievement, budget)
    results: list[tuple[bool, str]] = []
    t0 = time.time()

    _run_save_subsystems(results, allowed, budget_mode, achievement, cwd, grip_index, quiet)
    _save_context_delta(results, prev_hash)
    _save_context_chain(results, cwd)
    _signal_heartbeat()

    elapsed = time.time() - t0
    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)

    if not quiet:
        _report_save_results(results, cwd, encoded, achievement, elapsed)

    # V>>-ratified auto-broadcast (PR #3211 R2) — single-line caller via
    # call_from_caller in wa_principal_broadcast (kills the 5-line wrapper
    # the R1 review still flagged as duplicate; AGORA Speaker C R2 concern).
    try:
        from lib.wa_principal_broadcast import call_from_caller  # noqa: PLC0415
        call_from_caller("save", achievement)
    except Exception:  # noqa: BLE001 — only wraps the import itself
        pass

    return {"passed": passed, "failed": failed, "elapsed": round(elapsed, 1),
            "achievement": achievement}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Unified GRIP state persistence orchestrator.",
        prog="grip_save",
    )
    parser.add_argument(
        "achievement", nargs="*", default=None,
        help="Achievement description (auto-detected if omitted)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Minimal terminal output",
    )
    parser.add_argument(
        "--slack", metavar="CHANNEL_ID", default=None,
        help="Post digest to Slack channel (requires channel ID)",
    )
    parser.add_argument(
        "--mcp", action="store_true",
        help="Print MCP App digest as JSON to stdout",
    )
    return parser


def _handle_slack(channel_id: str) -> None:
    """Post digest to Slack via slack_canvas_digest (lazy import)."""
    try:
        from slack_canvas_digest import post_digest_to_slack
    except ImportError:
        print("Error: slack_canvas_digest module not found. "
              "Ensure lib/slack_canvas_digest.py exists.", file=sys.stderr)
        sys.exit(1)

    response = post_digest_to_slack(channel_id)
    if response.get("ok"):
        ts = response.get("ts", "")
        print(f"Digest posted to {channel_id} (ts: {ts})")
    else:
        error = response.get("error", "unknown")
        print(f"Slack API error: {error}", file=sys.stderr)
        sys.exit(1)


def _handle_mcp() -> None:
    """Print MCP App digest JSON (lazy import)."""
    try:
        from mcp_app_generator import generate_save_digest
    except ImportError:
        print("Error: mcp_app_generator module not found. "
              "Ensure lib/mcp_app_generator.py exists.", file=sys.stderr)
        sys.exit(1)

    digest = generate_save_digest()
    print(json.dumps(digest, indent=2))


if __name__ == "__main__":
    # This shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this the tool still runs and still records --
    # it just writes a forgeable demo-sealed record when a real seal was one
    # interpreter away (lib/pq_reexec.py).
    #
    # Imported as top-level `pq_reexec` off THIS checkout, never as
    # `lib.pq_reexec`: by now `lib` may already be bound to another tree's
    # package, and the import would miss the file or read another branch's copy.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    parser = build_parser()
    parsed = parser.parse_args()

    ach_parts = parsed.achievement
    ach = " ".join(ach_parts) if ach_parts else None
    result = save_all(achievement=ach, quiet=parsed.quiet)

    if parsed.slack:
        _handle_slack(parsed.slack)

    if parsed.mcp:
        _handle_mcp()

    sys.exit(0 if result["failed"] == 0 else 1)
