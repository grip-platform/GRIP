"""Deterministic bare-internal-number detector — the mechanical floor for the
canonical-output-style gloss clause (the pending edge-E check, harness task #438).

An internal reference (an issue / PR / task number like ``#4039``, a hypothesis
id ``H798``) is a *bare number* when it appears in assistant prose with NO
adjacent plain-language gloss — a subject word describing WHAT it is. The
operator reads ``#4039`` and cannot resolve it; worse, harness task numbers
COLLIDE with GitHub issue/PR numbers, so a bare ``#479`` mis-links to an
unrelated PR. Prose-only enforcement of the gloss clause demonstrably decays
late-session (N=3 operator corrections 2026-07-02 / 07-05 / 07-13), so this is
the deterministic floor a Stop-hook advisory consumes.

CONTRACT: ``detect_bare_numbers(text) -> {"verdict": "tag"|"clean",
"findings": [{"token","line","kind"}]}``. Pure, zero side effects, zero deps.

GLOSS HEURISTIC (advisory-grade — tuned toward NOT flagging): a token is
*glossed* iff a SUBJECT word — an alphabetic content token (>=3 chars) that is
not a label / function word / common verb / filler (``_STOP``) — appears within
``GLOSS_WINDOW`` chars of the token on the SAME line. Otherwise it is *bare*.
The stoplist deliberately absorbs the generic verbs and fillers that surround a
bare number ("merge #4039", "tracked by H798", "picking up task #479"), so only
a real domain / proper-noun subject ("AGORA", "happi/1.3", "hal-path") counts as
a gloss. This is a NUDGE, not a gate — false negatives are cheaper than false
positives (never nag on an already-glossed reference).

EXEMPTIONS (never flagged): fenced code blocks (``` fences), quote / verbatim
lines (leading ``>``), external upstream refs ``owner/repo#N`` (e.g.
``anthropics/claude-code#39342`` — exogenous + resolvable), and any token on a
line that also carries a URL.

COMPLEXITY: two SEQUENTIAL passes (build exempt char-ranges, then one scan over
token matches with a bisect exemption lookup) — O(len(text)); no nested scans.

FALSIFICATION: wrong if, over the sessions it governs, it flags glossed
references more often than it catches bare ones (advisory noise > signal) ->
widen ``_STOP`` / shrink ``GLOSS_WINDOW``, or demote to off. Track the audit
JSONL flag-rate against operator "that was already glossed" corrections.
"""
from __future__ import annotations

import bisect
import re

# Internal reference tokens that mean nothing without a gloss. The negative
# lookbehind on ``#`` keeps ``owner/repo#N`` (external, resolvable) out.
_TOKEN_RE = re.compile(r"(?<![\w/])#\d+\b|(?<![\w])H\d+\b")

# External upstream refs (owner/repo#N) are exogenous — never internal.
_EXTERNAL_REF_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#\d+")
_URL_RE = re.compile(r"https?://")

# A subject word must be >=3 chars (so "PR"/"#" never register) and may carry
# ``/ . _ -`` so domain terms like "happi/1.3" and "hal-path" count as content.
_CONTENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9._/-]{2,}")

# A leading ``Label:`` that DIRECTLY abuts the first internal-reference token on
# a line is a section / bucket label (``Remaining:``, ``Backlog:``, ``Parked:``,
# ``Icebox:`` ...), NOT a gloss. ``_STOP`` below covers the *known* anti-drift
# labels, but section labels are UNBOUNDED — ``Backlog: #12`` leaked because
# "backlog" is not stoplisted, so it masqueraded as a same-line subject and
# cleared the number. Masking the label is false-positive-SAFE on two axes:
#   (1) it fires only when a reference token DIRECTLY follows the colon (the
#       lookahead) — so ``hal-path: the fix (#12)``, with a real subject BETWEEN
#       the label and the number, is never masked;
#   (2) the label must be a PLAIN alphabetic word (``[A-Za-z]+``) — a domain term
#       carrying ``- / .`` (``hal-path:#12``, ``happi/1.3:#12``) is a likely
#       subject, not a bucket label, so it stays glossed. Bucket labels
#       (Backlog / Parked / Icebox / Remaining) are plain words and ARE masked.
# The colon is kept (not NUL-ed): it is inert to every downstream regex
# (_TOKEN_RE, _CONTENT_RE, _line_bounds) and keeping it preserves char offsets.
# (AGORA PR #4061 + PR #4074 DEFER, task #515.)
_SECTION_LABEL_RE = re.compile(r"^(\s*)([A-Za-z]+):(?=\s*(?:#\d+|H\d+))", re.MULTILINE)

# Labels + function words + common verbs + fillers that do NOT constitute a
# gloss. Only a real subject noun surviving this set proves the number was
# described. "task"/"pr"/"issue" are exactly the meaningless labels flagged.
_STOP = {
    # labels
    "task", "tasks", "pr", "prs", "issue", "issues", "wave", "hypothesis",
    "commit", "branch", "gen", "generation",
    # function words
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "of", "for",
    "is", "was", "are", "were", "be", "been", "this", "that", "these", "those",
    "it", "its", "as", "by", "with", "from", "into", "out", "off", "per", "via",
    "not", "my", "our", "your", "their", "his", "her", "so", "if", "then",
    # common verbs that typically flank a bare number
    "merge", "merged", "merging", "merges", "fix", "fixed", "fixing", "fixes",
    "ship", "shipped", "shipping", "ships", "push", "pushed", "pushing",
    "close", "closed", "closes", "closing", "open", "opened", "opens",
    "run", "ran", "running", "runs", "see", "seeing", "saw", "verify",
    "verified", "check", "checked", "add", "added", "adds", "build", "built",
    "use", "used", "get", "got", "put", "track", "tracked", "pick", "picking",
    "picked", "picks", "make", "made", "did", "done", "going",
    "went", "land", "landed", "hit", "set", "drive", "driving", "drove",
    "pending", "review", "reviewed", "note", "noted", "flag", "flagged",
    "surface", "surfaced", "start", "started", "resume", "resolve", "resolved",
    "address", "addressed", "converge", "converged", "poll", "polled",
    # lifecycle / deferral / reversion verbs that flank a bare number the same
    # way "merge #N" does. 2026-07-28: "defer #574 — done" shipped CLEAN because
    # "defer" was the only content word on the line and was not stoplisted, so it
    # masqueraded as the gloss (the same omission class as the #515 section-label
    # leak, one verb family over). A real subject elsewhere in the window still
    # clears the number ("the git-crypt rotation deferred (#574)" stays glossed),
    # so this only tightens the naked-verb case. Deliberately EXCLUDES the more
    # noun-ambiguous "draft"/"gate"/"pin"/"cut" (design is tuned toward not-flagging).
    #
    # "more" is doing real work in that sentence, and issue #4197 asked it to say
    # so plainly: "file", "wire" and "skip" below ARE noun-ambiguous too, they are
    # just less so in this codebase, where an internal reference is an issue / PR /
    # task and never a numbered file or wire. The accepted cost is that a line
    # whose ONLY content word is one of them ("the file #12 is ready" — "the",
    # "is" and "ready" are all stoplisted too) flags. One real subject anywhere in
    # the window clears it, which is the ordinary case and is pinned by
    # test_stoplisted_verb_used_as_noun_is_the_documented_boundary.
    "defer", "deferred", "defers", "deferring",
    "revert", "reverted", "reverts", "reverting",
    "bump", "bumped", "bumps", "bumping",
    "hold", "held", "holds", "holding",
    "wire", "wired", "wires", "wiring",
    "squash", "squashed", "squashes", "squashing",
    "rebase", "rebased", "rebases", "rebasing",
    "prune", "pruned", "prunes", "pruning",
    "delete", "deleted", "deletes", "deleting",
    "skip", "skipped", "skips", "skipping",
    "file", "filed", "files", "filing",
    # adverbs / fillers / state words
    "now", "then", "here", "there", "both", "all", "each", "one", "two",
    "three", "next", "first", "last", "only", "just", "still", "also", "again",
    "back", "down", "green", "red", "ready", "live", "clean", "good",
    "let", "me", "we", "you", "they", "them", "us", "will", "can", "should",
    "when", "while", "before", "after", "once", "which", "who", "what",
    # anti-drift / summary-block section labels — these lead a Remaining / Open /
    # Next / Needs / Done line and must NOT count as a gloss. 2026-07-16:
    # "Remaining: #23, #38, #499" shipped bare because "remaining" (a section
    # label, not a subject noun) masqueraded as an adjacent gloss and cleared
    # every number on the line. "done"/"open"/"next"/"shipped" were already here.
    "remaining", "needs", "outstanding", "blockers", "todo", "carryover", "wip",
}

GLOSS_WINDOW = 55  # chars either side of the token, same line


# Trailing / leading punctuation stripped before the stoplist check so "now."
# and "fix," normalise to "now" / "fix" (else the punctuation hides a stopword).
_STRIP = ".,;:!?()[]{}'\"`"


def _has_subject_word(fragment: str) -> bool:
    """True if the fragment carries a non-stoplisted content (subject) word."""
    return any(
        (norm := w.strip(_STRIP).lower()) and norm not in _STOP
        for w in _CONTENT_RE.findall(fragment)
    )


def _mask_section_labels(masked: str) -> str:
    """Blank a leading ``Label:`` that directly abuts a reference token so the
    bucket label cannot count as the gloss for the numbers it introduces.

    Same-length replacement (label chars -> NUL) keeps every token offset
    aligned with the source ``text`` used for line bounds and finding output.
    """
    return _SECTION_LABEL_RE.sub(
        lambda m: m.group(1) + "\x00" * len(m.group(2)) + ":", masked
    )


def _exempt_ranges(text: str) -> list[tuple[int, int]]:
    """Pass 1: char ranges that are exempt (fenced-code / quote / URL lines).

    Kept a separate pass so the token scan is a single (non-nested) loop.
    """
    ranges: list[tuple[int, int]] = []
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\n")
        stripped = bare.lstrip()
        is_fence_marker = stripped.startswith("```")
        exempt = (
            is_fence_marker
            or in_fence
            or stripped.startswith(">")
            or bool(_URL_RE.search(bare))
        )
        if is_fence_marker:
            in_fence = not in_fence
        if exempt:
            ranges.append((offset, offset + len(line)))
        offset += len(line)
    return ranges


def _is_exempt(starts: list[int], ranges: list[tuple[int, int]], pos: int) -> bool:
    idx = bisect.bisect_right(starts, pos) - 1
    return idx >= 0 and ranges[idx][0] <= pos < ranges[idx][1]


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    left = text.rfind("\n", 0, pos) + 1
    right = text.find("\n", pos)
    return left, (len(text) if right == -1 else right)


def detect_bare_numbers(text: str) -> dict:
    """Return findings for every internal reference token lacking a same-line gloss."""
    ranges = _exempt_ranges(text)
    starts = [r[0] for r in ranges]
    # Mask external refs (same length) so their trailing #N cannot be a token
    # AND cannot masquerade as a gloss content word inside a window.
    masked = _EXTERNAL_REF_RE.sub(lambda m: "\x00" * len(m.group(0)), text)
    # Blank leading section/bucket labels ("Backlog: #12") so they cannot
    # masquerade as a same-line gloss for the numbers they introduce (task #515).
    masked = _mask_section_labels(masked)
    findings: list[dict] = []
    for m in _TOKEN_RE.finditer(masked):
        start, end = m.span()
        if _is_exempt(starts, ranges, start):
            continue
        lo, hi = _line_bounds(text, start)
        w_start = max(lo, start - GLOSS_WINDOW)
        w_end = min(hi, end + GLOSS_WINDOW)
        window = masked[w_start:start] + masked[end:w_end]
        if not _has_subject_word(window):
            token = text[start:end]
            kind = "hypothesis" if token[0] == "H" else "issue-pr-or-task"
            findings.append({"token": token, "line": text[lo:hi].strip()[:160], "kind": kind})
    return {"verdict": "tag" if findings else "clean", "findings": findings}
