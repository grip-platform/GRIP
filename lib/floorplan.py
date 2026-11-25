"""Deterministic scaled floor-plan rendering — dimensioned SVG with a scale bar.

Build-from-zero for the estate gap measured 2026-09-13: no floor-plan or CAD
output existed anywhere (no DXF, no dimensioned SVG, no scale bar, no metric
coordinates). This module provides the representation layer ONLY: it turns
measured metres into a dimensioned SVG. It does not measure anything itself —
accuracy is driven by the input measurements, and `lib/artifact_accuracy.py`
provides the instruments to compare the output against a reference.

Design constraints:
- Pure stdlib. No geometry dependencies; rectangles only (KISS).
- Deterministic: same input => byte-identical SVG (fixed float formatting,
  stable ordering), so renders can be replayed and diffed.
- Metric everywhere: inputs in metres, dimension labels in metres, the scale
  bar labelled in metres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Room:
    """Axis-aligned room, metres. x/y is the top-left corner."""

    name: str
    x: float
    y: float
    w: float
    h: float


def _fmt(value: float) -> str:
    """Fixed-width metre formatting — never scientific notation."""
    return f"{value:.2f}"


def _px(value: float, ppm: float) -> float:
    return value * ppm


@dataclass
class FloorPlan:
    """A set of rooms plus an overall boundary, rendered at pixels-per-metre."""

    rooms: list[Room] = field(default_factory=list)
    width: float | None = None   # overall boundary width, metres (None = derive)
    height: float | None = None  # overall boundary height, metres (None = derive)
    ppm: float = 20.0    # pixels per metre
    margin: float = 40.0  # outer margin, pixels

    def add_room(self, name: str, x: float, y: float, w: float, h: float) -> "FloorPlan":
        if w <= 0 or h <= 0:
            raise ValueError(f"room '{name}' must have positive w/h (got {w} x {h})")
        self.rooms.append(Room(name=name, x=x, y=y, w=w, h=h))
        return self

    def svg(self) -> str:
        """Render the plan as a dimensioned SVG (deterministic)."""
        if not self.rooms:
            raise ValueError("cannot render an empty floor plan")
        width = self.width if self.width is not None else max(
            (r.x + r.w for r in self.rooms), default=0.0)
        height = self.height if self.height is not None else max(
            (r.y + r.h for r in self.rooms), default=0.0)
        if width <= 0 or height <= 0:
            raise ValueError("floor plan boundary must be positive")
        ppm = self.ppm
        m = self.margin
        vb_w = _px(width, ppm) + 2 * m
        vb_h = _px(height, ppm) + 2 * m

        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{vb_w:.1f}" height="{vb_h:.1f}" '
            f'viewBox="0 0 {vb_w:.1f} {vb_h:.1f}">',
            f'  <g transform="translate({m:.1f},{m:.1f})">',
            f'    <rect x="0" y="0" width="{_px(width, ppm):.2f}" '
            f'height="{_px(height, ppm):.2f}" fill="none" '
            f'stroke="#000" stroke-width="2"/>',
        ]

        for room in sorted(self.rooms, key=lambda r: (r.x, r.y, r.name)):
            rx, ry = _px(room.x, ppm), _px(room.y, ppm)
            rw, rh = _px(room.w, ppm), _px(room.h, ppm)
            parts.append(
                f'    <rect x="{rx:.2f}" y="{ry:.2f}" width="{rw:.2f}" '
                f'height="{rh:.2f}" fill="#f5f5f5" stroke="#333" '
                f'stroke-width="1.5"/>'
            )
            label_x, label_y = rx + rw / 2, ry + rh / 2
            parts.append(
                f'    <text x="{label_x:.2f}" y="{label_y:.2f}" '
                f'text-anchor="middle" dominant-baseline="middle" '
                f'font-family="sans-serif" font-size="12">{room.name}</text>'
            )
            parts.extend(_dimension_lines(room, ppm, m))

        parts.append(f'    {_scale_bar(ppm)}')
        parts.append("  </g>")
        parts.append("</svg>")
        return "\n".join(parts)


def _dimension_lines(room: Room, ppm: float, margin: float) -> list[str]:
    """Width dimension (top, outside the room) and height dimension (left)."""
    rx, ry = _px(room.x, ppm), _px(room.y, ppm)
    rw, rh = _px(room.w, ppm), _px(room.h, ppm)
    dy = -12.0  # offset of the width dimension line above the room
    dx = -12.0  # offset of the height dimension line left of the room

    def dim_line(x1: float, y1: float, x2: float, y2: float,
                 label: str, lx: float, ly: float) -> list[str]:
        return [
            f'    <line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" '
            f'y2="{y2:.2f}" stroke="#888" stroke-width="1"/>',
            f'    <line x1="{x1:.2f}" y1="{y1 - 4:.2f}" x2="{x1:.2f}" '
            f'y2="{y1 + 4:.2f}" stroke="#888" stroke-width="1"/>',
            f'    <line x1="{x2:.2f}" y1="{y2 - 4:.2f}" x2="{x2:.2f}" '
            f'y2="{y2 + 4:.2f}" stroke="#888" stroke-width="1"/>',
            f'    <text x="{lx:.2f}" y="{ly:.2f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="10" fill="#555">'
            f'{label} m</text>',
        ]

    lines = dim_line(rx, ry + dy, rx + rw, ry + dy,
                     _fmt(room.w), rx + rw / 2, ry + dy - 4)
    lines += dim_line(rx + dx, ry, rx + dx, ry + rh,
                      _fmt(room.h), rx + dx - 14, ry + rh / 2)
    return lines


def _scale_bar(ppm: float) -> str:
    """A scale bar whose labelled length is the nearest whole metre below
    100 px, so the label is always a round number in metres."""
    px_goal = 100.0
    metres = max(1, int(px_goal / ppm))
    px = metres * ppm
    y = 24.0
    label = f'{metres} m'
    return (
        f'<line x1="0" y1="{y:.1f}" x2="{px:.2f}" y2="{y:.1f}" '
        f'stroke="#000" stroke-width="2"/>'
        f'<line x1="0" y1="{y - 4:.1f}" x2="0" y2="{y + 4:.1f}" '
        f'stroke="#000" stroke-width="2"/>'
        f'<line x1="{px:.2f}" y1="{y - 4:.1f}" x2="{px:.2f}" y2="{y + 4:.1f}" '
        f'stroke="#000" stroke-width="2"/>'
        f'<text x="{px / 2:.2f}" y="{y - 8:.1f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="11">{label}</text>'
    )


def render_floorplan(rooms: Iterable[Room], width: float, height: float,
                     ppm: float = 20.0) -> str:
    """Convenience: build and render a plan in one call."""
    plan = FloorPlan(rooms=list(rooms), width=width, height=height, ppm=ppm)
    return plan.svg()
