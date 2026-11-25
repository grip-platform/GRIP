#!/usr/bin/env python3
"""
grip_recall.py -- Unified memory recall orchestrator.

Reads ALL GRIP memory subsystems in one shot. The inverse of grip_save.py.

Usage:
    python3 lib/grip_recall.py                        # Full recall
    python3 lib/grip_recall.py --quiet                # Single summary line
    python3 lib/grip_recall.py --quick                # Skip optional subsystems
    python3 lib/grip_recall.py --query "TUI epic"     # Targeted KONO search
    python3 lib/grip_recall.py --json                  # Machine-readable output
    python3 lib/grip_recall.py --startup /path/to/proj # Session-start mode

Called by: /recall command, startup hook (--startup)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# Add lib/ to path for shared imports
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from path_encoding import encode_project_path  # noqa: E402


def _import_memory_brief():
    """Import unified_memory_brief module.

    Now a standard import since the file was renamed from hyphenated to
    PEP 8 underscores. Kept as a function for error isolation — if the
    module has missing dependencies, callers get None instead of a crash.
    """
    try:
        import unified_memory_brief
        return unified_memory_brief
    except ImportError:
        return None


#: Truncation reasons, mirroring unified_memory_brief's public contract.
#: A single source of truth where the module is importable; the literals
#: otherwise, so this file stays readable standalone. Rendering treats
#: TRUNC_CONFIRMED_MORE as "8+ memories" and
#: TRUNC_PAGE_FULL_UNVERIFIED as "8 memories (possibly more)" -- a bare
#: "+" is an over-claim unless a further row was actually SEEN (AGORA
#: council, PR #5079).
try:
    from unified_memory_brief import (
        TRUNC_CONFIRMED_MORE,
        TRUNC_PAGE_FULL_UNVERIFIED,
    )
except ImportError:
    TRUNC_CONFIRMED_MORE = "confirmed_more"
    TRUNC_PAGE_FULL_UNVERIFIED = "page_full_unverified"


# Lazy imports with graceful fallback
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

GRIP_PYTHON = str(CLAUDE_DIR / "venv" / "bin" / "python3")
if not Path(GRIP_PYTHON).is_file():
    GRIP_PYTHON = "python3"


# ---------------------------------------------------------------------------
# Error isolation helper (DRY: all readers share this pattern)
# ---------------------------------------------------------------------------

def _safe_read(fn):
    """Decorator: wraps reader functions with error-isolated dict return."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return wrapper


# ---------------------------------------------------------------------------
# Individual subsystem readers (error-isolated)
# ---------------------------------------------------------------------------

@_safe_read
def _read_instance(project_path: str) -> dict[str, Any]:
    """Read latest instance state (gen, branch, messages, next_up)."""
    umb = _import_memory_brief()
    if not umb:
        return {"ok": False, "error": "unified-memory-brief not available"}
    # quiet=True (#3201): recall is a tolerant read; missing instance is a
    # legitimate "new project" case, not an error to surface to stderr.
    instance = umb.load_latest_instance(project_path, quiet=True)
    if not instance:
        return {"ok": False, "error": "no instance found"}

    lineage = instance.get("lineage", {})
    return {
        "ok": True,
        "generation": lineage.get("lineage_depth", 0),
        "branch": lineage.get("branch", "main"),
        "messages": instance.get("conversation", {}).get("message_count", 0),
        "next_up": instance.get("next_up"),
        "instance_id": instance.get("id", "unknown"),
        "_raw": instance,
    }


@_safe_read
def _read_achievements(instance_raw: dict | None) -> dict[str, Any]:
    """Extract achievement timeline from lineage chain."""
    if not instance_raw:
        return {"ok": False, "error": "no instance data"}

    umb = _import_memory_brief()
    if not umb:
        return {"ok": False, "error": "unified-memory-brief not available"}
    achievements = umb.extract_achievement_timeline(instance_raw)
    return {
        "ok": True,
        "count": len(achievements),
        "entries": achievements,
    }


@_safe_read
def _read_active_state(project_path: str) -> dict[str, Any]:
    """Read MEMORY.md active state."""
    encoded = encode_project_path(project_path)
    mem_path = PROJECTS_DIR / encoded / "memory" / "MEMORY.md"
    if not mem_path.exists():
        return {"ok": False, "error": "MEMORY.md not found"}

    lines = mem_path.read_text().splitlines()
    active = _parse_active_state(encoded)
    return {
        "ok": True,
        "line_count": len(lines),
        "items": active,
        "path": str(mem_path),
    }


@_safe_read
def _read_last_plan() -> dict[str, Any]:
    """Read last plan if recent (<24h) and same project."""
    plan_path = CLAUDE_DIR / "cache" / "last-plan.json"
    if not plan_path.exists():
        return {"ok": False, "error": "no cached plan"}

    data = json.loads(plan_path.read_text())
    plan_ts = data.get("timestamp", 0)
    age_hours = (time.time() - plan_ts) / 3600 if plan_ts else 999

    if age_hours > 24:
        return {"ok": False, "error": f"plan too old ({age_hours:.0f}h)"}

    return {
        "ok": True,
        "plan_id": data.get("plan_id", "unknown"),
        "name": data.get("name", "unnamed"),
        "age_hours": round(age_hours, 1),
        "step_count": len(data.get("steps", [])),
    }


@_safe_read
def _read_genome() -> dict[str, Any]:
    """Read genome pulse (generation, gene count, fitness, trend)."""
    summary = _read_genome_summary()
    if not summary:
        return {"ok": False, "error": "no genome data"}

    return {
        "ok": True,
        "generation": summary.get("generation", 0),
        "gene_count": summary.get("gene_count", 0),
        "fitness": summary.get("fitness", 0.0),
        "trend": summary.get("trend", ""),
        "top_genes": summary.get("top_genes", []),
    }


@_safe_read
def _read_kono(project_path: str, query: str | None = None) -> dict[str, Any]:
    """Recall KONO episodic memories + strategy transfers."""
    umb = _import_memory_brief()
    if not umb:
        return {"ok": False, "error": "unified-memory-brief not available"}

    if query:
        return _read_kono_targeted(project_path, query)

    memories, trunc_reason = umb.recall_permanent_memories_page(project_path)
    transfers = umb.recall_strategy_transfers(project_path)
    return {
        "ok": True,
        "memories": memories,
        # A PAGE length, not a total -- `memories_truncated` says which.
        # Printing this as a total is how /recall reported "8 memories"
        # against 379 matches (2026-08-25). The field carries the
        # truncation REASON (None / "confirmed_more" /
        # "page_full_unverified"), never a bare saturation flag.
        "memory_count": len(memories),
        "memories_truncated": trunc_reason,
        "transfers": transfers,
        "transfer_count": len(transfers),
    }


#: Page size for a targeted KONO query. Named so the saturation test
#: below cannot drift from the number actually requested.
_KONO_QUERY_LIMIT = 8


def _read_kono_targeted(
    project_path: str, query: str, *, substrate: Any = None
) -> dict[str, Any]:
    """Targeted KONO search by query (extracted for KISS).

    #3209 DIP: the MemorySubstrate may be injected (tests pass a fake). When not
    injected it is created AND owned here, so only an owned substrate is closed —
    an injected one's lifecycle belongs to the caller. Default path unchanged.
    """
    owns_substrate = substrate is None
    if owns_substrate:
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()
    try:
        # Over-fetch by ONE so saturation is confirmed rather than assumed.
        # Asking for exactly the page size cannot distinguish "there are more"
        # from "there are exactly this many", and the formatter would then
        # render "8+ memories" for a query matching exactly 8 -- the same
        # page-vs-total deception this module was fixed for, inverted.
        # (AGORA council, PR #5079.)
        #
        # Normalized through list(): the reader slices and sizes the result,
        # so a generator-returning substrate (valid for MemorySubstrate.recall
        # to grow into) must not raise TypeError where it previously worked.
        results = list(substrate.recall(
            query=query, limit=_KONO_QUERY_LIMIT + 1, min_fitness=0.1,
            expand_spatial=False, project_path=project_path,
        ))
    finally:
        if owns_substrate:
            substrate.close()
    memories = []
    for r in results[:_KONO_QUERY_LIMIT]:
        content = r.get("content", "")
        if len(content) > 200:
            content = content[:197] + "..."
        memories.append({
            "content": content,
            "fitness": r.get("fitness", 0),
            "is_permanent": r.get("is_permanent", False),
        })
    return {
        "ok": True,
        "memories": memories,
        # Same contract as the untargeted path: a page, plus the truncation
        # REASON. `>` against an over-fetched request means a further row
        # was actually seen, never merely that the page filled -- so the
        # only reason this path can emit is CONFIRMED more, and a bare "+"
        # is never rendered for an exactly-full page.
        "memory_count": len(memories),
        "memories_truncated": (
            TRUNC_CONFIRMED_MORE if len(results) > _KONO_QUERY_LIMIT else None
        ),
        "transfers": [],
        "transfer_count": 0,
        "query": query,
    }


@_safe_read
def _read_history(project_path: str) -> dict[str, Any]:
    """Read recent decisions from session history JSONL."""
    encoded = encode_project_path(project_path)
    proj_dir = PROJECTS_DIR / encoded
    if not proj_dir.exists():
        return {"ok": False, "error": "project dir not found"}

    jsonl_files = sorted(
        proj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    jsonl_files = [f for f in jsonl_files if not f.name.startswith("agent-")]

    if not jsonl_files:
        return {"ok": False, "error": "no session files"}

    latest = jsonl_files[0]
    entry_count = sum(1 for _ in open(latest))
    return {
        "ok": True,
        "session_file": latest.name,
        "entry_count": entry_count,
        "session_count": len(jsonl_files),
    }


# ---------------------------------------------------------------------------
# Differential (task-relevant) recall
# ---------------------------------------------------------------------------

def _detect_task_type(description: str) -> str:
    """Infer task type from description for dimension routing."""
    dl = description.lower()
    _patterns = {
        "debugging": ["debug", "fix", "error", "traceback", "broken", "crash", "fail"],
        "architecture": ["design", "architect", "refactor", "structure", "pattern"],
        "review": ["review", "audit", "check", "verify", "validate"],
        "research": ["research", "investigate", "explore", "analyse", "understand"],
        "implementation": ["implement", "build", "create", "add", "write", "code"],
    }
    for task_type, keywords in _patterns.items():
        if any(kw in dl for kw in keywords):
            return task_type
    return "general"


def _task_type_to_dimensions(task_type: str) -> list[int] | None:
    """Map task type to preferred KONO dimensions for focused recall."""
    _dim_map = {
        "debugging": [4, 7],      # procedural + episodic
        "architecture": [1, 10],  # factual + emergent
        "review": [4, 1],        # procedural + factual
        "research": [1, 10, 7],  # factual + emergent + episodic
        "implementation": [4, 7], # procedural + episodic
    }
    return _dim_map.get(task_type)


def recall_relevant(
    task_description: str, project_path: str | None = None,
) -> dict[str, Any]:
    """Task-relevant differential recall with context-aware routing.

    Combines four sources:
    1. KONO semantic search (dimension-routed by task type)
    2. Warm memory layer (compressed recent context)
    3. Delta journal (recent decisions matching task keywords)
    4. Genome genes filtered by domain fitness overlap

    Returns only context relevant to the given task, not everything.
    """
    project_path = project_path or os.getcwd()
    task_type = _detect_task_type(task_description)
    results: dict[str, Any] = {"task": task_description, "task_type": task_type}
    results["kono"] = _read_kono(project_path, query=task_description)
    results["warm_memory"] = _read_warm_memory(query=task_description)
    results["deltas"] = _read_recent_deltas(task_description)
    results["genome_genes"] = _read_relevant_genes(task_description)
    return results


@_safe_read
def _read_recent_deltas(
    task_description: str, max_age_hours: float = 72,
) -> dict[str, Any]:
    """Search delta journal for recent context changes."""
    from lib.context_delta import get_deltas_since
    cutoff = time.time() - (max_age_hours * 3600)
    deltas = get_deltas_since(cutoff, limit=20)
    return {"ok": True, "count": len(deltas), "deltas": deltas}


@_safe_read
def _read_relevant_genes(task_description: str) -> dict[str, Any]:
    """Filter genome genes by task keyword overlap."""
    genome_path = CLAUDE_DIR / "cache" / "genome.json"
    if not genome_path.exists():
        return {"ok": False, "error": "no genome cache"}

    data = json.loads(genome_path.read_text())
    genes = data.get("genes", {})
    task_words = set(task_description.lower().split())
    relevant = []
    for gene_name, gene_data in genes.items():
        gene_words = set(
            gene_name.lower().replace("-", " ").replace("_", " ").split()
        )
        overlap = task_words & gene_words
        if overlap:
            relevant.append({
                "gene": gene_name,
                "fitness": gene_data.get("fitness", 0),
                "matched_words": sorted(overlap),
            })
    relevant.sort(key=lambda x: x["fitness"], reverse=True)
    return {"ok": True, "count": len(relevant), "genes": relevant[:10]}


@_safe_read
def _read_warm_memory(
    query: str | None = None, *, layer: Any = None
) -> dict[str, Any]:
    """Read from the compressed warm memory layer (recent session history).

    #3209 DIP: the WarmMemoryLayer may be injected (tests pass a fake). When not
    injected it is created here. Default path unchanged.
    """
    if layer is None:
        from warm_memory import WarmMemoryLayer
        layer = WarmMemoryLayer()
    stats = layer.get_stats()

    if stats["total"] == 0:
        return {"ok": True, "count": 0, "entries": [], "stats": stats}

    entries = layer.recall(query, limit=3) if query else layer.recall("", limit=3)
    return {
        "ok": True,
        "count": len(entries),
        "entries": entries,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def _read_active_sessions() -> dict[str, Any]:
    """Live-session count via the heartbeat substrate (never budget-gated)."""
    try:
        from lib.session_heartbeat import get_active_sessions
        sessions = get_active_sessions()
    except ImportError:
        # The call stays inside the guard: substrate code may itself raise
        # ImportError lazily (and tests pin that contract).
        return {"ok": False, "error": "heartbeat not available"}
    return {"ok": True, "count": len(sessions), "sessions": sessions}


def _recall_one(
    reader,
    *,
    budget_key: str,
    optional: bool,
    budget_gated: bool,
    quick: bool,
    is_allowed,
    budget_mode: str,
) -> dict[str, Any]:
    """Gate one subsystem read (#3209 helper).

    Skip-string precedence matches the prior inline cascade exactly: a
    budget denial reports the budget reason even under --quick; an allowed
    optional subsystem under --quick reports the --quick reason; mandatory
    subsystems ignore --quick entirely.
    """
    if budget_gated and not is_allowed(budget_key):
        return {"ok": False, "error": f"skipped (budget: {budget_mode})"}
    if optional and quick:
        return {"ok": False, "error": "skipped (--quick)"}
    return reader()


def recall_all(
    project_path: str | None = None,
    quick: bool = False,
    query: str | None = None,
    budget=None,
) -> dict[str, Any]:
    """Orchestrate all subsystem reads. Returns structured dict.

    Args:
        project_path: Project directory (defaults to CWD).
        quick: Skip optional subsystems (plan, history, KONO).
        query: Targeted KONO semantic search query.
        budget: Optional TokenBudget — gates which subsystems are recalled.
    """
    project_path = project_path or os.getcwd()
    t0 = time.time()
    results: dict[str, Any] = {"project_path": project_path}

    # Budget-aware subsystem gating
    allowed = set(budget.recall_subsystems()) if budget else None

    def _is_allowed(subsystem: str) -> bool:
        if allowed is None:
            return True
        return subsystem in allowed

    budget_mode = budget.mode if budget else "abundant"

    def _instance_raw() -> Any:
        inst = results["instance"]
        return inst.get("_raw") if inst["ok"] else None

    # Dispatch table (#3209, CC 19 -> ~3): one row per subsystem —
    # (result_key, budget_key, optional, budget_gated, reader). Readers are
    # thunks so the achievements row can depend on the instance row already
    # being in `results` (rows run strictly in order). budget_key differs from
    # result_key only for genome ("genome_pulse" — the budget vocabulary).
    # active_sessions was never budget-gated; that exemption is preserved.
    plan: list[tuple[str, str, bool, bool, Any]] = [
        ("instance", "instance", False, True,
         lambda: _read_instance(project_path)),
        ("achievements", "achievements", False, True,
         lambda: _read_achievements(_instance_raw())),
        ("active_state", "active_state", False, True,
         lambda: _read_active_state(project_path)),
        ("last_plan", "last_plan", True, True, _read_last_plan),
        ("genome", "genome_pulse", False, True, _read_genome),
        ("kono", "kono", True, True,
         lambda: _read_kono(project_path, query=query)),
        ("history", "history", True, True,
         lambda: _read_history(project_path)),
        ("active_sessions", "active_sessions", True, False,
         _read_active_sessions),
        ("warm_memory", "warm_memory", True, True,
         lambda: _read_warm_memory(query)),
    ]
    for result_key, budget_key, optional, budget_gated, reader in plan:
        results[result_key] = _recall_one(
            reader,
            budget_key=budget_key,
            optional=optional,
            budget_gated=budget_gated,
            quick=quick,
            is_allowed=_is_allowed,
            budget_mode=budget_mode,
        )

    elapsed = time.time() - t0
    ok_count = sum(1 for k in ("instance", "achievements", "active_state",
                                "last_plan", "genome", "kono", "history",
                                "active_sessions", "warm_memory")
                   if results[k]["ok"])
    results["_meta"] = {
        "elapsed": round(elapsed, 1),
        "ok_count": ok_count,
        "total": 9,
        "budget_mode": budget_mode,
    }

    return results


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_kono_line(kono: dict) -> str:
    """The KONO summary payload, honest about paging.

    `memory_count` is a PAGE length. `memories_truncated` carries the
    truncation REASON, and the two reasons render differently:
    - "confirmed_more": a further row was actually seen -> "8+ memories".
    - "page_full_unverified": the raw fetch saturated but no extra row was
      confirmed -> "8 memories (possibly more)". A bare "+" here would be
      the same over-claim the targeted path's tests forbid.
    - anything else truthy (legacy callers, bools): rendered as the
      unverified form -- never as a bare "+", which claims a seen row.
    /recall reported "8 memories" against 379 matches until 2026-08-25.
    """
    parts = []
    if kono.get("memory_count"):
        reason = kono.get("memories_truncated")
        if reason == TRUNC_CONFIRMED_MORE:
            # ", " rather than " + " as the separator: with a saturated page the
            # old form rendered "8+ memories + 2 transfers", where the first "+"
            # means "at least" and the second is a list separator. Two meanings
            # for one token in a line whose whole job is to be read at a glance.
            more = "+"
            caveat = ""
        else:
            more = ""
            caveat = " (possibly more)" if reason else ""
        parts.append(f"{kono['memory_count']}{more} memories{caveat}")
    if kono.get("transfer_count"):
        parts.append(f"{kono['transfer_count']} transfers")
    return ", ".join(parts) if parts else "empty"

def _format_checklist_item(data: dict, label: str, detail_fn) -> str:
    """Format a single checklist item ([x] or [ ])."""
    if data["ok"]:
        return f"  [x] {label} ({detail_fn(data)})"
    return f"  [ ] {label} ({data['error']})"


def _format_checklist(results: dict[str, Any]) -> list[str]:
    """Build the checklist section of the full output."""
    lines = ["Memory Recalled:"]
    lines.append(_format_checklist_item(
        results["instance"], "Instance state",
        lambda d: f"Gen {d['generation']}, {d['messages']} msgs"))
    lines.append(_format_checklist_item(
        results["achievements"], "Achievements",
        lambda d: f"{d['count']} entries"))
    lines.append(_format_checklist_item(
        results["active_state"], "Active state",
        lambda d: f"MEMORY.md, {d['line_count']} lines"))
    lines.append(_format_checklist_item(
        results["last_plan"], "Last plan",
        lambda d: f"{d['name']}, {d['age_hours']}h ago"))
    lines.append(_format_checklist_item(
        results["genome"], "Genome pulse",
        lambda d: f"gen {d['generation']}, {d['gene_count']} genes, {d['fitness']:.3f}"))

    kono = results["kono"]
    if kono["ok"]:
        lines.append(f"  [x] KONO recall ({_format_kono_line(kono)})")
    else:
        lines.append(f"  [ ] KONO recall ({kono['error']})")

    lines.append(_format_checklist_item(
        results["history"], "Recent history",
        lambda d: f"{d['entry_count']} entries, {d['session_count']} sessions"))

    warm = results.get("warm_memory", {"ok": False, "error": "not available"})
    lines.append(_format_checklist_item(
        warm, "Warm memory",
        lambda d: f"{d['count']} entries, {d.get('stats', {}).get('total', 0)} total"))

    return lines


def _kono_match_identifier(content: str) -> str:
    """Extract an identifying token for a queried KONO memory.

    Surfaces the leading ``[bracketed-identifier]`` (e.g. ``[session_summary]``,
    ``[feedback_no_litellm]``) when present, else the first few words. This is
    the user-facing name/title of the matched memory — never a genome gene.
    """
    content = content.strip()
    if content.startswith("[") and "]" in content:
        return content[1:content.index("]")].strip()
    words = content.split()
    return " ".join(words[:6]) if words else "(untitled memory)"


def _format_kono_matches(kono: dict[str, Any]) -> list[str]:
    """Render queried KONO hits (identifier + snippet). Empty list if no query.

    Gated on ``kono.get("query")`` so non-query recall stays byte-identical —
    only ``_read_kono_targeted`` sets ``query``.
    """
    if not (kono.get("ok") and kono.get("query")):
        return []
    memories = kono.get("memories") or []
    if not memories:
        return []
    out = [f"  KONO Matches for “{kono['query']}”"]
    for m in memories:
        content = (m.get("content") or "").strip()
        ident = _kono_match_identifier(content)
        snippet = content[:100].replace("\n", " ")
        if len(content) > 100:
            snippet += "…"
        out.append(f"  ▸ {ident}  ·  {snippet}")
    out.append("")
    return out


def _format_digest(results: dict[str, Any]) -> list[str]:
    """Build the digest section of the full output."""
    lines = []
    W = 57
    lines.append("")
    lines.append("\u2500\u2500 Memory Recall " + "\u2500" * (W - 16))

    inst = results["instance"]
    if inst["ok"]:
        ach_entries = results["achievements"].get("entries", [])
        latest_ach = ach_entries[0]["achievement"] if ach_entries else "No recent work"
        lines.extend([
            "  Continuity",
            f"  . Gen {inst['generation']}: {latest_ach}",
            f"  . Branch: {inst['branch']} | {inst['messages']} messages",
            "",
        ])

    active = results["active_state"]
    if active["ok"] and active.get("items"):
        lines.append("  Active Work")
        lines.extend(f"  . {item}" for item in active["items"])
        lines.append("")

    plan = results["last_plan"]
    if plan["ok"]:
        lines.extend(["  Last Plan", f"  . {plan['name']} ({plan['age_hours']}h ago)", ""])

    lines.extend(_format_kono_matches(results.get("kono", {})))

    genome = results["genome"]
    if genome["ok"]:
        top = "  ".join(f"\u25b8 {g}" for g in genome.get("top_genes", []))
        lines.append(f"  Genome  gen {genome['generation']}"
                      f" \u00b7 {genome['gene_count']} genes"
                      f" \u00b7 {genome['fitness']:.3f} {genome['trend']}")
        if top:
            lines.append(f"  {top}")
        lines.append("")

    lines.append("\u2500" * W)
    return lines


def _format_full(results: dict[str, Any]) -> str:
    """Structured terminal output with checkmarks."""
    lines = _format_checklist(results)
    lines.extend(_format_digest(results))

    meta = results["_meta"]
    lines.append(f"\n/recall complete: {meta['ok_count']}/{meta['total']} "
                  f"subsystems ({meta['elapsed']}s)")

    return "\n".join(lines)


def _format_quiet(results: dict[str, Any]) -> str:
    """Single summary line for hooks/background."""
    meta = results["_meta"]
    inst = results["instance"]
    gen = inst.get("generation", "?") if inst["ok"] else "?"
    return f"recall: {meta['ok_count']}/{meta['total']} ok, Gen {gen} ({meta['elapsed']}s)"


def _format_json(results: dict[str, Any]) -> str:
    """Machine-readable JSON output."""
    # Strip internal _raw instance data from JSON output
    output = {}
    for k, v in results.items():
        if isinstance(v, dict):
            output[k] = {ik: iv for ik, iv in v.items() if not ik.startswith("_")}
        else:
            output[k] = v
    return json.dumps(output, indent=2, default=str)


def _extract_achievement_keywords(achievements: list) -> list[str]:
    """Extract keywords from achievements for diverse KONO recall."""
    _stop = {"feat:", "fix:", "chore:", "docs:", "refactor:", "test:",
             "the", "for", "with", "from", "that", "this", "into"}
    kw = []
    for a in achievements[:4]:
        words = [w for w in a["achievement"].split()
                 if len(w) > 4 and w.lower() not in _stop]
        kw.extend(words[:2])
    return kw


def _write_rules_if_stale(umb, instance, achievements, kono_memories, project_path: str) -> None:
    """Write rules/session-context.md unless recently modified (<5min)."""
    rules_file = Path.home() / ".claude" / "rules" / "session-context.md"
    if rules_file.exists() and (time.time() - rules_file.stat().st_mtime) < 300:
        return
    strategy_transfers = umb.recall_strategy_transfers(project_path)
    rules_content = umb.format_rules_brief(
        instance, achievements, kono_memories,
        project_path=project_path,
        strategy_transfers=strategy_transfers,
    )
    umb.write_rules_file(rules_content)


@_safe_read
def _format_startup(results: dict[str, Any], project_path: str) -> str:
    """Session-start mode: write rules + MEMORY.md + output unified-memory-brief XML."""
    umb = _import_memory_brief()
    if not umb:
        return ""

    # quiet=True (#3201): session-start recall tolerates missing instance
    # (new project / first session); empty-return is the silent-skip contract.
    instance = umb.load_latest_instance(project_path, quiet=True)
    if not instance:
        return ""

    achievements = umb.extract_achievement_timeline(instance)
    achievement_kw = _extract_achievement_keywords(achievements)

    kono_memories = umb.recall_permanent_memories(
        project_path, achievement_keywords=achievement_kw
    )
    kono_memories = umb.deduplicate(achievements, kono_memories)

    _write_rules_if_stale(umb, instance, achievements, kono_memories, project_path)

    compact = umb.format_compact_brief(instance, achievements)
    umb.inject_into_memory(project_path, compact)

    return umb.format_brief(instance, achievements, kono_memories)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Unified GRIP memory recall orchestrator.",
        prog="grip_recall",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Single summary line output",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip optional subsystems (plan, history, KONO)",
    )
    parser.add_argument(
        "--query", metavar="TOPIC", default=None,
        help="Targeted KONO semantic search",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--startup", metavar="PROJECT_PATH", default=None,
        help="Session-start mode: full recall + write rules + update MEMORY.md",
    )
    parser.add_argument(
        "--diff", metavar="TASK", default=None,
        help="Differential recall: return only task-relevant context",
    )
    parser.add_argument(
        "--instant", metavar="QUERY", default=None,
        help="Direct KONO search, no subsystem overhead (<500ms)",
    )
    parser.add_argument(
        "query_words", nargs="*", default=[],
        help="Bare query auto-routes to --instant when KONO is available",
    )
    return parser


def _instant_kono(query: str, max_attempts: int = 3) -> dict[str, Any]:
    """Direct KONO search with convergent query refinement.

    Target: <1s. Up to max_attempts KONO calls with query variation.
    """
    t0 = time.time()
    try:
        sys.path.insert(0, os.path.join(os.environ.get("HOME", ""), ".claude", "lib"))
        from grip_first_common import convergent_kono_search

        is_grip, score, etype, snippet, trail = convergent_kono_search(
            query, max_attempts=max_attempts,
        )

        results = []
        if is_grip is True:
            results.append({
                'score': round(score, 4),
                'type': etype,
                'snippet': snippet,
            })
        elif is_grip is None:
            return {
                'ok': False, 'query': query, 'results': [],
                'elapsed_ms': int((time.time() - t0) * 1000),
                'query_trail': trail, 'error': 'KONO unavailable',
            }

        return {
            'ok': bool(results),
            'query': query,
            'results': results,
            'elapsed_ms': int((time.time() - t0) * 1000),
            'query_trail': trail,
        }
    except Exception as e:
        return {
            'ok': False, 'query': query, 'results': [],
            'elapsed_ms': int((time.time() - t0) * 1000),
            'query_trail': [], 'error': str(e),
        }


if __name__ == "__main__":
    parser = build_parser()
    parsed = parser.parse_args()

    # Bare positional query → auto-route to instant KONO (Recall First)
    if parsed.query_words and not any([parsed.query, parsed.instant, parsed.diff]):
        query = " ".join(parsed.query_words)
        result = _instant_kono(query)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)

    # Instant KONO mode — explicit flag (fastest path)
    if parsed.instant:
        result = _instant_kono(parsed.instant)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)

    # Differential recall mode
    if parsed.diff:
        diff_results = recall_relevant(parsed.diff, project_path=os.getcwd())
        print(json.dumps(diff_results, indent=2, default=str))
        sys.exit(0)

    # Startup mode: delegate to unified-memory-brief functions
    if parsed.startup:
        output = _format_startup(
            recall_all(project_path=parsed.startup),
            parsed.startup,
        )
        if output:
            print(output)
            sys.exit(0)
        else:
            sys.exit(1)

    # Standard recall modes
    project_path = os.getcwd()
    results = recall_all(
        project_path=project_path,
        quick=parsed.quick,
        query=parsed.query,
    )

    if parsed.json_output:
        print(_format_json(results))
    elif parsed.quiet:
        print(_format_quiet(results))
    else:
        print(_format_full(results))

    sys.exit(0)
