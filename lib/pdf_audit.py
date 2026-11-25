"""pdf_audit — the pure logic behind ``bin/pdf-audit``.

``skills/professional-pdf/SKILL.md`` declares a visual audit MANDATORY before any
PDF is shipped, and names ``bin/pdf-audit`` as the tool. That tool did not exist,
so the memory ``feedback_professional_pdf_bug_prevention`` Rule 8 documents a
manual fallback instead ("manual when ``bin/pdf-audit`` isn't installed").

The gap is not cosmetic. Running the fallback by hand on 2026-08-01 still MISSED
a documented failure class, because the SKILL's inline table lists five classes
while the memory lists six — the checklist was not in front of the auditor at the
moment of judging. So the load-bearing job of this module is to put the COMPLETE
checklist next to the rendered pages, every time, rather than relying on recall.

Pure: no rendering, no subprocess, no filesystem writes. ``bin/pdf-audit`` owns
those. Everything here is unit-testable with plain data.
"""

from __future__ import annotations

from statistics import median
from typing import Dict, List, Sequence

# The COMPLETE set. Class 6 exists because a real PDF shipped with it
# (2026-05-31, page background painted only on body → white gutters), and
# class 4 because a footer div in the document body renders as content,
# floating mid-page, rather than as page chrome. Class 7 was added on
# 2026-08-09 after the SAME CSS mistake shipped a second time: the checklist
# named the missing bleed but never named the flush type it also causes, so an
# auditor could tick class 6 off and still miss what V>> actually objected to.
FAILURE_CLASSES: Sequence[tuple[int, str, str]] = (
    (1, "Cover not full-bleed",
     "Page 1: does the background fill the whole sheet, edge to edge?"),
    (2, "Mid-table page break",
     "Any page starting or ending mid-row?"),
    (3, "Orphan heading at page bottom",
     "Any page ending with a heading and no content under it?"),
    (4, "Footer floating mid-page",
     "A footer rendered as body content, with blank space below it, "
     "instead of as page chrome via @page @bottom-*?"),
    (5, "Coda alone on a near-empty last page",
     "A signature or closing block orphaned with vast white space?"),
    (6, "Content page background not full-bleed",
     "A non-white page colour stopping short of the sheet edges, "
     "leaving white gutters around an inset panel?"),
    (7, "Type squeezed against its background",
     "Text starting flush against a coloured background's edge, with no gap? "
     "Measured mechanically by bin/pdf-bleed-check — read its verdict below, "
     "and still look at any page it reports as NOT ASSESSED."),
)

# A page whose rendered PNG is far smaller than its siblings is nearly empty.
# Rule 8's observed instance: 22 KB against neighbours of 148-175 KB, i.e. ~14%
# of the median. 0.40 sits well above that and well below a normal light page.
_SPARSE_PAGE_RATIO = 0.40

# Below this many pages a median carries no signal, so the check is skipped
# rather than guessed at.
_MIN_PAGES_FOR_OUTLIER = 3


def sparse_pages(sizes: Dict[int, int],
                 ratio: float = _SPARSE_PAGE_RATIO) -> List[int]:
    """Page numbers whose render is suspiciously small versus the median.

    A strong automatic signal for failure class 5 (a near-empty final page).
    Returns [] when there are too few pages for a median to mean anything —
    silence here is "not assessed", never "assessed and clean".
    """
    if len(sizes) < _MIN_PAGES_FOR_OUTLIER:
        return []
    mid = median(sizes.values())
    if mid <= 0:
        return []
    return sorted(page for page, size in sizes.items() if size < mid * ratio)


def pages_to_inspect(total: int, table_pages: Sequence[int] = ()) -> List[int]:
    """The pages Rule 8 requires a human eye on: cover, tables, last, some middle.

    Not every page — the audit must stay under the two-minute budget its own
    falsification clause sets, or it becomes friction and gets skipped.
    """
    if total <= 0:
        return []
    if total <= 4:
        return list(range(1, total + 1))
    wanted = {1, total}
    wanted.update(p for p in table_pages if 1 <= p <= total)
    # Two middle pages, evenly spaced, to catch drift in long documents.
    wanted.update({total // 3, (2 * total) // 3})
    return sorted(p for p in wanted if 1 <= p <= total)


def checklist_text() -> str:
    """The complete failure-class checklist, rendered for the terminal."""
    # Never hardcode the count: the whole point of this module is that a lost
    # class is invisible, and a stale "six" in the header is how one gets lost.
    lines = [f"Check EVERY inspected page against all "
             f"{len(FAILURE_CLASSES)} classes:"]
    for num, name, question in FAILURE_CLASSES:
        lines.append(f"  {num}. {name}")
        lines.append(f"     {question}")
    return "\n".join(lines)


def format_report(page_files: Dict[int, str], sizes: Dict[int, int],
                  table_pages: Sequence[int] = ()) -> str:
    """The full operator-facing report: pages, sizes, suspicions, checklist."""
    if not page_files:
        return "No pages rendered — nothing to audit."

    inspect = pages_to_inspect(len(page_files), table_pages)
    sparse = sparse_pages(sizes)

    out = [f"{len(page_files)} page(s) rendered.", ""]
    for page in sorted(page_files):
        kb = sizes.get(page, 0) / 1024
        marks = []
        if page in inspect:
            marks.append("INSPECT")
        if page in sparse:
            marks.append("SPARSE — likely class 5")
        suffix = f"   <-- {', '.join(marks)}" if marks else ""
        out.append(f"  page {page}: {page_files[page]}  ({kb:.0f} KB){suffix}")

    out.append("")
    if sparse:
        out.append(
            f"WARNING: page(s) {', '.join(map(str, sparse))} rendered far smaller "
            f"than the median — inspect these FIRST."
        )
        out.append("")
    out.append(checklist_text())
    out.append("")
    out.append("Read each INSPECT page with the Read tool. Any violation -> "
               "fix the CSS, regenerate, re-audit. Do not ship past a violation.")
    return "\n".join(out)
