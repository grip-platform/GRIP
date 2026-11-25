#!/usr/bin/env python3
"""
KONO Preload — Pre-launch memory recall for warm session starts.

Runs BEFORE Claude Code launches. Queries KONO + instance state and writes
a fresh session-context.md so the session boots with full context at zero
in-session token cost.

Design:
  - Must complete in <1s (hard timeout: 2s)
  - Graceful degradation: if KONO/embeddings unavailable, falls back to
    instance index + session-context.md (already on disk)
  - Never crashes the launch — all errors produce warnings, not exits

Usage:
    python3 lib/kono_preload.py                      # Preload for ~/.claude
    python3 lib/kono_preload.py --project /path/to   # Preload for specific project
    python3 lib/kono_preload.py --cold                # No-op (for flag parity)
    python3 lib/kono_preload.py --json                # Output JSON instead of writing file

Exit codes:
    0 — preload succeeded (context written or already fresh)
    0 — --cold flag (no-op, always succeeds)
    1 — preload failed (warnings printed, launch should continue anyway)
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude. Local import bootstrap so the
# shim is reachable BEFORE we mutate sys.path below.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# C1 (AGORA #2910): import path_encoding ONCE at module load. The previous
# in-function `sys.path.insert(...)` ran on EVERY _read_instance_index /
# _read_latest_next_up call → cumulative sys.path pollution + per-call
# import churn. The guarded insert is idempotent; the import resolves once.
if str(CLAUDE_DIR / "lib") not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR / "lib"))
from next_step_block import (
    encode_project_path,
    read_instance_index as _read_instance_index,
    read_latest_next_up as _read_latest_next_up,
    next_step_lines as _next_step_lines,
)

# Warm start must not delay the session launch. This is the hard ceiling on
# the memory recall — now actually ENFORCED in-process (see
# _recall_kono_memories), not just checked after the fact.
HARD_TIMEOUT_S = 2.0



def _read_grip_instance_id() -> Optional[str]:
    """Read the current GRIP instance ID."""
    id_file = CLAUDE_DIR / ".grip-instance-id"
    if id_file.exists():
        try:
            return id_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
    return None


def _read_session_context() -> Optional[str]:
    """Read existing session-context.md if recent enough."""
    ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
    if not ctx_path.exists():
        return None

    try:
        age_hours = (time.time() - ctx_path.stat().st_mtime) / 3600
        if age_hours > 72:  # Stale after 3 days
            return None
        return ctx_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve_next_up(project: Path) -> dict:
    """Resolve the warm-start ``next_up`` dict: signed context-chain first, then
    BYTE-IDENTICAL legacy fallback (P6 — bootstrap-gated, human-merge only).

    Tries ``context_warmstart.resolve_next_up`` (the latest VERIFIED chain node's
    ``decision.next_step``). On ``None`` — absent chain, no verified next_step, or
    the chain module being unavailable — falls back to today's
    ``read_latest_next_up`` read of the unsigned instance index, producing output
    BYTE-IDENTICAL to pre-P6 behaviour. THE SAFETY CONTRACT: merging this changes
    NOTHING until a chain+verified-snapshot actually exist on disk. A BROKEN head
    that degrades to an older verified node emits one loud stderr line inside the
    resolver (never fabricates). Never raises — a launch is never crashed."""
    # Sibling-import convention (matches L48-50 / L186-187 / L236): ensure
    # CLAUDE_DIR/lib is on sys.path so the bare `from context_warmstart` resolves
    # under a PRODUCTION PYTHONPATH (cwd != ~/.claude/lib). Without this guard the
    # import raised ModuleNotFoundError, was swallowed by a broad `except
    # Exception: pass`, and the P6 chain path SILENTLY never fired in production.
    if str(CLAUDE_DIR / "lib") not in sys.path:
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
    try:
        from context_warmstart import resolve_next_up
    except ImportError:
        # A genuine ImportError of the chain module is a BUG, not a fallback
        # condition: surface it loudly (never swallow) so it gets fixed, while
        # still degrading to the byte-identical legacy read so a launch never
        # crashes (the P6 safety contract).
        print(
            "[GRIP WARM-START] context_warmstart import failed - P6 chain path "
            "INACTIVE, using legacy next_up (this is a BUG, not absent-chain "
            "fallback; check CLAUDE_DIR/lib is importable)",
            file=sys.stderr,
        )
        return _read_latest_next_up(project) or {}

    # The module imported: absent/empty CHAIN (resolver returns None / {}) is the
    # CORRECT byte-identical legacy fallback. Only a resolver RUNTIME failure is
    # caught here - never an ImportError (handled above), never silently.
    try:
        signed = resolve_next_up()
    except Exception:
        return _read_latest_next_up(project) or {}
    if signed:
        return signed
    return _read_latest_next_up(project) or {}


def _render_next_step(project_path: Optional[str] = None) -> str:
    """Render the fresh ``## Next Step`` block as a standalone string.

    Stdout surface for the SessionStart(clear) hook: the harness injects hook
    stdout into the new session's context synchronously, so a cleared session
    sees the fresh next_up even when it snapshotted rules/session-context.md
    before the rewrite landed (the file write alone loses that race)."""
    project = Path(project_path) if project_path else CLAUDE_DIR
    return "\n".join(_next_step_lines(_resolve_next_up(project))).rstrip()


def _read_holistic_kernel() -> Optional[str]:
    """R4: READ the pre-baked holistic kernel — NEVER synthesise here.

    Synthesis walks ~860 files and would blow the <1s/2s launch budget;
    it is out-of-band (on /save + the periodic mesh hook). This is exactly
    one bounded `read_text()`, guarded by the same staleness/graceful-
    degradation pattern as `_read_session_context`: missing or stale
    (>72h, i.e. no save/sync in 3 days) ⇒ return None and let preload fall
    back to today's behaviour. Never raises — a launch is never crashed."""
    kernel_path = CLAUDE_DIR / "rules" / "HOLISTIC.md"
    if not kernel_path.exists():
        return None
    try:
        age_hours = (time.time() - kernel_path.stat().st_mtime) / 3600
        if age_hours > 72:
            return None
        return kernel_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _build_recall_query(project_path: str, achievement: str = "") -> str:
    """Derive a recall query from real session anchors instead of a hardcoded
    generic phrase.

    Embedding similarity rewards topical overlap; the prior hardcoded
    ``"recent session context and active work"`` matched almost nothing in the
    corpus and surfaced low-score noise. Using the current ``next_up`` text +
    latest achievement yields high lexical AND semantic overlap with the
    memories that actually relate to ongoing work.

    Fail-open: if both anchors are empty (cold start, brand-new project), fall
    back to the prior hardcoded phrase so warm-start is never empty-query.
    """
    parts = []
    try:
        nxt = _read_latest_next_up(Path(project_path))
        if nxt and isinstance(nxt, dict):
            text = nxt.get("text") or nxt.get("next_up") or ""
            if text:
                parts.append(text[:200])
    except Exception:
        pass  # _read_latest_next_up is best-effort; never crash launch
    if achievement:
        parts.append(achievement[:200])
    return " ".join(parts).strip() or "recent session context and active work"


def _recall_kono_memories(
    project_path: str, limit: int = 11, timeout_s: float = HARD_TIMEOUT_S,
    achievement: str = "", substrate_factory: Optional[Callable[[], object]] = None,
) -> tuple:
    """Query KONO for recent high-fitness memories.

    Returns ``(memories, engine_status)``. ``engine_status`` is:

      * ``"ok"``                    — recall ran (the list may still be empty,
                                      which simply means nothing scored above
                                      the fitness floor for this folder)
      * ``"unavailable: <reason>"`` — the memory engine could not load. The
                                      most common cause is the ``apsw``
                                      dependency missing from the Python the
                                      launcher used. Previously this was
                                      swallowed by a bare ``except`` and shown
                                      as a misleading "0 memories"; now it is
                                      surfaced honestly so it can be fixed.
      * ``"slow"``                  — recall exceeded the warm-start time
                                      budget and was abandoned so the session
                                      launch is never delayed.
    """
    import threading

    def _default_factory():
        # Lazy import is load-bearing: it must raise INSIDE the worker so the
        # executor boundary converts it to an honest "unavailable:" status —
        # the module itself stays importable under an apsw-less system python3.
        sys.path.insert(0, str(CLAUDE_DIR / "lib"))
        from kono_substrate import MemorySubstrate

        return MemorySubstrate()

    make_substrate = substrate_factory or _default_factory

    def _do_recall():
        kono = make_substrate()
        try:
            return kono.recall(
                query=_build_recall_query(project_path, achievement),
                limit=limit,
                min_fitness=0.3,
                # min_score=0.10 drops hybrid-search residue (the 0.01 cluster
                # observed at warm-start when the query/document embedding
                # mismatched). Fail-open at <2 results lives in _finalize_results.
                min_score=0.10,
                project_path=project_path,
            )
        finally:
            kono.close()

    # DAEMON thread, not ThreadPoolExecutor (2026-08-18). The pool's worker is
    # NON-daemon and `concurrent.futures.thread` registers an atexit handler that
    # JOINS its workers, so `shutdown(wait=False)` does not release the process:
    # the interpreter blocks at exit until the recall finishes anyway. The old
    # comment here asserted the opposite ("an abandoned worker thread is
    # harmless") and was measured false — a probe of the identical construction
    # under this module's own interpreter printed its final line and was still
    # alive 6s later, killed only by the harness.
    #
    # That matters because this runs in the SessionStart path (settings.json,
    # timeout 10) and this module's contract is "must complete in <1s" and
    # "never delays the session launch". A slow recall against the 329 MB
    # embeddings.db therefore delayed every launch to the harness kill rather
    # than returning "slow" at the timeout as designed.
    #
    # A daemon thread is abandonable by construction: the interpreter does not
    # wait for it, so the timeout below is the real bound. The recall itself is
    # read-only (kono.close() runs in the worker's own finally), so abandoning
    # it cannot leave a half-written store.
    #
    # Falsifier: if a session ever observes a truncated or corrupt embeddings
    # read attributable to an abandoned recall, this must move to a subprocess
    # with terminate() rather than back to a joined pool.
    # `Future.result(timeout=...)` REJECTS a negative timeout; `Thread.join`
    # silently ignores it and returns at once, which would report "slow" where
    # the executor raised. Preserve the old contract rather than quietly
    # widening it (AGORA #4842).
    if timeout_s is not None and timeout_s < 0:
        raise ValueError(f"timeout_s must be non-negative, got {timeout_s!r}")
    # `None` would make Thread.join wait FOREVER, breaking the module's "<1s,
    # never delays the session launch" contract from the far side (AGORA #4842).
    # The default is already HARD_TIMEOUT_S; coerce an explicit None to it so no
    # caller can silently opt into an unbounded join.
    if timeout_s is None:
        timeout_s = HARD_TIMEOUT_S

    result: dict = {}

    def _worker() -> None:
        # `finally` records COMPLETION separately from success, and that
        # separation is load-bearing. `except Exception` deliberately does not
        # catch BaseException — swallowing KeyboardInterrupt/SystemExit inside a
        # worker is its own defect — but a BaseException would then leave both
        # "value" and "error" unset while the thread ends, and a caller reading
        # only those two keys would see a finished thread with no error and
        # report ("ok") for a run that was forcibly aborted. A false "ok" with
        # zero memories is exactly the silent-empty failure this module's
        # docstring exists to prevent. Both AGORA rounds are satisfied: the
        # handler stays narrow, and abnormal termination is still reported.
        try:
            value = _do_recall()
        except Exception as exc:  # noqa: BLE001 — reported to the caller below
            result["error"] = exc
        else:
            # `else`, NOT `finally`. A finally block runs while a BaseException
            # is unwinding the thread, so it would mark an aborted worker as
            # completed - measured: the SystemExit probe still reported ("ok").
            # Only the success path may record a value.
            result["value"] = value

    worker = threading.Thread(target=_worker, name="kono-preload-recall", daemon=True)
    worker.start()
    worker.join(timeout=timeout_s)
    if worker.is_alive():
        return ([], "slow")
    if "value" in result:
        return (result["value"], "ok")
    exc = result.get("error")
    if exc is None:
        # Neither branch ran: a BaseException (SystemExit, KeyboardInterrupt)
        # unwound the worker. Never call that "ok" - a false success with zero
        # rows is the silent-empty failure this module exists to prevent.
        return ([], "unavailable: recall worker terminated abnormally")
    if isinstance(exc, ModuleNotFoundError):
        return ([], f"unavailable: '{exc.name}' not installed in this Python")
    if isinstance(exc, ImportError):
        return ([], "unavailable: memory engine failed to import")
    return ([], f"unavailable: {type(exc).__name__}")


def _get_latest_generation(project_path: Path) -> tuple:
    """Resolve the display-ready generation label plus latest achievement.

    The generation label comes from the SHARED resolver
    (``grip_generation.resolve_generation``) — the exact same source the
    startup splash uses — so the splash line and the memory line can never
    again disagree (they previously showed "Gen 430" vs "Gen 484", or
    "Gen ?" vs "Gen 0"). Achievement/summary stay a local instance-index
    read because they are a different concern from the generation number.

    Returns:
        (gen_label: str, achievement: str, summary: str)
        where gen_label is "Gen N" or "new project (no GRIP history here)".
    """
    sys.path.insert(0, str(CLAUDE_DIR / "lib"))
    try:
        from grip_generation import resolve_generation

        gen_label = resolve_generation(str(project_path)).label
    except ImportError:
        gen_label = "new project"

    instance = _read_instance_index(project_path)
    if not instance:
        return (gen_label, "", "")

    lineage = instance.get("lineage", {})
    achievement = lineage.get("achievement", "")
    summary = instance.get("summary", "")
    return (gen_label, achievement, summary)



def _format_preload_context(
    project_path: Path,
    gen_label: str,
    achievement: str,
    kono_memories: list,
    holistic_kernel: Optional[str] = None,
    next_up: Optional[dict] = None,
) -> str:
    """Format the preloaded context as session-context.md content.

    ``gen_label`` is the display-ready label from the shared resolver
    ("Gen N" or "new project (no GRIP history here)") — already carries any
    "Gen " prefix, so it is interpolated verbatim (no extra "Gen ").
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    instance_id = _read_grip_instance_id() or "unknown"

    lines = [
        f"[GRIP WARM-START] Preloaded at {now}. "
        f"Instance: {instance_id[:8]} · {gen_label}.",
        "",
        "# Session Context (Preloaded)",
        "",
        f"Project: {project_path}",
        f"{gen_label} | Warm-started from KONO preload",
        "",
    ]

    # Lead with the synthesised holistic memory kernel — it IS the inherited
    # mental model. Absent/stale ⇒ section omitted (graceful degrade to
    # today's behaviour; never a crash, never an exit). R4: read-only.
    if holistic_kernel:
        lines.append("## Holistic Memory Kernel (synthesised, auto-inherited)")
        lines.append("")
        lines.append(holistic_kernel.rstrip())
        lines.append("")

    # Include latest achievement if available
    if achievement:
        # Truncate long achievements
        short = achievement[:200].split("\n")[0]
        lines.append(f"## Last Achievement")
        lines.append(f"- {short}")
        lines.append("")

    # Include KONO memories if available
    if kono_memories:
        lines.append("## Recalled Context (KONO)")
        for mem in kono_memories[:11]:
            content = mem.get("content", "")
            if content:
                # Truncate each memory to keep context compact
                short = content[:150].replace("\n", " ").strip()
                score = mem.get("score", 0)
                fitness = mem.get("fitness", 0)
                # Two-axis label — score is query similarity, fitness is the
                # evolutionary survival metric. Showing both makes the
                # `min_score` vs `min_fitness` filter behaviour visible to
                # the operator (was a silent debugging trap before).
                lines.append(f"- [s{score:.2f}/f{fitness:.2f}] {short}")
        lines.append("")

    # D3/D4 (V>>-steered POC): NO verbatim carry-forward of the previous
    # session-context.md. The old _extract_section("## Remaining Work" /
    # "## Key Technical Context") re-injected stale free-text every
    # warm-start with no recency gate — the perpetual drift anchor (the
    # 72h mtime gate never fired because warm-start rewrites the file).
    # Instead:
    #   * "## Next Step" is recomputed FRESH from the canonical latest-
    #     instance next_up (D2-correct max(serialized_at) selection), so
    #     "continue" reflects real state, never a frozen copy.
    #   * Durable operational facts are NOT duplicated here — canonical
    #     home is the always-loaded OCF kernel rules/operational-context.md.
    # existing_context param REMOVED (AGORA #2910): it was fully dead
    # post-D3/D4; the dead `_read_session_context()` call is also dropped
    # from preload() (one fewer hot-path read). Verbatim carry-forward of
    # the prior session-context.md is now structurally impossible — the
    # drift anchor cannot exist. _extract_section retained as a tested
    # utility for other modules.
    lines.extend(_next_step_lines(next_up))
    # C3 (AGORA #2910): only promise the pointer if the file truly exists
    # (it does in a real install — PARAMOUNT OCF kernel — but never emit a
    # broken promise on a partial/edge install).
    if (CLAUDE_DIR / "rules" / "operational-context.md").exists():
        lines.append(
            "Durable operational context: rules/operational-context.md "
            "(always-loaded; not duplicated here)."
        )
        lines.append("")

    return "\n".join(lines)


def _extract_section(text: str, header: str) -> Optional[str]:
    """Extract a markdown section from text (header to next ## or EOF)."""
    start = text.find(header)
    if start == -1:
        return None

    # Skip the header line itself
    content_start = text.find("\n", start)
    if content_start == -1:
        return None

    # Find next ## header or EOF
    next_header = text.find("\n## ", content_start + 1)
    if next_header == -1:
        section = text[content_start + 1:]
    else:
        section = text[content_start + 1:next_header]

    return section.strip() or None


def _write_or_print(context: str, result: dict, output_json: bool) -> dict:
    """Output preloaded context as JSON or write to session-context.md."""
    if output_json:
        result["context"] = context
        print(json.dumps(result, indent=2))
    else:
        ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
        try:
            # Always UTF-8. With no encoding Python uses the locale default,
            # which is cp1252 on Windows: the "·" in the Instance line was
            # written as byte 0xb7, so every UTF-8 reader of rules/ -- the KERN
            # meter (bin/grip-lexicon measure) included -- crashed on it, and a
            # character cp1252 cannot encode at all ("→") made this write raise.
            ctx_path.write_text(context, encoding="utf-8")
        except OSError as e:
            result["status"] = "error"
            result["error"] = str(e)
            print(f"[WARN] kono-preload: failed to write context: {e}", file=sys.stderr)
    return result


def preload(project_path: Optional[str] = None, output_json: bool = False) -> dict:
    """Run the preload pipeline. Returns status dict.

    Args:
        project_path: Project directory (default: ~/.claude)
        output_json: If True, print JSON to stdout instead of writing file

    Returns:
        {"status": "ok"|"cold"|"error",
         "generation_label": str,   # display-ready, shared with the splash
         "memories": int,           # count actually recalled
         "engine_status": str,      # "ok" | "unavailable: ..." | "slow"
         "ms": float}
    """
    start = time.monotonic()
    project = Path(project_path) if project_path else CLAUDE_DIR

    gen_label, achievement, _ = _get_latest_generation(project)
    holistic_kernel = _read_holistic_kernel()  # R4: one bounded read, measured below
    # P6: signed context-chain first, BYTE-IDENTICAL legacy fallback when absent/
    # broken (the safety contract — merging changes nothing until a chain exists).
    next_up = _resolve_next_up(project)  # always-fresh next step (P6 chain → legacy)
    # Recall is bounded in-process (ThreadPoolExecutor timeout), so the old
    # cosmetic "check elapsed AFTER the slow call" branch is gone — it could
    # never actually interrupt a slow recall, only report it post hoc.
    kono_memories, engine_status = _recall_kono_memories(
        str(project), achievement=achievement or "",
    )

    elapsed_ms = (time.monotonic() - start) * 1000

    context = _format_preload_context(
        project, gen_label, achievement, kono_memories,
        holistic_kernel, next_up=next_up,
    )
    result = {
        "status": "ok",
        "generation_label": gen_label,
        "memories": len(kono_memories),
        "engine_status": engine_status,
        "ms": round(elapsed_ms, 1),
    }
    return _write_or_print(context, result, output_json)


def _status_line(result: dict) -> str:
    """One plain-language line shown in the terminal at warm start.

    No GRIP jargon, no "[KONO]" / "Gen ?" / millisecond noise — a first-time
    viewer must understand WHAT happened, WHY, and HOW to act if it is wrong.
    """
    label = result.get("generation_label", "new project")
    engine = result.get("engine_status", "ok")
    count = result.get("memories", 0)

    if engine.startswith("unavailable"):
        reason = engine.split(":", 1)[1].strip() if ":" in engine else "engine could not load"
        body = (
            f"Memory: engine offline ({reason}). Past notes were NOT recalled "
            f"this session — run `grip doctor` to repair the memory engine"
        )
    elif engine == "slow":
        body = (
            "Memory: notes not preloaded — recall took too long and was set "
            "aside so the session starts without delay (the continuity block "
            "and Next Step were still written; saved notes are unaffected)"
        )
    elif count > 0:
        body = (
            f"Memory: ready — recalled {count} relevant "
            f"note{'s' if count != 1 else ''} from this folder's history"
        )
    else:
        body = (
            "Memory: ready — nothing to recall yet "
            "(this is the first GRIP session in this folder)"
        )
    return f"{body}. {label}."


def main():
    import argparse

    parser = argparse.ArgumentParser(description="KONO Preload — warm session starts")
    parser.add_argument("--project", help="Project directory (default: ~/.claude)")
    parser.add_argument("--cold", action="store_true", help="No-op (skip preload)")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr output")
    parser.add_argument(
        "--print-next-step", action="store_true",
        help="After preload, print the fresh '## Next Step' block to stdout "
             "(SessionStart hook context injection for /clear warm-starts)",
    )
    args = parser.parse_args()

    if args.cold:
        if not args.quiet:
            print(
                "Memory: cold start — past notes were not preloaded (use a warm "
                "start to recall this folder's history).",
                file=sys.stderr,
            )
        sys.exit(0)

    result = preload(project_path=args.project, output_json=args.json)

    if args.print_next_step and not args.json:
        print(_render_next_step(args.project))

    if not args.quiet and not args.json:
        print(_status_line(result), file=sys.stderr)

    sys.exit(0 if result["status"] in ("ok", "cold") else 1)


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

    main()
