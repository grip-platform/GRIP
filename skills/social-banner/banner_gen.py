#!/usr/bin/env python3
"""Nano Banana Pro (Gemini 3 Pro Image) banner generator.

Recomposes a source image into a wide social banner. The key is read
in-process from keychain `grip-gemini` (the service name, not the secret,
is the only thing on argv; the key returns via stdout and never crosses
argv/env). Saves every returned image part for inspection.

Prompts are PARAMETERISED: the composition discipline (headroom, safe-zone,
containment, mirror-V) is fixed, while the style / subjects / names / motifs
/ background are placeholders. The defaults reproduce the reference banner;
override per banner with `--key=value` flags so the same skill serves any
style, not one hardcoded instance.

Usage:
  banner_gen.py <mode> <infile> <outprefix> [model] [--style=..] [--subjects=..] \
                [--names=..] [--motifs=..] [--bg=..]
    mode  = list | fill | outpaint | linkedin | contained | mirror
    model = gemini-3-pro-image (default) | gemini-3-pro-image-preview | ...
"""
import sys
import subprocess
import pathlib
import mimetypes
from google import genai
from google.genai import types

# Default placeholder fills — reproduce the reference banner. Override via --key=value.
DEFAULTS = {
    "style": "hand-drawn thin black-ink-on-cream cartoon line art",
    "subjects": "two mirror-image grunge bass players in beanies and sunglasses",
    "names": "LAUR and SILJA",
    "motifs": "floating hand-drawn musical notes, treble and bass clefs",
    "bg": "#EDEBD4",
}


def key() -> str:
    return subprocess.run(
        ["security", "find-generic-password", "-s", "grip-gemini", "-w"],
        capture_output=True, text=True, check=True).stdout.strip()


PROMPTS = {
    # Preserve the centred composition, paint into the blank margins only.
    "fill": (
        "This is a wide cover banner in a {style} style. {subjects} face each other in "
        "the centre, with {motifs} between them. KEEP the central {subjects}, the existing "
        "{motifs} and the '{names}' caption EXACTLY as they are — do not redraw or move "
        "them. ONLY fill the empty {bg} margins on the far left and far right by scattering "
        "MORE {motifs} and subtle matching doodles in the IDENTICAL line style and the SAME "
        "background colour ({bg}). Keep it airy and minimal, not crowded. Preserve the "
        "ultra-wide banner aspect ratio. Output one finished image."
    ),
    # Re-imagine from the source into a true ultra-wide banner, subjects at the ends.
    "outpaint": (
        "Extend this {style} into an ULTRA-WIDE 4:1 cover banner. Anchor {subjects} at the "
        "far LEFT and far RIGHT ends, facing inward. Fill the wide centre with the {bg} "
        "background and a tasteful constellation of {motifs}, keeping the '{names}' caption "
        "near the bottom centre. Match the existing line style precisely. Airy, minimal, "
        "balanced. Output one finished ultra-wide image."
    ),
    # LinkedIn-safe: headroom + focal points clear of the bottom-left profile photo.
    "linkedin": (
        "Redraw this as a polished ULTRA-WIDE 4:1 LinkedIn cover banner in the SAME {style} "
        "({bg} background). {subjects}, one near the LEFT, one near the RIGHT, facing inward. "
        "CRITICAL COMPOSITION RULES: "
        "(1) Leave GENEROUS EMPTY BREATHING ROOM along the entire TOP and bottom edges — the "
        "{subjects} occupy only the middle ~75% of the height and must NOT touch or be cut by "
        "the top edge; give clear headroom above each. "
        "(2) Place the focal points in the UPPER half so they stay visible; a circular profile "
        "photo will later cover the BOTTOM-LEFT corner, so keep the extreme bottom-left area as "
        "plain {bg} with at most a few sparse {motifs} — no focal point or text there. "
        "(3) Fill the wide CENTRE with an elegant, balanced flow of {motifs}. "
        "(4) Keep the '{names}' caption at the BOTTOM-CENTRE, clearly rendered. "
        "Calm, airy, intentional, professional. Output one finished ultra-wide image."
    ),
    # Fully-contained subjects (no edge cuts) + margins on all four sides.
    "contained": (
        "Recompose this {style} ({bg} background) as a polished ULTRA-WIDE 4:1 cover banner. "
        "Keep the SAME style, {subjects} facing each other, the {motifs} between them, and the "
        "clearly-rendered '{names}' caption at the bottom-centre. ZOOM OUT so the {subjects} are "
        "FULLY CONTAINED — complete, with NOTHING cut off at any edge. Leave a clear margin of "
        "empty {bg} background on ALL FOUR sides: visible headroom at the top (subjects must not "
        "touch the top) and empty space below (they must not touch the bottom). Spread the "
        "{subjects} a little wider toward the left and right. Keep the extreme BOTTOM-LEFT corner "
        "as plain {bg} (a circular profile photo will sit there). Balanced, airy, professional. "
        "Output one finished ultra-wide image."
    ),
    # Mirror-symmetric: a subject + its exact reflection, caption readable at centre.
    "mirror": (
        "Recreate this as a clean, polished, PERFECTLY MIRROR-SYMMETRIC ultra-wide 4:1 cover "
        "banner, keeping the original's left-right reflection composition. The subject sits on "
        "the LEFT facing RIGHT; on the RIGHT is its EXACT horizontal mirror image (an identical "
        "reflection) facing LEFT; their focal elements meet in a gentle symmetric V at the bottom "
        "centre, like the source. Upgrade to crisp {style} ({bg} background). COMPOSITION: clear "
        "empty HEADROOM at the top (the subjects must not touch the top edge); subjects fully "
        "contained with breathing room; a symmetric spray of {motifs} across the centre between "
        "them. Render the '{names}' caption cleanly and READABLY (NOT mirrored) at the bottom "
        "centre. Keep the extreme bottom-left corner plain {bg} for a profile photo. Balanced, "
        "airy, professional. Output one finished ultra-wide image."
    ),
}


def list_image_models(client):
    for m in client.models.list():
        nm = getattr(m, "name", "")
        if "image" in nm.lower() or "nano" in nm.lower():
            print(nm, "|", getattr(m, "supported_actions",
                                   getattr(m, "supported_generation_methods", "")))


def iter_parts(resp):
    for cand in resp.candidates or []:
        yield from (cand.content.parts or [])


def save_parts(resp, outprefix) -> int:
    saved = 0
    for part in iter_parts(resp):
        data = getattr(getattr(part, "inline_data", None), "data", None)
        if data:
            ext = mimetypes.guess_extension(part.inline_data.mime_type or "image/png") or ".png"
            p = f"{outprefix}_{saved}{ext}"
            pathlib.Path(p).write_bytes(data)
            print("IMAGE:", p, len(data), "bytes")
            saved += 1
        elif getattr(part, "text", None):
            print("TEXT:", part.text[:300])
    return saved


def parse_overrides(argv):
    """--style=.. --names=.. etc. -> {style:.., names:..} (order-independent)."""
    return dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)


def render_prompt(mode, argv):
    return PROMPTS[mode].format(**{**DEFAULTS, **parse_overrides(argv)})


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = pos[0]
    client = genai.Client(api_key=key())
    if mode == "list":
        list_image_models(client)
        return

    infile, outprefix = pos[1], pos[2]
    model = pos[3] if len(pos) > 3 else "gemini-3-pro-image"
    img = types.Part.from_bytes(
        data=pathlib.Path(infile).read_bytes(),
        mime_type=mimetypes.guess_type(infile)[0] or "image/png")
    cfg = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
    resp = client.models.generate_content(
        model=model, contents=[render_prompt(mode, sys.argv), img], config=cfg)

    saved = save_parts(resp, outprefix)
    print(f"saved={saved} model={model}")
    if not saved:
        print("NO_IMAGE_RETURNED", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
