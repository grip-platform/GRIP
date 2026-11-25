#!/usr/bin/env python3
"""Genre routing for the canonical session report (V>> 2026-08-24).

`rules/canonical-output-style.md` calls the prose/bullets split "testable"
and `docs/cos-w0-constraint-lock.md` records it as a genre-scoped
amendment. This module is that test. It classifies a report BODY:

  ``prose``      — fewer than the bullet floor; the split does not apply.
  ``inventory``  — parallel facts, one per bullet. LEGITIMATE.
  ``template``   — a narrative cut into labelled report-part slots.
                   FORBIDDEN: the reader reassembles meaning the labels
                   took apart, and an empty slot still prints.
  ``narrative``  — causal / trade-off content fragmented into bullets.
                   FORBIDDEN: *because* and *therefore* have no home in a
                   chain of interpuncts.

The discriminator between ``template`` and ``inventory`` is WHAT THE LABEL
NAMES, not whether a label exists. A slot label names a REPORT PART
("**What** — …", "**Why** — …"); an inventory mini-header names a SUBJECT
("**The collision** — …"). Bolding alone is not the tell, which is why
`_REPORT_PART` is a closed vocabulary rather than a shape.

Detector only, deliberately NOT wired as a hook. `no-stubs.md` and
`tests-must-be-able-to-fail.md` both require a measured false-positive
window under 10% before a detector earns DENY, and this one has no window
yet. Import it, or run it over a file; nothing calls it in a gate.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

#: A body needs at least this many bullets before the split applies at all.
#: Below it the content is prose by default -- two bullets are a pair of
#: sentences wearing dashes, not an inventory.
BULLET_FLOOR = 3

#: Fraction of bullets that must carry the tell before a body is rejected.
#: A single causal bullet inside a real inventory is normal; a majority of
#: them means the author wrote an argument and reached for dashes.
MAJORITY = 0.5

_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")

#: Generic REPORT-PART nouns -- the GRASP slot family the amendment names.
#: A bullet opening with one of these is a slot, not a fact.
#:
#: Deliberately EXCLUDES outcome-domain nouns: status, result, impact, next,
#: outcome, action, problem, solution, approach, risk, goal, scope, blocked,
#: learnings. Those name a SUBJECT, and the amendment routes exactly that
#: genre -- "a multi-fact status or deliverable report" -- to bullets, so
#: banning them contradicted the rule this module implements. Caught by the
#: AGORA council on PR #5073 with a runnable counter-example: a close-out of
#: Status / Result / Next / Risk headers classified `template`, `ok=False`.
_REPORT_PART = {
    "what", "why", "how", "who", "when", "where",
    "summary", "context", "background", "evidence", "belief",
    "detail", "details", "note", "notes",
    "what was achieved", "what i believed", "cite.verify", "tldr",
}

#: A label whose FIRST word is a bare interrogative is a slot however long
#: it runs -- "What was achieved this session" is the same shape as "What".
#: Without this, only the exact strings above were caught and any longer
#: phrasing escaped detection entirely.
_INTERROGATIVE = {"what", "why", "how", "who", "when", "where"}

#: Opening label of a bullet: leading bold/emphasis or a bare word run
#: terminated by a dash, colon or en/em dash.
#:
#: No length cap. A cap of 24, then 48, each let a longer slot label return
#: None and escape detection ("What was achieved for the operator during this
#: long overnight loop" is 67 characters), so every cap was a blind spot one
#: notch further out (GRIP#5081).
#:
#: The label is matched word by word, lazily, so no space is claimed both by
#: the label and by the terminator's `\s*`. The single-class form overlapped
#: there and backtracked quadratically: 387 ms on one bullet holding 1,600
#: spaces, and unbounded it would have been worse. This form is linear.
_LABEL = re.compile(
    r"^(?:\*\*|__)?\s*([A-Za-z][A-Za-z0-9./_-]*?(?: +[A-Za-z0-9./_-]+?)*?)"
    r"\s*(?:(?:\*\*|__)\s*)?[-–—:]\s"
)

#: Causal / trade-off connectives -- content that belongs in prose.
_CAUSAL = re.compile(
    r"\b(?:because|therefore|thus|hence|consequently|so that|"
    r"which means|as a result|for that reason|owing to|due to the fact|"
    r"whereas|on the other hand|the trade-?off|root cause)\b",
    re.IGNORECASE,
)

#: Anything that reads as a citable referent: a URL, a repo-qualified id,
#: a path, or a seal-shaped hex run.
_EVIDENCE = re.compile(
    r"https?://\S+"
    r"|\b[A-Za-z][\w-]*#\d+\b"
    r"|\b[\w./-]+\.[A-Za-z]{1,6}(?::\d+)?\b"
    r"|\b[0-9a-f]{8,64}\b"
)


@dataclass
class GenreResult:
    """Verdict for one report body. ``ok`` is False only for a shape the
    amendment forbids -- ``prose`` and ``inventory`` both pass."""

    shape: str
    bullets: int = 0
    links_inline: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.shape in {"prose", "inventory"}


def _unfenced_lines(body: str) -> list[str]:
    """The body's lines with fenced regions removed.

    ONE definition of "the body", shared by the bullet count and the orphan
    scan. They used to disagree: ``_bullets`` counted marker lines inside
    code fences while the orphan scan skipped them, so a fenced sample
    quoting the banned template form -- which the rule and lock documents
    both do -- inflated the bullet count and flipped a compliant three-bullet
    close-out to ``template``. Caught by the AGORA council on PR #5073.
    """
    out, in_fence = [], False
    for line in (body or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return out


def _bullets(body: str) -> list[str]:
    return [m.group(1) for m in (_BULLET.match(ln) for ln in _unfenced_lines(body)) if m]


def _label_of(bullet: str) -> str | None:
    m = _LABEL.match(bullet)
    return m.group(1).strip().lower() if m else None


def _is_slot_label(label: str | None) -> bool:
    """True when a bullet's opening label names a REPORT PART, not a subject."""
    if not label:
        return False
    if label in _REPORT_PART:
        return True
    return label.split()[0] in _INTERROGATIVE


#: Closing elements the contract MANDATES below the manifest. Evidence on
#: these lines is correct, not orphaned: the witness card / see-it stanza is
#: the block's sanctioned closing element, and a star Insight block carries
#: its own file:line by design. Flagging them was a false positive the AGORA
#: council caught on PR #5073.
_CLOSING_HEAD = re.compile(
    r"^\s*(?:see it:|`?\s*★ Insight|\|?\s*witness\b|┆)",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^\s*(?:```|~~~)")


def _orphan_link_block(body: str) -> bool:
    """True when evidence sits on an ORDINARY tail line below the manifest.

    The banned shape is links gathered into a trailing dump instead of
    sitting in the bullet whose fact they evidence. It is NOT the witness
    card, the see-it stanza or a star Insight block -- the contract requires
    those to close the block, and an earlier version flagged all three.

    Fenced regions are skipped: a code block below the manifest is a sample,
    not a claim's evidence.

    The dead ``bullet_texts`` parameter this once declared was removed rather
    than wired, per rules/declared-input-must-reach-the-actor.md -- an input
    a signature declares and the body never reads is forbidden either way.
    """
    lines = _unfenced_lines(body)
    last_bullet = -1
    for i, line in enumerate(lines):
        if _BULLET.match(line):
            last_bullet = i
    if last_bullet < 0:
        return False

    bullet_indent = len(lines[last_bullet]) - len(lines[last_bullet].lstrip())
    in_closing = False
    for line in lines[last_bullet + 1:]:
        if _CLOSING_HEAD.match(line):
            in_closing = True
            continue
        if not line.strip():
            continue
        if in_closing:
            # Inside a closing element every row is sanctioned until the
            # block ends; a blank line does not end it (the see-it stanza
            # is one URL per line and the card has label rows).
            continue
        if len(line) - len(line.lstrip()) > bullet_indent:
            # A markdown lazy-continuation line belongs to the bullet above
            # it, and the contract explicitly sanctions the wrapped layout:
            # "A URL on its own line soft-wraps and stays clickable in a
            # narrow pane." Flagging it condemned compliant output. Every
            # genuine dump is flush-left.
            continue
        if _EVIDENCE.search(line) and not _BULLET.match(line):
            return True
    return False


def classify(body: str) -> GenreResult:
    """Route a report body to its genre. Pure; no I/O."""
    bullets = _bullets(body)
    n = len(bullets)
    if n < BULLET_FLOOR:
        return GenreResult(shape="prose", bullets=n)

    slots = [b for b in bullets if _is_slot_label(_label_of(b))]
    if len(slots) / n > MAJORITY:
        return GenreResult(
            shape="template",
            bullets=n,
            reasons=[f"{len(slots)}/{n} bullets open with a report-part slot label"],
        )

    causal = [b for b in bullets if _CAUSAL.search(b)]
    if len(causal) / n > MAJORITY:
        return GenreResult(
            shape="narrative",
            bullets=n,
            reasons=[f"{len(causal)}/{n} bullets carry a causal connective"],
        )

    orphaned = _orphan_link_block(body)
    return GenreResult(
        shape="inventory",
        bullets=n,
        links_inline=not orphaned,
        reasons=(["evidence tokens sit below the last bullet, not inside it"]
                 if orphaned else []),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: cos_summary_genre.py <file>", file=sys.stderr)
        return 2
    r = classify(open(argv[1], encoding="utf-8").read())
    print(f"shape={r.shape} bullets={r.bullets} links_inline={r.links_inline} ok={r.ok}")
    for reason in r.reasons:
        print(f"  - {reason}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
