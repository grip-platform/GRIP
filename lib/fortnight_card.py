"""The fortnight river — a deterministic surface for a SPAN of time.

Every other GRIP delight surface renders a POINT. ``ship_card`` marks a moment,
``grip_haiku`` renders the genome as it stands, ``verdict_card`` renders one
ruling, ``rave_card`` celebrates one landing. None of them renders *duration*,
so a fortnight catch-up had no surface at all. This module draws those days as
a river: one row per day, the stream widening with that day's throughput and
running visibly dry when nothing merged.

The dry bed is the load-bearing design choice. A quiet day rendered as blank
space reads as missing data; rendered as a dry channel it reads as a fact, which
is what it is — this fortnight had two genuine still stretches and the frame
should say so rather than hide them.

Pure and deterministic by construction: no clock, no randomness, no network and
no I/O anywhere in the core, so identical input always yields an identical
frame. That is what makes the output Goodhart-testable by exact string rather
than by shape. The worked anchor, pinned by
``test_the_documented_example_is_true`` so this sentence cannot drift away from
the code again: ``flow_row(0, 1, 8) == "····"`` — a still day draws a SHORT dry
bed, never one scaled to the requested width.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# The river ramp. Index 0 is a dry bed; the rest thicken with volume.
_DRY = "·"
_FLOW = "≋"
_TRICKLE = "~"

_HEADER_RULE = "╭─────────────────────────────────────────────────────────────╮"
_FOOTER_RULE = "╰─────────────────────────────────────────────────────────────╯"
_BAR = "│"

#: Width of a still day's dry bed. Short on purpose — see :func:`flow_row`.
DRY_WIDTH = 4

#: Widest a river row may draw, in characters. Chosen so the whole frame,
#: including the date gutter and the count column, stays inside 62 columns and
#: therefore never soft-wraps in a narrow terminal pane.
MAX_FLOW = 34


@dataclass(frozen=True)
class DayFlow:
    """One day of the river.

    ``label`` is an optional milestone note rendered inline on that row.
    """

    date: str
    weekday: str
    count: int
    label: str = ""


@dataclass(frozen=True)
class FortnightCard:
    """A built, not-yet-rendered river."""

    title: str
    days: tuple[DayFlow, ...]
    stats: tuple[str, ...] = field(default=())


def flow_row(count: int, peak: int, width: int = MAX_FLOW) -> str:
    """Render one day's stream as a bar of river glyphs.

    ``count`` is that day's volume, ``peak`` the largest volume across the whole
    span, so every row is scaled against the same denominator and the rows stay
    comparable to each other.

    Three bands, deliberately distinguishable at a glance:

    * ``count == 0`` → a SHORT dry bed of ``·``, deliberately not full width.
      A full-width bed was the first design and it was wrong: it drew more ink
      than the busiest day, so a still day read as the loudest one and the
      picture contradicted the number printed beside it. A still day should
      look still.
    * a volume that scales to nothing → a single ``~`` trickle, so a real but
      small day can never be mistaken for a dry one.
    * anything else → ``≋`` repeated proportionally.

    Pure. ``peak <= 0`` is treated as a dry span rather than raising, because a
    fortnight in which nothing merged is a legitimate input.
    """
    if width <= 0:
        return ""
    if count <= 0 or peak <= 0:
        return _DRY * DRY_WIDTH
    scaled = round(count / peak * width)
    if scaled <= 0:
        return _TRICKLE
    return _FLOW * scaled


def build_fortnight_card(
    title: str,
    days: Sequence[DayFlow],
    stats: Sequence[str] = (),
) -> FortnightCard:
    """Assemble a card. Pure; performs no scaling (that happens at render)."""
    return FortnightCard(title=title, days=tuple(days), stats=tuple(stats))


def peak_of(days: Sequence[DayFlow]) -> int:
    """Largest single-day volume across ``days``; 0 for an empty span. Pure."""
    return max((d.count for d in days), default=0)


def total_of(days: Sequence[DayFlow]) -> int:
    """Sum of every day's volume. Pure."""
    return sum(d.count for d in days)


def dry_days(days: Sequence[DayFlow]) -> int:
    """How many days in the span saw nothing at all. Pure."""
    return sum(1 for d in days if d.count <= 0)


def _day_line(day: DayFlow, peak: int) -> str:
    """One rendered river row: date gutter, stream, count, optional milestone."""
    gutter = f"{day.date} {day.weekday}"
    stream = flow_row(day.count, peak)
    line = f"{gutter}  {stream} {day.count}"
    if day.label:
        line = f"{line}  {day.label}"
    return line.rstrip()


def _content_lines(card: FortnightCard) -> list[str]:
    """Every body line of the card, in order. Pure."""
    peak = peak_of(card.days)
    lines = [card.title, ""]
    lines.extend(_day_line(d, peak) for d in card.days)
    if card.days:
        lines.append("")
        lines.append(
            f"{total_of(card.days)} across {len(card.days)} days"
            f"  ·  peak {peak}  ·  {dry_days(card.days)} still"
        )
    lines.extend(card.stats)
    return lines


def render(card: FortnightCard) -> str:
    """Render the river as a left-ruled block.

    Follows ``ship_card.render``: a header rule, each body line prefixed by the
    shared left rule, then a footer rule, and deliberately **no right border**,
    so the ambiguous-width river glyphs can never misalign the frame on a
    terminal that measures them differently. Pure and deterministic.
    """
    body = [f"{_BAR} {line}".rstrip() for line in _content_lines(card)]
    return "\n".join([_HEADER_RULE, *body, _FOOTER_RULE])


def render_fortnight_card(
    title: str,
    days: Sequence[DayFlow],
    stats: Sequence[str] = (),
) -> str:
    """Convenience: build and render in one call. Pure and deterministic."""
    return render(build_fortnight_card(title, days, stats))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI shell — the only impure surface in this module.

    Reads ``DATE,WEEKDAY,COUNT[,LABEL]`` triples on stdin, one per line, and
    prints the river. The pure core above stays I/O-free so it can be tested by
    exact string.
    """
    import sys

    raw = sys.stdin.read().strip().splitlines()
    days: list[DayFlow] = []
    for line in raw:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            count = int(parts[2])
        except ValueError:
            continue
        label = parts[3] if len(parts) > 3 else ""
        days.append(DayFlow(parts[0], parts[1], count, label))
    title = (argv or sys.argv[1:] or ["the fortnight"])[0]
    print(render_fortnight_card(title, days))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin shell
    raise SystemExit(main())
