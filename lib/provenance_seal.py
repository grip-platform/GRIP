#!/usr/bin/env python3
"""provenance_seal.py — a deterministic notarial seal for a /prove-it artifact.

A notary's seal binds a document to an authority with a mark that is hard to
forge, and that breaks if the document is altered. The Provenance Seal is the
structural analogue for an AI response:

    SMT mapping (Gentner Structure-Mapping — systematic, one-to-one, relational):
      notary's seal              ::  this provenance sigil
      the sealed document        ::  the verified response
      the issuing authority      ::  the deterministic verifier (prove_it floor)
      the seal's intricate mill  ::  the SHA-256-derived tooth pattern
      forgery-resistance         ::  SHA-256 collision-resistance
      tampering BREAKS the seal  ::  altering a source changes its SHA, which
                                     changes the tooth pattern (the seal "breaks")
      a reader trusts on sight   ::  the grounding rate + colour readable at a glance

    The mapping is relational, not surface: it does NOT rest on "both are round
    and decorative". Every base relation (bind, authorise, resist-forgery,
    break-on-tamper, trust-on-sight) maps to exactly one target relation. The
    load-bearing one is break-on-tamper: the seal's pattern is a pure function of
    the source SHA-256 digests, so a single changed byte in any source yields a
    visibly different seal — exactly as a wax seal cracks when a sealed letter is
    opened.

Pure + deterministic: same provenance record -> byte-identical SVG (no time, no
randomness). Stdlib only. The seal DECORATES a /prove-it artifact; it never
verifies — verification is prove_it.py's deterministic floor. This module reads a
provenance record (prove_it.provenance() / HAPPI cite.verify completed.usage) and
renders. Origin: 2026-06-19, /legal-provenance delight wave.
"""
from __future__ import annotations

import hashlib
import html
import math

# Trust palette — keyed to the provenance tally, not to anyone's opinion.
_GREEN = "#3fb950"   # every citation exact-verified
_AMBER = "#d29922"   # all grounded, some only whitespace/typographic-variant
_RED = "#f85149"     # at least one citation NOT in its source (unproven)
_INK = "#e6edf3"
_DARK = "#0d1117"

_TEETH = 48  # milled-edge teeth around the rim


def seal_colour(tally: dict) -> str:
    """The seal's colour states the trust level deterministically:
    RED if any citation is not_found, AMBER if all grounded but some fuzzy,
    GREEN only when every citation is an exact verbatim match."""
    if tally.get("not_found", 0) > 0:
        return _RED
    if tally.get("fuzzy", 0) > 0:
        return _AMBER
    return _GREEN


def fingerprint(prov: dict) -> str:
    """A single document fingerprint: SHA-256 over the sorted per-source SHA-256
    digests. Changing ANY source's bytes changes its sha256, hence this, hence the
    tooth pattern below — the break-on-tamper relation made mechanical."""
    shas = sorted(s.get("sha256", "") for s in prov.get("sources", {}).values())
    return hashlib.sha256("".join(shas).encode("utf-8")).hexdigest()


def _teeth_path(cx: float, cy: float, r_in: float, r_amp: float, fp: str) -> str:
    """Radial teeth whose individual lengths are driven by the fingerprint bytes —
    the seal's hard-to-forge mill. Returns one SVG <path> 'd' string."""
    fp_bytes = bytes.fromhex(fp)  # 32 bytes
    segs = []
    for i in range(_TEETH):
        b = fp_bytes[i % len(fp_bytes)]
        r_out = r_in + r_amp * (b / 255.0)
        ang = 2 * math.pi * i / _TEETH
        x1 = cx + r_in * math.cos(ang)
        y1 = cy + r_in * math.sin(ang)
        x2 = cx + r_out * math.cos(ang)
        y2 = cy + r_out * math.sin(ang)
        segs.append(f"M{x1:.2f} {y1:.2f}L{x2:.2f} {y2:.2f}")
    return "".join(segs)


def _rim_text(prov: dict, fp: str) -> str:
    """Curved rim legend — the seal's inscription. sha prefix makes the fingerprint
    legible; the grounding rate states the proof at a glance."""
    pct = int(round(prov.get("grounding_rate", 0.0) * 100))
    return (f"DETERMINISTIC PROVENANCE · {pct}% GROUNDED · "
            f"sha256 {fp[:8]} · prove-it")


def seal_svg(prov: dict, size: int = 132) -> str:
    """Render the Provenance Seal for a provenance record as a self-contained SVG.

    Deterministic: identical `prov` -> byte-identical SVG. `prov` is the record from
    prove_it.provenance() or a HAPPI cite.verify completed.usage (keys: sources{},
    tally{}, grounding_rate)."""
    tally = prov.get("tally", {})
    fp = fingerprint(prov)
    half = size / 2.0
    r_mill_in = half - 13
    return _seal_markup({
        "size": size, "c": half, "r_outer": half - 4, "r_mill_in": r_mill_in,
        "teeth": _teeth_path(half, half, r_mill_in, 8.0, fp),
        "rim": html.escape(_rim_text(prov, fp)),
        "pct": int(round(prov.get("grounding_rate", 0.0) * 100)),
        "colour": seal_colour(tally),
        "v": tally.get("verified", 0), "f": tally.get("fuzzy", 0),
        "nf": tally.get("not_found", 0),
    })


def _seal_markup(g: dict) -> str:
    """Assemble the seal SVG string from pre-computed geometry `g`."""
    cx = cy = g["c"]
    size, colour, pct, rim = g["size"], g["colour"], g["pct"], g["rim"]
    r_outer, r_mill_in = g["r_outer"], g["r_mill_in"]
    r_text = r_mill_in - 7
    rim_path = f"M{cx} {cy - r_text} A{r_text} {r_text} 0 1 1 {cx - 0.01} {cy - r_text}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}" role="img" '
        f'aria-label="Provenance seal: {pct}% grounded, '
        f'{g["v"]} verified, {g["f"]} fuzzy, {g["nf"]} not found">'
        f'<defs><path id="seal-rim" d="{rim_path}" fill="none"/></defs>'
        f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="{_DARK}" '
        f'stroke="{colour}" stroke-width="1.5"/>'
        f'<path d="{g["teeth"]}" stroke="{colour}" stroke-width="1.4" '
        f'stroke-linecap="round" fill="none" opacity="0.9"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r_mill_in - 2}" fill="none" '
        f'stroke="{colour}" stroke-width="0.8" opacity="0.55"/>'
        f'<text font-family="ui-monospace,Menlo,monospace" font-size="6.3" '
        f'fill="{colour}" letter-spacing="0.7" opacity="0.95">'
        f'<textPath href="#seal-rim" startOffset="2%">{rim}</textPath></text>'
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" '
        f'font-family="-apple-system,Segoe UI,sans-serif" font-size="26" '
        f'font-weight="700" fill="{colour}">{pct}%</text>'
        f'<text x="{cx}" y="{cy + 13}" text-anchor="middle" '
        f'font-family="-apple-system,Segoe UI,sans-serif" font-size="7.5" '
        f'fill="{_INK}" letter-spacing="2" opacity="0.8">GROUNDED</text>'
        f'<circle cx="{cx - 14}" cy="{cy + 24}" r="2.6" fill="{_GREEN}"/>'
        f'<circle cx="{cx}" cy="{cy + 24}" r="2.6" fill="{_AMBER}"/>'
        f'<circle cx="{cx + 14}" cy="{cy + 24}" r="2.6" fill="{_RED}"/>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# Embedding — post-process a /prove-it HTML artifact (leaves prove_it.py untouched)
# ---------------------------------------------------------------------------

_HEADER_ANCHOR = "<div class='banner'>"
_SEAL_WRAP_OPEN = ('<div class="prov-seal" style="position:absolute;top:12px;'
                   'right:18px;width:96px;height:96px">')
_SEAL_WRAP_CLOSE = "</div>"


def embed_seal(artifact_html: str, prov: dict) -> str:
    """Inject the Provenance Seal into the header of a /prove-it HTML artifact.

    Idempotent: a second call does not add a second seal. Returns the html
    unchanged if the expected header anchor is absent (fail-soft — never corrupts
    the artifact)."""
    if 'class="prov-seal"' in artifact_html:
        return artifact_html
    if _HEADER_ANCHOR not in artifact_html:
        return artifact_html
    seal = _SEAL_WRAP_OPEN + seal_svg(prov, size=96) + _SEAL_WRAP_CLOSE
    # make <header> a positioning context so the absolute seal anchors to it
    out = artifact_html.replace("header{", "header{position:relative;", 1)
    return out.replace(_HEADER_ANCHOR, seal + _HEADER_ANCHOR, 1)
