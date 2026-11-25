#!/usr/bin/env python3
"""
reasoning_audit.py — The /catch-me-up reasoning audit skill.

Catalogues session achievements, classifies each claim, applies falsification
tests, translates to plain language, and persists validated results.

Uses the coordinator pattern from grip_transfer.py: each step is error-isolated
via _safe(). Partial results are acceptable; total failure returns empty AuditResult.

Usage:
    python3 lib/reasoning_audit.py                                     # Audit session-context.md
    python3 lib/reasoning_audit.py --session-context path/to/file.md  # Custom context file
    python3 lib/reasoning_audit.py --json                              # Output as JSON
"""

import argparse
import itertools
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"
STATE_DIR = CLAUDE_DIR / "state"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Category(str, Enum):
    DISCOVERY = "discovery"
    IMPLEMENTATION = "implementation"
    APPLICATION = "application"
    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"


class Verdict(str, Enum):
    STRONG_PASS = "strong_pass"
    WEAK_PASS = "weak_pass"
    INCONCLUSIVE = "inconclusive"
    WEAK_FAIL = "weak_fail"
    STRONG_FAIL = "strong_fail"


@dataclass
class Achievement:
    description: str
    category: Category = Category.OBSERVATION
    falsification_test: str = ""
    evidence: str = ""
    verdict: Verdict = Verdict.INCONCLUSIVE
    source: str = ""
    novelty_score: float = 0.0


@dataclass
class AuditResult:
    achievements: list = field(default_factory=list)
    summary: str = ""
    timestamp: str = ""
    total_claims: int = 0
    strong_passes: int = 0
    weak_passes: int = 0
    failures: int = 0


# ---------------------------------------------------------------------------
# Core helper
# ---------------------------------------------------------------------------

def _safe(fn):
    """Call fn(), return None on any error."""
    try:
        return fn()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Section extractor (shared with grip_transfer.py pattern)
# ---------------------------------------------------------------------------

def _extract_section(text: str, heading: str) -> list[str]:
    """Extract bullet items under a ## heading."""
    lines = text.splitlines()
    in_section = False
    items = []
    for line in lines:
        if line.startswith(f"## {heading}"):
            in_section = True
            continue
        elif line.startswith("## ") and in_section:
            break
        if in_section and line.startswith("- "):
            items.append(line[2:].strip())
    return items


# ---------------------------------------------------------------------------
# Step 1: Catalogue
# ---------------------------------------------------------------------------

#: Context key -> source label. ``completed``/``completed_work`` was REMOVED
#: (2026-09-11): it sourced from a ``## Remaining Work`` heading that no writer
#: has ever emitted -- ``unified_memory_brief.write_rules_file`` emits exactly
#: ``## Recent Achievements``, ``## Key Memories`` and ``## Strategy Transfer``
#: -- and it labelled that heading ``completed_work``, so had the section ever
#: existed the audit would have filed UNFINISHED work as a completed
#: achievement. A parse target with no writer and an inverted label is worse
#: than no parse target.
_SOURCE_MAP = [
    ("achievements", "achievements"),
    ("decisions", "strategy_transfer"),
]


def _achievements_from_key(session_context: dict, key: str, source: str) -> list[Achievement]:
    """Return achievements for one context key — single flat iteration."""
    return [Achievement(description=i, source=source) for i in session_context.get(key, []) if i]


def _catalogue_achievements(session_context: dict, allow_digest_fallback: bool = True) -> list[Achievement]:
    """Assemble Achievement objects from all available context sources.

    Uses itertools.chain.from_iterable — no nested for loops in function body.
    """
    results = list(itertools.chain.from_iterable(
        _achievements_from_key(session_context, key, src) for key, src in _SOURCE_MAP
    ))
    # The digest fallback reads the GLOBAL session-context.md, so it must not
    # run when the caller named its own source: an audit asked about file X that
    # silently answers about file Y is worse than one that answers "nothing".
    # This is why `--session-context` never worked -- an injected path that
    # yielded no sections fell straight through to the global file.
    if not results and allow_digest_fallback:
        digest = _import_digest()
        if digest:
            parsed = _safe(digest.parse_achievements) or []
            results = [Achievement(description=a, source="digest") for a in parsed]
    return results


def _import_digest():
    """Lazy-import digest_data; return None if unavailable."""
    try:
        import digest_data
        return digest_data
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Step 2: Classify
# ---------------------------------------------------------------------------

# Flat keyword → category map built once at module load.
# Single dict scan per achievement eliminates the nested-loop O(n²) perception.
_KEYWORD_CATEGORY: dict[str, Category] = {
    "discover": Category.DISCOVERY, "novel": Category.DISCOVERY,
    "insight": Category.DISCOVERY, "duality": Category.DISCOVERY,
    "kernel": Category.DISCOVERY,
    "built": Category.IMPLEMENTATION, "implement": Category.IMPLEMENTATION,
    "created": Category.IMPLEMENTATION, "pr #": Category.IMPLEMENTATION,
    "merged": Category.IMPLEMENTATION,
    "applied": Category.APPLICATION, "used": Category.APPLICATION,
    "pattern": Category.APPLICATION, "refactor": Category.APPLICATION,
    "hypothesis": Category.HYPOTHESIS, "h-": Category.HYPOTHESIS,
    "testable": Category.HYPOTHESIS, "predict": Category.HYPOTHESIS,
}


def _classify_one(achievement: Achievement) -> Achievement:
    """Assign category via flat keyword dict (single pass over description)."""
    text = achievement.description.lower()
    for kw, category in _KEYWORD_CATEGORY.items():
        if kw in text:
            achievement.category = category
            return achievement
    achievement.category = Category.OBSERVATION
    return achievement


def _classify_each(achievements: list[Achievement]) -> list[Achievement]:
    """Classify each achievement in-place and return the list."""
    for a in achievements:
        _classify_one(a)
    return achievements


# ---------------------------------------------------------------------------
# Step 3: Falsify
# ---------------------------------------------------------------------------

_FALSIFICATION_TESTS: dict[Category, str] = {
    Category.DISCOVERY: (
        "Would a senior engineer say 'obviously'? "
        "Is there independent replication? "
        "Does it pass the Gentner structural mapping test (not surface analogy)?"
    ),
    Category.IMPLEMENTATION: (
        "Does it work? Is it better than before? Do tests pass?"
    ),
    Category.APPLICATION: (
        "Was the pattern correctly applied? Are the structural mappings valid?"
    ),
    Category.HYPOTHESIS: (
        "Is the claim testable? Is the metric measurable? Is the deadline set?"
    ),
    Category.OBSERVATION: (
        "Is the observation reproducible? Is it distinguishable from noise?"
    ),
}


def _determine_verdict(achievement: Achievement) -> Verdict:
    """Derive verdict from evidence and category."""
    if achievement.category == Category.HYPOTHESIS:
        return Verdict.INCONCLUSIVE
    has_evidence = len(achievement.evidence) > 20
    is_impl_with_pr = (
        achievement.category == Category.IMPLEMENTATION
        and ("PR" in achievement.source or "commit" in achievement.source)
    )
    if has_evidence or is_impl_with_pr:
        return Verdict.WEAK_PASS
    return Verdict.INCONCLUSIVE


def _falsify_each(achievements: list[Achievement]) -> list[Achievement]:
    """Set falsification_test and verdict on each achievement."""
    for a in achievements:
        a.falsification_test = _FALSIFICATION_TESTS.get(a.category, "")
        a.verdict = _determine_verdict(a)
    return achievements


# ---------------------------------------------------------------------------
# Step 4: Translate to plain language
# ---------------------------------------------------------------------------

_VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.STRONG_PASS: "Verified",
    Verdict.WEAK_PASS: "Verified",
    Verdict.INCONCLUSIVE: "Needs More Evidence",
    Verdict.WEAK_FAIL: "Partially Disproved",
    Verdict.STRONG_FAIL: "Disproved",
}

_BUCKET_HEADINGS = {
    "Verified": "### Verified",
    "Needs More Evidence": "### Needs More Evidence",
    "Unverified Hypotheses": "### Unverified Hypotheses",
    "Partially Disproved": "### Partially Disproved",
    "Disproved": "### Disproved",
}


_BUCKET_ORDER = [
    "Verified",
    "Needs More Evidence",
    "Unverified Hypotheses",
    "Partially Disproved",
    "Disproved",
]


def _achievement_to_line(achievement: Achievement, bucket_key: str) -> str:
    """Format a single achievement as a markdown bullet."""
    if bucket_key == "Verified":
        return f"- {achievement.description} — confirmed, no counter-evidence found"
    return f"- {achievement.description} — {achievement.falsification_test}"


def _bucket_key(achievement: Achievement) -> str:
    """Return the plain-language bucket name for an achievement."""
    if achievement.category == Category.HYPOTHESIS:
        return "Unverified Hypotheses"
    return _VERDICT_LABELS.get(achievement.verdict, "Needs More Evidence")


def _explain_empty(source: Optional[dict]) -> str:
    """Say WHY an audit found nothing, so silence is never mistaken for calm.

    Three distinguishable causes, and the reader needs to know which:
      * the source file is missing entirely;
      * it exists but carries none of the headings this parses, which is the
        shape-race between its several writers rather than a quiet session;
      * it parsed fine and the session really was quiet.
    """
    if not source:
        return ("_Source unavailable — the audit could not read its context "
                "file at all, so this zero means 'cannot see', not 'nothing "
                "happened'._")
    parsed = set(source.get("parsed_headings") or ())
    present = set(source.get("headings_present") or ())
    path = source.get("path", "session-context.md")
    stamp = source.get("written") or "unknown time"
    age = source.get("age_minutes")
    age_s = "" if age is None else f", {age} min ago"
    if not present:
        head = (f"_Source carries no recognised headings — `{path}` "
                f"({source.get('bytes', 0)} bytes, written {stamp}{age_s}) has "
                f"none of {', '.join(sorted(EMITTED_HEADINGS))}, so this zero "
                f"means **cannot see**, not **nothing happened**.")
        # The shared-writer story and its recovery advice are TRUE ONLY of the
        # default path. Caught on PR #5493 by Codex and by AGORA Speaker A
        # independently: the first draft asserted both unconditionally and told
        # the reader to re-run `/save`, which the same commit documents as NOT a
        # writer of this file. Advice naming an operation that cannot repair the
        # audited source is worse than no advice.
        if source.get("is_default_path"):
            return head + (" Several writers share that path — "
                           "`hooks/startup/grip.sh`, `hooks/session-end.sh` and "
                           "`lib/grip_recall.py` — and the last to run decided "
                           "its shape. A recall pass rewrites it; `/save` does "
                           "NOT (it passes `--inject-memory`, never "
                           "`--write-rules`)._")
        return head + (" This file was named explicitly, so nothing in GRIP "
                       "rewrites it — whatever produced it must emit one of "
                       "those headings, or point `--session-context` at the "
                       "default source._")
    missing = parsed - present
    if missing:
        return (f"_Source has {', '.join(sorted(present))} but not "
                f"{', '.join(sorted(missing))} — partial shape, so the zero may "
                f"be incomplete rather than true (`{path}`, written {stamp}"
                f"{age_s})._")
    return (f"_Source parsed cleanly (`{path}`, written {stamp}{age_s}) and "
            f"held no achievements — this zero is a genuinely quiet session._")


def _translate_plain(achievements: list[Achievement], source: Optional[dict] = None) -> str:
    """Produce a plain-language markdown audit document.

    Single pass over achievements to build buckets (O(n)),
    then single pass over fixed bucket_order list to render (O(k), k=5).
    """
    n = len(achievements)
    header = f"## Session Audit\n\nExamined {n} claim{'s' if n != 1 else ''} from this session.\n"
    if n == 0:
        header += "\n" + _explain_empty(source) + "\n"

    # O(n) grouping pass — no nested iteration
    buckets: dict[str, list[str]] = {}
    for a in achievements:
        key = _bucket_key(a)
        buckets.setdefault(key, []).append(_achievement_to_line(a, key))

    # O(k) rendering pass over fixed 5-element list
    sections = [header]
    for key in _BUCKET_ORDER:
        lines = buckets.get(key)
        if lines:
            sections.append(_BUCKET_HEADINGS[key] + "\n" + "\n".join(lines))

    return "\n\n".join(sections).strip()


# ---------------------------------------------------------------------------
# Step 5: Persist
# ---------------------------------------------------------------------------

def _persist_validated(achievements: list[Achievement]) -> str:
    """Write audit summary to state file; register hypotheses with engine."""
    STATE_DIR.mkdir(exist_ok=True, parents=True)

    validated = [
        a for a in achievements
        if a.verdict in (Verdict.STRONG_PASS, Verdict.WEAK_PASS)
    ]
    hypotheses = [a for a in achievements if a.category == Category.HYPOTHESIS]

    state_path = STATE_DIR / "reasoning-audit-last.json"
    payload = {
        "last_audit_at": datetime.now(timezone.utc).isoformat(),
        "milestone_count": len(achievements),
        "validated_count": len(validated),
        "total_count": len(achievements),
    }
    try:
        state_path.write_text(json.dumps(payload, indent=2))
    except OSError:
        pass

    _register_hypotheses(hypotheses)

    return (
        f"Persisted audit state: {len(validated)} validated, "
        f"{len(hypotheses)} hypotheses registered."
    )


def _register_hypotheses(hypotheses: list[Achievement]) -> None:
    """Attempt to register hypothesis-category claims with the hypothesis engine."""
    if not hypotheses:
        return
    try:
        import hypothesis_engine
        for h in hypotheses:
            _safe(lambda: hypothesis_engine.register_claim(h.description))
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Session context loader
# ---------------------------------------------------------------------------

def _load_session_context(context_path: Optional[Path] = None) -> dict:
    """Parse session-context.md into a structured dict."""
    path = context_path or (CLAUDE_DIR / "rules" / "session-context.md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    ctx = {
        "achievements": _extract_section(text, "Recent Achievements"),
        "decisions": _extract_section(text, "Strategy Transfer"),
    }
    ctx["_source"] = describe_source(path, text)
    return ctx


#: Headings the writers actually emit. Kept beside the parser so a reader/writer
#: divergence shows up as a diagnosis rather than as a silent empty result.
EMITTED_HEADINGS = ("Recent Achievements", "Key Memories", "Strategy Transfer")


def describe_source(path: Path, text: str) -> dict:
    """Describe the context file so an empty audit can say WHY it is empty.

    An audit that reports "0 claims" when its source is unparseable is telling
    the reader "nothing happened" when the truth is "I cannot see anything" --
    the two are indistinguishable at the output, and only one of them is a
    defect. This makes them distinguishable.
    """
    import os
    import time

    headings = [h for h in EMITTED_HEADINGS if ("## " + h) in text]
    try:
        mtime = os.path.getmtime(path)
        written = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(mtime))
        age_min = (time.time() - mtime) / 60.0
    except OSError:
        written, age_min = None, None
    return {
        "path": str(path),
        "is_default_path": path == (CLAUDE_DIR / "rules" / "session-context.md"),
        "bytes": len(text),
        "headings_present": headings,
        "parsed_headings": ["Recent Achievements", "Strategy Transfer"],
        "written": written,
        "age_minutes": None if age_min is None else round(age_min, 1),
    }


# ---------------------------------------------------------------------------
# Main coordinator
# ---------------------------------------------------------------------------

def run_audit(session_context: Optional[dict] = None,
              context_path: Optional[Path] = None) -> AuditResult:
    """Run the full 5-step reasoning audit.

    Coordinator pattern: each step is error-isolated via _safe().
    Partial results are acceptable; total failure returns empty AuditResult.
    """
    _explicit_context = session_context is not None
    if session_context is None:
        session_context = _load_session_context(context_path)

    # Step 1: Catalogue
    # A caller-supplied context (explicit dict or --session-context path) pins the
    # source; only the default, unpinned run may fall back to the digest.
    _pinned = context_path is not None or _explicit_context
    achievements = _safe(lambda: _catalogue_achievements(session_context, allow_digest_fallback=not _pinned)) or []

    # Step 2: Classify
    if achievements:
        achievements = _safe(lambda: _classify_each(achievements)) or achievements

    # Step 3: Falsify
    if achievements:
        achievements = _safe(lambda: _falsify_each(achievements)) or achievements

    # Step 4: Translate
    summary = _safe(lambda: _translate_plain(achievements, session_context.get("_source"))) or ""

    # Step 5: Persist
    _safe(lambda: _persist_validated(achievements))

    now = datetime.now(timezone.utc).isoformat()
    _fail_verdicts = (Verdict.WEAK_FAIL, Verdict.STRONG_FAIL)

    return AuditResult(
        achievements=achievements,
        summary=summary,
        timestamp=now,
        total_claims=len(achievements),
        strong_passes=sum(1 for a in achievements if a.verdict == Verdict.STRONG_PASS),
        weak_passes=sum(1 for a in achievements if a.verdict == Verdict.WEAK_PASS),
        failures=sum(1 for a in achievements if a.verdict in _fail_verdicts),
    )


# ---------------------------------------------------------------------------
# IDR decision-chain introspection (--idr) — "why did I decide X / what led here"
# ---------------------------------------------------------------------------

def _render_decision_why(anatomy):
    """Render a decision_anatomy dict (a ``Decision.to_dict()``) to a plain why-string.

    Reuses the canonical ``lib.decision_record.render_plain`` when the anatomy
    round-trips through ``from_model_dict``; falls back to a direct field render
    otherwise. Fail-soft — never raises; returns ``None`` when nothing to show.
    """
    if not anatomy:
        return None
    try:
        from lib.decision_record import from_model_dict, render_plain
        return render_plain(from_model_dict(anatomy))
    except Exception:
        bits = []
        if anatomy.get("what"):
            bits.append(f"Decision: {anatomy['what']}")
        if anatomy.get("why"):
            bits.append(f"Why: {anatomy['why']}")
        if anatomy.get("falsification_criteria"):
            bits.append(f"Would be wrong if: {anatomy['falsification_criteria']}")
        return "\n".join(bits) or None


def idr_decision_chain(limit: int = 20) -> list:
    """The ``--idr`` catalogue source: surface each signed decision + its WHY.

    Composes EXISTING readers (no new chain, no new command): the signed
    PrecogIDR chain (``lib.precog.idr.read_idr_chain``) + the decision's mind
    (``lib.decision_record`` via ``_render_decision_why``). Answers 'why did I
    decide X / what led here' over the tamper-evident records — the decision-side
    complement of /catch-me-up's achievement audit. Fail-soft throughout: a
    missing reader or an anatomy-less record degrades to routing metadata, never
    raises. Returns the newest ``limit`` entries in chain order.
    """
    try:
        from lib.precog.idr import read_idr_chain
        idrs = read_idr_chain() or []
    except Exception:
        return []
    recent = idrs[-limit:] if limit and limit > 0 else idrs
    out = []
    for idr in recent:
        out.append({
            "id": getattr(idr, "id", None),
            "kind": getattr(idr, "kind", None),
            "predecessor": getattr(idr, "predecessor_idr", None),
            "fingerprint": getattr(idr, "fingerprint", None),
            "why": _render_decision_why(getattr(idr, "decision_anatomy", None)),
        })
    return out


def _render_idr_chain(entries: list) -> str:
    """Plain-language render of the decision chain (rules/canonical-output-style.md)."""
    if not entries:
        return ("No signed decision records yet — the IDR chain is empty. Decisions "
                "notarised via the delegated/audited path will appear here.\n")
    lines = [f"DECISION CHAIN — {len(entries)} most-recent signed record(s), oldest -> newest:", ""]
    for i, e in enumerate(entries, 1):
        head = f"#{i}  {e.get('kind') or 'decision'}"
        if e.get("predecessor"):
            head += f"  (follows {str(e['predecessor'])[:12]})"
        lines.append(head)
        why = e.get("why")
        if why:
            lines.extend(f"    {wl}" for wl in why.splitlines())
        else:
            lines.append("    (no decision-anatomy on this record — routing metadata only)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GRIP Reasoning Audit — /catch-me-up skill backend",
    )
    parser.add_argument(
        "--session-context", type=Path, default=None, metavar="FILE",
        help="Path to session-context.md (default: ~/.claude/rules/session-context.md)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output full audit result as JSON",
    )
    parser.add_argument(
        "--idr", action="store_true",
        help="Introspect the signed IDR decision-chain (decision -> why / what led here) "
             "instead of the achievement audit",
    )
    parser.add_argument(
        "--idr-limit", type=int, default=20, metavar="N",
        help="With --idr: show the newest N signed decisions (default 20)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.idr:
        entries = idr_decision_chain(limit=args.idr_limit)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(_render_idr_chain(entries))
        return

    result = run_audit(context_path=args.session_context)

    if args.json:
        output = asdict(result)
        # Serialise enum values to strings for JSON
        for a in output["achievements"]:
            a["category"] = a["category"]
            a["verdict"] = a["verdict"]
        print(json.dumps(output, indent=2))
        return

    print(result.summary or "No claims found in session context.")
    print(
        f"\n---\nTotal: {result.total_claims} | "
        f"Verified: {result.weak_passes + result.strong_passes} | "
        f"Failures: {result.failures}"
    )


if __name__ == "__main__":
    main()
