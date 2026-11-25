#!/usr/bin/env python3
"""
Unified Memory Brief - Combines instance JSON, KONO, and lineage into
a single injection at session start.

Replaces separate inject_instance_next_up + recall_project_memories flows.

Usage:
    python3 unified-memory-brief.py [--inject-memory] [--skip-if-no-instance] <project_path>

Output: Structured markdown wrapped in <unified-memory-brief> tags.
When --inject-memory is passed, also writes a compact brief into
the project's memory/MEMORY.md for persistent context injection.
Exit 0 on success; exit 1 on no instance found (default). With
--skip-if-no-instance, a missing instance is a clean no-op (exit 0)
instead — for best-effort callers (e.g. grip_save) where a project that
is not yet GRIP-initialised is an expected, non-failure condition.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Imported from _LIB, BEFORE CLAUDE_DIR/lib goes on the path below, so a
# checkout always partitions with its own copy rather than the shared tree's
# (rules/shared-tree-is-not-canonical.md). Deliberately not guarded by
# try/except: a memory writer that has silently lost its pin-protection must
# fail loudly, not quietly resume evicting binding records.
from binding_memory import partition_pinned  # noqa: E402

CLAUDE_DIR = get_claude_dir()
PROJECTS_DIR = CLAUDE_DIR / "projects"

sys.path.insert(0, str(CLAUDE_DIR / "lib"))
from path_encoding import encode_project_path  # noqa: E402
from memory_lock import locked_write  # noqa: E402
import next_step_block  # noqa: E402


def _emit_no_instance(reason: str, *, quiet: bool = False, **details: Any) -> None:
    """CBC funnel for every None-return out of load_latest_instance.

    Issue #3201: every failure path through ``load_latest_instance`` previously
    returned a bare ``None`` with no diagnostic, which surfaced through the
    ``grip_save.py::_run`` wrapper as ``[ ] Memory brief: `` — a checkbox with
    empty text, looking like a swallowed error. Same anti-pattern shape as the
    hyphen-refactor silent-skip fixed in PR #2706.

    This helper is the structural choke point: adding a new ``return None``
    branch to ``load_latest_instance`` without a reason argument is a syntax
    error, and the helper unconditionally writes the structured diagnostic to
    stderr unless the caller opts into ``quiet=True`` (used by background
    recall paths where missing instance is a legitimate expected outcome,
    e.g. ``grip_recall.py``).

    The CLI ``main()`` invocation (issue #3201's reproduction surface) keeps
    the default ``quiet=False`` so the operator always sees the reason.
    """
    if not quiet:
        parts = [f"[memory-brief] no instance ({reason})"]
        for key, value in details.items():
            parts.append(f"{key}={value!r}")
        # ASCII `|` separator (not `·` U+00B7) — terminal-encoding-agnostic.
        # AGORA #3207 round 2 Speaker C concern B mitigation: removes the
        # cp1252/legacy-encoding edge case where a non-ASCII separator could
        # raise UnicodeEncodeError on print. Anchored by test_emit_no_instance
        # _uses_ascii_separator in tests/test_unified_memory_brief.py.
        print(" | ".join(parts), file=sys.stderr)
    return None


def load_latest_instance(
    project_path: str, *, quiet: bool = False
) -> Optional[Dict[str, Any]]:
    """Load the latest instance JSON for a project.

    Returns the parsed instance dict on success, or ``None`` on any failure
    (missing index, corrupt JSON, empty instances list, missing instance_id,
    missing per-instance file, corrupt per-instance file). Every None-return
    path emits a structured diagnostic to stderr via :func:`_emit_no_instance`
    unless ``quiet=True`` (callers that legitimately tolerate missing
    instances — e.g. background recall on new projects — opt into silence).
    """
    encoded = encode_project_path(project_path)
    grip_dir = PROJECTS_DIR / encoded / "grip"
    index_file = grip_dir / "index.json"

    if not index_file.exists():
        return _emit_no_instance(
            "index.json missing", quiet=quiet,
            project=project_path, encoded=encoded, index_file=str(index_file),
        )

    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # AGORA #3207 Speaker C: UnicodeDecodeError is a ValueError subclass,
        # NOT an OSError subclass, so the prior (JSONDecodeError, OSError) tuple
        # let invalid-encoding index files reach an uncaught traceback. Listing
        # it explicitly closes the structural gap; cause field carries the
        # exception MESSAGE (not just class) so the operator sees the line/col
        # or invalid-byte detail without re-running.
        return _emit_no_instance(
            "index.json unreadable", quiet=quiet,
            project=project_path, index_file=str(index_file),
            cause=f"{type(e).__name__}: {e}",
        )

    instances = index.get("instances", [])
    if not instances:
        return _emit_no_instance(
            "index.json has empty instances", quiet=quiet,
            project=project_path, index_file=str(index_file),
        )

    # #3210: when EVERY entry lacks a usable serialized_at there is no
    # chronological signal — the prior max() then returned an insertion-order-
    # arbitrary entry and masked the data-quality problem downstream as
    # "latest entry has no instance_id". Surface it as a distinct, actionable
    # diagnostic. A single timestampless instance is unambiguous (it IS the only
    # candidate), so only >=2 all-empty entries trip this.
    dict_instances = [i for i in instances if isinstance(i, dict)]
    if len(dict_instances) > 1 and not any(
        i.get("serialized_at") for i in dict_instances
    ):
        return _emit_no_instance(
            "all instance timestamps are empty — cannot pick latest", quiet=quiet,
            project=project_path, index_file=str(index_file),
            instance_count=len(instances),
        )

    # D2 contract (council §3.3): max(serialized_at), not positional-last —
    # same bug #2910 fixed in kono_preload; concurrent/retried saves append out
    # of wall-clock order so instances[-1] != newest. DRY (#3210): route through
    # next_step_block.select_latest_instance — the ONE D2-correct selector —
    # which also filters non-dict entries (the hand-rolled max() would
    # AttributeError on a stray non-dict element).
    latest = next_step_block.select_latest_instance(instances)
    if latest is None:
        return _emit_no_instance(
            "no valid instance entry in index", quiet=quiet,
            project=project_path, index_file=str(index_file),
        )
    latest_id = latest.get("instance_id")
    if not latest_id:
        return _emit_no_instance(
            "latest entry has no instance_id", quiet=quiet,
            project=project_path,
        )

    inst_file = grip_dir / f"{latest_id}.json"
    if not inst_file.exists():
        return _emit_no_instance(
            "per-instance file missing", quiet=quiet,
            project=project_path, instance_id=latest_id, inst_file=str(inst_file),
        )

    try:
        return json.loads(inst_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # Same CBC-closure as the index.json branch above (AGORA #3207 Speaker C).
        return _emit_no_instance(
            "per-instance file unreadable", quiet=quiet,
            project=project_path, instance_id=latest_id, inst_file=str(inst_file),
            cause=f"{type(e).__name__}: {e}",
        )


def validate_achievement(text: str) -> str:
    """Reject achievements that are clearly corrupted transcript fragments."""
    if not text:
        return "Session checkpoint"

    # Reject if contains markdown table fragments
    if '|' in text and text.count('|') > 1:
        return "Session checkpoint"

    # Reject if contains JSON/code artifacts
    if any(frag in text for frag in ['{}', '[]', '",', "',", '))', '`,']):
        return "Session checkpoint"

    # Reject if contains path fragments (transcript leakage)
    if '/Users/' in text or '~/.claude/' in text:
        return "Session checkpoint"

    # Reject if too short after prefix (e.g., just "feat: -")
    prefix_match = re.match(r'^(feat|fix|chore|refactor|docs|test|perf|style):\s*', text)
    if prefix_match and len(text) - len(prefix_match.group(0)) < 3:
        return "Session checkpoint"

    return text.strip()


def extract_achievement_timeline(
    instance: Dict[str, Any], max_achievements: int = 12
) -> List[Dict[str, Any]]:
    """Extract achievement timeline from lineage, newest first."""
    lineage_info = instance.get("lineage", {})
    lineage_chain = lineage_info.get("lineage", [])

    # Boilerplate that records that a session happened, not what it did.
    #
    # "Auto-checkpoint at N% context" is by far the dominant case and was missing
    # here: measured 2026-07-31 across the live lineage, 6,745 of 6,973 entries
    # (96.7%) were auto-checkpoints and only 209 recorded real work. Because the
    # timeline is truncated to a handful of rows before a new session reads it,
    # leaving this unfiltered meant telemetry crowded genuine achievements out of
    # the window entirely — the inherited summary described the act of saving
    # rather than anything that was accomplished.
    skip_phrases = [
        "Session checkpoint", "Continued the lineage",
        "Auto-serialized at session end", "Session ended",
    ]

    # Boilerplate carrying a VARIABLE suffix, so it must be matched as a
    # substring — the skip_phrases list above compares for exact equality and
    # therefore cannot catch these.
    #
    # "Auto-checkpoint at N% context" is the dominant case and was going
    # unfiltered: measured 2026-07-31 across the live lineage, 6,745 of 6,973
    # entries (96.7%) were auto-checkpoints and only 209 recorded real work.
    # Because the timeline is truncated to a handful of rows before a new session
    # reads it, that noise crowded genuine achievements out of the window
    # entirely — the inherited summary described the act of saving rather than
    # anything accomplished.
    prefilter_phrases = ("Session checkpoint", "Auto-checkpoint")

    seen_texts = set()
    achievements = []
    for entry in reversed(lineage_chain):
        achievement = entry.get("achievement", "")
        if not achievement:
            continue

        # Pre-filter obvious skips (before cleaning), substring-matched
        if any(p in achievement for p in prefilter_phrases):
            continue

        gen = entry.get("generation", 0)

        # Clean: take first line only (handle both real and escaped newlines)
        achievement = achievement.split("\n")[0].strip()
        if "\\n" in achievement:
            achievement = achievement.split("\\n")[0].strip()
        # Strip pipe chars that break markdown tables
        achievement = achievement.replace("|", "-")
        # Remove trailing date artifacts (e.g. " - 2026-02-09")
        achievement = re.sub(r'\s*-\s*\d{4}-\d{2}-\d{2}\s*$', '', achievement)
        # Strip trailing quotes/artifacts
        achievement = achievement.rstrip('"').rstrip("'").strip()

        # Validate: reject corrupted transcript fragments (defense in depth)
        achievement = validate_achievement(achievement)

        # Post-clean filter
        if any(phrase == achievement for phrase in skip_phrases):
            continue
        if not achievement:
            continue

        # Truncate to 120 chars
        if len(achievement) > 120:
            achievement = achievement[:117] + "..."

        # Skip duplicate achievement texts across generations
        if achievement.lower() in seen_texts:
            continue
        seen_texts.add(achievement.lower())

        achievements.append({"gen": gen, "achievement": achievement})

        if len(achievements) >= max_achievements:
            break

    return achievements


#: Truncation REASONS returned by :func:`recall_permanent_memories_page`.
#: The strings are part of the public contract: ``grip_recall`` renders
#: them differently, so a renderer must be able to tell a CONFIRMED extra
#: row ("8+ memories") from a saturated raw fetch that leaves the
#: existence of more rows unproven ("8 memories (possibly more)").
TRUNC_CONFIRMED_MORE = "confirmed_more"
TRUNC_PAGE_FULL_UNVERIFIED = "page_full_unverified"


def recall_permanent_memories(
    project_path: str,
    limit: int = 8,
    achievement_keywords: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Recall KONO memories for the project, preferring permanent ones.

    Returns a PAGE of at most ``limit`` memories. Callers that show the
    operator a number must use :func:`recall_permanent_memories_page`
    instead and say whether the page saturated -- ``len()`` of this result
    is a page size, and printing it as a total is how ``/recall`` came to
    report "8 memories" against 379 matches (2026-08-25).
    """
    memories, _ = recall_permanent_memories_page(
        project_path, limit=limit, achievement_keywords=achievement_keywords
    )
    return memories


def recall_permanent_memories_page(
    project_path: str,
    limit: int = 8,
    achievement_keywords: Optional[List[str]] = None,
) -> "tuple[List[Dict[str, Any]], Optional[str]]":
    """``(page, reason)`` -- the memories, and WHY more may exist.

    ``reason`` is ``None`` (complete), ``TRUNC_CONFIRMED_MORE`` (a further
    unique row was actually seen beyond the page), or
    ``TRUNC_PAGE_FULL_UNVERIFIED`` (the raw fetch saturated, so more
    cannot be ruled out, but no extra row was confirmed). The distinction
    is the contract: a bare "+" is an over-claim unless a row was seen,
    so the two cases must render differently. (AGORA council, PR #5079.)

    The reason is computed where the truth is already known: the
    substrate is asked for ``limit * 2`` and deduplicated, so a
    post-dedup count above ``limit`` means the page does not show
    everything this query found. That costs nothing.

    Reporting the real total would mean an unbounded recall, measured on
    2026-08-25 at ~1100 ms against ~155 ms for the page -- roughly seven
    times the cost, on a surface that runs every session. The honest cheap
    answer is to stop claiming the page is the total, not to buy the total.

    Uses achievement keywords for diverse recall instead of generic project name.
    """
    try:
        from kono_substrate import MemorySubstrate

        substrate = MemorySubstrate()

        # Build a richer query from recent achievements for diversity
        project_name = Path(project_path).name
        if achievement_keywords:
            query = "{} {}".format(project_name, " ".join(achievement_keywords[:6]))
        else:
            query = project_name

        requested = limit * 2
        results = substrate.recall(
            query=query,
            limit=requested,
            min_fitness=0.3,
            expand_spatial=False,
            project_path=project_path,
        )
        substrate.close()

        if not results:
            return [], None

        # Saturation of the RAW fetch, taken before dedup collapses it. If
        # the substrate returned everything we asked for, there may be more
        # beyond the window regardless of what dedup leaves behind.
        raw_saturated = len(results) >= requested

        # Sort: permanent first, then by fitness descending
        results.sort(
            key=lambda r: (r.get("is_permanent", False), r.get("fitness", 0)),
            reverse=True,
        )

        # Deduplicate by content prefix (prevents N identical results)
        seen_content = set()
        unique_results = []
        for r in results:
            key = r.get("content", "")[:80].strip().lower()
            if key in seen_content:
                continue
            seen_content.add(key)
            unique_results.append(r)
        results = unique_results

        # The truncation REASON, not a bare flag. A further unique row
        # confirmed beyond the page is `confirmed_more`; a saturated raw
        # fetch without a confirmed extra row is `page_full_unverified`.
        #
        # The second half matters because dedup runs in between: 16 rows
        # that collapse to exactly 8 unique would otherwise report
        # "complete" and render a bare "8 memories" -- the original defect,
        # reachable again through the duplicate-heavy path. Flagged by the
        # AGORA council on PR #5079 and reproduced before fixing.
        if len(results) > limit:
            reason = TRUNC_CONFIRMED_MORE
        elif raw_saturated:
            reason = TRUNC_PAGE_FULL_UNVERIFIED
        else:
            reason = None

        memories = []
        for r in results[:limit]:
            content = r.get("content", "")
            if len(content) > 200:
                content = content[:197] + "..."
            memories.append({
                "content": content,
                "fitness": r.get("fitness", 0),
                "is_permanent": r.get("is_permanent", False),
                "dimension_name": r.get("dimension_name", "unknown"),
            })

        return memories, reason

    except ImportError:
        return [], None
    except Exception:
        return [], None


def recall_strategy_transfers(
    project_path: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Recall permanent KONO memories from OTHER projects (strategy transfer).

    Permanent memories are cross-validated 3+ times and represent proven
    strategies worth transferring across projects. Project-specific session
    state stays compartmentalized; only validated patterns transfer.
    """
    try:
        from kono_substrate import MemorySubstrate

        substrate = MemorySubstrate()
        # Recall permanent memories across ALL projects
        results = substrate.recall(
            query="strategy pattern workflow",
            limit=limit * 3,
            min_fitness=0.5,
            expand_spatial=False,
        )

        # Filter to permanent only, exclude current project
        current_project = str(project_path).rstrip("/")
        transfers = []
        seen_content = set()
        for r in results:
            if not r.get("is_permanent", False):
                continue
            mem_project = r.get("project_path", "")
            if mem_project.rstrip("/") == current_project:
                continue
            key = r.get("content", "")[:80].strip().lower()
            if key in seen_content:
                continue
            seen_content.add(key)
            content = r.get("content", "")
            if len(content) > 150:
                content = content[:147] + "..."
            transfers.append({
                "content": content,
                "source_project": Path(mem_project).name if mem_project else "global",
            })
            if len(transfers) >= limit:
                break

        return transfers

    except (ImportError, Exception):
        return []


def deduplicate(
    achievements: List[Dict[str, Any]],
    kono_memories: List[Dict[str, Any]],
    overlap_threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Remove KONO memories that overlap significantly with achievements."""
    if not achievements or not kono_memories:
        return kono_memories

    # Build keyword set from all achievements
    achievement_keywords = set()
    for a in achievements:
        words = a["achievement"].lower().split()
        significant = {w for w in words if len(w) > 4}
        achievement_keywords.update(significant)

    filtered = []
    for mem in kono_memories:
        content_words = set(mem["content"].lower().split())
        significant_content = {w for w in content_words if len(w) > 4}
        overlap = len(significant_content & achievement_keywords)
        if overlap < overlap_threshold:
            filtered.append(mem)

    return filtered


def _fmt_achievement_timeline(achievements: List[Dict[str, Any]]) -> List[str]:
    """Section 1: Achievement Timeline (top 5). [] when there are no achievements."""
    if not achievements:
        return []
    out = [
        "## Achievement Timeline (Last {})".format(min(len(achievements), 5)),
        "",
        "| Gen | Achievement |",
        "|-----|-------------|",
    ]
    for a in achievements[:5]:  # Limit to top 5
        out.append("| {} | {} |".format(a["gen"], a["achievement"]))
    out.append("")
    return out


def _fmt_active_state(instance: Dict[str, Any]) -> List[str]:
    """Section 2: Active State (from next_up). [] unless completed/remaining/blockers."""
    next_up = instance.get("next_up")
    if not (next_up and isinstance(next_up, dict)):
        return []
    completed = next_up.get("completed", [])
    remaining = next_up.get("remaining", [])
    blockers = next_up.get("blockers", [])
    notes = next_up.get("notes", "")
    if not (completed or remaining or blockers):
        return []
    out = ["## Active State", ""]
    if completed:
        out.append("**Completed**: " + "; ".join(completed[:5]))
    if remaining:
        out.append("**Remaining**: " + "; ".join(remaining[:5]))
    if blockers:
        out.append("**Blockers**: " + "; ".join(blockers[:3]))
    if notes and isinstance(notes, str) and len(notes) < 200:
        out.append("**Notes**: " + notes)
    out.append("")
    return out


def _fmt_kono_memories(kono_memories: List[Dict[str, Any]]) -> List[str]:
    """Section 3: Key Memories (KONO). [] when none."""
    if not kono_memories:
        return []
    out = ["## Key Memories (KONO)", ""]
    for mem in kono_memories:
        perm_tag = "PERMANENT, " if mem["is_permanent"] else ""
        fitness_str = "{:.2f}".format(mem["fitness"])
        out.append("- [{}{}] {}".format(perm_tag, fitness_str, mem["content"]))
    out.append("")
    return out


def _focus_keywords(achievements: List[Dict[str, Any]]) -> str:
    """Recent-focus keyword string from the last few achievements ('general' if none)."""
    focus_keywords: List[str] = []
    for a in achievements[:3]:
        # Take first few meaningful words from each
        words = [
            w for w in a["achievement"].split()
            if len(w) > 3 and w.lower() not in ("feat:", "fix:", "chore:", "docs:", "the", "for", "with", "from")
        ]
        focus_keywords.extend(words[:3])
    return ", ".join(focus_keywords[:6]) if focus_keywords else "general"


def _fmt_session_context(
    instance: Dict[str, Any], achievements: List[Dict[str, Any]]
) -> List[str]:
    """Section 4: Session Context (generation / branch / focus). Always present."""
    lineage_info = instance.get("lineage", {})
    gen = lineage_info.get("lineage_depth", 0)
    branch = lineage_info.get("branch", "main")
    focus_str = _focus_keywords(achievements)
    return [
        "## Session Context",
        "",
        "Generation: {} | Branch: {} | Focus: {}".format(gen, branch, focus_str),
        "",
    ]


def format_brief(
    instance: Dict[str, Any],
    achievements: List[Dict[str, Any]],
    kono_memories: List[Dict[str, Any]],
) -> str:
    """Format the unified memory brief as structured markdown.

    Coordinator (#3209, CC 20 -> ~1): orchestrate per-section formatters, each
    of which returns its lines (or [] when its section is absent). Output is
    byte-for-byte identical to the prior monolith — sections append in the same
    order, with the same trailing blank lines.
    """
    lines = ["<unified-memory-brief>", ""]
    lines += _fmt_achievement_timeline(achievements)
    lines += _fmt_active_state(instance)
    lines += _fmt_kono_memories(kono_memories)
    lines += _fmt_session_context(instance, achievements)
    lines.append("</unified-memory-brief>")
    return "\n".join(lines)


BRIEF_START_MARKER = "<!-- GRIP-BRIEF-START -->"
BRIEF_END_MARKER = "<!-- GRIP-BRIEF-END -->"
MEMORY_LINE_LIMIT = 195  # 5-line safety margin from 200-line platform truncation

# The OTHER cap, and the one that actually fires. The harness truncates MEMORY.md
# on load at ~24.4 KiB of characters (24.4 * 1024 = 24_986); 24_000 leaves roughly
# the same proportional margin the line limit takes. Kept as a literal rather than
# imported for the reason lib/auto_memory_writer.py records at its own copy: that
# module and lib/grip_doctor.py use incompatible sys.path conventions, and a
# duplicated literal that works beats an import that silently disables the check.
MEMORY_CHAR_LIMIT = 24_000


def _predecessor_generation(
    current_gen: int, achievements: List[Dict[str, Any]]
) -> Optional[int]:
    """Nearest distinct predecessor generation from the displayed lineage.

    Derived from the achievement timeline (the same chain rendered in the
    table directly above the context line), NOT arithmetic ``current_gen - 1``:
    ``extract_achievement_timeline`` dedups achievements by text and skips
    checkpoint phrases, so ``current_gen - 1`` may name a generation absent
    from the chain. Returns the newest distinct generation strictly older than
    ``current_gen`` (always a real chain member), or ``None`` when no such
    predecessor exists (first/single-generation session) — preserving the
    plain single-``Gen`` line for back-compat.
    """
    older = {
        a["gen"]
        for a in achievements
        if isinstance(a.get("gen"), int) and a["gen"] < current_gen
    }
    return max(older) if older else None


def format_compact_brief(
    instance: Dict[str, Any],
    achievements: List[Dict[str, Any]],
) -> str:
    """Format a compact brief for MEMORY.md injection (~16-20 lines)."""
    lines = [BRIEF_START_MARKER, "# Session Lineage", ""]

    # Achievement table (top 8)
    if achievements:
        top = achievements[:8]
        lines.append("| Gen | Achievement |")
        lines.append("|-----|-------------|")
        for a in top:
            lines.append("| {} | {} |".format(a["gen"], a["achievement"]))
        lines.append("")

    # Active state (remaining + blockers only)
    next_up = instance.get("next_up")
    if next_up and isinstance(next_up, dict):
        remaining = next_up.get("remaining", [])
        blockers = next_up.get("blockers", [])
        if remaining:
            lines.append("**Remaining**: " + "; ".join(remaining[:5]))
        if blockers:
            lines.append("**Blockers**: " + "; ".join(blockers[:3]))
        if remaining or blockers:
            lines.append("")

    # One-line context with generation bridge (memory-chain predecessor pointer,
    # mirroring the IDR hash-chain: each generation links to the one it came from)
    lineage_info = instance.get("lineage", {})
    gen = lineage_info.get("lineage_depth", 0)
    branch = lineage_info.get("branch", "main")
    pred = _predecessor_generation(gen, achievements)
    if pred is not None:
        lines.append("Gen {} <- Gen {} | Branch: {}".format(gen, pred, branch))
    else:
        lines.append("Gen {} | Branch: {}".format(gen, branch))

    lines.append(BRIEF_END_MARKER)
    return "\n".join(lines)


def strip_brief_section(content: str) -> str:
    """Remove existing GRIP brief section (between markers) from content."""
    start_idx = content.find(BRIEF_START_MARKER)
    end_idx = content.find(BRIEF_END_MARKER)
    if start_idx == -1 or end_idx == -1:
        return content

    end_idx += len(BRIEF_END_MARKER)
    # Also strip trailing newlines after the end marker
    while end_idx < len(content) and content[end_idx] == "\n":
        end_idx += 1

    return content[:start_idx] + content[end_idx:]


# A MEMORY.md pointer is a bullet whose whole purpose is to name one memory
# file, in the shape lib/auto_memory_writer.py writes:
#     - [hook](./file.md) — hook.
# The line must END after the link, or after a dash-introduced description.
# Without that end anchor a prose bullet ("- [notes](notes.md) for background
# reading") also matched and inflated the headline figure. Leading whitespace
# is allowed so an indented pointer is not silently under-counted, and an
# optional link title is tolerated. The separator must be an EM/EN DASH,
# which is what auto_memory_writer actually writes, or " -- ", the operator's
# rendering of an em dash (rules/em-dash-double-hyphen.md) that the live index
# uses: measured 2026-11-24, the em/en form alone matched 5 of 56 pointers
# (GRIP#9366). A single hyphen stays out, because accepting it let dashed
# prose ('- [notes](notes.md) - see this') through. Trailing whitespace is
# tolerated, which a bare $ rejected.
_POINTER_RE = re.compile(
    r'^\s*- \[[^\]]+\]\([^)\s]*\.md(?:\s+"[^"]*")?\)\s*(?:(?:[—–]|--)\s.*)?\s*$',
    re.M,
)
TRUNCATION_LOG = CLAUDE_DIR / "logs" / "memory-truncation.jsonl"


def _log_truncation(
    memory_file: Path, discarded: List[str], kept_count: int
) -> None:
    """Record that MEMORY.md lines were discarded by the line-limit trim.

    The trim keeps the HEAD and drops the TAIL, which is where every append
    lands — so it does not bound a growing file, it destroys the most recent
    entries. It previously did so silently and returned success, which is why
    the loss went unnoticed: 286 memory files ended up unreferenced with no
    signal anywhere that anything had been removed.

    This does not change what is written. It makes the removal observable, so
    the loss can be measured before the eviction policy itself is redesigned.
    ``pointers_discarded`` is the load-bearing figure: it turns "trimmed 5
    lines" into "destroyed 3 memory pointers".

    Only ever called for a trim that REACHED DISK. A failed write destroyed
    nothing, so it produces no record at all — a log whose purpose is
    measuring real loss must not contain entries for losses that did not
    occur.

    The JSONL append is fail-open, and the stderr warning sits OUTSIDE that
    guard with a guard of its own: a logging failure must not swallow the
    only human-visible signal, and a signal failure (a closed stderr) must
    not escape and fail a write that already succeeded.
    """
    pointers = len(_POINTER_RE.findall("\n".join(discarded)))

    try:
        record = {
            "event": "memory_index_truncated",
            "file": str(memory_file),
            "lines_kept": kept_count,
            "lines_discarded": len(discarded),
            "pointers_discarded": pointers,
            # First NON-BLANK discarded line: the trim boundary can land on a
            # blank line, and a blank sample makes the record useless exactly
            # when someone is trying to identify what was lost.
            "first_discarded": next(
                (line[:200] for line in discarded if line.strip()), ""
            ),
        }
        TRUNCATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TRUNCATION_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except (OSError, ValueError, TypeError):
        pass

    # Outside the guard above — a logging failure is exactly when the
    # human-visible warning matters most. Emitted whenever content was
    # actually destroyed, NOT only when the destroyed lines were
    # pointer-shaped: a discarded tail of plain prose is a real loss too, and
    # gating on shape reproduced the silence this exists to end.
    #
    # There is no cross-call dedup. Suppressing a repeat is how a recurring
    # loss in a long-lived process becomes permanently invisible after its
    # first occurrence. The noise this once guarded against came from the
    # phantom-newline bug, which is fixed at source in _trim_to_line_limit —
    # so a warning now means content really was destroyed, and every
    # occurrence deserves saying.
    #
    # Its own guard: print to a closed stderr raises BrokenPipeError, and an
    # unguarded raise here would propagate out of a write that had ALREADY
    # succeeded, turning a signal failure into a reported write failure.
    if discarded:
        try:
            print(
                f"[memory] WARNING: truncation discarded {len(discarded)} "
                f"line(s), {pointers} pointer(s), from "
                # Every project's index is named MEMORY.md, so .name alone
                # made every warning read identically and the one
                # human-visible channel unusable for acting on the loss.
                f"{memory_file.parent.parent.name}/{memory_file.name}",
                file=sys.stderr,
            )
        except (OSError, ValueError):
            # OSError covers BrokenPipeError; ValueError covers writing to
            # an already-CLOSED stream ('I/O operation on closed file'),
            # which is not an OSError and would otherwise still escape.
            pass


def _trim_to_line_limit(combined: str) -> tuple:
    """Trim to ``MEMORY_LINE_LIMIT``, returning (text, discarded_lines).

    ``combined`` always ends in "\\n", so ``split`` yields a trailing "" that
    is not a line of content. Counting it made a file already AT the limit
    look over it, firing the trim and recording a phantom discard on every
    inject for any full project.

    Whenever ``discarded`` is non-empty the kept count is exactly
    ``MEMORY_LINE_LIMIT`` by construction, so the caller does not need it
    returned separately.

    With no pinned block present ``partition_pinned`` returns the input
    unchanged and the reassembly below is the identity, so the no-pin path is
    byte-identical to the behaviour that predates binding records — by
    construction rather than by care.
    """
    lines = combined.split("\n")
    # `while`, not `if`: a newline-terminated brief with no AI content ends in
    # "\n\n", so split leaves TWO trailing empties and a single pop still left
    # one behind to re-trigger the phantom trim.
    while lines and lines[-1] == "":
        lines.pop()

    # The cut is positional, so the pinned block is lifted out of the
    # evictable remainder first and put back afterwards. Without this a
    # binding record competes for POSITION against the newest arrival, and
    # position is the one axis on which a record written after an
    # irreversible harm always loses: it is old by the time it matters.
    pinned, evictable, anchor = partition_pinned(lines)
    budget = max(0, MEMORY_LINE_LIMIT - len(pinned))

    discarded: List[str] = []
    if len(evictable) > budget:
        discarded = evictable[budget:]
        evictable = evictable[:budget]

    evictable, char_discarded = _trim_to_char_limit(evictable, pinned, anchor)
    discarded.extend(char_discarded)

    # Clamped, so a block that sat in the doomed tail lands at the end of what
    # survives rather than being relocated to the top of a file it never
    # headed.
    at = min(anchor, len(evictable))
    lines = evictable[:at] + pinned + evictable[at:]

    return "\n".join(lines) + "\n", discarded


def _assembled_length(evictable: List[str], pinned: List[str], anchor: int) -> int:
    """Characters the file will occupy once the pinned block is put back."""
    at = min(anchor, len(evictable))
    lines = evictable[:at] + pinned + evictable[at:]
    return len("\n".join(lines)) + 1  # + the trailing newline


def _trim_to_char_limit(
    evictable: List[str], pinned: List[str], anchor: int
) -> tuple:
    """Drop from the evictable tail until the file fits the CHARACTER budget.

    The line cap above is the wrong dimension for the failure it is meant to
    prevent. The harness truncates MEMORY.md on load at a CHARACTER budget, and
    line count is uncorrelated with it: measured 2026-09-19, the live index was
    150 lines -- comfortably inside the 195-line cap -- and 29,804 characters,
    some 5,000 over. Its last 20 pointers reached no session at all, and the
    line-capped trimmer had no way to see that, because nothing it counts had
    moved. The same blind spot is recorded in lib/grip_doctor.py, which observed
    a 195-line / 46k-character index being truncated.

    The pinned block is never dropped here, for the same reason it is lifted out
    of the positional cut: a binding record must not lose to volume. If pinned
    content alone exceeded the budget this could not help, which is precisely why
    rules/binding-memory.md caps that block at 1,200 characters.
    """
    if _assembled_length(evictable, pinned, anchor) <= MEMORY_CHAR_LIMIT:
        return evictable, []
    discarded: List[str] = []
    while evictable and _assembled_length(evictable, pinned, anchor) > MEMORY_CHAR_LIMIT:
        discarded.insert(0, evictable.pop())
    return evictable, discarded


def _read_memory_index(memory_file: Path) -> Optional[str]:
    """MEMORY.md's text, "" when absent, or None when it exists but cannot be read.

    Read as UTF-8, which is how locked_write and Claude Code write it. With no
    encoding Python used the locale default -- cp1252 on Windows -- so every
    non-ASCII character was double-encoded on each /save, compounding run after
    run. None, never "", for an unreadable file: falling through with an empty
    string made the caller replace the whole MEMORY.md with the brief, silently
    deleting every pointer in it.
    """
    if not memory_file.exists():
        return ""
    try:
        return memory_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# A pointer line that a session wrote INSIDE the brief block: a bullet that
# starts with a markdown link to a .md file, whatever follows the link.
# strip_brief_section deletes the whole block on every save, so such a line
# used to vanish with no message (GRIP#9327: on 2026-11-22 a pointer sat under
# the Gen line). This is looser than _POINTER_RE on purpose. That pattern must
# end after the link or an em or en dash, so it misses the " -- " separator the
# live index uses, and a rescue errs toward keeping. The brief's own bullets
# read "- [tag] text", with a space after the bracket, so they never match.
_BLOCK_POINTER_RE = re.compile(r'^\s*- \[[^\]]+\]\([^)\s]+\.md(?:\s+"[^"]*")?\)')


def _pointers_inside_brief(content: str) -> List[str]:
    """Every pointer line between the brief markers, in order, each once.

    It scans the region strip_brief_section deletes, from the first START
    marker to the first END marker, so the rescue and the strip agree."""
    start_idx = content.find(BRIEF_START_MARKER)
    end_idx = content.find(BRIEF_END_MARKER)
    if start_idx == -1 or end_idx < start_idx:
        return []
    block = content[start_idx + len(BRIEF_START_MARKER):end_idx]
    lines = (line.rstrip() for line in block.splitlines())
    return list(dict.fromkeys(line for line in lines if _BLOCK_POINTER_RE.match(line)))


def _keep_rescued_pointers(rescued: List[str], ai_content: str) -> str:
    """``ai_content`` with every rescued pointer at its top, just below the new
    END marker, where the bottom-first trim reaches it last. Any other copy of a
    rescued pointer is dropped, so one copy stays. Skipping a pointer that was
    already present instead lost both copies when that copy sat in the trimmed
    tail (Codex on GRIP#9365). With nothing rescued the text is unchanged."""
    if not rescued:
        return ai_content
    moved = set(rescued)
    rest = [line for line in ai_content.splitlines() if line.rstrip() not in moved]
    return "\n".join(rescued + rest)


def inject_into_memory(project_path: str, compact_brief: str) -> bool:
    """Write compact brief into the project's memory/MEMORY.md."""
    encoded = encode_project_path(project_path)
    memory_dir = PROJECTS_DIR / encoded / "memory"
    memory_file = memory_dir / "MEMORY.md"

    # An index that exists but cannot be read is never rewritten.
    existing = _read_memory_index(memory_file)
    if existing is None:
        return False

    # Strip any existing brief, keeping every pointer written inside it.
    rescued = _pointers_inside_brief(existing)
    ai_content = _keep_rescued_pointers(rescued, strip_brief_section(existing).strip())

    # Combine: brief at top, AI content below
    if ai_content:
        combined = compact_brief + "\n\n" + ai_content + "\n"
    else:
        combined = compact_brief + "\n"

    # Enforce line limit - trim from bottom of AI content if over
    combined, discarded = _trim_to_line_limit(combined)

    written = False
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        locked_write(memory_file, combined)
        written = True
    except OSError:
        written = False

    # Logged ONLY after a write that reached disk. A failed write destroyed
    # nothing — MEMORY.md is untouched — so an entry named
    # "memory_index_truncated" carrying nonzero counts would describe a world
    # that does not exist. A write_succeeded=false field was not enough: the
    # event NAME and the counts still asserted a truncation. The caller
    # already learns of the failure from the False return.
    if discarded and written:
        _log_truncation(memory_file, discarded, MEMORY_LINE_LIMIT)

    return written


def format_rules_brief(
    instance: Dict[str, Any],
    achievements: List[Dict[str, Any]],
    kono_memories: List[Dict[str, Any]],
    project_path: str = "",
    strategy_transfers: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Format a compact session context for rules/ auto-loading (~300 tokens)."""
    lines = [
        "# Previous Session Context",
        "",
        "MANDATORY: At session start, present the Remaining Work items below",
        "as the FIRST output. Format: 'Open items from last session:' + bullets.",
        "Then ask which to continue. Skip if no Remaining Work or user has a new request.",
        "Only use this context if working in the same project.",
        "",
    ]

    # Project identifier for compartmentalization
    if project_path:
        lines.append("Project: {}".format(project_path))

    # One-line summary
    lineage_info = instance.get("lineage", {})
    gen = lineage_info.get("lineage_depth", 0)
    branch = lineage_info.get("branch", "main")
    msg_count = instance.get("conversation", {}).get("message_count", 0)
    siblings = lineage_info.get("siblings", [])
    latest_achievement = achievements[0]["achievement"] if achievements else "No recent work"

    summary = "Gen {} | Branch: {} | {} messages | Last: {}".format(
        gen, branch, msg_count, latest_achievement
    )
    # Show tree structure when branches/forks exist
    if siblings:
        summary += " | Siblings: {}".format(", ".join(siblings[:3]))
    fork_point = lineage_info.get("fork_point")
    if fork_point and branch != "main":
        summary += " | Forked from: {}".format(fork_point)
    lines.append(summary)
    lines.append("")

    # Active state — unified canonical "## Next Step" block (council §3.3).
    # Replaces the divergent "## Open Items (Action Required)" + "## Blockers"
    # renderer; now flows through next_step_block.next_step_lines so both the
    # warm-start (kono_preload) and session-end (--write-rules) paths emit an
    # identical format into rules/session-context.md.
    lines.extend(next_step_block.next_step_lines(instance.get("next_up")))

    # Recent achievements (top 6, deduplicated)
    if achievements:
        lines.append("## Recent Achievements")
        for a in achievements[:6]:
            lines.append("- Gen {}: {}".format(a["gen"], a["achievement"]))
        lines.append("")

    # Key memories (top 3, unique, project-specific)
    if kono_memories:
        lines.append("## Key Memories")
        for mem in kono_memories[:3]:
            perm = "permanent" if mem["is_permanent"] else "transient"
            content = mem["content"]
            if len(content) > 120:
                content = content[:117] + "..."
            lines.append("- [{}] {}".format(perm, content))
        lines.append("")

    # Strategy transfers (permanent memories from other projects)
    if strategy_transfers:
        lines.append("## Strategy Transfer")
        lines.append("Proven patterns from other projects (permanent, cross-validated):")
        for t in strategy_transfers[:3]:
            lines.append("- [{}] {}".format(t["source_project"], t["content"]))
        lines.append("")

    return "\n".join(lines)


def write_rules_file(content: str) -> bool:
    """Write session context to rules/ for Claude Code auto-loading."""
    rules_file = CLAUDE_DIR / "rules" / "session-context.md"
    try:
        rules_file.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    inject_memory = False
    write_rules = False
    skip_if_no_instance = False
    flags = {"--inject-memory", "--write-rules", "--skip-if-no-instance"}
    args = [a for a in argv[1:] if a not in flags]
    if "--inject-memory" in argv:
        inject_memory = True
    if "--write-rules" in argv:
        write_rules = True
    if "--skip-if-no-instance" in argv:
        skip_if_no_instance = True

    if len(args) < 1:
        print(
            "Usage: python3 unified-memory-brief.py [--inject-memory] "
            "[--write-rules] [--skip-if-no-instance] <project_path>",
            file=sys.stderr,
        )
        sys.exit(1)

    return {
        "project_path": args[0],
        "inject_memory": inject_memory,
        "write_rules": write_rules,
        "skip_if_no_instance": skip_if_no_instance,
    }

def _build_achievement_keywords(achievements: list) -> list:
    """Extract keyword hints from recent achievements for KONO recall."""
    stop = {"feat:", "fix:", "chore:", "docs:", "refactor:", "test:",
            "the", "for", "with", "from", "that", "this", "into"}
    kw = []
    for a in achievements[:4]:
        words = [w for w in a["achievement"].split() if len(w) > 4 and w.lower() not in stop]
        kw.extend(words[:2])
    return kw


def _run_inject(project_path: str, instance, achievements: list) -> None:
    compact = format_compact_brief(instance, achievements)
    if inject_into_memory(project_path, compact):
        print("[MEMORY] Compact brief injected into MEMORY.md", file=sys.stderr)
    else:
        print("[MEMORY] Failed to inject into MEMORY.md", file=sys.stderr)


def _run_write_rules(project_path: str, instance, achievements: list, kono_memories: list) -> None:
    strategy_transfers = recall_strategy_transfers(project_path)
    rules_content = format_rules_brief(
        instance, achievements, kono_memories,
        project_path=project_path,
        strategy_transfers=strategy_transfers,
    )
    if write_rules_file(rules_content):
        print("[RULES] Session context written to rules/session-context.md", file=sys.stderr)
    else:
        print("[RULES] Failed to write rules file", file=sys.stderr)


def main():
    """Main execution for unified memory brief generation."""
    parsed_args = _parse_args(sys.argv)
    project_path = parsed_args["project_path"]
    skip_if_no_instance = parsed_args["skip_if_no_instance"]

    # In skip mode the loud "no instance (index.json missing)" diagnostic is
    # suppressed (quiet=True) — a not-yet-initialised project is expected for a
    # best-effort caller, so we emit a calm skip note instead of an alarm.
    instance = load_latest_instance(project_path, quiet=skip_if_no_instance)
    if not instance:
        if skip_if_no_instance:
            # Best-effort caller (e.g. grip_save) opted in: a project with no
            # GRIP instance has nothing to inject — a clean no-op, not a
            # failure. Exit 0 with a calm note so the caller's pass/fail tally
            # is not inflated. The default (flag absent) preserves the
            # documented exit-1 contract for callers that detect no-instance.
            print(
                f"[memory-brief] skipped: no GRIP instance for {project_path!r} "
                "(not initialised) — nothing to inject (best-effort no-op)",
                file=sys.stderr,
            )
            sys.exit(0)
        sys.exit(1)

    achievements = extract_achievement_timeline(instance)
    kono_memories = deduplicate(
        achievements,
        recall_permanent_memories(project_path, achievement_keywords=_build_achievement_keywords(achievements)),
    )

    print(format_brief(instance, achievements, kono_memories))

    if parsed_args["inject_memory"]:
        _run_inject(project_path, instance, achievements)
    if parsed_args["write_rules"]:
        _run_write_rules(project_path, instance, achievements, kono_memories)



if __name__ == "__main__":
    main()
