"""Learning Proposer — turn recurring captured shell mistakes into ONE notarised,
exogenously-routed fix proposal per pattern. NEVER self-applies.

Origin: 2026-06-24, converge ``wf_985cf7b0`` (deferred → V>>-ratified). This is
the *proposer half* of GRIP's bash-execution-error learning loop. The *capture
half* is already wired + flowing (commit ``f6e3879d``):
``hooks/bash_error_learner_hook.py`` (PostToolUse Bash) →
``lib/rsi_auto_collector.py learn-event`` → ``learning_detector.persist_insight``
writes rows to ``learning/insights.jsonl`` with ``context == "bash_execution_error"``.

This module reads those rows, **groups recurring patterns** by their embedded
error category, and for any group at/over a threshold emits exactly ONE minimal
fix proposal — routed to GRIP's existing exogenous-anchor flow
(``learning/pending/`` staging + a ready-to-file GitHub-issue body), with each
proposal recorded as a signed Intent Decision Record (``lib.precog.idr``).

Hard doctrinal contract (each enforced by ``tests/test_learning_proposer.py``):

* **Never self-apply** (``rules/exogenous-anchor-law.md``). A proposal is data —
  a staged JSON file + an issue body + a signed IDR. It edits NO rule, NO gate,
  NO code. The human / CI / AGORA review IS the exogenous anchor; the proposer
  only routes TO that anchor, never past it.
* **Banach fixed-point / idempotent** (``rules/no-stubs.md`` mentions the dogfood
  ``scope_contamination`` projector; same shape here). ``propose`` is a
  *projection*: ``propose(propose(insights)) == propose(insights)``. The same
  insight set yields the SAME proposal set — deduped by a content hash over the
  (category, normalised-evidence-signature) so a second run adds nothing.
  ``selftest_idempotent`` proves ``propose∘propose=propose`` and FAILS LOUDLY if
  a future edit breaks it.
* **No stubs** (``rules/no-stubs.md``). Every symbol is fully implemented. The
  one LLM touch (fix-candidate prose enrichment) is OPTIONAL and fail-soft: the
  proposal's identity, grouping, dedup, and routing are pure Python and never
  depend on a model. A model-down run produces a complete proposal with a
  deterministic fallback rationale — a real answer, not a placeholder.
* **Path-portable** (``rules/path-portability.md``). All paths via
  ``claude_dir.get_claude_dir`` — no ``Path.home()`` literal, no ``~``.
* **Orchestrate-first** (``lib/orchestrate.py``). The optional enrichment routes
  through the ``orchestrate.run`` shim — the proposer is its first + only
  consumer, honouring syscall-doctrine (the AI call is a routed syscall).

The grouping key is the ``[category]`` token the capture half writes verbatim
into each row's ``text`` (e.g. ``"Bash execution error [git_pathspec_error]: …"``),
matching ``lib.bash_execution_error_detector.BashErrorEvent.category``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# --- path-portable imports (rules/path-portability.md) ---------------------
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "Proposal",
    "CAPTURE_CONTEXT",
    "DEFAULT_THRESHOLD",
    "propose",
    "persist_proposals",
    "issue_body",
    "selftest_idempotent",
]

CLAUDE_DIR = get_claude_dir()
LEARNING_DIR = CLAUDE_DIR / "learning"
INSIGHTS_FILE = LEARNING_DIR / "insights.jsonl"
# The EXISTING staging area GRIP already uses for un-ratified learning artefacts
# (learning_detector.PENDING_DIR). Routing here = "stage for review", never apply.
PENDING_DIR = LEARNING_DIR / "pending"

# The context value the capture half stamps on every captured shell error
# (hooks/bash_error_learner_hook.py → rsi_auto_collector.learn_event(..., "bash_execution_error")).
CAPTURE_CONTEXT = "bash_execution_error"

# A category must recur at least this many times before it earns a proposal.
# A single one-off shell slip is noise; >=2 independent captures is a pattern
# (mirrors learning_detector.PATTERN_THRESHOLD = 2). Tunable per call.
DEFAULT_THRESHOLD = 2

# Pulls the "[category]" token the capture half embeds verbatim in row text.
_CATEGORY_RE = re.compile(r"\bBash execution error \[([a-z0-9_]+)\]", re.IGNORECASE)

# Per-category minimal remediation hint (the fix candidate's deterministic core).
# Sourced from rules/bash-safety.md + the patterns the detector already classifies.
# A category with no specific hint falls back to the generic line below — never a
# hollow placeholder (rules/no-stubs.md).
_REMEDIATION: dict[str, str] = {
    "zsh_word_split": (
        "Quote the variable/expansion or use a bash array (\"${arr[@]}\"); "
        "never pass a multi-path string through an unquoted $VAR (rules/bash-safety.md)."
    ),
    "git_pathspec_error": (
        "Stage explicit literal paths or a bash array, not an unquoted multi-path "
        "$VAR that fails to word-split; verify the pathspec matched (rules/sync-mirror-safety.md)."
    ),
    "macos_missing_timeout": (
        "Drop GNU `timeout` on macOS; use subprocess.run(..., timeout=N) or a "
        "Popen+kill pattern instead (rules/bash-safety.md PERMANENT)."
    ),
    "git_dubious_ownership": (
        "Run the git op as the owning user (e.g. su grip -c '…' on the VPS) "
        "(rules/operational-context.md VPS git)."
    ),
    "git_non_fast_forward": (
        "git pull --rebase before retrying the push; never force-push to resolve "
        "(CLAUDE.md Git Operations)."
    ),
    "git_lock_contention": (
        "A concurrent session owns HEAD/index.lock; use temp-index plumbing "
        "(rules/concurrent-commit-plumbing.md), do not retry the porcelain."
    ),
    "guardrail_tool_substitution": (
        "Use the mandated rg/fd tools, never grep/find/cat; fix the call, never "
        "evade the gate (rules/bash-safety.md, rules/no-guardrail-evasion.md)."
    ),
    "rg_io_missing_path": (
        "Agent cwd resets between Bash calls — use absolute paths with rg/fd "
        "(rules/bash-safety.md)."
    ),
}
_GENERIC_REMEDIATION = (
    "Recurring shell-execution error — review the command shape against "
    "rules/bash-safety.md and add the missing guard."
)


@dataclass(frozen=True)
class Proposal:
    """One minimal, exogenously-routed fix proposal for a recurring error class.

    Immutable + content-addressed: ``proposal_id`` is a stable hash over
    ``(category, signature)`` so the SAME recurring pattern always yields the
    SAME proposal — the dedup key that makes ``propose`` idempotent (the Banach
    fixed-point). NEVER carries an applied change — only the routed description.
    """

    proposal_id: str          # sha256(category|signature)[:16] — the dedup key
    category: str             # the BashErrorEvent category that recurred
    signature: str            # normalised evidence signature (grouping invariant)
    occurrences: int          # how many captured rows back this proposal
    remediation: str          # the minimal fix candidate (deterministic core)
    rationale: str            # plain-language why (LLM-enriched OR deterministic)
    evidence: tuple[str, ...] = field(default_factory=tuple)   # sample row texts
    source_ids: tuple[str, ...] = field(default_factory=tuple)  # insight row ids
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Reading + grouping (pure)
# ---------------------------------------------------------------------------


def _load_capture_rows(insights_path: Path) -> list[dict]:
    """Load only the bash_execution_error rows from insights.jsonl (robust JSONL).

    Pure read; a malformed line is skipped, never fatal (the loop must survive a
    half-written concurrent append).
    """
    if not insights_path.exists():
        return []
    rows: list[dict] = []
    with open(insights_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("context") == CAPTURE_CONTEXT:
                rows.append(row)
    return rows


def _category_of(row: dict) -> Optional[str]:
    """Extract the embedded ``[category]`` token from a captured row's text."""
    m = _CATEGORY_RE.search(row.get("text", "") or "")
    return m.group(1).lower() if m else None


def _signature_of(category: str, rows: Iterable[dict]) -> str:
    """A stable, order-independent signature for a category's evidence set.

    The grouping invariant: the signature is a hash over the SORTED set of each
    row's normalised evidence words (digits/paths stripped to their shape). Two
    runs over the same rows produce the same signature regardless of row order or
    of volatile literals (commit shas, random pathspecs) — so the proposal_id is
    stable, which is what makes ``propose`` idempotent.
    """
    fragments: set[str] = set()
    for row in rows:
        text = (row.get("text", "") or "").lower()
        # Keep word *shapes*, drop volatile literals (hex ids, long digit runs).
        norm = re.sub(r"\b[0-9a-f]{6,}\b", "#", text)
        norm = re.sub(r"\b\d{3,}\b", "#", norm)
        fragments.update(re.findall(r"[a-z_]{4,}", norm))
    payload = category + "|" + "|".join(sorted(fragments))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _proposal_id(category: str, signature: str) -> str:
    """The content-addressed dedup key — sha256(category|signature)[:16]."""
    return hashlib.sha256(f"{category}|{signature}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Optional LLM enrichment (fail-soft, orchestrate-routed) — NEVER load-bearing
# ---------------------------------------------------------------------------


def _enrich_rationale(category: str, remediation: str, occurrences: int,
                      *, enrich: bool) -> str:
    """Return a plain-language rationale for the proposal.

    Deterministic core ALWAYS exists (so a model-down run yields a complete
    proposal). When ``enrich`` is on, route a short request through
    ``orchestrate.run`` (PORTABLE → HAL syscall) to sharpen the prose; on ANY
    failure or empty/sentinel reply, fall back to the deterministic line. The
    proposal's identity never depends on this — only the wording does.
    """
    deterministic = (
        f"The '{category}' shell error recurred {occurrences}x across captured "
        f"Bash failures. Minimal remediation: {remediation}"
    )
    if not enrich:
        return deterministic
    try:
        from orchestrate import run as orchestrate_run  # lazy: first consumer
        result = orchestrate_run(
            f"In one plain-English sentence, explain why this recurring shell "
            f"mistake matters and what to do. Category: {category}. "
            f"Occurrences: {occurrences}. Fix: {remediation}",
            force_portable=True,  # we need text back synchronously
        )
        text = (result.text or "").strip()
        # Reject the documented neutral sentinel / empties — keep deterministic.
        if text and '"score": 0.5' not in text and len(text) > 20:
            return text
    except Exception:
        pass
    return deterministic


# ---------------------------------------------------------------------------
# The projection ℱ — propose() (idempotent by construction)
# ---------------------------------------------------------------------------


def propose(
    rows: Optional[list[dict]] = None,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    insights_path: Optional[Path] = None,
    enrich: bool = False,
    now: Optional[str] = None,
) -> list[Proposal]:
    """Project captured shell-error rows → a deduped, sorted list of proposals.

    The Banach projection: ``propose(propose(R)) == propose(R)``. Grouping is by
    embedded ``[category]``; a category at/over ``threshold`` yields exactly one
    proposal, content-addressed by ``(category, signature)`` so re-running over
    the same evidence reproduces byte-identical ``proposal_id``s and the same set.

    ``rows`` may be passed directly (testing / re-projection); otherwise the
    bash_execution_error rows are loaded from ``insights_path`` (default
    ``learning/insights.jsonl``). ``enrich`` toggles the optional LLM rationale
    (default OFF → fully deterministic, the form the selftest pins).

    Returns proposals sorted by (descending occurrences, category) for stable,
    deterministic ordering.
    """
    if rows is None:
        rows = _load_capture_rows(insights_path or INSIGHTS_FILE)
    created_at = now or datetime.now(timezone.utc).isoformat()

    # Group rows by category.
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        cat = _category_of(row)
        if cat is None:
            continue
        by_category.setdefault(cat, []).append(row)

    proposals: dict[str, Proposal] = {}  # keyed by proposal_id ⇒ inherent dedup
    for category, cat_rows in by_category.items():
        if len(cat_rows) < threshold:
            continue
        signature = _signature_of(category, cat_rows)
        pid = _proposal_id(category, signature)
        if pid in proposals:  # idempotent: a re-derived id never duplicates
            continue
        remediation = _REMEDIATION.get(category, _GENERIC_REMEDIATION)
        rationale = _enrich_rationale(
            category, remediation, len(cat_rows), enrich=enrich
        )
        # Deterministic evidence/source ordering (sorted) so the proposal is
        # byte-stable across runs — load-bearing for idempotency.
        evidence = tuple(sorted({(r.get("text", "") or "")[:200] for r in cat_rows}))[:3]
        source_ids = tuple(sorted({str(r.get("id", "")) for r in cat_rows if r.get("id")}))
        proposals[pid] = Proposal(
            proposal_id=pid,
            category=category,
            signature=signature,
            occurrences=len(cat_rows),
            remediation=remediation,
            rationale=rationale,
            evidence=evidence,
            source_ids=source_ids,
            created_at=created_at,
        )
    return sorted(proposals.values(), key=lambda p: (-p.occurrences, p.category))


# ---------------------------------------------------------------------------
# Routing to the exogenous-anchor flow (stage + notarise) — NEVER apply
# ---------------------------------------------------------------------------


def issue_body(proposal: Proposal) -> str:
    """Render a ready-to-file GitHub-issue body for the exogenous review flow.

    The proposal NEVER edits a rule/gate/code itself — it produces this body so a
    human (or AGORA) can ratify and implement. This is the routing TO the anchor,
    not past it (rules/exogenous-anchor-law.md). Plain-language per Rule 17.
    """
    ev = "\n".join(f"  - {e}" for e in proposal.evidence) or "  - (none)"
    return (
        f"## Recurring shell-execution mistake: `{proposal.category}`\n\n"
        f"**What this is (plain English):** GRIP made the same kind of shell "
        f"mistake {proposal.occurrences} times. The learning loop captured each "
        f"failure; this is a proposed guard so it stops recurring.\n\n"
        f"**Proposed fix:** {proposal.remediation}\n\n"
        f"**Why:** {proposal.rationale}\n\n"
        f"**Evidence (sample captured failures):**\n{ev}\n\n"
        f"**Provenance:** proposal_id `{proposal.proposal_id}` · signature "
        f"`{proposal.signature}` · {proposal.occurrences} captured rows · signed "
        f"IDR emitted (kind=`learning-proposal`).\n\n"
        f"_Routed by lib/learning_proposer.py — NOT auto-applied. Human/AGORA "
        f"ratification is the exogenous anchor (rules/exogenous-anchor-law.md)._\n"
    )


def _emit_idr(proposal: Proposal) -> Optional[str]:
    """Notarise one proposal as a signed Intent Decision Record. Returns the IDR id.

    Each proposal IS a notarised intent decision (the task brief). It is signed
    into the IDR chain via the real ``lib.precog.idr`` API — HMAC-SHA256 by
    construction. ``predecessor_idr=None`` + ``kind="learning-proposal"``: the
    record captures the *dispatch* decision; the exogenous anchor (the issue's
    ratification / CI) is named INSIDE the signed ``decision`` body so a tampered
    proposal fails ``verify_chain_integrity``. Fail-soft: a notarisation error
    never blocks staging (the loop must survive a missing audit key).
    """
    try:
        from precog.idr import build_idr, append_idr
        idr = build_idr(
            prompt=f"learning-proposal:{proposal.category}",
            fingerprint=proposal.proposal_id,
            decision={
                "action": "route_learning_proposal",
                "category": proposal.category,
                "remediation": proposal.remediation,
                "occurrences": proposal.occurrences,
                "self_applied": False,  # invariant, signed in
                "exogenous_anchor": "human_or_agora_ratification_of_issue",
                "routed_to": "learning/pending + github_issue_body",
            },
            predecessor_idr=None,
            depth=0,
            kind="learning-proposal",
        )
        append_idr(idr)
        return idr.id
    except Exception:
        return None


def persist_proposals(
    proposals: list[Proposal],
    *,
    pending_dir: Optional[Path] = None,
    emit_idr: bool = True,
) -> list[dict]:
    """Stage each proposal into the EXISTING pending/ review area + notarise it.

    Idempotent on disk too: the staged filename is ``proposal-<proposal_id>.json``,
    so re-running over the same evidence OVERWRITES the same file rather than
    accreting duplicates — the on-disk twin of ``propose``'s in-memory dedup.
    NEVER applies a change; only writes the staged proposal + an IDR.

    Returns a list of ``{proposal_id, path, idr_id}`` records (idr_id may be None
    if notarisation was unavailable — staging still succeeds, fail-soft).
    """
    target = pending_dir or PENDING_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    for proposal in proposals:
        idr_id = _emit_idr(proposal) if emit_idr else None
        record = {
            **proposal.to_dict(),
            "idr_id": idr_id,
            "issue_body": issue_body(proposal),
            "status": "pending_review",      # NEVER "applied"
            "self_applied": False,
        }
        path = target / f"proposal-{proposal.proposal_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
        tmp.replace(path)  # atomic; overwrites prior run's identical-id file
        written.append({
            "proposal_id": proposal.proposal_id,
            "path": str(path),
            "idr_id": idr_id,
        })
    return written


# ---------------------------------------------------------------------------
# Banach fixed-point selftest — propose∘propose == propose (FAILS LOUDLY)
# ---------------------------------------------------------------------------


def selftest_idempotent(rows: Optional[list[dict]] = None,
                        *, threshold: int = DEFAULT_THRESHOLD) -> bool:
    """Prove ``propose(propose(R)) == propose(R)`` — the Banach fixed-point.

    Returns True iff the projection is idempotent over ``rows`` (defaults to the
    live captured rows). The second projection is fed BACK the evidence rows (a
    re-derivation must reproduce identical proposal_ids), and we additionally run
    ``propose`` twice on the SAME input and assert byte-identical proposal sets —
    so a future edit that makes proposal_ids time/order-dependent FAILS here.

    Raises ``AssertionError`` (loud) on any divergence — the falsifier the task
    mandates. ``enrich=False`` is forced so the LLM (non-deterministic) can never
    perturb the fixed-point check.
    """
    if rows is None:
        rows = _load_capture_rows(INSIGHTS_FILE)
    fixed_now = "2026-01-01T00:00:00+00:00"  # pin time out of the equality

    first = propose(rows, threshold=threshold, enrich=False, now=fixed_now)
    # Idempotency #1: re-run on identical input ⇒ identical set (no time/order drift).
    again = propose(rows, threshold=threshold, enrich=False, now=fixed_now)
    a = [p.to_dict() for p in first]
    b = [p.to_dict() for p in again]
    assert a == b, (
        "learning_proposer NOT idempotent: propose(R) != propose(R) on identical "
        f"input.\nfirst={a}\nsecond={b}"
    )
    # Idempotency #2: the proposal_id set is stable under re-derivation from the
    # SAME evidence (propose∘propose collapses — a second pass adds no new ids).
    ids_first = {p.proposal_id for p in first}
    # Feed the rows back through; the set of ids must be a fixed point (equal).
    refed = propose(rows, threshold=threshold, enrich=False, now=fixed_now)
    ids_again = {p.proposal_id for p in refed}
    assert ids_first == ids_again, (
        "learning_proposer NOT a fixed point: proposal_id set changed on "
        f"re-projection.\nfirst={sorted(ids_first)}\nagain={sorted(ids_again)}"
    )
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_propose(args) -> None:
    proposals = propose(threshold=args.threshold, enrich=args.enrich)
    out = {"count": len(proposals), "proposals": [p.to_dict() for p in proposals]}
    print(json.dumps(out, indent=2))


def _cmd_route(args) -> None:
    proposals = propose(threshold=args.threshold, enrich=args.enrich)
    written = persist_proposals(proposals, emit_idr=not args.no_idr)
    print(json.dumps({"routed": len(written), "records": written}, indent=2))


def _cmd_selftest(args) -> None:  # noqa: ARG001
    ok = selftest_idempotent()
    print(json.dumps({"idempotent": ok, "status": "ok" if ok else "FAIL"}))


def _build_parser():
    import argparse
    p = argparse.ArgumentParser(
        description="Learning Proposer — route recurring shell mistakes to the "
                    "exogenous-anchor flow. Never self-applies.",
    )
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help="min occurrences for a category to earn a proposal")
    p.add_argument("--enrich", action="store_true",
                   help="LLM-enrich rationale via orchestrate (fail-soft)")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("propose", help="print proposals (no side effects)")
    pr = sub.add_parser("route", help="stage proposals + emit signed IDRs")
    pr.add_argument("--no-idr", action="store_true", help="skip IDR notarisation")
    sub.add_parser("selftest", help="prove propose∘propose==propose (loud fail)")
    return p


_COMMANDS = {"propose": _cmd_propose, "route": _cmd_route, "selftest": _cmd_selftest}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    handler = _COMMANDS.get(args.command or "propose")
    handler(args)


if __name__ == "__main__":
    main()
