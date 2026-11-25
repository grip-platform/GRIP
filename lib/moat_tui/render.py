"""grip-moat — PRESENTATION layer. Every function here is PURE.

Data in, ANSI string out. No file reads, no prints, no terminal queries, no
clock, no randomness, and no imports from ``model.py`` or ``app.py``. That
purity is the whole point: a test constructs a ``LegResult``-shaped object and
asserts on the exact returned string, with no TTY and no ledger present.

Two consequences of the purity contract, both deliberate:

* ``Frame`` and ``Theme`` are defined HERE, not in ``app.py``, so the dependency
  edge stays one-way (app -> render, never render -> app).
* The data types this module renders (``LegResult``, ``DecisionRow``,
  ``BeliefScope``, ``DecisionView``, ``IndexEntry``, ``TamperSession``) are owned
  by ``model.py`` / ``tamper.py``. Rather than import them — which would drag
  their import-time path resolution into a module that must never touch disk —
  every field access goes through :func:`_get`, which reads an attribute *or* a
  mapping key. Dataclasses, dicts and ``SimpleNamespace`` all render identically.

House style follows ``bin/codec-call`` and ``lib/evolution_march.py``: one pad
formula (:func:`_pad`) so borders cannot drift, 256-colour palette, and colour
applied AFTER width maths so an escape sequence can never corrupt a column.

Honesty rules encoded in the vocabulary (see :func:`status_glyph` /
:func:`status_colour`): ``unavailable`` and ``no-data`` render GREY with marks
that are visually distinct from the tamper mark. "Cannot verify" must never read
as "TAMPERED" — a faked red is worse than no feature.
"""

from __future__ import annotations

import re
import textwrap
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = (
    "Theme",
    "Frame",
    "theme",
    "paint",
    "status_glyph",
    "status_colour",
    "visible_width",
    "truncate",
    "wrap_plain",
    "hrule",
    "render_header",
    "render_forest",
    "render_legs",
    "render_decision",
    "render_belief_panel",
    "render_council_panel",
    "render_tamper",
    "render_proof",
    "render_help",
    "render_footer",
    "render_screen",
    "SCREENS",
)

# --------------------------------------------------------------------------
# Palette + theme
# --------------------------------------------------------------------------

_RESET = "\033[0m"

_PALETTE: dict[str, str] = {
    "reset": _RESET,
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[38;5;46m",    # verified
    "amber": "\033[38;5;214m",   # degraded / fuzzy
    "red": "\033[38;5;196m",     # broken — the tamper colour, earned only
    "grey": "\033[38;5;240m",    # unavailable / no-data — "cannot verify"
    "cyan": "\033[38;5;51m",     # addresses, digests, cursor
    "blue": "\033[38;5;39m",     # belief chain
    "ml": "\033[38;5;213m",      # the moat / tamper heading
    "white": "\033[38;5;255m",
}

_EMPTY_PALETTE: dict[str, str] = {}

#: Screen identifiers ``Frame.screen`` may carry.
SCREENS = ("forest", "decision", "tamper", "proof", "help")

_CLEAR_HOME = "\033[2J\033[H"


@dataclass(frozen=True)
class Theme:
    """ANSI palette, or an empty one when colour is disabled."""

    enabled: bool
    palette: Mapping[str, str]


def theme(colour: bool) -> Theme:
    """Build the ANSI palette, or an all-empty one when ``colour`` is off.

    Callers pass ``colour=isatty()`` or a forced flag, matching the house
    ``--colour`` / ``--no-colour`` convention so output stays clean in pipes.
    """
    return Theme(enabled=bool(colour), palette=_PALETTE if colour else _EMPTY_PALETTE)


def paint(theme: Theme, key: str, text: str) -> str:
    """Wrap ``text`` in the palette code for ``key``, or return it bare.

    The single colour chokepoint — every coloured string in this module goes
    through it, so ``--no-colour`` is provably total rather than depending on
    each call site remembering.
    """
    body = "" if text is None else str(text)
    if theme is None or not getattr(theme, "enabled", False):
        return body
    code = getattr(theme, "palette", _EMPTY_PALETTE).get(key, "")
    return f"{code}{body}{_RESET}" if code else body


# --------------------------------------------------------------------------
# Status vocabulary — the honesty surface
# --------------------------------------------------------------------------

_STATUS_GLYPH: dict[str, str] = {
    "verified": "●",      # ● solid — positive proof
    "degraded": "◐",      # ◐ half  — keyless / unverifiable scheme
    "broken": "✕",        # ✕ cross — the tamper mark, earned only
    "unavailable": "·",   # · dot   — nothing to see, not a failure
    "no-data": "∅",       # ∅ empty — honest absence
    "unknown": "?",
}

_STATUS_COLOUR: dict[str, str] = {
    "verified": "green",
    "degraded": "amber",
    "fuzzy": "amber",
    "broken": "red",
    "unavailable": "grey",
    "no-data": "grey",
    "unknown": "grey",
}

_STATUS_LABEL: dict[str, str] = {
    "verified": "verified",
    "degraded": "degraded",
    "broken": "BROKEN",
    "unavailable": "cannot verify",
    "no-data": "no data",
    "unknown": "unknown",
}


def _norm_status(status: Any) -> str:
    """Normalise a status token: str-Enum aware, ``_``/space folded to ``-``."""
    raw = getattr(status, "value", status)
    text = str(raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    return text or "unknown"


def status_glyph(status: str) -> str:
    """One display glyph per status.

    Mirrors the shipped artifact vocabulary so screen and HTML agree, and gives
    ``unavailable`` / ``no-data`` marks that are visually NOT the tamper mark.
    """
    return _STATUS_GLYPH.get(_norm_status(status), _STATUS_GLYPH["unknown"])


def status_colour(status: str) -> str:
    """Palette key per status.

    Grey is load-bearing: "cannot verify" must never read as red.
    """
    return _STATUS_COLOUR.get(_norm_status(status), "grey")


def _status_label(status: Any) -> str:
    key = _norm_status(status)
    return _STATUS_LABEL.get(key, key)


def _status_cell(status: Any, th: Theme, width: int = 0) -> str:
    """``● verified`` — glyph + label, both in the status colour, padded.

    Pads via :func:`_pad`, not ``str.ljust``: an unrecognised status falls
    through to its raw token (see :func:`_status_label`), so a wide-character
    token would drift the column by the number of two-column glyphs it carries.
    One pad formula, no exceptions — that is the whole point of ``_pad``.
    """
    key = status_colour(status)
    label = _status_label(status)
    if width:
        label = _pad(label, width)
    return paint(th, key, f"{status_glyph(status)} {label}")


# --------------------------------------------------------------------------
# Width maths — ANSI-safe
# --------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _char_width(ch: str) -> int:
    """East Asian Width: ``W``/``F`` occupy two columns, everything else one.

    Matches ``bin/codec-call`` exactly — ``A`` (ambiguous) counts as one, which
    is how kitty and iTerm2 render it by default.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(text: str) -> int:
    """Display width in monospace columns, ANSI escapes stripped.

    Column maths must not count escape bytes, or every coloured row misaligns.
    """
    plain = _ANSI_RE.sub("", "" if text is None else str(text))
    return sum(_char_width(ch) for ch in plain)


def truncate(text: str, width: int, *, ellipsis: str = "…") -> str:
    """Clip to ``width`` visible columns without ever severing an escape.

    A truncation that cut mid-escape would leak colour into the rest of the
    screen, so escapes are emitted whole and a reset is appended when any were
    seen.
    """
    body = "" if text is None else str(text)
    if width <= 0:
        return ""
    if visible_width(body) <= width:
        return body
    ell_w = visible_width(ellipsis)
    budget = width - ell_w
    if budget <= 0:
        return ellipsis if ell_w <= width else ""
    out: list[str] = []
    used = 0
    saw_ansi = False
    i = 0
    while i < len(body):
        match = _ANSI_RE.match(body, i)
        if match:
            out.append(match.group())
            saw_ansi = True
            i = match.end()
            continue
        cw = _char_width(body[i])
        if used + cw > budget:
            break
        out.append(body[i])
        used += cw
        i += 1
    out.append(ellipsis)
    if saw_ansi:
        out.append(_RESET)
    return "".join(out)


def _pad(text: str, width: int) -> str:
    """Truncate then right-pad to EXACTLY ``width`` visible columns.

    The only pad formula in this module. Borders cannot drift because nothing
    else computes ``width - len(...)`` by hand (the bug ``bin/codec-call``
    consolidated away into ``panel_line``).
    """
    clipped = truncate(text, width)
    return clipped + " " * max(0, width - visible_width(clipped))


def wrap_plain(text: str, width: int, *, limit: int = 0) -> list[str]:
    """Wrap UNCOLOURED prose, hard-capped at ``limit`` lines when non-zero.

    The cap keeps a long caption from pushing the layout off-screen; the final
    line gains a ``…`` so the elision is visible rather than silent.
    """
    if width <= 0:
        return []
    body = _ANSI_RE.sub("", "" if text is None else str(text)).strip()
    if not body:
        return []
    lines: list[str] = []
    for para in body.splitlines():
        stripped = para.strip()
        if stripped:
            lines.extend(textwrap.wrap(stripped, width=width) or [stripped])
    if limit and len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = truncate(lines[-1], max(1, width - 2), ellipsis="") + " …"
    return lines


def hrule(width: int, *, char: str = "─", label: str = "") -> str:
    """One horizontal rule, optionally labelled. Panel separators live here."""
    if width <= 0:
        return ""
    if not label:
        return char * width
    tag = f" {str(label).strip()} "
    tag_w = visible_width(tag)
    if tag_w >= width:
        return truncate(tag, width)
    left = 2 if width - tag_w >= 4 else 0
    return char * left + tag + char * (width - tag_w - left)


# --------------------------------------------------------------------------
# Duck-typed field access — see the module docstring
# --------------------------------------------------------------------------

_DASH = "—"


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from an attribute or a mapping key; ``default`` on miss.

    Lets render consume model dataclasses, plain dicts, or test namespaces
    interchangeably, without importing the modules that own those types.
    """
    if obj is None:
        return default
    try:
        value = getattr(obj, name)
    except AttributeError:
        try:
            value = obj[name]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            return default
    return default if value is None else value


def _text(value: Any, default: str = _DASH) -> str:
    """Coerce a value (str-Enum aware) to a display string."""
    if value is None:
        return default
    resolved = getattr(value, "value", value)
    rendered = str(resolved).strip()
    return rendered or default


def _seq(value: Any) -> tuple:
    """Coerce to a tuple, treating str/bytes/Mapping as NOT a sequence."""
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return ()
    if isinstance(value, Sequence):
        return tuple(value)
    try:
        return tuple(value)
    except TypeError:
        return ()


def _short_addr(addr: Any, keep: int = 16) -> str:
    """``sha256:5d916577ca20f703…`` — enough to eyeball, short enough to align."""
    text = _text(addr, "")
    if not text:
        return _DASH
    prefix, _, digest = text.partition(":")
    if digest:
        return f"{prefix}:{digest[:keep]}" + ("…" if len(digest) > keep else "")
    return text[:keep] + ("…" if len(text) > keep else "")


def _scalar(value: Any, width: int = 60) -> str:
    """Render one field value compactly, collapsing containers to SHAPES.

    Nested structures never dump their payload — a belief body or a council
    synthesis must not leak through a generic field renderer.
    """
    if value is None:
        return _DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value}"
    if isinstance(value, str):
        return truncate(" ".join(value.split()), width) or _DASH
    if isinstance(value, Mapping):
        keys = list(value.keys())[:4]
        more = "…" if len(value) > len(keys) else ""
        return "{" + ", ".join(_text(k) for k in keys) + more + f"}} ({len(value)} keys)"
    items = _seq(value)
    if items and all(isinstance(i, (str, int, float, bool)) for i in items[:6]):
        joined = ", ".join(_text(i) for i in items[:6])
        return truncate(joined, width) + (" …" if len(items) > 6 else "")
    return f"[{len(items)} items]"


def _kv_rows(data: Any, width: int, th: Theme, *, limit: int = 8, indent: str = "    ") -> list[str]:
    """Generic ``key  value`` rows — decision keys are entirely kind-dependent."""
    if not isinstance(data, Mapping) or not data:
        return [indent + paint(th, "grey", "(none)")]
    keys = list(data.keys())[:limit]
    label_w = min(22, max((visible_width(_text(k)) for k in keys), default=6))
    value_w = max(8, width - visible_width(indent) - label_w - 2)
    rows = [
        indent
        + paint(th, "grey", _pad(_text(k), label_w))
        + "  "
        + paint(th, "white", _scalar(data[k], value_w))
        for k in keys
    ]
    if len(data) > len(keys):
        rows.append(indent + paint(th, "dim", f"… {len(data) - len(keys)} more field(s)"))
    return rows


# --------------------------------------------------------------------------
# Shared chrome
# --------------------------------------------------------------------------

def _section(label: str, width: int, th: Theme, *, key: str = "amber") -> str:
    """``  LABEL ─────────`` — the house section heading (codec-call style)."""
    prefix = f"  {paint(th, key, label)} "
    rule_w = max(0, width - visible_width(prefix))
    return prefix + paint(th, "dim", "─" * rule_w)


def _box_top(inner_w: int, title: str, th: Theme) -> str:
    """Top border, optionally carrying an already-painted title.

    Each colour run is emitted self-contained so a painted title's reset cannot
    bleed into the remaining border characters.
    """
    if not title:
        return paint(th, "green", "═" * inner_w)
    tag = f" {title} "
    tag_w = visible_width(tag)
    if tag_w >= inner_w:
        return truncate(tag, inner_w)
    left = 2 if inner_w - tag_w >= 4 else 0
    return (
        paint(th, "green", "═" * left)
        + tag
        + paint(th, "green", "═" * (inner_w - tag_w - left))
    )


def _box_rows(inner: Sequence[str], width: int, th: Theme, *, title: str = "") -> list[str]:
    """A double-ruled box spanning ``width`` columns, house double-line style."""
    inner_w = max(1, width - 2)
    edge = paint(th, "green", "║")
    rows = [paint(th, "green", "╔") + _box_top(inner_w, title, th) + paint(th, "green", "╗")]
    rows += [edge + _pad(line, inner_w) + edge for line in inner]
    rows.append(
        paint(th, "green", "╚") + paint(th, "green", "═" * inner_w) + paint(th, "green", "╝")
    )
    return rows


def _narrow(width: int) -> bool:
    """Below this the layout stacks and drops secondary columns."""
    return width < 72


def _bar(segments: Sequence[tuple], width: int, th: Theme) -> str:
    """Proportional coloured bar. ``segments`` is ``[(count, palette_key), ...]``.

    The last NON-ZERO segment absorbs rounding so the bar is always exactly
    ``width`` blocks. It must be the last *non-zero* one rather than simply the
    last: letting a zero-count segment soak up the remainder painted a RED block
    for ``0 not-found`` at odd widths (bar_w 9, 13, 17, 21, …), fabricating a
    tamper signal out of rounding error. A faked red is the one thing this
    module may never emit, so a zero count now paints nothing, always.
    """
    counts = [max(0, int(count)) for count, _ in segments]
    keys = [key for _, key in segments]
    total = sum(counts)
    if total <= 0 or width <= 0:
        return paint(th, "grey", "·" * max(0, width))
    last = max(idx for idx, count in enumerate(counts) if count > 0)
    pieces: list[str] = []
    remaining = width
    for idx, count in enumerate(counts):
        if count <= 0:
            continue
        take = remaining if idx == last else min(remaining, round(count / total * width))
        remaining -= take
        if take > 0:
            pieces.append(paint(th, keys[idx], "█" * take))
    return "".join(pieces)


def _fit(lines: Sequence[str], rows: int, th: Theme) -> list[str]:
    """Pad or clip a block to exactly ``rows`` lines, marking any elision."""
    body = list(lines)
    if rows <= 0:
        return body
    if len(body) > rows:
        body = body[: max(0, rows - 1)]
        body.append(paint(th, "dim", f"  … {len(lines) - len(body)} more line(s)"))
        return body
    return body + [""] * (rows - len(body))


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

_COUNT_BASES = ("decisions", "belief", "citations", "council")


def _counts_line(counts: Mapping[str, int], th: Theme) -> str:
    """``413/413 decisions · 6159 belief · 0 dropped`` — loaded-vs-raw visible."""
    data = counts if isinstance(counts, Mapping) else {}
    if not data:
        return paint(th, "grey", "no counts reported")
    parts: list[str] = []
    seen: set[str] = set()
    for base in _COUNT_BASES:
        if base not in data:
            continue
        loaded = data[base]
        raw = data.get(f"{base}_raw")
        seen.update({base, f"{base}_raw"})
        if raw is not None and raw != loaded:
            parts.append(paint(th, "amber", f"{loaded}/{raw} {base}"))
        else:
            parts.append(f"{paint(th, 'bold', str(loaded))} {paint(th, 'grey', base)}")
    for key in ("rejected", "dropped"):
        if key in data:
            seen.add(key)
            value = data[key]
            hue = "grey" if not value else "red"
            parts.append(paint(th, hue, f"{value} {key}"))
    parts += [
        f"{paint(th, 'bold', str(v))} {paint(th, 'grey', str(k))}"
        for k, v in data.items()
        if k not in seen
    ]
    return paint(th, "grey", " · ").join(parts)


def render_header(
    paths_line: str,
    head_id: str,
    counts: Mapping[str, int],
    theme: Theme,
    width: int,
) -> str:
    """The masthead: PINNED data root, HEAD as captured, loaded-vs-raw counts.

    Showing the resolved root is the visible defence against the session-
    namespacing trap (a TUI reading ``context-<sid>.jsonl`` would render a
    vacuously green screen over nothing). Showing loaded-vs-raw makes a silently
    dropped line obvious. HEAD is labelled *as captured* because the chain is
    appended to live.
    """
    th = theme
    label_w = 8
    title = paint(th, "ml", "◆ GRIP MOAT ◆")
    inner_w = max(1, width - 2)
    rows = [
        f" {paint(th, 'grey', _pad('root', label_w))} {paint(th, 'white', _text(paths_line))}",
        f" {paint(th, 'grey', _pad('head', label_w))} {paint(th, 'cyan', _text(head_id, 'unresolved'))}"
        f" {paint(th, 'dim', '(as captured)')}",
        f" {paint(th, 'grey', _pad('ledger', label_w))} {_counts_line(counts, th)}",
    ]
    if not _narrow(width):
        rows.insert(0, " " + paint(th, "dim", "decided · believed · cited · anchored"))
    body = [truncate(r, inner_w) for r in rows]
    return "\n".join(_box_rows(body, width, th, title=title))


# --------------------------------------------------------------------------
# Forest (browse) pane
# --------------------------------------------------------------------------

def _forest_row(row: Any, selected: bool, kind_w: int, th: Theme, width: int) -> str:
    """One browsable decision: cursor, status glyph, kind, ts, truncated id."""
    marker = paint(th, "cyan", "▸") if selected else " "
    glyph = paint(th, status_colour(_get(row, "status")), status_glyph(_get(row, "status")))
    kind = _pad(_text(_get(row, "kind"), "?"), kind_w)
    kind = paint(th, "bold" if selected else "white", kind)
    stamp = paint(th, "grey", _text(_get(row, "ts"), "")[:20])
    node = _text(_get(row, "node_id"), "?")
    line = f" {marker} {glyph} {kind}  {stamp}  {paint(th, 'dim', node)}"
    if selected:
        depth = _get(row, "depth")
        if depth not in (None, 0) and not _narrow(width):
            line += paint(th, "grey", f"  d{depth}")
    return truncate(line, width)


def render_forest(
    rows: Sequence[Any],
    cursor: int,
    top: int,
    height: int,
    query: str,
    theme: Theme,
    width: int,
) -> str:
    """The browse pane: a windowed viewport over the decision ledger.

    Windowing is what keeps a 413-row (or 6173-row) ledger renderable in
    constant output size, and what makes the pane usable given the real shape —
    400 isolated roots plus one deep chain, so this is a BREADTH surface.
    """
    th = theme
    items = _seq(rows)
    total = len(items)
    view_h = max(1, int(height) if height else 1)
    start = max(0, min(int(top or 0), max(0, total - view_h)))
    window = items[start : start + view_h]
    kind_w = min(30, max((visible_width(_text(_get(r, "kind"), "?")) for r in window), default=8))

    head = _section("DECISIONS", width, th)
    tally = f"{total} record(s)"
    if query:
        tally += f"  ·  filter {paint(th, 'cyan', _text(query))}"
    lines = [head, "  " + paint(th, "grey", tally)]
    if not window:
        lines.append("  " + paint(th, "grey", "no decisions match — nothing to show"))
        return "\n".join(lines)
    lines += [
        _forest_row(row, start + offset == cursor, kind_w, th, width)
        for offset, row in enumerate(window)
    ]
    above, below = start, max(0, total - start - len(window))
    if above or below:
        # truncate() like every sibling line: at the supported floor (width=24)
        # this indicator measures 27 display columns and would wrap, breaking the
        # fixed-height layout. Colour is applied AFTER truncation so the SGR codes
        # are never counted toward the width budget.
        lines.append(
            "  " + paint(th, "dim", truncate(f"▲ {above} above  ·  ▼ {below} below", max(1, width - 2)))
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The four-leg verification strip
# --------------------------------------------------------------------------

def _leg_name(leg: Any) -> str:
    return _text(_get(leg, "name"), "LEG").upper()


def _leg_rows(legs: Sequence[Any], th: Theme, width: int) -> list[str]:
    """The leg rows WITHOUT a section heading, so a caller can supply its own.

    Extracted so the tamper screen's ``BASELINE`` heading does not stack on top
    of ``render_legs``' own ``VERIFICATION`` heading (one strip, one title).
    """
    items = _seq(legs)
    if not items:
        return ["  " + paint(th, "grey", "no legs reported")]
    name_w = min(10, max(visible_width(_leg_name(leg)) for leg in items))
    status_w = max(visible_width(_status_label(_get(leg, "status"))) for leg in items)
    rows = []
    for leg in items:
        name = paint(th, "bold", _pad(_leg_name(leg), name_w))
        cell = _status_cell(_get(leg, "status"), th, status_w)
        head = _text(_get(leg, "headline"), "")
        line = f"    {cell}  {name}"
        if head and not _narrow(width):
            line += "  " + paint(th, "cyan", head)
        rows.append(truncate(line, width))
    return rows


def render_legs(legs: Sequence[Any], theme: Theme, width: int) -> str:
    """The one-glance verdict bar: IDR / BELIEF / CITE / DER.

    Rendered identically on the decision and tamper screens, because this strip
    is the thing that visibly cascades when the forger goes to work.
    """
    return "\n".join([_section("VERIFICATION", width, theme)] + _leg_rows(legs, theme, width))


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

def render_belief_panel(
    entries: Sequence[Any],
    scope: Any,
    theme: Theme,
    width: int,
) -> str:
    """Belief nodes citing this decision, plus the verify scope in force.

    Renders IDENTIFIERS AND COUNTS ONLY. The belief chain may carry client or
    personal content, so no ``summary`` / ``next_step`` text is ever shown.
    """
    th = theme
    items = _seq(entries)
    lines = [_section("WHAT WAS BELIEVED", width, th, key="blue")]
    if scope is not None:
        nodes = len(_seq(_get(scope, "nodes")))
        snap = _short_addr(_get(scope, "snapshot_id"), 24)
        note = f"scope {nodes} node(s) rooted at {snap}"
        if _get(scope, "truncated", False):
            note += "  (truncated)"
        lines.append("    " + paint(th, "grey", truncate(note, max(8, width - 4))))
    if not items:
        lines.append("    " + paint(th, "grey", "no belief node cites this decision"))
        return "\n".join(lines)
    lines.append(
        "    " + paint(th, "grey", f"{len(items)} belief node(s) recorded this decision")
    )
    kind_w = min(20, max((visible_width(_text(_get(e, "kind"), "?")) for e in items[:12]), default=6))
    for entry in items[:12]:
        node = _text(_get(entry, "id"), "?")
        kind = paint(th, "blue", _pad(_text(_get(entry, "kind"), "?"), kind_w))
        stamp = paint(th, "grey", _text(_get(entry, "ts"), "")[:20])
        lines.append(truncate(f"      {kind}  {stamp}  {paint(th, 'dim', node)}", width))
    if len(items) > 12:
        lines.append("      " + paint(th, "dim", f"… {len(items) - 12} more"))
    lines.append("    " + paint(th, "dim", "identifiers only — belief text withheld"))
    return "\n".join(lines)


def render_council_panel(record: Mapping[str, object] | None, theme: Theme, width: int) -> str:
    """Speaker models, seal and memory head for the joined council record.

    Explicitly omits ``prompt``, per-speaker ``answer`` and the judge synthesis —
    the record carries all three as free text and the judge body is embedded
    wholesale in the signed DER payload.
    """
    th = theme
    lines = [_section("COUNCIL RECORD", width, th, key="ml")]
    if not isinstance(record, Mapping) or not record:
        lines.append("    " + paint(th, "grey", "no council record joins this decision"))
        return "\n".join(lines)
    speakers = _seq(record.get("speakers"))
    models = [_text(_get(s, "model"), "?") for s in speakers] or ["—"]
    label_w = 10
    rows = [
        ("speakers", f"{len(speakers)}  " + paint(th, "white", truncate(", ".join(models), max(8, width - 20)))),
        ("seal", paint(th, "cyan", _short_addr(record.get("sha256"), 24))),
        ("idr", paint(th, "cyan", _text(record.get("idr_id")))),
        ("sealed", paint(th, "grey", _text(record.get("sealed_at")))),
    ]
    lines += [
        truncate(f"    {paint(th, 'grey', _pad(k, label_w))}  {v}", width) for k, v in rows
    ]
    lines.append("    " + paint(th, "dim", "prompt, answers and synthesis withheld — privacy"))
    return "\n".join(lines)


_SUB_LEG_MARK: dict[Any, tuple[str, str]] = {
    True: ("●", "green"),
    False: ("✕", "red"),
    "confirmed": ("●", "green"),
    "pending": ("◐", "amber"),
    "absent": ("·", "grey"),
    "skipped": ("·", "grey"),
    "error": ("·", "grey"),
}


def _sub_leg(name: str, value: Any, th: Theme) -> str:
    """One DER sub-leg. ``timestamp`` is a STRING, the other three are bools."""
    glyph, key = _SUB_LEG_MARK.get(value, ("?", "grey"))
    shown = "ok" if value is True else ("FAIL" if value is False else _text(value))
    return paint(th, key, f"{glyph} {name} {shown}")


def _der_panel(leg: Any, theme: Theme, width: int) -> list[str]:
    """The four DER sub-legs, with ``timestamp`` marked informational."""
    th = theme
    lines = [_section("DER ANCHOR", width, th)]
    if leg is None:
        lines.append("    " + paint(th, "grey", "no receipt built for this decision"))
        return lines
    detail = _get(leg, "detail")
    checks = detail if isinstance(detail, Mapping) else {}
    if not checks:
        lines.append(f"    {_status_cell(_get(leg, 'status'), th)}  " + paint(th, "cyan", _text(_get(leg, "headline"), "")))
        return lines
    order = ("fixity", "authenticity", "joint_causation", "timestamp")
    cells = [_sub_leg(k.replace("_", "-"), checks[k], th) for k in order if k in checks]
    cells += [_sub_leg(str(k), v, th) for k, v in checks.items() if k not in order]
    joined = paint(th, "grey", "  ·  ").join(cells)
    lines.append(truncate("    " + joined, width))
    lines.append("    " + paint(th, "dim", "timestamp is informational — ok gates on the first three"))
    return lines


def _cite_panel(leg: Any, theme: Theme, width: int) -> list[str]:
    """Citation tally + a proportional bar. Fuzzy is AMBER-with-caveat."""
    th = theme
    lines = [_section("WHAT IT CITED", width, th)]
    if leg is None:
        lines.append("    " + paint(th, "grey", "no citation data"))
        return lines
    detail = _get(leg, "detail")
    tally = detail if isinstance(detail, Mapping) else {}
    verified = int(tally.get("verified", 0) or 0)
    fuzzy = int(tally.get("fuzzy", 0) or 0)
    missing = int(tally.get("not_found", 0) or 0)
    lines.append(f"    {_status_cell(_get(leg, 'status'), th)}  " + paint(th, "cyan", _text(_get(leg, "headline"), "")))
    if verified or fuzzy or missing:
        bar_w = max(8, min(40, width - 34))
        bar = _bar([(verified, "green"), (fuzzy, "amber"), (missing, "red")], bar_w, th)
        counts = (
            f"{paint(th, 'green', str(verified) + ' verified')}  "
            f"{paint(th, 'amber', str(fuzzy) + ' fuzzy')}  "
            f"{paint(th, 'red', str(missing) + ' not-found')}"
        )
        lines.append(truncate(f"    {bar}  {counts}", width))
    if fuzzy:
        lines.append(
            "    "
            + paint(th, "amber", "fuzzy = typographic match; the bundled independent verifier is stricter")
        )
    return lines


def _find_leg(legs: Sequence[Any], name: str) -> Any:
    for leg in _seq(legs):
        if _leg_name(leg) == name.upper():
            return leg
    return None


def render_decision(view: Any, theme: Theme, width: int) -> str:
    """THE ONE SCREEN: what was decided, believed, cited, and what anchors it.

    Decision fields render generically because ``decision`` keys are entirely
    kind-dependent across the 20 live kinds.
    """
    th = theme
    if view is None:
        return "  " + paint(th, "grey", "no decision selected")
    row = _get(view, "row")
    idr = _get(view, "idr")
    legs = _seq(_get(view, "legs"))
    label_w = 8
    head_rows = [
        ("kind", paint(th, "bold", _text(_get(row, "kind") or _get(idr, "kind")))),
        ("id", paint(th, "white", _text(_get(row, "node_id") or _get(idr, "id")))),
        ("when", paint(th, "grey", _text(_get(row, "ts") or _get(idr, "ts")))),
        ("addr", paint(th, "cyan", _text(_get(row, "addr"), "not addressed"))),
    ]
    lines = [_section("WHAT WAS DECIDED", width, th)]
    lines += [truncate(f"    {paint(th, 'grey', _pad(k, label_w))}  {v}", width) for k, v in head_rows]
    lines.append("")
    lines += _kv_rows(_get(idr, "decision"), width, th)
    lines.append("")
    lines.append(render_legs(legs, th, width))
    lines.append("")
    lines.append(render_belief_panel(_get(view, "believed_by"), _get(view, "scope"), th, width))
    lines.append("")
    lines += _cite_panel(_find_leg(legs, "cite"), th, width)
    lines.append("")
    lines += _der_panel(_find_leg(legs, "der"), th, width)
    lines.append("")
    lines.append(render_council_panel(_get(view, "council_record"), th, width))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Tamper — the delight surface
# --------------------------------------------------------------------------

def _vector_rows(vectors: Sequence[Any], th: Theme, width: int) -> list[str]:
    """The forger's menu. Optional — read from ``session.vectors`` when present."""
    items = _seq(vectors)
    if not items:
        return []
    rows = [_section("THE FORGER'S DESK", width, th, key="ml")]
    label_w = min(20, max(visible_width(_text(_get(v, "label"), "?")) for v in items))
    for index, vector in enumerate(items, start=1):
        key = _text(_get(vector, "key"), str(index))
        label = _pad(_text(_get(vector, "label"), key), label_w)
        caption = _text(_get(vector, "caption"), "")
        hue = "ml" if _get(vector, "honest_limit", False) else "white"
        line = f"   {paint(th, 'cyan', str(index))}  {paint(th, hue, label)}  {paint(th, 'grey', caption)}"
        rows.append(truncate(line, width))
    return rows


def _step_label(step: Any, index: int) -> str:
    for name in ("label", "caption", "vector", "key"):
        value = _get(step, name)
        if value is not None:
            return _text(_get(value, "label") or _get(value, "key") or value)
    return f"step {index}"


def _delta_name_w(before: Sequence[Any], after: Sequence[Any]) -> int:
    """Shared label column for the delta rows AND the merkle row beneath them.

    Both must use ONE width or the merkle row hangs off the leg column — the
    kind of drift the single ``_pad`` formula exists to prevent.
    """
    names = [_leg_name(l) for l in _seq(before)] + [_leg_name(l) for l in _seq(after)]
    return max(6, min(10, max((len(n) for n in names), default=6)))


#: The three GATING sub-checks of a DER receipt, in pincer order. ``timestamp``
#: is excluded deliberately: it does not gate ``ok``, so showing it moving would
#: overstate the break.
_DER_SUBLEGS = ("fixity", "authenticity", "joint_causation")

#: Direction of a sub-check that MOVED, in the forger's own terms.
_SUB_MOVE: dict[tuple[Any, Any], tuple[str, str]] = {
    (False, True): ("← repaired", "green"),
    (True, False): ("← died", "red"),
}


def _sub_delta_row(was: Any, now: Any, th: Theme, width: int, name_w: int) -> list[str]:
    """The DER sub-checks under a leg row, marking the ones that MOVED.

    WITHOUT this the pincer is invisible, which is the whole demo: step 2
    repairs fixity to green in the SAME keypress that kills authenticity, and
    the leg-level verdict reads ``BROKEN -> BROKEN`` either side of it. A
    cascade that renders only the leg status hides the exact moment the trap
    closes — so the sub-checks are shown whenever a receipt carries them.
    """
    pre = _get(was, "detail")
    post = _get(now, "detail")
    if not isinstance(post, Mapping) or not any(k in post for k in _DER_SUBLEGS):
        return []
    prior = pre if isinstance(pre, Mapping) else {}
    cells = []
    for key in _DER_SUBLEGS:
        if key not in post:
            continue
        cell = _sub_leg(key.replace("_", "-"), post[key], th)
        tag, hue = _SUB_MOVE.get((prior.get(key), post[key]), ("", ""))
        cells.append(cell + (" " + paint(th, hue, tag) if tag else ""))
    if not cells:
        return []
    pad = " " * (8 + name_w + 2)
    return [truncate(pad + paint(th, "grey", "  ·  ").join(cells), width)]


def _leg_delta_rows(before: Sequence[Any], after: Sequence[Any], th: Theme, width: int) -> list[str]:
    """``BELIEF  ● verified  ->  ✕ BROKEN  <- changed`` per leg, sub-checks below."""
    pre = {_leg_name(l): l for l in _seq(before)}
    post = {_leg_name(l): l for l in _seq(after)}
    names = list(pre) + [n for n in post if n not in pre]
    if not names:
        return []
    name_w = _delta_name_w(before, after)
    rows = []
    for name in names:
        was = _get(pre.get(name), "status")
        now = _get(post.get(name), "status")
        changed = _norm_status(was) != _norm_status(now)
        line = (
            f"        {paint(th, 'bold' if changed else 'grey', _pad(name, name_w))}  "
            f"{_status_cell(was, th, 13)}  {paint(th, 'dim', '->')}  {_status_cell(now, th, 13)}"
        )
        if changed:
            line += "  " + paint(th, "ml", "← changed")
        rows.append(truncate(line, width))
        rows += _sub_delta_row(pre.get(name), post.get(name), th, width, name_w)
    return rows


def _merkle_row(step: Any, th: Theme, width: int, name_w: int) -> list[str]:
    """The headline number moving old->new, aligned to the leg-name column."""
    was = _text(_get(step, "merkle_before"), "")
    now = _text(_get(step, "merkle_after"), "")
    if not was and not now:
        return []
    moved = was != now
    hue = "ml" if moved else "grey"
    line = (
        f"        {paint(th, 'grey', _pad('merkle', name_w))}  "
        f"{paint(th, 'dim', was or _DASH)}  {paint(th, 'dim', '->')}  {paint(th, hue, now or _DASH)}"
    )
    if moved:
        line += "  " + paint(th, "ml", "← root moved")
    return [truncate(line, width)]


def render_tamper(session: Any, theme: Theme, width: int) -> str:
    """THE DELIGHT SURFACE — the cascade, rendered honestly.

    Baseline legs, then one before->after column per applied step with changed
    legs highlighted and the Merkle root moving old->new. A vector flagged
    ``honest_limit`` renders in its own style explaining that the forge
    SUCCEEDED and why that is a documented boundary, not a defeat.
    """
    th = theme
    if session is None:
        return "  " + paint(th, "grey", "no tamper session open")
    lines = [
        _section("TAMPER", width, th, key="ml"),
        "  " + paint(th, "grey", f"subject {_text(_get(session, 'subject_id'))}"),
        "  " + paint(th, "dim", "every red below is produced by the same shipped verifier that produced the green"),
        "",
        _section("BASELINE — pristine, before any forge", width, th),
        *_leg_rows(_get(session, "baseline"), th, width),
    ]
    steps = _seq(_get(session, "steps"))
    if not steps:
        lines += ["", "  " + paint(th, "grey", "pristine — press a vector key to forge")]
    lines += ["", *_step_rows(steps, th, width)]
    catalogue = _vector_rows(_get(session, "vectors"), th, width)
    if catalogue:
        lines += ["", *catalogue]
    lines += ["", "  " + paint(th, "green", "READ-ONLY — every mutation is in memory; no ledger is written")]
    return "\n".join(lines)


def _step_rows(steps: Sequence[Any], th: Theme, width: int) -> list[str]:
    """One block per applied vector: caption, per-leg delta, Merkle move."""
    rows: list[str] = []
    if steps:
        rows.append(_section("CASCADE", width, th, key="ml"))
    for index, step in enumerate(steps, start=1):
        honest = bool(_get(step, "honest_limit", False))
        hue = "ml" if honest else "amber"
        rows.append(
            truncate(f"    {paint(th, 'cyan', str(index))}  {paint(th, hue, _step_label(step, index))}", width)
        )
        caption = _text(_get(step, "caption"), "")
        if caption:
            rows += ["        " + paint(th, "grey", line) for line in wrap_plain(caption, max(8, width - 8), limit=3)]
        if honest:
            rows.append(
                "        "
                + paint(th, "ml", "HONEST LIMIT — this forge SUCCEEDS: identity is out-of-band")
            )
        before, after = _get(step, "before"), _get(step, "after")
        rows += _leg_delta_rows(before, after, th, width)
        rows += _merkle_row(step, th, width, _delta_name_w(before, after))
        rows.append("")
    return rows


# --------------------------------------------------------------------------
# Inclusion proof + help + footer
# --------------------------------------------------------------------------

def render_proof(proof: Mapping[str, object] | None, verified: bool, theme: Theme, width: int) -> str:
    """Selective disclosure: one decision proven committed, revealing no other."""
    th = theme
    lines = [_section("INCLUSION PROOF", width, th)]
    if not isinstance(proof, Mapping) or not proof:
        lines.append("    " + paint(th, "grey", "no proof available for this node"))
        return "\n".join(lines)
    path = _seq(proof.get("proof"))
    label_w = 12
    rows = [
        ("node", paint(th, "white", _text(proof.get("node_id")))),
        ("content addr", paint(th, "cyan", _text(proof.get("content_addr")))),
        ("forest root", paint(th, "cyan", _text(proof.get("forest_root")))),
        ("proof path", f"{len(path)} sibling hash(es) over n={_text(proof.get('node_n'), '?')}"),
    ]
    lines += [truncate(f"    {paint(th, 'grey', _pad(k, label_w))}  {v}", width) for k, v in rows]
    verdict = "verified" if verified else "broken"
    lines.append(f"    {paint(th, 'grey', _pad('round-trip', label_w))}  {_status_cell(verdict, th)}")
    lines.append(
        "    " + paint(th, "dim", "proves THIS decision is committed to the root, revealing no other")
    )
    return "\n".join(lines)


_KEYS = (
    ("j / k  ↑ ↓", "move the cursor"),
    ("enter", "open the selected decision"),
    ("/", "filter by id, kind or timestamp"),
    ("t", "tamper mode — become the forger"),
    ("p", "inclusion proof for this decision"),
    ("1-9 / r", "apply a tamper vector / reset to pristine"),
    ("esc", "back"),
    ("? / q", "help / quit"),
)

_LEG_MEANING = (
    ("IDR", "the decision was signed and its forest still hashes to the same Merkle root. "
            "It does NOT prove the decision was wise, only that it has not been altered."),
    ("BELIEF", "the belief nodes around this decision form an unbroken signed chain. "
               "A missing link renders ORPHANED ROOT (structural), never TAMPERED."),
    ("CITE", "every quote was found verbatim in its cited source. Fuzzy means a typographic "
             "match only — the bundled independent verifier is stricter and may reject it."),
    ("DER", "fixity, authenticity and joint-causation over a sealed council record. It does "
            "NOT prove WHO signed: identity is out-of-band."),
)


def render_help(theme: Theme, width: int) -> str:
    """Keymap plus, per leg, what it proves AND what it does not prove."""
    th = theme
    lines = [_section("KEYS", width, th)]
    key_w = max(visible_width(k) for k, _ in _KEYS)
    lines += [
        f"    {paint(th, 'cyan', _pad(k, key_w))}  {paint(th, 'grey', d)}" for k, d in _KEYS
    ]
    lines += ["", _section("WHAT EACH LEG PROVES", width, th)]
    for name, meaning in _LEG_MEANING:
        lines.append(f"    {paint(th, 'bold', name)}")
        lines += ["      " + paint(th, "grey", line) for line in wrap_plain(meaning, max(8, width - 6))]
    lines += [
        "",
        "    " + paint(th, "grey", "grey means CANNOT VERIFY (absent key, missing crypto) — never tampering"),
        "    " + paint(th, "green", "nothing this tool does writes to any ledger"),
    ]
    return "\n".join(lines)


def render_footer(mode: str, message: str, theme: Theme, width: int) -> str:
    """Screen name, transient message, and the persistent READ-ONLY indicator."""
    th = theme
    badge = paint(th, "green", "READ-ONLY")
    left = f" {paint(th, 'bold', _text(mode, 'moat').upper())}"
    note = _text(message, "")
    if note:
        left += f"  {paint(th, 'amber', note)}"
    hint = paint(th, "dim", "? help")
    gap = max(1, width - visible_width(left) - visible_width(badge) - visible_width(hint) - 3)
    body = f"{left}{' ' * gap}{hint}  {badge}"
    return paint(th, "dim", hrule(width)) + "\n" + truncate(body, width)


# --------------------------------------------------------------------------
# Frame + the single composed screen
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """Immutable plain data for one screen. ``app.py`` adapts its state into this.

    Defining Frame here (rather than in ``app.py``) is what keeps the dependency
    edge one-way and lets every render test run without importing the app.
    """

    screen: str = "forest"
    width: int = 100
    height: int = 40
    colour: bool = True
    paths_line: str = ""
    head_id: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    rows: Sequence[Any] = ()
    cursor: int = 0
    top: int = 0
    query: str = ""
    view: Any = None
    scope: Any = None
    entries: Sequence[Any] = ()
    session: Any = None
    proof: Mapping[str, object] | None = None
    proof_verified: bool = False
    message: str = ""


def _body_forest(frame: Frame, th: Theme, width: int, rows: int) -> str:
    return render_forest(frame.rows, frame.cursor, frame.top, max(1, rows - 3), frame.query, th, width)


def _body_decision(frame: Frame, th: Theme, width: int, rows: int) -> str:
    if frame.view is None:
        return render_forest(frame.rows, frame.cursor, frame.top, max(1, rows - 3), frame.query, th, width)
    return render_decision(frame.view, th, width)


def _body_tamper(frame: Frame, th: Theme, width: int, rows: int) -> str:
    return render_tamper(frame.session, th, width)


def _body_proof(frame: Frame, th: Theme, width: int, rows: int) -> str:
    return render_proof(frame.proof, frame.proof_verified, th, width)


def _body_help(frame: Frame, th: Theme, width: int, rows: int) -> str:
    return render_help(th, width)


_BODY: dict[str, Any] = {
    "forest": _body_forest,
    "decision": _body_decision,
    "tamper": _body_tamper,
    "proof": _body_proof,
    "help": _body_help,
}


def render_screen(frame: Frame) -> str:
    """Compose header + body + footer into the ENTIRE screen as ONE string.

    One string means one write in ``app.py`` — no partial repaints, no tearing,
    and a test can assert the whole screen exactly. The clear/home escape is
    emitted only when colour is on, so ``--no-colour`` output stays pipe-safe
    and contains no ESC byte at all.
    """
    width = max(24, int(getattr(frame, "width", 100) or 100))
    height = max(8, int(getattr(frame, "height", 40) or 40))
    th = theme(bool(getattr(frame, "colour", False)))
    screen = _text(getattr(frame, "screen", "forest"), "forest")

    header = render_header(frame.paths_line, frame.head_id, frame.counts, th, width)
    footer = render_footer(screen, frame.message, th, width)
    chrome = len(header.splitlines()) + len(footer.splitlines()) + 1
    rows = max(1, height - chrome)

    body_fn = _BODY.get(screen, _body_forest)
    body = body_fn(frame, th, width, rows)
    lines = [header, ""] + _fit(body.splitlines(), rows, th) + [footer]
    return (_CLEAR_HOME if th.enabled else "") + "\n".join(lines)
