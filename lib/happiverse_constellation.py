"""HAPPIVERSE constellation — a PURE, DETERMINISTIC public star-map renderer.

The landing page for ``your-gateway.example.com``: an honest star-map of the
HAPPIverse — the set of *witnessable* nodes people can join. A HAPPIverse
"node" is someone who installed GRIP+HAL+HAPPI (``your-gateway.example.com``) and
stood up their own publicly-verifiable node with its own signed decision chain
(see ``lib/happiverse_proof.py`` for node 001's public proof).

Ethos (do NOT inflate): "Don't trust it — witness it." Exactly ONE node is real
and live today — ``node-001``, the bright anchor star linking to its existing
in-browser proof (``apps/happiverse-proof/index.html``). Everything else is an
OPEN INVITATION: faint empty slots that light up only when a real node
registers. The registry ships with ``node-001`` ONLY. The renderer draws
exactly one star per registry node — it can never fake a member.

LEAK-SAFETY (PARAMOUNT — this is a PUBLIC, LinkedIn-promoted artifact, see
``rules/visual-artifact-leak-prevention.md`` + ``no-sensitive-info-in-grip.md``):
this renderer reads ONLY the six whitelisted, public-safe fields of each node —
``node_id``, ``gh``, ``display``, ``joined``, ``proof_url``, ``status``. Any
other field on a node dict (a stray tailnet IP, hostname, MagicDNS name, email,
phone) is NEVER read, so it can never reach the output. Leak-safe *by schema*,
not by scanning: the private-infra fields simply do not exist in the read set.

Determinism: ``render(nodes, as_of)`` is a pure function — stdlib only, no
network, no clock, no randomness. Star positions come from a SHA-256 hash of the
(stable) ``node_id``; the "as of" date is an explicit argument. The same
(registry, date) input always produces byte-identical HTML — unit-testable and
Goodhart-safe (the animation is client-side CSS; the emitted HTML string is
fixed).

CLI:
    python -m lib.happiverse_constellation render \\
        --registry apps/happiverse-proof/nodes.json \\
        --date 2026-07-01 --out apps/happiverse-proof/constellation.html
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from ipaddress import ip_address
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Optional

# The ONLY fields a public node carries. The renderer reads nothing else, so a
# node dict may not smuggle a private-infra field into the rendered page. This
# is the leak-safe-by-schema guarantee — mirror of the allowlist discipline in
# ``lib/happiverse_proof.WITNESS_ROW_FIELDS``.
NODE_FIELDS = ("node_id", "gh", "display", "joined", "proof_url", "status", "kind")

# ``kind`` separates an identity star (one per GitHub user) from a MACHINE star
# (one per nickname per user). It is a constant drawn from this pair and never
# free text, so it carries no private data. A legacy node predating machine
# stars has no ``kind``; it reads as "" and is treated as an identity star.
KIND_USER, KIND_MACHINE = "user", "machine"

# Exact per-node HTML marker emitted once per rendered node-star and NOWHERE
# else (not in CSS, not on empty slots, not in the legend container). Tests count
# occurrences of this string to assert: node-stars rendered == registry length.
NODE_STAR_MARKER = 'data-node-star="1"'

# Public, leak-safe brand links (informational hrefs only — no scripts, no
# stylesheets, no private infra).
INSTALL_URL = "https://your-gateway.example.com"
ABOUT_URL = "https://your-gateway.example.com"
HAPPI_URL = "https://happi.md"
GITHUB_BASE = "https://github.com/"
INSTALL_CMD = "curl -fsSL https://your-gateway.example.com | bash"

# SVG canvas + star-grid geometry. All rendered coordinates are INTEGERS by
# construction (grid maths uses floor division and integer jitter), so no
# coordinate can ever emit a substring like a private tailnet IP octet.
_VIEW_W, _VIEW_H = 1120, 560
_MAP_X, _MAP_Y, _MAP_W, _MAP_H = 60, 40, 1000, 480
_COLS = 8
_BASE_ROWS = 5
_MIN_SLOTS = 8          # always leave open invitations visible
_JITTER = 16            # deterministic integer star jitter (px)
_BG_STARS = 70          # decorative micro-starfield count (no labels, no nodes)


# --------------------------------------------------------------------------- #
# pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    """HTML-escape a value for safe text/attribute interpolation."""
    return html.escape("" if value is None else str(value), quote=True)


def _hash_int(seed: str) -> int:
    """Deterministic non-negative int from a seed string (SHA-256)."""
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)


def _jitter(seed: str, span: int) -> int:
    """Deterministic integer in [-span, span] from a seed."""
    return _hash_int(seed) % (2 * span + 1) - span


def _clean_node(node: dict, validate: bool = True) -> dict:
    """Project a node onto the whitelisted fields ONLY, and validate its values.

    This is the leak firewall, and it is a CHOKEPOINT: ``render`` and
    ``save_registry`` both project through here, so nothing reaches the public
    HTML or the public registry without passing. A node carrying ``ip``,
    ``host``, ``magicdns`` etc. is stripped to the public set.

    Validating here rather than only in ``add_node`` closes the hole a council
    found and this code reproduced: a HAND-EDITED ``nodes.json`` carrying
    ``{"kind": "machine", "display": "device.tailXXXXXXXX.ts.net"}`` rendered that
    MagicDNS name verbatim, because the join-time guards never ran. Join-time-only
    validation is a fiction whenever the file can be edited — and it can.

    ``validate=False`` exists for readers that must inspect a registry they know
    to be dirty (a migration, a diagnostic). It never runs on a publish path."""
    cleaned = {k: ("" if node.get(k) is None else str(node.get(k, ""))) for k in NODE_FIELDS}
    if validate:
        # Publish exactly what is validated: normalise first, then check.
        cleaned["display"] = _norm_display(cleaned["display"])
        cleaned["proof_url"] = cleaned["proof_url"].strip()
        _check_node_fields(cleaned)
    return cleaned


def _check_node_fields(node: dict) -> None:
    """Every public field, each against its own positive rule.

    The scanner is REQUIRED here: this runs only on the publish path."""
    if not _NODE_ID.match(node["node_id"]):
        raise ValueError(f"node_id must look like node-NNN, refused: {node['node_id']!r}")
    if not _GH_NAME.match(node["gh"]):
        raise ValueError(f"gh must be a GitHub username, refused: {node['gh']!r}")
    if node["joined"] and not _ISO_DAY.match(node["joined"]):
        raise ValueError(f"joined must be an ISO date, refused: {node['joined']!r}")
    if node["status"] not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES}, refused: {node['status']!r}")
    if node["kind"] not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, refused: {node['kind']!r}")
    _check_display(node["display"], required_scan=True)
    _check_proof_url(node["proof_url"], required_scan=True)


def _pick_anchor(nodes: list) -> Optional[dict]:
    """The anchor star: ``node-001`` if present, else the first node, else None.

    The anchor is rendered brightest (twinkling) and, at launch, is the sole live
    node linking to its existing in-browser proof."""
    if not nodes:
        return None
    for node in nodes:
        if node.get("node_id") == "node-001":
            return node
    return nodes[0]


def _order_nodes(nodes: list, anchor: Optional[dict]) -> list:
    """Anchor first, then the remaining nodes in registry order (stable)."""
    if anchor is None:
        return list(nodes)
    return [anchor] + [n for n in nodes if n is not anchor]


def _grid_dims(n_nodes: int) -> tuple:
    """(cols, rows) sized so every node fits AND >= _MIN_SLOTS stay empty."""
    need = n_nodes + _MIN_SLOTS
    rows = max(_BASE_ROWS, -(-need // _COLS))  # ceil division, integer
    return _COLS, rows


def _cell_center(cell: int, cols: int, rows: int) -> tuple:
    """Deterministic integer (cx, cy) for a grid cell, with a stable jitter."""
    col = cell % cols
    row = cell // cols
    cell_w = _MAP_W // cols
    cell_h = _MAP_H // rows
    cx = _MAP_X + col * cell_w + cell_w // 2 + _jitter(f"jx-{cell}", _JITTER)
    cy = _MAP_Y + row * cell_h + cell_h // 2 + _jitter(f"jy-{cell}", _JITTER)
    return cx, cy


def _first_free(start: int, occupied: dict, num: int, center: int) -> int:
    """Deterministic next free cell from ``start`` (skips occupied + centre).

    Extracted so ``_assign_cells`` holds a single loop — the probe is bounded by
    ``num`` (there are always more cells than nodes at launch scale)."""
    cell = start
    probes = 0
    while (cell in occupied or cell == center) and probes <= num:
        cell = (cell + 1) % num
        probes += 1
    return cell


def _assign_cells(ordered: list, cols: int, rows: int) -> tuple:
    """Map each node to a distinct grid cell. Returns (occupied, center_cell).

    The anchor takes the central cell; every other node hashes to a cell and
    resolves collisions via ``_first_free``. One star per cell guarantees no
    overlap and preserves the honest one-star-per-node count."""
    num = cols * rows
    center = (rows // 2) * cols + (cols // 2)
    occupied: dict = {}
    anchor = ordered[0] if ordered else None
    for node in ordered:
        if node is anchor:
            occupied[center] = node
        else:
            start = _hash_int(node["node_id"] or node["gh"]) % num
            occupied[_first_free(start, occupied, num, center)] = node
    return occupied, center


# --------------------------------------------------------------------------- #
# SVG fragments                                                                #
# --------------------------------------------------------------------------- #
def _render_bg_stars() -> str:
    """A deterministic decorative micro-starfield. Pure set-dressing: tiny dots,
    no labels, no links, not nodes — they never carry the node-star marker."""
    parts = ["<g class=\"bg\" aria-hidden=\"true\">"]
    for i in range(_BG_STARS):
        cx = _hash_int(f"bgx-{i}") % _VIEW_W
        cy = _hash_int(f"bgy-{i}") % _VIEW_H
        r = 1 + _hash_int(f"bgr-{i}") % 2
        tw = _hash_int(f"bgt-{i}") % 3
        parts.append(
            f'<circle class="ms tw{tw}" cx="{cx}" cy="{cy}" r="{r}"/>'
        )
    parts.append("</g>")
    return "".join(parts)


def _render_slot(cx: int, cy: int, idx: int) -> str:
    """A faint empty slot — an OPEN INVITATION. Lights up only when a real node
    registers. Deliberately dim and unlabelled; carries no node-star marker."""
    return (
        f'<g class="slot" aria-hidden="true">'
        f'<circle class="slot-ring" cx="{cx}" cy="{cy}" r="10"/>'
        f'<circle class="slot-dot tw{idx % 3}" cx="{cx}" cy="{cy}" r="2"/>'
        f"</g>"
    )


def _render_node_star(node: dict, cx: int, cy: int, is_anchor: bool) -> str:
    """One real node-star. Carries the honest ``NODE_STAR_MARKER`` exactly once.

    Only whitelisted fields are read. If the node has a ``proof_url`` the star is
    a link to its public proof; a still-joining node (no proof yet) is a bright,
    un-linked star. The anchor is larger, live-green with a gold twinkle."""
    display = _esc(node["display"] or node["node_id"])
    gh = _esc(node["gh"])
    proof = node["proof_url"].strip()
    status = (node["status"] or "joining") if proof else "proof pending"
    live = status == "live"
    core_cls = "core anchor twinkle" if is_anchor and proof else ("core live" if live else "core joining")
    glow_r = 26 if is_anchor else 16
    core_r = 7 if is_anchor else 5
    label_y = cy + (glow_r + 18)
    title = f"{display} -- {gh or 'node'} · {_esc(status)}"

    inner = (
        f'<circle class="glow{" pulse" if is_anchor else ""}" cx="{cx}" cy="{cy}" r="{glow_r}"/>'
        f'<circle class="{core_cls}" cx="{cx}" cy="{cy}" r="{core_r}"/>'
        f'<text class="star-label{" anchor" if is_anchor else ""}" x="{cx}" y="{label_y}" '
        f'text-anchor="middle">{display}</text>'
    )
    open_g = (
        f'<g class="node-star{" is-anchor" if is_anchor else ""}" {NODE_STAR_MARKER} '
        f'data-node="{_esc(node["node_id"])}">'
    )
    if proof:
        body = (
            f'<a href="{_esc(proof)}" aria-label="{title} -- open proof">'
            f"<title>{title} -- open the proof</title>{inner}</a>"
        )
    else:
        body = f"<title>{title}</title>{inner}"
    return f"{open_g}{body}</g>"


def _render_map_svg(occupied: dict, center: int, anchor: Optional[dict], cols: int, rows: int) -> str:
    """Assemble the self-contained inline SVG star-map (deterministic order)."""
    parts = [
        f'<svg class="sky" viewBox="0 0 {_VIEW_W} {_VIEW_H}" role="img" '
        f'aria-label="HAPPIverse star-map: live and open node slots" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">',
        '<defs>'
        '<radialGradient id="vign" cx="50%" cy="42%" r="75%">'
        '<stop offset="0%" stop-color="#10203a" stop-opacity="0.55"/>'
        '<stop offset="70%" stop-color="#0d1117" stop-opacity="0"/>'
        '</radialGradient>'
        '<radialGradient id="glowg" cx="50%" cy="50%" r="50%">'
        '<stop offset="0%" stop-color="#5ce08a" stop-opacity="0.9"/>'
        '<stop offset="100%" stop-color="#5ce08a" stop-opacity="0"/>'
        '</radialGradient>'
        '</defs>',
        f'<rect x="0" y="0" width="{_VIEW_W}" height="{_VIEW_H}" fill="url(#vign)"/>',
        _render_bg_stars(),
    ]
    for cell in range(cols * rows):
        cx, cy = _cell_center(cell, cols, rows)
        node = occupied.get(cell)
        if node is None:
            parts.append(_render_slot(cx, cy, cell))
        else:
            parts.append(_render_node_star(node, cx, cy, node is anchor))
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# HTML sections                                                               #
# --------------------------------------------------------------------------- #
def _counts(cleaned: list) -> tuple:
    """(live, joining, total) — honest tallies from status."""
    live = sum(1 for n in cleaned if n["status"] == "live")
    joining = sum(1 for n in cleaned if n["status"] == "joining")
    return live, joining, len(cleaned)


def _render_legend(cleaned: list, anchor: Optional[dict]) -> str:
    """A plain list of the REAL nodes: display, @gh → GitHub, and a proof link.

    Anchor first. A joining node with no proof yet shows 'proof pending' — the
    page never pretends a node is verifiable before it is."""
    if not cleaned:
        return '<p class="muted">No nodes registered yet -- be node 002.</p>'
    rows = []
    for node in _order_nodes(cleaned, anchor):
        display = _esc(node["display"] or node["node_id"])
        gh = node["gh"].strip()
        proof = node["proof_url"].strip()
        live = node["status"] == "live" and bool(proof)
        dot = "live" if live else "joining"
        gh_html = (
            f'<a class="gh" href="{_esc(GITHUB_BASE + gh)}">@{_esc(gh)}</a>'
            if gh else '<span class="muted">—</span>'
        )
        proof_html = (
            f'<a class="verify" href="{_esc(proof)}">witness the proof →</a>'
            if proof else '<span class="muted">proof pending</span>'
        )
        rows.append(
            f'<li class="node-row"><span class="rdot {dot}" aria-hidden="true"></span>'
            f'<span class="nm">{display}</span>{gh_html}{proof_html}'
            f'<span class="st {dot}">{_esc(node["status"] or "joining") if proof else "proof pending"}</span></li>'
        )
    return '<ul class="nodes">' + "".join(rows) + "</ul>"


def _anchor_proof_url(anchor: Optional[dict]) -> str:
    """The anchor's public proof URL (default '/', the existing witness page)."""
    if anchor and anchor["proof_url"].strip():
        return anchor["proof_url"].strip()
    return "/"


def _css() -> str:
    """Self-contained stylesheet — matches apps/happiverse-proof/index.html."""
    return """
:root{--bg:#0d1117;--panel:#161b22;--ink:#e6edf3;--muted:#9da7b3;--line:#2b333d;
--accent:#58a6ff;--live:#5ce08a;--amber:#e3b341;--star:#ffd479;}
*{box-sizing:border-box;}html{-webkit-text-size-adjust:100%;}
body{margin:0;background:radial-gradient(1200px 700px at 50% -8%,#12203a 0%,#0d1117 60%),var(--bg);
color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
padding:clamp(1rem,4vw,3rem);}
main{max-width:920px;margin:0 auto;}
h1{font-size:clamp(1.7rem,6vw,2.6rem);line-height:1.15;margin:0 0 .3rem;letter-spacing:-.01em;}
h2{font-size:1.15rem;margin:2.2rem 0 .5rem;}
.label{color:var(--accent);font-weight:600;letter-spacing:.14em;text-transform:uppercase;font-size:.78rem;}
p{color:var(--ink);}.muted{color:var(--muted);}
code,.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;}
.tag{font-size:1.05rem;color:var(--live);font-weight:600;margin:.1rem 0 .4rem;}
.lede{color:var(--muted);max-width:64ch;}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:1.1rem 1.25rem;margin:1.1rem 0;}
.count{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap;}
.count .big{font-size:clamp(2.2rem,9vw,3.4rem);font-weight:800;color:var(--live);line-height:1;}
.count .cta{font-size:1.05rem;color:var(--ink);font-weight:600;}
.count .sub{color:var(--muted);font-size:.9rem;width:100%;}
.mapwrap{position:relative;border:1px solid var(--line);border-radius:14px;overflow:hidden;
background:#0a0e14;margin:1.1rem 0;}
.sky{display:block;width:100%;height:auto;}
.sky .ms{fill:#c9d6ef;opacity:.35;}
.sky .slot-ring{fill:none;stroke:#3a4759;stroke-width:1;stroke-dasharray:3 4;opacity:.5;}
.sky .slot-dot{fill:#4a5a72;opacity:.55;}
.sky .glow{fill:url(#glowg);opacity:.5;}
.sky .core{fill:var(--live);}
.sky .core.joining{fill:var(--amber);}
.sky .core.anchor{fill:var(--star);}
.sky .star-label{fill:var(--muted);font-size:13px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}
.sky .star-label.anchor{fill:var(--ink);font-weight:700;}
.sky a{cursor:pointer;}
.sky a:focus-visible{outline:3px solid var(--accent);}
.nodes{list-style:none;margin:.4rem 0 0;padding:0;}
.node-row{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;
padding:.55rem .2rem;border-bottom:1px solid var(--line);font-size:.94rem;}
.rdot{width:11px;height:11px;border-radius:50%;flex:none;background:var(--muted);}
.rdot.live{background:var(--live);box-shadow:0 0 8px var(--live);}
.rdot.joining{background:var(--amber);}
.nm{font-weight:600;}
a.gh{color:var(--accent);text-decoration:none;font-family:ui-monospace,Menlo,monospace;font-size:.88rem;}
a.verify{color:var(--live);text-decoration:none;font-size:.88rem;}
a.gh:hover,a.verify:hover{text-decoration:underline;}
.st{margin-left:auto;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;
border:1px solid var(--line);border-radius:20px;padding:.1rem .55rem;color:var(--muted);}
.st.live{color:var(--live);border-color:#1f7a47;}
.st.joining{color:var(--amber);border-color:#7a5a1f;}
.steps{margin:.4rem 0 0;padding-left:1.2rem;}.steps li{margin:.35rem 0;}
pre{background:#0a0e14;border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;
overflow-x:auto;-webkit-overflow-scrolling:touch;}
pre code{color:var(--live);font-size:.92rem;}
a{color:var(--accent);}
.ethos{border-left:3px solid var(--live);padding-left:1rem;color:var(--muted);}
.footnote{font-size:.82rem;color:var(--muted);margin-top:2.6rem;border-top:1px solid var(--line);padding-top:1rem;}
@media (prefers-reduced-motion:no-preference){
.sky .twinkle{animation:tw 3.2s ease-in-out infinite;}
.sky .pulse{animation:pu 4.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center;}
.sky .tw0{animation:tw 3.6s ease-in-out infinite;}
.sky .tw1{animation:tw 2.8s ease-in-out infinite;}
.sky .tw2{animation:tw 4.2s ease-in-out infinite;}
@keyframes tw{0%,100%{opacity:.45;}50%{opacity:1;}}
@keyframes pu{0%,100%{opacity:.35;}50%{opacity:.7;}}
}
""".strip()


def _head_html() -> str:
    """The document head + opening -- static, self-contained (inline <style>)."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>The HAPPIverse -- a star-map of witnessable nodes</title>\n"
        "<meta name=\"description\" content=\"A map of registered HAPPIverse nodes. "
        "Proof links are shown where available. Other nodes are marked proof pending.\">\n"
        f"<style>\n{_css()}\n</style>\n"
        "</head>\n<body>\n<main>\n"
    )


def _hero_html(live: int, joining: int, total: int) -> str:
    """The headline + honest live-count panel."""
    sub = (
        f"{joining} lighting up now · {total} registered"
        if joining
        else f"{total} registered · the rest of the sky is yours"
    )
    return (
        "<p class=\"label\">HAPPIVERSE · star-map · HAPPI/1.6</p>\n"
        "<h1>The HAPPIverse</h1>\n"
        "<p class=\"tag\">Don't trust it -- witness it.</p>\n"
        "<p class=\"lede\">Each star represents a registered <strong>node</strong>. "
        "Registration alone does not prove a signed decision chain. "
        "Open a proof link to check a chain in your browser. "
        "An amber star is a node that is not live yet. Where it has a proof link, you can still check that proof.</p>\n"
        "<div class=\"panel count\">"
        f"<span class=\"big\">{total}</span>"
        f"<span class=\"cta\">nodes registered</span>"
        f"<span class=\"sub\">{sub}</span>"
        "</div>\n"
    )


def _join_html(proof_url: str, as_of: str) -> str:
    """The join call-to-action, witnessable-ethos note, and powered-by footer."""
    return (
        "<h2>Join the HAPPIverse</h2>\n"
        "<div class=\"panel\">\n"
        "<p style=\"margin-top:0\">GRIP and HAL are public today. "
        "Setting up a node requires access to those repositories.</p>\n"
        "</div>\n"
        "<p class=\"ethos\"><strong>Check the available proof:</strong> "
        "Open node 001's proof and re-check the Ed25519 signature over "
        f"its decision chain <a href=\"{_esc(proof_url)}\">in your own browser</a>.</p>\n"
        "<p class=\"footnote\">"
        f"Star-map as of {_esc(as_of)}. Powered by "
        f"<a href=\"{_esc(ABOUT_URL)}\">your-gateway.example.com</a> + "
        f"<a href=\"{_esc(HAPPI_URL)}\">happi.md</a>. "
        "This page is static and self-contained — it makes no cross-node, connected-network, or "
        "external-anchor claim. Each node proves only its own chain.</p>\n"
        "</main>\n</body>\n</html>\n"
    )


def _build_html(cleaned: list, anchor: Optional[dict], as_of: str) -> str:
    """Assemble the full self-contained HTML document (no external resources)."""
    cols, rows = _grid_dims(len(cleaned))
    occupied, center = _assign_cells(_order_nodes(cleaned, anchor), cols, rows)
    svg = _render_map_svg(occupied, center, anchor, cols, rows)
    live, joining, total = _counts(cleaned)
    return (
        _head_html()
        + _hero_html(live, joining, total)
        + f"<div class=\"mapwrap\">{svg}</div>\n"
        + "<h2>The nodes</h2>\n"
        + _render_legend(cleaned, anchor) + "\n"
        + _join_html(_anchor_proof_url(anchor), as_of)
    )


# --------------------------------------------------------------------------- #
# public API                                                                  #
# --------------------------------------------------------------------------- #
def render(nodes: list, as_of: str) -> str:
    """Render the self-contained HTML star-map. PURE + DETERMINISTIC.

    ``nodes`` is the registry list; each node is projected onto the six
    whitelisted public fields before anything is read, so private-infra fields
    cannot leak. ``as_of`` is the explicit date string shown on the page — there
    is no clock, no network, no randomness, so ``render`` is a pure function of
    its arguments and byte-identical across runs."""
    cleaned = [_clean_node(n) for n in (nodes or [])]
    anchor = _pick_anchor(cleaned)
    return _build_html(cleaned, anchor, as_of)


# --------------------------------------------------------------------------- #
# registry IO + join mutation                                                 #
# --------------------------------------------------------------------------- #
def _default_doc() -> dict:
    """A fresh leak-safe registry document wrapper."""
    return {
        "_comment": (
            "PUBLIC leak-safe HAPPIverse node registry. Each node carries ONLY these "
            "seven fields: node_id, gh, display, joined, proof_url, status, kind. NEVER add "
            "tailnet IPs, hostnames, MagicDNS names, emails, or phone numbers — the "
            "renderer reads only the six fields and this file is published publicly. "
            "A user may own several MACHINE stars (join --machine, kind=machine): one "
            "per nickname in display; a nickname is never a hostname or an address."
        ),
        "schema": list(NODE_FIELDS),
        "nodes": [],
    }


def load_registry(path: Path) -> list:
    """Load the node list from a registry file (object-wrapped or bare array)."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        nodes = data.get("nodes", [])
        return nodes if isinstance(nodes, list) else []
    return data if isinstance(data, list) else []


def _load_doc(path: Path) -> dict:
    """Load the full registry document, normalising a bare array into a doc."""
    p = Path(path)
    if not p.exists():
        return _default_doc()
    data = json.loads(p.read_text())
    if isinstance(data, dict):
        data.setdefault("nodes", [])
        return data
    doc = _default_doc()
    doc["nodes"] = data if isinstance(data, list) else []
    return doc


def _next_node_id(nodes: list) -> str:
    """The next ``node-NNN`` id: max existing sequence + 1, zero-padded to 3."""
    top = 0
    for node in nodes:
        nid = str(node.get("node_id", ""))
        if nid.startswith("node-") and nid[5:].isdigit():
            top = max(top, int(nid[5:]))
    return f"node-{top + 1:03d}"


# A nickname is validated by an ALLOWLIST, never a denylist of hostname shapes.
# The first version of this guard enumerated suffixes (.ts.net, .local, a few
# vendor names) and a HAL council (2026-09-22, 4 non-Anthropic voices,
# REQUEST_CHANGES) named five bypasses that all worked when tested:
# "fd7a:115c:a1e0::1", "db01.internal", "nas.home.arpa", "mybox.ts",
# "foo.example.org". Enumerating what a hostname looks like cannot be finished;
# stating what a nickname may contain can. Every named bypass needs a "." or a
# ":", and neither is in the permitted set.
_NICK_EXTRA = set(" '-")
_NICK_MAX = 40

# A bare word with no dot ("workbox", "srv-03") is deliberately ALLOWED: it is
# indistinguishable from a legitimate nickname and carries no routable
# information. That is the accepted residue of this design, not an oversight.

# A proof URL is either site-relative ("/witness.html") or an absolute https URL
# on a publicly-resolvable host. These suffixes are never publicly resolvable, so
# a URL on one of them is private infrastructure by construction.
_PRIVATE_HOST_SUFFIXES = (
    ".ts.net", ".local", ".internal", ".lan", ".corp", ".home", ".arpa", ".intranet",
)

# A public host is dot-separated DNS labels ending in an ALPHABETIC top level.
# Requiring the TLD to be alphabetic is what rejects every numeric address form
# at once — dotted-quad, hex ("0x7f.0x0.0x0.0x1"), octal and integer — without
# enumerating them. A council found the hex form passing ip_address() untouched.
_PUBLIC_HOST = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE
)

# EVERY public field carries a positive rule. The first version guarded only
# display and proof_url, and a council found that gh, node_id, joined and status
# were free text that reached the rendered HTML unchecked — a gh of
# "db01.internal" rendered as a link, and a tailnet host in node_id or an
# "ssh user@host" in status rendered verbatim. A firewall over two of seven
# fields is not a firewall.
_NODE_ID = re.compile(r"^node-\d{3,}$")
_GH_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")   # GitHub's own rule
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STATUSES = ("", "joining", "live")
_KINDS = ("", "user", "machine")


def _fold_apostrophes(text: str) -> str:
    """NFKC, then every apostrophe variant folded to the straight one — so the
    charset check and the key agree on what "the second Mac" contains."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    for curly in ("\u2019", "\u2018", "\u02bc", "\u00b4", "`"):
        text = text.replace(curly, "'")
    return text


def _norm_display(display: str) -> str:
    """The form a display is PUBLISHED in: folded apostrophes, NFKC, collapsed
    whitespace — case preserved.

    ``_clean_node`` writes this back, so the bytes that were validated are the
    bytes that get published. Checking a normalised form while publishing the raw
    one leaves a gap for any character pair that differs between them; a council
    named it, and fullwidth "ＶＰＳ" was the reproduction — checked as "VPS",
    published as the fullwidth form."""
    return " ".join(_fold_apostrophes(display).split())


def _norm_nick(display: str) -> str:
    """Normalise a machine nickname for keying: folded apostrophes, casefold,
    collapsed whitespace — so "the second Mac" and "the second Mac" are one star.
    Trailing punctuation is stripped so "Blackbook." keys as "blackbook"."""
    return " ".join(_fold_apostrophes(display).split()).casefold().strip(".,;:!?")


def _scan_public_text(value: str, what: str, required: bool = False) -> None:
    """Run the canonical leak scanner over a value bound for the public map.

    ``required=True`` on the PUBLISH path (``_clean_node``, reached by both
    ``render`` and ``save_registry``): a missing scanner there is a hard refusal,
    because the allowlist permits a bare word like "mybox" by design and the
    scanner's denylist is the only thing that knows which bare words are real
    private hosts. A council put it exactly right — the loud warning does not
    un-publish the byte.

    ``required=False`` on the advisory pre-checks inside ``add_node``: those give
    a better error earlier, and a broken scanner must not block a join that the
    publish boundary will gate anyway. That keeps the resolution path open
    (``rules/gate-resolution-escape-hatch.md``): a registry can still be loaded
    and repaired when the scanner is broken; it just cannot be published."""
    try:
        from lib.happiverse_proof import leak_hits  # local import: no cycle at module load
    except Exception as exc:
        if required:
            raise ValueError(
                f"leak scanner unavailable ({exc!r}) — refusing to publish {what}; "
                "the allowlist alone does not know which bare words are private hosts"
            ) from exc
        print(
            f"[happiverse] WARNING: leak scanner unavailable ({exc!r}); {what} checked "
            "by the allowlist only until the publish boundary",
            file=sys.stderr,
        )
        return
    hits = leak_hits({what: value})
    if hits:
        raise ValueError(f"{what} carries private data, refused: {hits}")


def _check_display(display: str, required_scan: bool = False) -> None:
    """Refuse any display name outside the permitted character set, then scan it.

    ``display`` is a public field and free text, so it is the obvious way to
    smuggle a hostname onto the map. Permitted: unicode letters and digits,
    space, apostrophe, hyphen; at most 40 characters; at least one letter or
    digit. A dot or a colon is never permitted, which is what rejects every
    hostname, FQDN and IP form without enumerating them.

    This applies to EVERY star, identity and machine alike. The first version
    guarded only machine stars, and a council found that
    ``grip-happiverse-join <user> "db01.internal"`` (no --machine) still wrote a
    private hostname straight onto the public map. The cost of the uniform rule
    is that a display carrying a dot — "J. Smith" — is refused; that is the
    deliberate trade, not an oversight."""
    text = _fold_apostrophes(display).strip()
    if not text:
        raise ValueError("a star needs a display name")
    if len(text) > _NICK_MAX:
        raise ValueError(f"display longer than {_NICK_MAX} characters, refused: {display!r}")
    bad = sorted({c for c in text if not (c.isalnum() or c in _NICK_EXTRA)})
    if bad:
        raise ValueError(
            f"display may contain only letters, digits, space, apostrophe and hyphen; "
            f"refused {display!r} for {bad!r}"
        )
    if not any(c.isalnum() for c in text):
        raise ValueError(f"display needs at least one letter or digit, refused: {display!r}")
    _scan_public_text(text, "display", required=required_scan)


def _check_proof_url(proof_url: str, required_scan: bool = False) -> None:
    """Refuse a proof URL that points at private infrastructure.

    Pre-existing hole, found by the same council and reproduced before fixing:
    ``--proof-url https://device.tailXXXXXXXX.ts.net/proof?ip=<tailnet-ip>`` put both
    the host and the address into the rendered HTML. Empty is fine (a star may
    have no proof yet); otherwise it is a site-relative path or an https URL on
    a publicly-resolvable host."""
    url = str(proof_url or "").strip()
    if not url:
        return
    # A proof link is a scheme, a host and a path. A query or a fragment adds no
    # reachable proof and is a free-text channel onto a public page: a council
    # named it and "?u=user%3Apw" was the reproduction — percent-encoded
    # credentials sailed past every structural rule and only the denylist stood,
    # which is the whack-a-mole this design replaced everywhere else.
    if "?" in url or "#" in url:
        raise ValueError(f"proof_url must carry no query or fragment, refused: {url!r}")
    if url.startswith("/"):
        if url.startswith("//"):
            raise ValueError(f"proof_url must not be protocol-relative, refused: {url!r}")
        _scan_public_text(url, "proof_url", required=required_scan)
        return
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"proof_url must be https or a site-relative path, refused: {url!r}")
    # Userinfo survives every host check, because urlsplit().hostname strips it:
    # "https://user:pw@your-gateway.example.com/p" has a perfectly clean hostname
    # and put "user:pw" into the rendered HTML. Reproduced before fixing.
    if parts.username or parts.password:
        raise ValueError(f"proof_url must not carry credentials, refused: {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"proof_url has an unreadable port, refused: {url!r}") from exc
    if port not in (None, 443):
        raise ValueError(f"proof_url must be on the default https port, refused: {url!r}")
    # A trailing dot is a legal FQDN form and made every suffix test miss
    # ("foo.local." does not end with ".local") — strip it before any check.
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError(f"proof_url has no host, refused: {url!r}")
    if not _PUBLIC_HOST.match(host) or any(host.endswith(s) for s in _PRIVATE_HOST_SUFFIXES):
        raise ValueError(f"proof_url host is not publicly resolvable, refused: {url!r}")
    _scan_public_text(url, "proof_url", required=required_scan)


def _is_machine(node: dict) -> bool:
    """True when a registry node is a machine star. A legacy node with no
    ``kind`` reads as an identity star."""
    return str(node.get("kind", "")).strip().lower() == KIND_MACHINE


def _matches(node: dict, gh: str, display: str, machine: bool) -> bool:
    """Whether an existing node is the one this join updates.

    The two star kinds are DISJOINT by construction: a plain join never matches a
    machine star and a machine join never matches an identity star. Before
    ``kind`` existed, a plain join matched any node of that user by list order,
    so a machine star registered first was silently overwritten and renamed —
    found by the council's Gemini voice, which traced the ordering the tests did
    not cover."""
    if str(node.get("gh", "")).strip().lower() != str(gh or "").strip().lower():
        return False
    if machine:
        return _is_machine(node) and _norm_nick(node.get("display", "")) == _norm_nick(display)
    return not _is_machine(node)


def add_node(
    nodes: list,
    gh: str,
    display: str = "",
    proof_url: str = "",
    status: str = "joining",
    joined: str = "",
    machine: bool = False,
) -> tuple:
    """Add (or idempotently update) a node.

    Writes ONLY the six leak-safe fields. By default a node is keyed by GitHub
    username: if ``gh`` already exists (case-insensitive) the EARLIEST node with
    that username is updated in place (same ``node_id``, no duplicate) and
    ``added`` is False; otherwise a new ``node-NNN`` is appended and ``added`` is
    True. With ``machine=True`` the key is ``(gh, display)``: the display is the
    machine's NICKNAME (never a hostname, address or MagicDNS name — the
    renderer's leak firewall and the shipped leak scans still apply), so one user
    may own several machine stars and re-joining the same nickname updates that
    one star. Returns ``(nodes, added)``."""
    gh = (gh or "").strip()
    if not gh:
        raise ValueError("a GitHub username is required")
    if not _GH_NAME.match(gh):
        raise ValueError(f"gh must be a GitHub username, refused: {gh!r}")
    if machine and not (display or "").strip():
        raise ValueError("a machine star needs a display nickname")
    _check_display(display or gh)
    _check_proof_url(proof_url)
    display = (display or gh).strip()
    kind = KIND_MACHINE if machine else KIND_USER
    matches = [n for n in nodes if _matches(n, gh, display, machine)]
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous join: {len(matches)} existing {kind} nodes match "
            f"{gh!r}/{display!r}; fix the registry before joining"
        )
    for node in matches[:1]:
        if True:
            # Capture prior values BEFORE re-projecting; clear() drops any stray
            # (non-whitelisted) field so it can never persist to the public file.
            nid = node.get("node_id") or _next_node_id(nodes)
            prev_joined = node.get("joined", "")
            prev_proof = node.get("proof_url", "")
            node.clear()
            node.update(
                {
                    "node_id": nid,
                    "gh": gh,
                    "display": display,
                    "joined": joined or prev_joined,
                    "proof_url": proof_url or prev_proof,
                    "status": status,
                    "kind": kind,
                }
            )
            return nodes, False
    nodes.append(
        {
            "node_id": _next_node_id(nodes),
            "gh": gh,
            "display": display,
            "joined": joined,
            "proof_url": proof_url,
            "status": status,
            "kind": kind,
        }
    )
    return nodes, True


def save_registry(path: Path, nodes: list, doc: Optional[dict] = None) -> None:
    """Persist the registry, preserving the leak-safe wrapper + schema."""
    document = doc if doc is not None else _default_doc()
    # Re-project every node onto the whitelist — belt-and-braces against a
    # private-infra field ever being written to the public registry.
    document["nodes"] = [_clean_node(n) for n in nodes]
    Path(path).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def render_to_file(registry_path: Path, out_path: Path, as_of: str) -> str:
    """Render the registry to ``out_path`` and return the HTML."""
    nodes = load_registry(registry_path)
    html_doc = render(nodes, as_of)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_doc, encoding="utf-8")
    return html_doc


def join(
    registry_path: Path,
    out_path: Path,
    gh: str,
    as_of: str,
    display: str = "",
    proof_url: str = "",
    status: str = "joining",
    joined: str = "",
    machine: bool = False,
) -> tuple:
    """Register/update a node then re-render the star-map. Returns (node_id, added).

    Thin public entry point for ``bin/grip-happiverse-join``: preserves the
    leak-safe registry wrapper, writes ONLY the six whitelisted fields, and
    re-renders the self-contained HTML. ``machine=True`` registers a machine
    star keyed by ``(gh, display)`` — see :func:`add_node`."""
    doc = _load_doc(registry_path)
    nodes = doc.get("nodes", [])
    nodes, added = add_node(nodes, gh, display, proof_url, status, joined, machine=machine)
    save_registry(registry_path, nodes, doc)
    render_to_file(registry_path, out_path, as_of)
    node_id = next(
        (n["node_id"] for n in nodes if _matches(n, gh, display or gh, machine)),
        "",
    )
    return node_id, added


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="HAPPIverse constellation star-map renderer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("render", help="render the registry to a self-contained HTML star-map")
    rn.add_argument("--registry", required=True, type=Path)
    rn.add_argument("--date", required=True, help="explicit 'as of' date, e.g. 2026-07-01")
    rn.add_argument("--out", required=True, type=Path)
    ns = ap.parse_args(argv)

    if ns.cmd == "render":
        render_to_file(ns.registry, ns.out, ns.date)
        nodes = load_registry(ns.registry)
        print(f"wrote star-map ({len(nodes)} node(s)) → {ns.out} [as of {ns.date}]")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
