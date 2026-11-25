"""Plain-language + delight++ render layer for the canonical /update-team post.

The problem it solves: the default #grip-updates render dumped raw commit
subjects ("feat(swarm): worktree coordinator runtime ... (#3764)") — correct,
but unreadable for a non-coder teammate. This module turns each shipped PR into
a short "what it does for a person" line plus a warm 1-2 sentence narrative,
then renders a delightful Slack Block Kit for #grip-updates.

Two hard invariants (both load-bearing):

1. PRECISION — PR numbers are NEVER sent to, nor trusted from, the LLM. Only
   titles (+ conventional-commit type) go in; the model returns prose only; the
   verbatim ``(#number)`` is re-attached deterministically from
   ``PREntry.number`` caller-side, and any number the model sneaks into a line
   is stripped. A rounded/hallucinated number cannot enter the output
   (canonical-output-style + output-tier-default precision invariant).

2. FAIL-SOFT — the translation is a SINGLE batched HAL call (the AI syscall,
   routed through ``lib.hal_llm_adapter`` per syscall-doctrine;
   dispatch-economics: one call for the whole list, never N per-PR calls). ANY
   failure — HAL offline, malformed JSON, length mismatch — degrades to the
   verbatim PR titles. The post never breaks because the plain layer is
   unavailable.

Public API
----------
translate(prs) -> PlainTranslation
render_delight_blocks(digest, summary, translation) -> list[dict]
human_category(cc_type) -> str
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lib.aurora_card import format_count
from lib.work_digest import PREntry, WorkDigest, unmeasured_note
from lib.work_summary import WorkSummary


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlainTranslation:
    """Output of the batched plain-language translation.

    narrative: warm 1-2 sentence overview (no PR numbers), or None on failure.
    lines:     {pr_number: "what it does for a person"} — only the PRs the model
               translated; a missing number falls back to the verbatim title.
    """
    narrative: Optional[str]
    lines: Dict[int, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "PlainTranslation":
        """The fail-soft value: no narrative, no translated lines."""
        return cls(narrative=None, lines={})


# ---------------------------------------------------------------------------
# Humanised category labels — the tasteful delight++ header map
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "feat":     ":sparkles: New capabilities",
    "fix":      ":wrench: Fixes",
    "refactor": ":broom: Under-the-hood",
    "perf":     ":zap: Faster now",
    "test":     ":test_tube: Safety nets",
    "docs":     ":books: Docs",
    "chore":    ":package: Housekeeping",
    "ci":       ":gear: Build & CI",
    "build":    ":gear: Build & CI",
    "style":    ":art: Polish",
    "revert":   ":rewind: Reverted",
    "other":    ":small_blue_diamond: Also shipped",
    "open / in-flight": ":hourglass_flowing_sand: In flight",
}


def human_category(cc_type: str) -> str:
    """Map a conventional-commit type to a warm, emoji-led section header."""
    return _CATEGORY_LABELS.get(cc_type, f":small_blue_diamond: {cc_type}")


# ---------------------------------------------------------------------------
# Batched HAL translation (the AI syscall — fail-soft, precision-safe)
# ---------------------------------------------------------------------------

def _build_translation_prompt(items: List[Tuple[str, str]]) -> str:
    """Prompt HAL to translate PR titles into per-item benefit lines + narrative.

    ``items`` is an ordered list of (cc_type, title). The model keys its output
    by 1-based INDEX — never a PR number (the caller re-attaches those).
    """
    numbered = "\n".join(
        f"{i}. [{t}] {title}" for i, (t, title) in enumerate(items, start=1)
    )
    n = len(items)
    return (
        "You turn a software team's shipped work into plain English for a "
        "teammate who does NOT write code. For EACH numbered item, write ONE "
        "short line saying what it does FOR A PERSON (a benefit), not what it is "
        "called. No jargon, no commit prefixes (feat/fix/refactor), no issue or "
        "PR numbers.\n\n"
        f"Items ({n}):\n{numbered}\n\n"
        "Also write a warm 1-2 sentence 'narrative' summarising the work overall "
        "(no numbers, no jargon).\n\n"
        "Return ONLY a JSON object, no markdown, no prose around it:\n"
        '{"narrative": "...", "lines": ["line for item 1", "line for item 2"]}\n'
        f"The lines array MUST have exactly {n} entries, in item order."
    )


_MODEL_NUM_RE = re.compile(r"\(?#\d+\)?")
_LEADING_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def _sanitise_line(line: str) -> str:
    """Strip any PR/issue number the model emitted (numbers are re-attached
    deterministically caller-side, never trusted from the LLM) and drop a
    leading bullet/numbering artefact. Falls back to the original if emptied."""
    cleaned = _MODEL_NUM_RE.sub("", line).strip()
    cleaned = _LEADING_BULLET_RE.sub("", cleaned).strip()
    return cleaned or line


def _extract_json(raw: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response. None on miss.

    Tries the whole response (the json_mode happy path), then a ```json fence,
    then the widest brace span. Every candidate is validated by json.loads.
    """
    if not raw or not raw.strip():
        return None
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_translation(raw: str, prs: Tuple[PREntry, ...]) -> PlainTranslation:
    """Parse the HAL JSON into a PlainTranslation, re-attaching PR numbers by
    position. A length mismatch drops the lines (keeping any narrative);
    bad shape → empty. Fail-soft throughout."""
    obj = _extract_json(raw)
    if obj is None:
        return PlainTranslation.empty()

    narrative = obj.get("narrative")
    narrative = (
        narrative.strip()
        if isinstance(narrative, str) and narrative.strip()
        else None
    )

    raw_lines = obj.get("lines")
    lines: Dict[int, str] = {}
    if isinstance(raw_lines, list) and len(raw_lines) == len(prs):
        for pr, line in zip(prs, raw_lines):
            if isinstance(line, str) and line.strip():
                lines[pr.number] = _sanitise_line(line.strip())
    return PlainTranslation(narrative=narrative, lines=lines)


def translate(prs: Tuple[PREntry, ...]) -> PlainTranslation:
    """Batched HAL translation of PR titles → benefit lines + narrative.

    ONE call for the whole list (dispatch-economics). Fail-soft to empty on any
    error so the render degrades to verbatim titles. PR numbers never leave here.
    """
    if not prs:
        return PlainTranslation.empty()
    try:
        from lib.hal_llm_adapter import LlamaEngine  # type: ignore
    except ImportError:
        return PlainTranslation.empty()

    prompt = _build_translation_prompt([(pr.type, pr.title) for pr in prs])
    try:
        raw = LlamaEngine().chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.2,
            json_mode=True,
        )
    except Exception:  # noqa: BLE001 — fail-soft is the contract
        return PlainTranslation.empty()
    return _parse_translation(raw or "", prs)


# ---------------------------------------------------------------------------
# Delight++ Block Kit render
# ---------------------------------------------------------------------------

def _benefit_line(pr: PREntry, plain_map: Dict[int, str]) -> str:
    """One benefit line: plain translation if available else the verbatim title,
    with the PR number re-attached VERBATIM either way (precision invariant)."""
    text = plain_map.get(pr.number, pr.title)
    return f"• {text}  (#{pr.number})"


def _benefit_section(cc_type: str, prs: List[PREntry], plain_map: Dict[int, str]) -> dict:
    """A humanised category section: warm emoji header + benefit lines."""
    header = human_category(cc_type)
    rows = "\n".join(_benefit_line(pr, plain_map) for pr in prs)
    return {"type": "section",
            "text": {"type": "mrkdwn", "text": f"*{header}*\n{rows}"}}


def _group_by_type(prs: Tuple[PREntry, ...]) -> Dict[str, List[PREntry]]:
    """Group PRs by conventional-commit type — single pass."""
    groups: Dict[str, List[PREntry]] = {}
    for pr in prs:
        groups.setdefault(pr.type, []).append(pr)
    return groups


def _header_block() -> dict:
    return {"type": "header",
            "text": {"type": "plain_text",
                     "text": "What GRIP shipped for you", "emoji": True}}


def _tldr_block(summary: WorkSummary, narrative: Optional[str]) -> dict:
    """TL;DR headline (deterministic) + optional warm plain-language narrative."""
    body = f"*TL;DR* — {summary.tldr}"
    if narrative:
        body += f"\n{narrative}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": body}}


def _merged_sections(digest: WorkDigest, plain_map: Dict[int, str]) -> List[dict]:
    """One humanised section per non-empty category, in digest.categories order."""
    groups = _group_by_type(digest.merged_prs)
    out: List[dict] = []
    for t, _count in digest.categories:
        prs = groups.get(t, [])
        if prs:
            out.append(_benefit_section(t, prs, plain_map))
    return out


def _open_section(digest: WorkDigest, plain_map: Dict[int, str]) -> List[dict]:
    """The in-flight section, or [] when nothing is open."""
    if not digest.open_prs:
        return []
    return [_benefit_section("open / in-flight", list(digest.open_prs), plain_map)]


def _stats_accent(digest: WorkDigest) -> dict:
    """Honest pop-number fields — REAL digest counts only, never fabricated.

    "Honest" has to include admitting ignorance: a counter that could not be
    measured shows "unknown" and its reason, never a confident 0. Note
    ``digest.pr_count`` rather than ``len(digest.merged_prs)`` — an unread PR
    list is also an empty one.
    """

    def _one(field_name: str, value) -> str:
        return format_count(value, digest.unmeasured_reason(field_name) or "")

    fields = [
        {"type": "mrkdwn",
         "text": f"*Merged*\n{_one('merged_prs', digest.pr_count)} PRs"},
        {"type": "mrkdwn",
         "text": f"*Commits*\n{_one('commit_count', digest.commit_count)}"},
        {"type": "mrkdwn",
         "text": f"*Tests*\n{_one('test_units', digest.test_units)}"},
        {"type": "mrkdwn",
         "text": f"*Hypotheses*\n{_one('hypothesis_count', digest.hypothesis_count)}"},
    ]
    return {"type": "section", "fields": fields}


def _footer_block() -> dict:
    return {"type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": ":sparkles: _GRIP · the machine remembers "
                                  "what matters_"}]}


def render_delight_blocks(
    digest: WorkDigest,
    summary: WorkSummary,
    translation: PlainTranslation,
) -> List[dict]:
    """Delight++ Block Kit for #grip-updates: header -> TL;DR + narrative ->
    plain benefit sections -> honest pop-number accent -> GRIP signature footer.

    Every benefit line keeps its (#ref) verbatim; the accent uses ONLY real
    digest counts (no fabricated metrics)."""
    plain_map = translation.lines
    blocks: List[dict] = [
        _header_block(),
        _tldr_block(summary, translation.narrative),
        {"type": "divider"},
    ]
    blocks.extend(_merged_sections(digest, plain_map))
    blocks.extend(_open_section(digest, plain_map))
    blocks.append({"type": "divider"})
    blocks.append(_stats_accent(digest))
    note = unmeasured_note(digest)
    if note:
        # A post the team reads as fact says so when it was built on sources
        # that could not be read — in the post, not only in a log.
        blocks.append({"type": "section", "text": {
            "type": "mrkdwn", "text": f":warning: *Incomplete report* — {note}"}})
    blocks.append(_footer_block())
    return blocks
