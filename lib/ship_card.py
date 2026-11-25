"""GRIP Ship Card — a deterministic terminal celebration for shipping moments.

The dopamine hit of shipping, made visible. When a PR merges or a sprint closes,
GRIP renders a small, tasteful card: what shipped (plain language), a unicode
*momentum sparkline* of the session, and a deterministic flourish. User-facing
delight at the exact moment work lands.

Design (mirrors the proven pure-lib delight pattern of ``lib/rave_card.py`` and
``lib/handoff_chime.py``):

  * **Pure + deterministic** — no clock, no randomness. The flourish is selected
    by a SHA-256 hash of the title, so the same ship renders the same card every
    time (Goodhart-testable: assert exact output).
  * **Zero non-stdlib deps** — only ``hashlib``/``dataclasses``/``typing``.
  * **Left-ruled, not right-bordered** — the card is framed by a left rule
    (``┃``) with NO right border, so it needs no display-width padding. This is
    deliberate: the diamond ◆ and the block ticks ▁▂▃▄▅▆▇█ are East-Asian-Width
    *ambiguous* (some terminals render them width-2), which would break a padded
    right border; with only a shared left prefix, every line shifts identically
    and stays mutually aligned on ANY terminal.

The CORE (:func:`sparkline`, :func:`build_ship_card`, :func:`render`) is pure —
it takes explicit data and returns a string. Only :func:`main` reaches the world
(reads git for live counts), keeping the testable surface I/O-free.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Sequence

#: Sparkline ramp, low→high. Eight width-1 block glyphs (U+2581..U+2588).
_SPARK_TICKS = "▁▂▃▄▅▆▇█"

#: Deterministic flourishes. Selected by ``hash(title) % len`` — never random,
#: so a given ship always renders the same line (testable). Tasteful, not noisy.
_FLOURISHES = (
    "shipped is better than perfect — but this was both.",
    "another brick in the cathedral.",
    "the loop tightens; the substrate grows.",
    "small diffs, compounding gains.",
    "correct by construction, green by proof.",
    "momentum is a feature.",
    "done. verified. onward.",
    "the machine that improves itself just did.",
)


def sparkline(values: Sequence[float]) -> str:
    """Render ``values`` as a unicode sparkline over the 8-level ramp.

    Pure + deterministic. Empty input → ``""``. All-equal input → a flat row of
    the MID tick (no false peak from a zero-span normalisation). Otherwise each
    value is min-max normalised onto ``_SPARK_TICKS`` so the smallest is ▁ and
    the largest is █.

    Goodhart-testable by exact output, e.g.
    ``sparkline([1,2,3,4,5,6,7,8]) == "▁▂▃▄▅▆▇█"`` and
    ``sparkline([0, 7]) == "▁█"``.
    """
    nums = [float(v) for v in values]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    span = hi - lo
    if span == 0:
        return _SPARK_TICKS[len(_SPARK_TICKS) // 2] * len(nums)
    top = len(_SPARK_TICKS) - 1
    return "".join(_SPARK_TICKS[round((v - lo) / span * top)] for v in nums)


def _seed(title: str) -> int:
    """Deterministic seed from the title (SHA-256 → int). No clock, no random —
    same title ⇒ same flourish, mirroring ``rave_card._seed``."""
    return int(hashlib.sha256(title.encode("utf-8")).hexdigest(), 16)


def _flourish_for(title: str) -> str:
    """Pick the deterministic flourish for ``title``."""
    return _FLOURISHES[_seed(title) % len(_FLOURISHES)]


@dataclass(frozen=True)
class ShipCard:
    """An immutable shipping-celebration card.

    ``title`` — what shipped (PR title / sprint name).
    ``shipped`` — plain-language win lines (rendered as ✓ bullets).
    ``momentum`` — per-step counts (commits, tests, files…) for the sparkline.
    ``stats`` — short human stat fragments (e.g. ``"3 commits"``, ``"97 tests"``).
    ``flourish`` — the deterministic closing line.
    """

    title: str
    shipped: tuple[str, ...]
    momentum: tuple[float, ...]
    stats: tuple[str, ...] = field(default_factory=tuple)
    flourish: str = ""


def build_ship_card(
    title: str,
    shipped: Sequence[str],
    momentum: Sequence[float] = (),
    stats: Sequence[str] = (),
) -> ShipCard:
    """Assemble a :class:`ShipCard`, choosing the flourish deterministically.

    Pure: identical arguments always yield an identical card (the flourish is
    hash-derived from ``title``, never random). Whitespace-only ``shipped`` lines
    are dropped so the card never renders an empty bullet.
    """
    clean_title = title.strip() or "(untitled)"
    clean = tuple(s.strip() for s in shipped if s and s.strip())
    return ShipCard(
        title=clean_title,
        shipped=clean,
        momentum=tuple(float(m) for m in momentum),
        stats=tuple(s.strip() for s in stats if s and s.strip()),
        # Key the flourish on the NORMALISED title so "  foo" and "foo" (which
        # render the SAME title line) get the SAME flourish — determinism holds
        # across leading/trailing whitespace, not just byte-exact strings.
        flourish=_flourish_for(clean_title),
    )


def _collapse_blanks(lines: list[str]) -> list[str]:
    """Collapse runs of blank lines to a single blank and trim trailing blanks,
    so no layout path (e.g. a flourish with no wins/momentum) yields a double
    blank. The leading line is always the title (never blank)."""
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out


def _content_lines(card: ShipCard) -> list[str]:
    """The card's logical body lines (the header banner lives in the rule)."""
    lines = [card.title, ""]
    lines.extend(f"✓ {s}" for s in card.shipped)
    spark = sparkline(card.momentum)
    if spark or card.stats:
        lines.append("")
        parts = ([f"Momentum {spark}"] if spark else []) + list(card.stats)
        lines.append("  ·  ".join(parts))
    if card.flourish:
        lines.append("")
        lines.append(f"“{card.flourish}”")
    return _collapse_blanks(lines)


#: The card frame: a left rule only (no right border ⇒ no width-padding ⇒ robust
#: to ambiguous-width glyphs). The shared left prefix keeps every body line
#: mutually aligned regardless of each line's rendered width.
_BAR = "┃"
_HEADER_RULE = "┏━ ◆ GRIP SHIPPED ◆"
_FOOTER_RULE = "┗━"


def render(card: ShipCard) -> str:
    """Render the card as a left-ruled celebration block.

    A fixed header rule, each body line prefixed by ``┃ `` (the shared left
    rule), then a footer rule. No right border and no padding — so the diamond
    and the ambiguous-width spark ticks never misalign the frame on any terminal.
    Pure + deterministic: identical input ⇒ identical output.
    """
    body = [f"{_BAR} {line}".rstrip() for line in _content_lines(card)]
    return "\n".join([_HEADER_RULE, *body, _FOOTER_RULE])


def render_ship_card(
    title: str,
    shipped: Sequence[str],
    momentum: Sequence[float] = (),
    stats: Sequence[str] = (),
) -> str:
    """Convenience: build + render in one call. Pure + deterministic."""
    return render(build_ship_card(title, shipped, momentum, stats))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``ship_card "<title>" "<win 1>" "<win 2>" …`` → prints the card.

    The pure core stays I/O-free; this thin shell is the only impure surface.
    Richer live data (git commit/test/file counts → momentum + stats) is gathered
    by the ``/ship-card`` skill, which calls :func:`render_ship_card` directly.
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    title = args[0] if args else "GRIP session"
    shipped = args[1:]
    print(render_ship_card(title, shipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ShipCard",
    "sparkline",
    "build_ship_card",
    "render",
    "render_ship_card",
]
