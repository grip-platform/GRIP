"""Congruent Continuity — the pre-hoc PRECOG watcher, L0 skeleton (WARN-only).

The watcher answers ONE question before a tool call runs: *does the recorded
continuity chain give any exogenous reason to think this action is on the
wrong track?* It returns DATA — ``{"level": "ok"|"warn", "reasons": [...]}`` —
and never blocks, never prints, and never writes to the chain. Printing is the
hook's job (``hooks/continuity-watch-hook.py``); blocking is nobody's job yet:
the watcher ships WARN-only until its precision is measured on the post-hoc
corpus (locked decision D3, ``docs/congruent-continuity-genesis.md:81``).

L0 = the deterministic floor only (``CONGRUENCE.md:139-140``). Three checks:

(a) **unsettled irreversible commitment matching this tool** — an
    ``intended_action.tool`` equal to ``tool_name`` on an unsettled commitment
    with ``severity == "irreversible"`` means the irreversible action is about
    to run while its load-bearing premise is unverified. Worklist source:
    ``lib/continuity_chain.py:240-252`` (``unsettled``).
(b) **already-refuted premise** — any unsettled commitment whose falsifier is
    machine-settleable (``probe:`` convention, ``lib/continuity_settle.py:66-68``)
    is probed via ``lib/continuity_settle.py:86-103`` (``run_probe``); an
    INCONGRUENT result warns. CONGRUENT and UNVERIFIABLE stay silent — the
    brake table maps UNVERIFIABLE to ``none``/observe (``CONGRUENCE.md:132``).
    The probe result is NOT written back: settlement is the post-hoc worker's
    job (``lib/continuity_settle.py:106-144``); the watcher only reads.
(c) **uncommitted irreversible action** — a destructive Bash pattern
    (force-push / ``rm -rf`` outside tmp / ``git reset --hard``) with no
    covering continuity-commit for this tool. A commitment covers only while
    its latest settlement is not INCONGRUENT (``settlement_of``,
    ``lib/continuity_chain.py:255-261``) — a refuted premise cannot license
    the action it intended.

Matching in (a) and (c) is TOOL-granularity by design: L0 has no model, so it
does not attempt fuzzy command-shape matching — that is the statistical layer's
job (predictor stack L1, ``CONGRUENCE.md:141-147``). The destructive-pattern
regexes are a WARN-only floor with documented false-positive/negative classes
(e.g. a quoted ``push -f`` inside a commit message can match; a ``+refspec``
force-push cannot) — acceptable because the watcher never blocks.

Every signal here derives from ARTEFACTS (chain records, probe exit codes,
command text) — never from the agent's own prose or confidence, which is the
banned self-report class (``CONGRUENCE.md:53-58``).
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from lib.continuity_chain import (
    COMMIT_KIND,
    commit_addr_of,
    contamination_roots,
    read_continuity_chain,
    settlement_of,
    unsettled,
)
from lib.continuity_settle import classify_falsifier, run_probe

__all__ = ["check_action"]

# Destructive-Bash floor patterns. Segment-scoped ([^|;&]*) so a flag in one
# shell segment cannot satisfy a match rooted in another ("git push a && rm -f b").
_FORCE_PUSH_RE = re.compile(
    r"\bgit\b[^|;&]*\bpush\b[^|;&]*(?:\s--force(?:-with-lease)?|\s-f)(?=\s|$)"
)
_RESET_HARD_RE = re.compile(r"\bgit\b[^|;&]*\breset\b[^|;&]*\s--hard\b")

# Targets under these roots are "inside tmp" for the rm -rf check. /var/folders
# is macOS's per-user temp root (where $TMPDIR points); $TMPDIR itself is added
# at call time so a relocated temp dir stays covered.
_TMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders")


def _in_tmp(target: str) -> bool:
    """True when the path target lives under a temp root (deletion reversible-ish)."""
    t = target.rstrip("/")
    roots = list(_TMP_ROOTS)
    tmpdir = (os.environ.get("TMPDIR") or "").rstrip("/")
    if tmpdir:
        roots.append(tmpdir)
    return any(t == root or t.startswith(root + "/") for root in roots)


def _segments(command: str) -> list[list[str]]:
    """Token lists per shell segment (split on && || ; |); lenient on quoting."""
    out: list[list[str]] = []
    for seg in re.split(r"&&|\|\||;|\|", command):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        if tokens:
            out.append(tokens)
    return out


def _rm_targets_outside_tmp(tokens: list[str]) -> list[str]:
    """Non-tmp targets of an ``rm`` carrying both recursive and force flags."""
    if tokens and tokens[0] == "sudo":
        tokens = tokens[1:]
    if not tokens or tokens[0] != "rm":
        return []
    short = "".join(t[1:] for t in tokens[1:] if t.startswith("-") and not t.startswith("--"))
    long_flags = {t for t in tokens[1:] if t.startswith("--")}
    recursive = "r" in short or "R" in short or "--recursive" in long_flags
    force = "f" in short or "--force" in long_flags
    if not (recursive and force):
        return []
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    return [t for t in targets if not _in_tmp(t)]


def _destructive_patterns(command: str) -> list[str]:
    """Human-readable labels for the destructive-Bash floor patterns found."""
    found: list[str] = []
    if _FORCE_PUSH_RE.search(command):
        found.append("git force-push")
    if _RESET_HARD_RE.search(command):
        found.append("git reset --hard")
    for tokens in _segments(command):
        outside = _rm_targets_outside_tmp(tokens)
        if outside:
            found.append("rm -rf outside tmp (" + ", ".join(outside) + ")")
            break
    return found


def _warn_unsettled_irreversible(tool_name: str, worklist: list) -> list[str]:
    """Check (a): unsettled irreversible commitments whose intended tool matches."""
    reasons: list[str] = []
    for node in worklist:
        decision = node.decision
        intended = decision.get("intended_action") or {}
        if decision.get("severity") == "irreversible" and intended.get("tool") == tool_name:
            reasons.append(
                f"unsettled irreversible commitment targets {tool_name}: "
                f"claim={decision.get('claim')!r} [{commit_addr_of(node)}]"
            )
    return reasons


def _warn_refuted_probes(worklist: list, *, cwd: Path | None = None) -> list[str]:
    """Check (b): probe-settleable unsettled premises that are ALREADY refuted."""
    reasons: list[str] = []
    for node in worklist:
        falsifier = str(node.decision.get("falsifier", ""))
        if classify_falsifier(falsifier) != "probe":
            continue
        result = run_probe(falsifier, cwd=cwd)
        if result.verdict == "INCONGRUENT":
            reasons.append(
                f"probe refuted an unsettled premise (INCONGRUENT): "
                f"claim={node.decision.get('claim')!r} "
                f"evidence={result.evidence} [{commit_addr_of(node)}]"
            )
    return reasons


def _has_live_covering_commitment(tool_name: str, chain_path: Path | None) -> bool:
    """A commit covers ``tool_name`` while its latest settlement is not INCONGRUENT."""
    for node in read_continuity_chain(path=chain_path):
        if node.kind != COMMIT_KIND:
            continue
        if (node.decision.get("intended_action") or {}).get("tool") != tool_name:
            continue
        verdict = settlement_of(commit_addr_of(node), path=chain_path)
        if verdict is None or verdict.decision.get("verdict") != "INCONGRUENT":
            return True
    return False


def _warn_suspect_premises(tool_name: str, chain_path: Path | None) -> list[str]:
    """Check (d): a commitment targeting this tool that inherited a refuted premise.

    ``contamination_roots()`` (``lib/continuity_chain.py``) maps each refuted
    commitment to its contamination closure, recomputed from the signed chain —
    so a settlement written by the post-hoc worker reaches the pre-hoc watcher
    with no extra plumbing. The tainted set is the union of those closures
    (exactly ``suspect()``), taken from the SAME map so the closure is computed
    once per check rather than once per refutation. WARN-only like every other
    check here (D3 stands)."""
    refuted_by = contamination_roots(path=chain_path)
    tainted: frozenset[str] = frozenset().union(*refuted_by.values())
    if not tainted:
        return []
    reasons: list[str] = []
    for node in read_continuity_chain(path=chain_path):
        if node.kind != COMMIT_KIND:
            continue
        if (node.decision.get("intended_action") or {}).get("tool") != tool_name:
            continue
        addr = commit_addr_of(node)
        if addr not in tainted:
            continue
        roots = ", ".join(sorted(r for r, closure in refuted_by.items() if addr in closure))
        reasons.append(
            f"SUSPECT premise targets {tool_name}: claim={node.decision.get('claim')!r} "
            f"[{addr}] inherited a refuted commitment ({roots})"
        )
    return reasons


def _warn_uncovered_destructive(
    tool_name: str, tool_input: dict[str, Any], chain_path: Path | None
) -> list[str]:
    """Check (c): destructive Bash patterns with no live covering commitment."""
    if tool_name != "Bash":
        return []
    patterns = _destructive_patterns(str(tool_input.get("command", "")))
    if not patterns or _has_live_covering_commitment(tool_name, chain_path):
        return []
    return [
        "uncommitted irreversible action: "
        + "; ".join(patterns)
        + " — no covering continuity-commit (pre-register one via "
        "lib.continuity_chain.commit before acting)"
    ]


def check_action(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    chain_path: Path | None = None,
) -> dict[str, Any]:
    """L0 congruence check for one imminent tool call. Read-only; never blocks.

    Returns ``{"level": "ok"|"warn", "reasons": [str, ...]}``. ``chain_path``
    of None reads the live session-namespaced chain
    (``lib/continuity_chain.py:81-83``); tests pass an explicit tmp chain. A
    missing chain file reads as empty (``lib/precog/idr.py:388-389``), so a
    fresh install is silently 'ok'.
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    worklist = unsettled(path=chain_path)
    reasons = _warn_unsettled_irreversible(tool_name, worklist)
    reasons += _warn_refuted_probes(worklist)
    reasons += _warn_uncovered_destructive(tool_name, tool_input, chain_path)
    reasons += _warn_suspect_premises(tool_name, chain_path)
    return {"level": "warn" if reasons else "ok", "reasons": reasons}
