"""Relay Node Constellation — ANSI terminal card renderer.

Renders the relay-host shim's nodes as a live constellation:
  - Each node a star with health glyph (● alive / ○ down / ◐ degraded)
  - Platform glyph (  macOS /  Linux /  Windows)
  - Channels it serves
  - Latency (when alive)
  - Failover-ladder order with arrows

Pure stdlib, deterministic, zero I/O — data flows IN via
``render_constellation(nodes_health, registry_nodes)``, ANSI string flows
OUT.  The data/decoration split makes the renderer Goodhart-testable:
feed fixed NodeHealth + RelayNode lists, assert exact glyph/text presence.

Fail-soft: an empty list renders a "no nodes registered" card, never raises.
"""

from __future__ import annotations

from typing import Sequence

# ---------------------------------------------------------------------------
# ANSI palette (minimal — reset + a handful of colours)
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
WHITE  = "\033[97m"
BLUE   = "\033[34m"

# ---------------------------------------------------------------------------
# Glyph tables — pure data, no logic
# ---------------------------------------------------------------------------

# Health glyphs
_ALIVE    = f"{GREEN}●{RESET}"      # alive
_DOWN     = f"{RED}○{RESET}"        # down / unknown
_DEGRADED = f"{YELLOW}◐{RESET}"     # reachable but not 2xx

# Platform glyphs (Unicode)
_PLATFORM: dict[str, str] = {
    "macos":   "",   # Apple logo
    "linux":   "",   # Tux / Nerd-Font Linux
    "windows": "",   # Windows logo
}
_PLATFORM_DEFAULT = "?"

# Channel colour tags
_CHANNEL_COLOUR: dict[str, str] = {
    "whatsapp": GREEN,
    "slack":    CYAN,
    "discord":  BLUE,
    "web":      YELLOW,
}


# ---------------------------------------------------------------------------
# Per-node helpers  (each < 15 lines — CC < 5)
# ---------------------------------------------------------------------------

def _health_glyph(alive: bool, latency_ms: float | None) -> str:
    if not alive:
        return _DOWN
    if latency_ms is not None and latency_ms > 500:
        return _DEGRADED
    return _ALIVE


def _platform_glyph(platform: str) -> str:
    return _PLATFORM.get(platform.lower(), _PLATFORM_DEFAULT)


def _channel_tag(channel: str) -> str:
    colour = _CHANNEL_COLOUR.get(channel, WHITE)
    return f"{colour}{channel}{RESET}"


def _latency_str(alive: bool, latency_ms: float | None) -> str:
    if not alive:
        return f"{DIM}offline{RESET}"
    if latency_ms is None:
        return f"{DIM}?ms{RESET}"
    colour = GREEN if latency_ms < 200 else YELLOW if latency_ms < 500 else RED
    return f"{colour}{latency_ms:g}ms{RESET}"


def _node_line(
    index: int,
    total: int,
    node_name: str,
    platform: str,
    channels: Sequence[str],
    alive: bool,
    latency_ms: float | None,
    detail: str,
    is_last: bool,
) -> list[str]:
    """Render one node as 2-3 lines: header row + detail row + optional arrow."""
    health   = _health_glyph(alive, latency_ms)
    plat     = _platform_glyph(platform)
    lat      = _latency_str(alive, latency_ms)
    ch_tags  = "  ".join(_channel_tag(c) for c in channels)
    ladder   = f"{DIM}#{index + 1}{RESET}"

    header = (
        f"  {ladder}  {health}  {BOLD}{WHITE}{node_name}{RESET}"
        f"  {plat}  {lat}  [{ch_tags}]"
    )
    detail_line = f"     {DIM}{detail[:90]}{RESET}"

    lines = [header, detail_line]
    if not is_last:
        lines.append(f"     {DIM}↓ failover{RESET}")
    return lines


# ---------------------------------------------------------------------------
# Card frame helpers
# ---------------------------------------------------------------------------

_CARD_WIDTH = 72

def _border(char: str = "─") -> str:
    return f"{DIM}{char * _CARD_WIDTH}{RESET}"


def _title_row() -> str:
    title = "  RELAY NODE CONSTELLATION"
    return f"{BOLD}{CYAN}{title}{RESET}"


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_constellation(
    nodes_health: list,   # list[NodeHealth]
    registry_nodes: list, # list[RelayNode]
) -> str:
    """Render the relay constellation card as an ANSI string.

    Args:
        nodes_health:   list of NodeHealth from RelayHost.health_all()
        registry_nodes: list of RelayNode from RelayHost.nodes()
                        (provides platform + channels; ordered = failover order)

    Returns:
        Multi-line ANSI string, never raises.  Empty inputs → "no nodes" card.
    """
    # Build a fast name -> health lookup
    health_by_name: dict[str, object] = {h.name: h for h in nodes_health}

    lines: list[str] = [
        "",
        _border("─"),
        _title_row(),
        _border("─"),
        "",
    ]

    if not registry_nodes:
        lines += [
            f"  {_DOWN}  {DIM}no relay nodes registered{RESET}",
            f"  {DIM}add nodes to config/relay-nodes.json{RESET}",
            "",
            _border("─"),
            "",
        ]
        return "\n".join(lines)

    total = len(registry_nodes)
    for i, node in enumerate(registry_nodes):
        h = health_by_name.get(node.name)
        alive      = h.alive      if h else False
        latency_ms = h.latency_ms if h else None
        detail     = h.detail     if h else "no health data"

        node_lines = _node_line(
            index=i,
            total=total,
            node_name=node.name,
            platform=node.platform,
            channels=list(node.channels),
            alive=alive,
            latency_ms=latency_ms,
            detail=detail,
            is_last=(i == total - 1),
        )
        lines.extend(node_lines)
        lines.append("")

    # Summary footer
    alive_count = sum(1 for h in nodes_health if h.alive)
    summary_colour = GREEN if alive_count == total else (YELLOW if alive_count else RED)
    summary = (
        f"  {summary_colour}{alive_count}/{total} node(s) alive{RESET}"
        f"  {DIM}· failover order: left→right above{RESET}"
    )
    lines += [summary, "", _border("─"), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry-point (module-level __main__ for `python3 -m lib.relay_constellation`)
# ---------------------------------------------------------------------------

def _main() -> int:  # pragma: no cover — integration path, tested via bin/relay-map
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from lib.relay_host import RelayHost

    host = RelayHost()
    nodes_health   = host.health_all()
    registry_nodes = host.nodes()
    print(render_constellation(nodes_health, registry_nodes))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
