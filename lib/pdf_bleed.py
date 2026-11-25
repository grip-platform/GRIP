"""pdf_bleed — measure whether a page's background reaches the sheet edge, and
whether the type on it has any breathing room.

Why this exists
---------------
On 2026-08-09 V>> rejected a shipped 6-page A4 PDF: *"never ever produce another
background colour/image/drawable that has no padding or margin. the text looks
squeezed. either make the background fit the whole width, or add proper
padding/margin."*

The cause was a single CSS mistake — ``@page { margin: 16mm 17mm 18mm }`` with the
paper colour set only on ``html, body``. The colour therefore painted the content
box, not the sheet, which produced BOTH faults at once: white gutters around an
inset coloured panel, and (because the panel's edge *is* the content-box edge)
every line of type starting flush against that edge with zero inset.

That failure was ALREADY documented — row 6 of ``skills/professional-pdf/SKILL.md``
has described it since 2026-05-31 — and it shipped anyway. So the finding is not
"write the rule down"; the rule was written down. It is that **prose in a skill
file did not prevent recurrence**, because it depends on a human or an agent
recalling and applying it at the moment of judging. This module is the mechanical
replacement: it measures the rendered pixels and returns a verdict nobody has to
remember to reach.

What it measures
----------------
Two things, from a rasterised page:

1. **Bleed** — the modal colour of a thin ring at the extreme sheet edge versus
   the modal colour of the page's core. If the core is coloured and the ring is
   not, the background stops short: an inset panel.
2. **Inset** — the gap, per side, between the ink's bounding box and the
   background panel's bounding box. Below a threshold, the type is squeezed.

The two are one fault with one cause, which is why they are measured together:
making the background full-bleed usually fixes the inset for free, because the
type then sits at the page margin instead of at a panel edge.

Design
------
Pure: no subprocess, no filesystem, no network, no third-party imports. It is
handed decoded pixels and returns data. ``bin/pdf-bleed-check`` owns rasterising
(``pdftoppm``) and printing. That split is what makes the whole thing testable
against synthetic rasters, with no PDF and no renderer in the loop.

Known blind spots, named rather than hidden
-------------------------------------------
1. **Anything that is not one flat page background.** A photograph, gradient or
   full-page chart is reported ``NOT_ASSESSED``, never clean — silence means "I
   could not judge this", the only safe meaning for a check whose job is to stop
   a bad page.
2. **A coloured callout box inside an otherwise white page.** Only the page-level
   background is measured. Flush text inside a small highlighted panel is NOT
   detected; finding arbitrary sub-panels needs connected-component analysis,
   which is a different and much larger tool.
3. **A page whose ink covers more than half the 1mm edge ring** (solid bars
   bleeding off all four sides) defeats the stock-colour estimate. It comes back
   unjudged rather than clean — fail-safe, and anchored by a test.
4. **One shared threshold.** 4mm is applied to every side of every page; a design
   that deliberately runs a rule or a tab off one edge will warn.
5. **Ink is measured in clusters, not pixels.** Isolated specks — riso grain,
   halftone spatter, JPEG ringing — are discarded before the bounding box is
   taken, because a stray pixel is not a block of type. A mark thinner than a
   hairline AND shorter than a few pixels is therefore invisible to this check.
   The filter restores whole strokes from their cores, so it does not shave the
   ends off a real rule and cannot turn a genuine squeeze into a pass; both
   directions are anchored by tests. Added 2026-08-09 after five speckle pixels
   out of 51,892 failed a page whose type sat 8.6mm clear.

Falsification — when to change or drop this
-------------------------------------------
It is wrong, or mis-tuned, if over the next ~20 PDFs: it never once fails a
document that would otherwise have shipped (it is ceremony — the 2026-08-09
rejection says otherwise today); OR it fails documents the operator considers
fine more often than it catches real ones (the threshold or the stock estimate is
mis-tuned — narrow it, do not delete it, or the defect returns); OR a squeezed
page still ships *past* a green run (the measurement is defective — widen it).
Track: PDFs rejected by V>> for squeezed type after this shipped (target 0), and
false failures on the skill's own canonical template (target 0; measured 0 on
2026-08-09 against a cover plus body page rendered from ``reference.md``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Colour = Tuple[int, int, int]
Box = Tuple[int, int, int, int]  # left, top, right, bottom — all inclusive, px

MM_PER_INCH = 25.4
WHITE: Colour = (255, 255, 255)

# Two flat fills rendered by poppler from one CSS colour are byte-identical; the
# spread comes from anti-aliasing at edges. 10 absorbs that without merging
# genuinely different tints (#EBE6D9 sits 38 from white at its furthest channel).
COLOUR_TOL = 10

# Deliberately tighter than COLOUR_TOL. A near-white tint (#FAFAF8) is treated as
# white stock — an invisible panel cannot look squeezed — while a real light tint
# (#F5F0E6, 10-25 off white) still gets assessed.
WHITE_TOL = 6

# Anti-aliasing along the panel/stock boundary produces linear blends of the two
# colours. Those pixels are an artefact of the edge, not ink, so they are excluded
# by distance-to-the-segment rather than by shrinking the search box — which keeps
# a genuinely flush page reading as 0.0mm instead of a softened 1.0mm.
BLEND_TOL = 14

# V>>'s standard is qualitative ("proper padding"). 4mm is the smallest gap that
# still reads as deliberate at A4; the shipped defect measured 0mm and a normal
# page margin is 15-20mm, so nothing sits near this line by accident.
DEFAULT_MIN_INSET_MM = 4.0

# The ring sampled for the sheet's stock colour. 1mm is inside every printer's
# unprintable area yet outside any sane page margin.
EDGE_BAND_MM = 1.0

# The core region is inset this fraction from each side: well inside any plausible
# page margin, so it samples the background rather than the gutter.
CORE_INSET_FRACTION = 0.30

# Below this share the core is not one flat colour and the panel model does not
# apply. Measured: a flat text page renders 95%+ of its core as a single colour.
MIN_DOMINANT_SHARE = 0.50

# The colour survey samples every Nth pixel. Modal colour is stable under
# sampling; the bounding boxes below are always measured at full resolution.
SURVEY_STRIDE = 3

# The rasteriser rounds the page box up to a whole number of pixels, so the
# outermost ring of the raster can be a sliver of canvas rather than page.
# Measured 2026-08-09: WeasyPrint's A4 lands 413.2px into poppler's 414px raster
# and leaves one pure-white column at the right edge (Chrome's lands exactly).
# Without this, that one column reads as ink at 0.0mm and fails every full-bleed
# WeasyPrint document. Ink is therefore SEARCHED inside the eroded box but
# MEASURED against the true panel box, so real gaps keep their real values and a
# genuinely flush page still reports well under the threshold.
SHEET_EDGE_SKIP_PX = 1

# Riso grain, halftone spatter, JPEG ringing and anti-alias fringes all deposit
# isolated specks near the sheet edge. A single stray pixel is not a text block,
# but it drags the ink bounding box outward and fails an otherwise clean page.
# Measured 2026-08-09 on the shipped broadside: its bridge sheet reported ink
# 2.5mm from the left edge on the strength of FIVE speckle pixels out of 51,892,
# while the real type sat 8.6mm clear on both sides. A pixel therefore only
# counts as ink when its own neighbourhood holds a cluster.
DESPECKLE_RADIUS_PX = 2

# Four matching pixels within the 5x5 window. A one-pixel-wide stem of type
# contributes five and survives comfortably; a two-pixel speck contributes two
# and does not. The headroom matters in one direction only: dropping real type
# would hide a real squeeze, so this errs toward keeping thin marks. Measured on
# the same document, raising it to six ate real type — one page's right inset
# jumped from 20.3mm to 85.7mm. Set to 0 to disable the filter entirely.
MIN_INK_CLUSTER_PX = 4

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_WHITE_STOCK = "WHITE_STOCK"
STATUS_BLANK = "BLANK"
STATUS_NOT_ASSESSED = "NOT_ASSESSED"

#: Statuses meaning "this page was never judged" — never count them as a pass.
UNJUDGED = (STATUS_NOT_ASSESSED, STATUS_BLANK)

SIDES = ("left", "right", "top", "bottom")


class PpmError(ValueError):
    """The bytes handed in are not a usable binary (P6) PPM."""


# --------------------------------------------------------------------------
# colour arithmetic
# --------------------------------------------------------------------------

def channel_delta(a: Colour, b: Colour) -> int:
    """Largest per-channel difference between two colours."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def close(a: Colour, b: Colour, tol: int = COLOUR_TOL) -> bool:
    """Are these the same colour, allowing for anti-aliasing spread?"""
    return channel_delta(a, b) <= tol


def is_white(colour: Colour) -> bool:
    """Is this the white of unprinted paper (or close enough to look like it)?"""
    return channel_delta(colour, WHITE) <= WHITE_TOL


def is_blend(colour: Colour, a: Colour, b: Colour, tol: int = BLEND_TOL) -> bool:
    """Is this colour a mix of ``a`` and ``b`` — i.e. an anti-aliased edge pixel?

    Distance from the segment a..b in RGB. Without this, the one-pixel ring where
    a coloured panel meets white paper reads as ink sitting 0mm from the panel
    edge, and every inset panel would fail for the wrong reason.
    """
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (colour[0] - a[0], colour[1] - a[1], colour[2] - a[2])
    denom = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2]
    if denom == 0:
        return channel_delta(colour, a) <= tol
    along = (ac[0] * ab[0] + ac[1] * ab[1] + ac[2] * ab[2]) / denom
    along = 0.0 if along < 0.0 else (1.0 if along > 1.0 else along)
    return max(abs(ac[i] - along * ab[i]) for i in range(3)) <= tol


def px_to_mm(pixels: int, dpi: float) -> float:
    """Convert a pixel count to millimetres at the given render resolution."""
    return pixels * MM_PER_INCH / dpi


# --------------------------------------------------------------------------
# the raster
# --------------------------------------------------------------------------

def _end_of_line(data: bytes, i: int) -> int:
    """Index of the newline that ends the comment starting at ``i``."""
    while i < len(data) and data[i:i + 1] not in (b"\n", b"\r"):
        i += 1
    return i


def _skip_ignorable(data: bytes, i: int) -> int:
    """Advance past whitespace and ``#`` comments to the next token byte."""
    while i < len(data):
        char = data[i:i + 1]
        if char == b"#":
            i = _end_of_line(data, i)
        elif char.isspace():
            i += 1
        else:
            return i
    raise PpmError("truncated PPM header")


def _next_token(data: bytes, i: int) -> Tuple[bytes, int]:
    """The next whitespace-delimited header token, and the index just past it."""
    start = _skip_ignorable(data, i)
    i = start
    while i < len(data) and not data[i:i + 1].isspace():
        i += 1
    if start == i:
        raise PpmError("truncated PPM header")
    return data[start:i], i


def _ppm_header_fields(data: bytes) -> Tuple[List[bytes], int]:
    """The four header tokens (magic, width, height, maxval) and the data offset."""
    fields: List[bytes] = []
    i = 0
    for _ in range(4):
        token, i = _next_token(data, i)
        fields.append(token)
    return fields, i + 1  # exactly one whitespace byte separates header and data


@dataclass(frozen=True)
class Raster:
    """One rendered page: width, height, and packed 8-bit RGB rows."""

    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise PpmError(f"degenerate raster {self.width}x{self.height}")
        expected = self.width * self.height * 3
        if len(self.rgb) < expected:
            raise PpmError(f"raster is short: {len(self.rgb)} bytes for "
                           f"{self.width}x{self.height} (need {expected})")

    def pixel(self, x: int, y: int) -> Colour:
        off = (y * self.width + x) * 3
        return (self.rgb[off], self.rgb[off + 1], self.rgb[off + 2])

    @property
    def full_box(self) -> Box:
        return (0, 0, self.width - 1, self.height - 1)

    @classmethod
    def from_ppm(cls, data: bytes) -> "Raster":
        """Parse a binary PPM (P6) — what ``pdftoppm`` writes by default.

        PPM rather than PNG because it needs no decoder: the check stays
        dependency-free, so it cannot be skipped for want of an install.
        """
        fields, pos = _ppm_header_fields(data)
        if fields[0] != b"P6":
            raise PpmError(f"not a binary PPM: magic {fields[0]!r}")
        width, height, maxval = (int(f) for f in fields[1:4])
        if maxval != 255:
            raise PpmError(f"unsupported maxval {maxval} (only 8-bit is handled)")
        return cls(width, height, data[pos:pos + width * height * 3])


# --------------------------------------------------------------------------
# region survey and bounding boxes
# --------------------------------------------------------------------------

def _row_colours(raster: Raster, y: int, left: int, right: int,
                 stride: int) -> List[Colour]:
    """Every ``stride``-th colour in one row segment."""
    rgb = raster.rgb
    start = (y * raster.width + left) * 3
    stop = start + (right - left + 1) * 3
    return [(rgb[o], rgb[o + 1], rgb[o + 2])
            for o in range(start, stop, stride * 3)]


def survey(raster: Raster, box: Box, stride: int = SURVEY_STRIDE) -> Counter:
    """Count the colours inside ``box``. Used for modal colour and flatness."""
    left, top, right, bottom = box
    counts: Counter = Counter()
    for y in range(top, bottom + 1, stride):
        counts.update(_row_colours(raster, y, left, right, stride))
    return counts


def dominant(counts: Counter) -> Tuple[Optional[Colour], float]:
    """The most common colour, and the share of samples it accounts for."""
    total = sum(counts.values())
    if not total:
        return None, 0.0
    colour, hits = counts.most_common(1)[0]
    return colour, hits / total


def edge_ring(raster: Raster, band_px: int) -> Counter:
    """Colours in the outermost ``band_px`` ring — the sheet's stock colour."""
    band = max(1, min(band_px, raster.width // 2, raster.height // 2))
    w, h = raster.width, raster.height
    boxes = ((0, 0, w - 1, band - 1), (0, h - band, w - 1, h - 1),
             (0, 0, band - 1, h - 1), (w - band, 0, w - 1, h - 1))
    counts: Counter = Counter()
    for box in boxes:
        counts.update(survey(raster, box, stride=1))
    return counts


def core_box(raster: Raster, fraction: float = CORE_INSET_FRACTION) -> Box:
    """The central region, inset from every side — samples background, not gutter."""
    dx = int(raster.width * fraction)
    dy = int(raster.height * fraction)
    return (dx, dy, max(dx, raster.width - 1 - dx), max(dy, raster.height - 1 - dy))


def _classify(colour: Colour, cache: Dict[Colour, bool],
              matches: Callable[[Colour], bool]) -> bool:
    """Memoised predicate. A page holds a few hundred distinct colours, so this
    turns a full-resolution scan into dict lookups rather than predicate calls."""
    hit = cache.get(colour)
    if hit is None:
        hit = cache[colour] = matches(colour)
    return hit


def _match_mask(raster: Raster, box: Box,
                matches: Callable[[Colour], bool]) -> bytearray:
    """A flat width*height mask over ``box``: 1 where the colour matches."""
    left, top, right, bottom = box
    width = right - left + 1
    cache: Dict[Colour, bool] = {}
    rgb = raster.rgb
    mask = bytearray(width * (bottom - top + 1))
    for i in range(len(mask)):
        y, x = divmod(i, width)
        o = ((top + y) * raster.width + left + x) * 3
        if _classify((rgb[o], rgb[o + 1], rgb[o + 2]), cache, matches):
            mask[i] = 1
    return mask


def _integral(mask: Sequence[int], width: int) -> List[int]:
    """Summed-area table, so any window total costs four lookups."""
    stride = width + 1
    table = [0] * (stride * (len(mask) // width + 1))
    running = 0
    for i in range(len(mask)):
        y, x = divmod(i, width)
        running = mask[i] if x == 0 else running + mask[i]
        table[(y + 1) * stride + x + 1] = table[y * stride + x + 1] + running
    return table


def _neighbours(table: Sequence[int], stride: int, x: int, y: int,
                width: int, height: int, radius: int) -> int:
    """Matching pixels within ``radius`` of (x, y), the pixel itself included."""
    x0, y0 = max(0, x - radius), max(0, y - radius)
    x1, y1 = min(width - 1, x + radius) + 1, min(height - 1, y + radius) + 1
    return (table[y1 * stride + x1] - table[y0 * stride + x1]
            - table[y1 * stride + x0] + table[y0 * stride + x0])


def _cluster_cores(mask: Sequence[int], width: int, height: int, minimum: int,
                   radius: int) -> bytearray:
    """Pixels whose own neighbourhood holds at least ``minimum`` matches."""
    table = _integral(mask, width)
    cores = bytearray(len(mask))
    for i in range(len(mask)):
        if not mask[i]:
            continue
        y, x = divmod(i, width)
        if _neighbours(table, width + 1, x, y, width, height, radius) >= minimum:
            cores[i] = 1
    return cores


def despeckle(mask: Sequence[int], width: int, minimum: int,
              radius: int = DESPECKLE_RADIUS_PX) -> bytearray:
    """Clear matching pixels that are neither part of nor touching a cluster.

    Two passes, because a mark must survive whole. The first finds the solid
    cores — pixels with at least ``minimum`` matches around them. The second
    restores every matching pixel within ``radius`` of a core, which returns the
    tips of a stroke that the first pass alone erodes away. That restoration is
    the safety half: without it the ends of a hairline are trimmed by up to
    ``radius``, and a genuine 3mm squeeze would measure 4mm and pass.
    """
    height = len(mask) // width
    cores = _cluster_cores(mask, width, height, minimum, radius)
    table = _integral(cores, width)
    kept = bytearray(len(mask))
    for i in range(len(mask)):
        if not mask[i]:
            continue
        y, x = divmod(i, width)
        if _neighbours(table, width + 1, x, y, width, height, radius):
            kept[i] = 1
    return kept


def _mask_bbox(mask: bytearray, width: int, left: int,
               top: int) -> Optional[Box]:
    """Bounding box of the set pixels, offset into the raster's coordinates."""
    first, last = mask.find(1), mask.rfind(1)
    if first < 0:
        return None
    lo, hi = width, -1
    for y in range(first // width, last // width + 1):
        row = mask[y * width:(y + 1) * width]
        start = row.find(1)
        if start >= 0:
            lo, hi = min(lo, start), max(hi, row.rfind(1))
    return (left + lo, top + first // width, left + hi, top + last // width)


def scan_bbox(raster: Raster, box: Box, matches: Callable[[Colour], bool],
              min_cluster: int = 0) -> Optional[Box]:
    """Bounding box of the pixels in ``box`` for which ``matches`` is true.

    With ``min_cluster`` above zero a pixel only counts when at least that many
    matching pixels lie within ``DESPECKLE_RADIUS_PX`` of it. That discards
    isolated specks — grain, spatter, ringing — without moving the edge of any
    real mark, which is the difference between measuring a block of type and
    measuring dust.
    """
    width = box[2] - box[0] + 1
    mask = _match_mask(raster, box, matches)
    if min_cluster > 0:
        mask = despeckle(mask, width, min_cluster)
    return _mask_bbox(mask, width, box[0], box[1])


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PageReport:
    """What was measured on one page, and what it means."""

    page: int
    dpi: float
    size_px: Tuple[int, int]
    status: str
    stock: Optional[Colour] = None
    panel: Optional[Colour] = None
    full_bleed: Optional[bool] = None
    insets_mm: Optional[Dict[str, float]] = None
    problems: Tuple[str, ...] = ()
    note: str = ""

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAIL

    @property
    def warned(self) -> bool:
        return self.status == STATUS_WARN

    @property
    def judged(self) -> bool:
        """False when the page was skipped — never treat that as a pass."""
        return self.status not in UNJUDGED


def _ink_predicate(stock: Colour, panel: Colour) -> Callable[[Colour], bool]:
    """Ink is anything that is neither the stock, nor the panel, nor their blend."""
    def matches(colour: Colour) -> bool:
        return not (close(colour, stock) or close(colour, panel)
                    or is_blend(colour, stock, panel))
    return matches


def erode(box: Box, amount: int) -> Box:
    """Shrink a box by ``amount`` on every side, never past its own centre."""
    left, top, right, bottom = box
    return (min(left + amount, right), min(top + amount, bottom),
            max(right - amount, left), max(bottom - amount, top))


def _insets(inner: Box, outer: Box, dpi: float) -> Dict[str, float]:
    """Gap on each side between the ink box and the panel box, in millimetres."""
    return {"left": px_to_mm(inner[0] - outer[0], dpi),
            "top": px_to_mm(inner[1] - outer[1], dpi),
            "right": px_to_mm(outer[2] - inner[2], dpi),
            "bottom": px_to_mm(outer[3] - inner[3], dpi)}


def _survey_page(raster: Raster, dpi: float):
    """The two colours the whole verdict turns on, plus the core's flatness."""
    panel, share = dominant(survey(raster, core_box(raster)))
    band = max(1, round(EDGE_BAND_MM * dpi / MM_PER_INCH))
    stock, _ = dominant(edge_ring(raster, band))
    return panel, share, stock


def _skip_verdict(panel, share, stock, base) -> Optional[PageReport]:
    """A report for a page that cannot, or need not, be judged. Else None."""
    if panel is None or share < MIN_DOMINANT_SHARE:
        return PageReport(
            status=STATUS_NOT_ASSESSED, panel=panel, **base,
            note=(f"page core is not one flat colour (dominant colour covers "
                  f"{share:.0%}, need {MIN_DOMINANT_SHARE:.0%}) — a photograph, "
                  f"gradient or full-page graphic. NOT judged; look at it."))
    if stock is None:
        return PageReport(status=STATUS_NOT_ASSESSED, panel=panel, **base,
                          note="could not sample the sheet edge.")
    if is_white(panel) and is_white(stock):
        return PageReport(status=STATUS_WHITE_STOCK, stock=stock, panel=panel,
                          full_bleed=True, **base,
                          note="white stock, no coloured background panel.")
    return None


def _problems(full_bleed: bool, panel: Colour, stock: Colour,
              insets: Dict[str, float], min_inset_mm: float):
    """Every complaint about this page, and the sides that are too tight."""
    problems = []
    if not full_bleed:
        problems.append(f"background stops short of the sheet edge: the panel is "
                        f"{panel} but the sheet edge is {stock}")
    tight = [s for s in SIDES if insets[s] < min_inset_mm]
    problems.extend(
        f"ink sits {insets[s]:.1f}mm from the background's {s} edge "
        f"(minimum {min_inset_mm:.1f}mm) — the text is squeezed" for s in tight)
    return problems, tight


def analyse_page(raster: Raster, page: int = 1, dpi: float = 50.0,
                 min_inset_mm: float = DEFAULT_MIN_INSET_MM) -> PageReport:
    """Judge one rendered page for bleed and for squeezed type."""
    base = dict(page=page, dpi=dpi, size_px=(raster.width, raster.height))
    panel, share, stock = _survey_page(raster, dpi)
    skip = _skip_verdict(panel, share, stock, base)
    if skip is not None:
        return skip

    full_bleed = close(stock, panel)
    panel_box = (raster.full_box if full_bleed else
                 scan_bbox(raster, raster.full_box, lambda c: not close(c, stock)))
    if panel_box is None:
        return PageReport(status=STATUS_NOT_ASSESSED, stock=stock, panel=panel,
                          full_bleed=full_bleed, **base,
                          note="no panel found against the sheet stock.")

    search_box = erode(panel_box, SHEET_EDGE_SKIP_PX)
    ink_box = scan_bbox(raster, search_box, _ink_predicate(stock, panel),
                        min_cluster=MIN_INK_CLUSTER_PX)
    if ink_box is None:
        return PageReport(status=STATUS_BLANK, stock=stock, panel=panel,
                          full_bleed=full_bleed, **base,
                          note="no ink on this page — nothing to squeeze.")

    insets = _insets(ink_box, panel_box, dpi)
    problems, tight = _problems(full_bleed, panel, stock, insets, min_inset_mm)
    status = STATUS_FAIL if tight else (STATUS_WARN if problems else STATUS_OK)
    return PageReport(status=status, stock=stock, panel=panel,
                      full_bleed=full_bleed, insets_mm=insets,
                      problems=tuple(problems), **base)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def worst_status(reports: Sequence[PageReport]) -> str:
    """The overall verdict: any FAIL beats any WARN beats anything unjudged."""
    seen = {r.status for r in reports}
    for status in (STATUS_FAIL, STATUS_WARN, STATUS_NOT_ASSESSED):
        if status in seen:
            return status
    return STATUS_OK


def exit_code(reports: Sequence[PageReport], strict: bool = False) -> int:
    """0 clean · 1 a page failed · in strict mode, 1 when a page merely warned.

    A warning is the operator's stated *preference* (full-bleed over an inset
    panel), not the defect itself, so by default it does not fail the run.
    ``--strict`` promotes it for callers that want the preference enforced.
    """
    if any(r.failed for r in reports):
        return 1
    if strict and any(r.warned for r in reports):
        return 1
    return 0


def _format_page(report: PageReport) -> List[str]:
    """The measured detail for one page, as report lines."""
    lines = [f"  page {report.page}: {report.status}"]
    if report.insets_mm:
        gaps = "  ".join(f"{s} {report.insets_mm[s]:.1f}mm" for s in SIDES)
        bleed = "full-bleed" if report.full_bleed else "INSET PANEL"
        lines.append(f"      background {report.panel} {bleed}; ink gap: {gaps}")
    if report.note:
        lines.append(f"      {report.note}")
    lines.extend(f"      - {problem}" for problem in report.problems)
    return lines


def _summary_lines(reports: Sequence[PageReport], min_inset_mm: float) -> List[str]:
    """The verdict paragraph, naming failed, warned and unjudged pages."""
    failed = [r.page for r in reports if r.failed]
    warned = [r.page for r in reports if r.warned]
    unjudged = [r.page for r in reports if not r.judged]
    lines = []
    if failed:
        lines.append(f"FAIL on page(s) {', '.join(map(str, failed))}: text is "
                     f"squeezed against its background (under {min_inset_mm:.1f}mm). "
                     f"Fix: @page {{ margin: 0 }}, background on html/body, and "
                     f"the real margin as padding on an inner wrapper.")
    if warned:
        lines.append(f"WARN on page(s) {', '.join(map(str, warned))}: the "
                     f"background does not reach the sheet edge. Full-bleed is "
                     f"preferred unless the page is meant to be white.")
    if unjudged:
        lines.append(f"NOT ASSESSED: page(s) {', '.join(map(str, unjudged))}. "
                     f"These were skipped, not cleared — look at them.")
    if not (failed or warned):
        lines.append(f"No squeezed type on the {len(reports) - len(unjudged)} "
                     f"page(s) that could be judged.")
    return lines


def format_report(reports: Sequence[PageReport],
                  min_inset_mm: float = DEFAULT_MIN_INSET_MM) -> str:
    """The operator-facing summary. States what was NOT judged, explicitly."""
    if not reports:
        return "No pages rendered — nothing measured. This is NOT a pass."
    lines = [f"Background bleed + type inset (minimum gap {min_inset_mm:.1f}mm):", ""]
    for report in sorted(reports, key=lambda r: r.page):
        lines.extend(_format_page(report))
    lines.append("")
    lines.extend(_summary_lines(reports, min_inset_mm))
    return "\n".join(lines)
