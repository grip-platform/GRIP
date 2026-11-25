"""Self-report tagger: an agent's claims about its OWN behaviour are unverified by default.

Origin (2026-07-14, hypothesis H798): first-pass model self-reports about the
model's own reasoning, observations, and negatives ("I checked and it's not
there") were externally contradicted 4/4 times in one session. Even a
self-CRITICAL account is a fresh confabulation — the reliable move is a
controlled ground-truth test, never a more elaborate self-account.

This module is the pure classifier behind ``hooks/self-report-tagger.py``.
``classify(text)`` finds assertions the agent makes about its OWN
reasoning/observations/behaviour and reports the ones carrying no adjacent
evidence::

    {
        "self_reports": [{"span": str, "kind": str}, ...],
        "verdict": "tag" | "ok",
    }

Kinds:

``own-negative``
    First-person denials and empty-search claims: "I checked X and found
    nothing", "I never ran Y", "I didn't change Z".
``own-observation``
    First-person perception claims with no evidence: "I saw / observed /
    verified / confirmed ...".
``own-reasoning-history``
    Claims about the agent's own past reasoning: "my earlier reasoning
    was ...", "I did this because ...".

An evidence marker in the same paragraph OR an immediately adjacent one
(claim±1 — the natural claim-then-receipt markdown shape) suppresses the
tag — the point is UNVERIFIED self-reports only. Evidence shapes (kept
deliberately minimal and local; the premise-reproduce gate owns the richer
dispatch-side vocabulary): a fenced command/output block, an exit-code
mention, a ``file:line`` citation, a "reproduced:"/"controlled test" label,
a probe→status arrow, a git-porcelain status line (optionally blockquoted),
a blockquoted shell command, a backtick-quoted commit SHA, or an explicit
"ground truth …:"/"probes returned" citation. Paragraphs discussing the
detection machinery itself are exempt from the L1.5 semantic tier only
(meta-discussion guard); the L1 floor always applies.

Third-party claims ("the server is down") are NOT self-reports — they are
premise-gate territory (``lib/premise_reproduce_gate.py``). No I/O, no
state, no environment reads.
"""

from __future__ import annotations

import re

__all__ = ["classify", "quotable_sources"]

# First-person self-report shapes: (kind, compiled pattern).
_SELF_REPORT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("own-negative", re.compile(
        r"\bI\s+(?:checked|searched|looked|grepped|scanned|reviewed)\b"
        r"[^.\n]{0,120}?\b(?:no|not|nothing|nowhere|none|absent|empty"
        r"|couldn'?t\s+find|can'?t\s+find|doesn'?t\s+exist)\b", re.I)),
    ("own-negative", re.compile(
        r"\bI\s+(?:never|didn'?t|did\s+not|haven'?t|have\s+not)\s+"
        r"(?:ran|run|changed?|touched|modified|edited|sent|deleted|wrote"
        r"|created|pushed|committed)\b", re.I)),
    ("own-observation", re.compile(
        r"\bI\s+(?:saw|observed|verified|confirmed|noticed|watched)\b", re.I)),
    ("own-reasoning-history", re.compile(
        r"\bmy\s+(?:earlier|previous|prior|original)\s+"
        r"(?:reasoning|analysis|assumption|conclusion|thinking)\b", re.I)),
    ("own-reasoning-history", re.compile(
        r"\bI\s+did\s+(?:this|that|it)\s+because\b", re.I)),
)

# Minimal adjacent-evidence shapes that make a self-report VERIFIED.
# The last four entries are the day-zero calibration additions (task #477,
# FP classes a + d from logs/self-report-tagger.jsonl): blockquoted verbatim
# command output, git-porcelain status lines, backtick-quoted commit SHAs,
# and explicit probe-output citation labels. Widened ONLY after the
# falsification tests pinned the evidence-check boundary (order was
# load-bearing: recall loosened last, never first).
_EVIDENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```.+?```", re.S),                      # fenced command/output
    re.compile(r"\bexit\s+(?:code|status)\b|\becho\s+\$\?|\bexit(?:ed)?\s+0\b", re.I),
    re.compile(r"\b[\w./-]+\.[A-Za-z]{1,5}:\d+\b"),        # file:line citation
    re.compile(r"\brepro(?:duced|duction)?\s*:|\bcontrolled\s+(?:[\w-]+\s+)?test\b", re.I),
    re.compile(r"(?:->|→)\s*(?:HTTP\s*)?\d{3}\b"),          # probe -> status code
    # git-porcelain status line, optionally blockquoted: "> UU settings.json".
    # The path token must contain . or / so prose like "AM I sure" never matches.
    re.compile(r"^\s*(?:>\s?)?[MADRCU?!]{2}\s+\S*[./]\S*\s*$", re.M),
    re.compile(r"^\s*>\s?[$%]\s?\S", re.M),               # blockquoted shell command
    re.compile(r"`[^`\n]*\b[0-9a-f]{7,40}\b[^`\n]*`"),      # backticked commit SHA
    # explicit probe-output citation: "ground truth ...:" / "the probes returned"
    re.compile(r"\bground[- ]truth\b[^.\n]{0,40}:|\bprobes?\s+(?:returned|showed|confirmed)\b", re.I),
)


# --- quoted-evidence stripping (issue #4409) --------------------------------
#
# Quoting tool output IS the behaviour this rule set wants; the detector was
# punishing it. Two observed fires, both substring-derivable from a Europe PMC
# ``abstractText`` printed by a ``curl`` call earlier in the same turn:
#   "depleted dopamine by up to 99% using 6-OHDA across both nucleus accumbens
#    and neostriatum"
#   "De Dreu tested male participants only"
# An earlier session reproduced it against different tools (deed clauses from
# ``unzip``, a Google Calendar ``responseStatus`` field), so this is tool-agnostic.
#
# The fix treats a verbatim quote of tool output as an EVIDENCE MARKER, exactly
# like the fenced blocks and file:line citations in _EVIDENCE_PATTERNS. That is
# the architecturally consistent move: "I read the abstract: '<quote>'" is a
# claim about the agent's own behaviour whose receipt is sitting right there in
# the sentence.
#
# An earlier draft instead STRIPPED the quote and judged the remainder. Behaviour
# testing killed it: the real fires pair first-person prose with the quote
# ("I read the abstract: …"), so the residue is still first-person and still
# tagged — the false positive survived. Removal also had no way to express "the
# quote IS the evidence", which is the actual semantics.
#
# Admissibility is what keeps this honest: output the agent authored itself
# (echo/printf/heredoc) is never evidence, so a claim cannot be laundered by
# printing it and quoting it back. See quotable_sources.
_MIN_QUOTE_CHARS = 40
_QUOTED_SPAN = re.compile(
    r'"([^"]{%d,})"|“([^”]{%d,})”|`([^`]{%d,})`'
    % (_MIN_QUOTE_CHARS, _MIN_QUOTE_CHARS, _MIN_QUOTE_CHARS)
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Commands whose output the agent AUTHORED in the same breath. Quoting your own
# echo proves nothing, so these never count as evidence — otherwise a fabricated
# claim could be laundered by printing it first and then quoting it.
#
# Widened after cross-provider review of #4409 pointed out that blocking
# echo/printf/heredoc blocks three SPELLINGS of "the agent prints text", not the
# capability: an inline interpreter, an awk BEGIN block, `jq -n`, a herestring,
# or a base64 payload all emit arbitrary bytes just as well.
#
# This stays a syntactic approximation of a provenance question, and the review
# was right that provenance is the real fix. The residual gap is documented in
# quotable_sources: an agent can still write a claim into a real file and read it
# back, which is deliberate guardrail evasion (rules/no-guardrail-evasion.md)
# rather than an accident this detector is built to catch.
_SELF_AUTHORED = re.compile(
    r"(?:^|[|;&]\s*)(?:echo|printf|yes)\b"                          # direct printers
    r"|<<<"                                                          # herestring
    r"|<<-?\s*['\"]?\w+"                                             # heredoc
    r"|\b(?:python3?|node|perl|ruby|deno)\b[^|;&\n]*\s-[a-zA-Z]*[ce]\b"  # inline scripts
    # Shells take -c only. `bash -e script.sh` is errexit on a real file, not an
    # inline string, and must stay admissible -- so this alternative deliberately
    # does NOT reuse the [ce] class above.
    # Naming only the shells one happens to think of leaves every other one as
    # a way through. `dash` IS /bin/sh on Debian and Ubuntu, so the most
    # ordinary shell on the most ordinary CI image was the one omitted. What a
    # shell is symlinked to cannot rescue the match either: this reads the
    # command STRING, so the name written is the name matched.
    #
    # The closing \b is what stops the list swallowing ordinary words --
    # "dashboard" is not dash and "ssh" is not sh, because neither offers a
    # word boundary where the shell name would have to begin or end.
    r"|\b(?:bash|dash|zsh|ksh|mksh|pdksh|tcsh|csh|fish|ash|sh)"
    r"\b[^|;&\n]*\s-[a-zA-Z]*c\b"                                        # shell -c
    r"|\bjq\b[^|;&\n]*\s-[a-zA-Z]*n\b"                               # jq --null-input
    r"|\bawk\b[^|;&\n]*BEGIN\s*\{"                                   # awk generator
    r"|\bbase64\b[^|;&\n]*\s-{1,2}d\w*\b"                            # decoded literal
)


def _normalise(text: str) -> str:
    """Whitespace-collapsed, lowercased form — a re-wrapped quote still matches."""
    return " ".join(text.split()).lower()


def quotable_sources(tool_calls) -> tuple[str, ...]:
    """Tool outputs admissible as quoted evidence (i.e. NOT self-authored).

    ``tool_calls`` is an iterable of ``{"command": str, "output": str}``. Output
    from ``echo``/``printf``/a heredoc is excluded: the agent wrote it, so
    quoting it back is not evidence of anything.
    """
    admissible = []
    for call in tool_calls or ():
        command = str((call or {}).get("command") or "")
        output = str((call or {}).get("output") or "")
        if output.strip() and not _SELF_AUTHORED.search(command):
            admissible.append(output)
    return tuple(admissible)


def _is_quoted_from(segment: str, haystack: str) -> bool:
    """True when a long-enough segment reproduces source text verbatim."""
    body = segment.strip()
    return len(body) >= _MIN_QUOTE_CHARS and _normalise(body) in haystack


def _quotes_a_source(paragraph: str, haystack: str) -> bool:
    """True when the paragraph reproduces admissible tool output verbatim.

    Checked as a quotation-marked span first (the shape the observed fires
    took), then as a whole pasted sentence.
    """
    for match in _QUOTED_SPAN.finditer(paragraph):
        content = next(g for g in match.groups() if g is not None)
        if _is_quoted_from(content, haystack):
            return True
    return any(_is_quoted_from(s, haystack)
               for s in _SENTENCE_SPLIT.split(paragraph))


def _quoted_evidence_flags(paragraphs: list[str], sources: tuple[str, ...]) -> list[bool]:
    """Per-paragraph: does it carry a verbatim quote of admissible tool output?"""
    if not sources:
        return [False] * len(paragraphs)
    haystack = " || ".join(_normalise(s) for s in sources)
    return [_quotes_a_source(p, haystack) for p in paragraphs]


def _paragraphs(text: str) -> list[str]:
    """Blank-line-separated paragraphs; the adjacency window for evidence."""
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _has_evidence(paragraph: str) -> bool:
    """True when any minimal evidence shape appears in the paragraph."""
    return any(pattern.search(paragraph) for pattern in _EVIDENCE_PATTERNS)


def _spans_for(kind: str, pattern: re.Pattern[str], paragraph: str) -> list[dict]:
    """Every span one self-report pattern matches in one paragraph."""
    return [{"span": m.group(0), "kind": kind} for m in pattern.finditer(paragraph)]


def _reports_in(paragraph: str) -> list[dict]:
    """Every self-report span in one paragraph (deduped by span text)."""
    found: list[dict] = []
    seen: set[str] = set()
    for kind, pattern in _SELF_REPORT_PATTERNS:
        _extend_unseen(found, seen, _spans_for(kind, pattern, paragraph))
    return found


def _extend_unseen(found: list[dict], seen: set[str], reports: list[dict]) -> None:
    """Append reports whose span text has not been recorded yet."""
    for report in reports:
        if report["span"] not in seen:
            seen.add(report["span"])
            found.append(report)


# First-person markers that make a paragraph ELIGIBLE for the semantic tier
# (L1.5) when the exact regex floor found nothing in it.
_FIRST_PERSON = re.compile(r"\bI\b|\bmy\b", re.I)

# Reported-span budget for the L1.5 tier. The span is a WINDOW CENTRED ON THE
# FIRST-PERSON MATCH, not a leading prefix (task #642): the whole point of the
# excerpt is to show the reader the construction that made the paragraph
# eligible, and a fixed prefix routinely cut that construction off. Measured on
# logs/self-report-tagger.jsonl before this change: of 1787 own-semantic spans,
# 1431 (80.1%) hit the cap exactly and 475 (26.6%) contained no first-person
# marker at all -- so a quarter of every advisory ever raised showed a reader an
# excerpt with the trigger provably absent, which is why the false-positive rate
# recorded in task #642 is itself unreliable evidence. Budget unchanged at 160
# so advisory and audit-log size are unaffected; only WHICH 160 changed.
_SPAN_BUDGET = 160
_SPAN_LEAD = 60  # characters of run-up kept before the match, for context

# Meta-discussion guard (task #477, FP class c): prose ABOUT the confabulation
# machinery itself — naming the gates, quoting the advisory, describing a
# fire — reads confab-shaped to the L1.5 classifier (its training corpus IS
# that vocabulary). Same carve-out shape as no-guardrail-evasion's
# self-documentation exemption: documenting the detector is not the failure
# the detector exists to catch. Guards ONLY the L1.5 tier; the L1 regex floor
# is untouched (narrowing the L2 pre-filter is the sanctioned FP-bounding
# move per rules/llm-augmentation-pattern.md — never a monotonicity breach).
_META_DISCUSSION = re.compile(
    r"\bself[- ]report(?:s|er|ing)?\b|\bconfab(?:ulat\w+)?\b"
    r"|\[UNVERIFIED SELF-REPORT"
    r"|\bpremise[- ]reproduce\b|\bphantom[- ]chase\b|\btool[- ]error[- ]self[- ]check\b"
    r"|\bthe\s+(?:gate|hook|detector|classifier|tagger)\s+(?:fire[ds]|flag(?:s|ged)|tag(?:s|ged)|warn(?:s|ed)?)\b",
    re.I,
)


# Structural non-prose guard (FP class d, 2026-07-27): markdown HEADINGS and
# TABLES are section labels and quoted data, not assertions about the agent's
# own reasoning. A heading's substance is restated in the body paragraphs that
# follow it, and those ARE evaluated on their own; a table reproduces file or
# command content rather than claiming anything about how the agent reasoned.
# Flagging either adds an advisory with no checkable claim behind it.
# Measured on logs/self-report-tagger.jsonl for 2026-07-27: 6 of that day's 52
# own-semantic spans were heading-only or table-only paragraphs (5 headings,
# 1 table). Deliberately NOT exempted: list blocks and short lines — sampling
# showed those do carry real self-reports ("the 109 facts I pulled from your
# email ..."), so exempting them would suppress signal, not noise.
# Guards ONLY the L1.5 tier; the L1 regex floor is untouched, so this narrows
# the L2 pre-filter exactly as the meta-discussion guard above does
# (rules/llm-augmentation-pattern.md — never a monotonicity breach).
# ATX headings ONLY (`## Heading`). Setext headings (text underlined with ===
# or ---) are deliberately NOT exempt: every heading false positive in the
# measured window was ATX, so widening to setext would be speculative scope
# with no observed instance behind it. A setext heading therefore still
# reaches the L1.5 consult -- an accepted, documented recall gap in the
# EXEMPTION, not in the detector (worst case is the pre-existing advisory).
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s")
# A markdown table is identified by its DELIMITER ROW, not by pipe count.
# Counting pipes is not sound: `^\s*\|` swallows "    | grep 'error'", and
# even a two-pipe rule swallows "    | cat log.txt | grep error" (both raised
# by the AGORA council on PR #4176, rounds 1 and 2). GitHub-flavoured markdown
# REQUIRES a delimiter row -- `|---|---|`, optionally with alignment colons --
# so requiring one separates real tables from pipe-heavy shell output, which
# never has a delimiter row regardless of how many pipes it carries.
_TABLE_LINE = re.compile(r"^\s*\|")
_TABLE_DELIMITER = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _is_structural_non_prose(paragraph: str) -> bool:
    """True for an all-ATX-heading paragraph, or a real markdown table.

    A paragraph that MIXES a heading or table with prose is not exempt — the
    prose in it still deserves evaluation.

    A table must carry a DELIMITER ROW, not merely pipes: pipe-heavy shell
    output ("| cat log.txt | grep error") is not a table and must stay
    eligible for the semantic tier.

    Note on whitespace-only lines: they cannot occur inside a paragraph here,
    because ``_paragraphs`` splits on ``\\n\\s*\\n`` — a line of spaces IS a
    paragraph boundary. The ``line.strip()`` filter therefore only drops
    leading/trailing blanks, and cannot widen the exemption.
    """
    lines = [line for line in paragraph.splitlines() if line.strip()]
    if not lines:
        return False
    if all(_HEADING_LINE.match(line) for line in lines):
        return True
    return (all(_TABLE_LINE.match(line) for line in lines)
            and any(_TABLE_DELIMITER.match(line) for line in lines))


def _trigger_window(paragraph: str) -> str:
    """Whitespace-collapsed excerpt centred on the first-person match.

    The excerpt exists so a reader can judge the advisory, which requires
    seeing the first-person construction that made the paragraph eligible. A
    leading prefix hid it whenever the marker sat past the budget.

    Short paragraphs are returned whole. A window that does not reach an edge
    is marked with an ellipsis on that side, so a mid-paragraph excerpt can
    never be misread as a sentence that genuinely starts (or ends) there.

    The match is re-searched on the COLLAPSED text rather than reused from the
    caller's gate, because collapsing runs of whitespace shifts every index
    after the first one. Collapsing cannot destroy the match itself -- neither
    marker contains whitespace, and a single space preserves the word
    boundaries both patterns rely on -- but a no-match result still degrades
    to the leading window rather than raising.
    """
    collapsed = " ".join(paragraph.split())
    if len(collapsed) <= _SPAN_BUDGET:
        return collapsed
    match = _FIRST_PERSON.search(collapsed)
    start = max(0, (match.start() if match else 0) - _SPAN_LEAD)
    end = start + _SPAN_BUDGET
    return "{}{}{}".format(
        "..." if start else "", collapsed[start:end],
        "..." if end < len(collapsed) else "")


def _semantic_report(paragraph: str, consult) -> list[dict]:
    """L1.5 consult for one L1-silent paragraph (monotone: add-only).

    ``consult`` is a dependency-injected ``callable(str) -> bool`` — in
    production the SIGD discriminative classifier
    (``lib/confab_sigd_classifier.consult``: CF-IOF margin + abstention);
    ``None`` keeps the pure regex floor byte-identical to v1. A consult
    that raises is treated as abstention — the semantic tier must never
    break the floor. Paragraphs discussing the detection machinery itself
    (meta-discussion guard) or carrying no prose at all (structural guard)
    are skipped.

    The reported span is a window centred on the first-person match
    (``_trigger_window``), never a leading prefix -- the excerpt must
    carry the construction that made the paragraph eligible.
    """
    if consult is None or not _FIRST_PERSON.search(paragraph):
        return []
    if _META_DISCUSSION.search(paragraph):
        return []
    if _is_structural_non_prose(paragraph):
        return []
    try:
        flagged = bool(consult(paragraph))
    except Exception:  # noqa: BLE001 — semantic tier fails open to the floor
        return []
    span = _trigger_window(paragraph)
    return [{"span": span, "kind": "own-semantic"}] if flagged else []


def _unverified_reports(paragraphs: list[str], consult=None, sources=()) -> list[dict]:
    """Self-reports from every paragraph lacking evidence within +/-1 paragraph.

    The adjacency window is claim-paragraph plus its immediate neighbours
    (task #477, FP class b): a receipt one blank line away — the natural
    claim-then-blockquote markdown shape — suppresses exactly like same-
    paragraph evidence. Evidence further away never suppresses.

    Two tiers per paragraph: the L1 regex floor first; when the floor is
    silent, the optional L1.5 semantic consult (which may only ADD a tag,
    never remove an L1 one — the monotonicity invariant from
    ``rules/llm-augmentation-pattern.md``).
    """
    quoted = _quoted_evidence_flags(paragraphs, sources)
    evidence = [_has_evidence(p) or quoted[i] for i, p in enumerate(paragraphs)]
    unverified: list[dict] = []
    for i, paragraph in enumerate(paragraphs):
        if any(evidence[max(0, i - 1):i + 2]):
            continue
        floor = _reports_in(paragraph)
        unverified.extend(floor if floor else _semantic_report(paragraph, consult))
    return unverified


def classify(text: str, semantic_consult=None, quoted_sources=()) -> dict:
    """Find UNVERIFIED first-person self-reports in ``text``.

    A self-report whose paragraph also carries an evidence marker is
    suppressed (it is verified, or at least verifiable, in place).
    ``semantic_consult`` (optional ``callable(str) -> bool``) is the L1.5
    discriminative tier consulted only for first-person paragraphs the
    regex floor missed; omitted, behaviour is byte-identical to the pure
    floor. Returns the dict documented in the module docstring;
    ``verdict`` is ``"tag"`` when at least one unverified self-report
    survives.
    """
    body = text if isinstance(text, str) else ""
    unverified = _unverified_reports(
        _paragraphs(body), semantic_consult, tuple(quoted_sources or ()))
    return {
        "self_reports": unverified,
        "verdict": "tag" if unverified else "ok",
    }
