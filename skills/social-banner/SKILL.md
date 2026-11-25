---
name: social-banner
description: Turn any source image into a platform-correct social banner (LinkedIn cover 1584x396 4:1 by default) using a deterministic ImageMagick+Pillow base, Nano Banana Pro (gemini-3-pro-image) generative recompose, and an in-context desktop+mobile verify mock. Use when the user asks to make/resize/improve a LinkedIn banner, cover image, profile header, or social banner.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /social-banner
  - /banner
  - "linkedin banner"
  - "cover image"
  - "profile banner"
  - "make a banner"
integrates:
  - banner_compose.py
  - banner_gen.py
  - banner_verify.py
see_also:
  - optimizing-images
  - converge
  - verify-canonical-not-claimed
---

# /social-banner — image → verified social banner (one command)

Take a source image and produce a **platform-correct, in-context-verified** social
banner. Three composable tools + one PARAMOUNT discipline: **never trust the
dimensions metadata — view the pixels and the in-situ mock before delivering.**

**Plain English**: point it at an image; it resizes/recomposes it to the exact
banner size, optionally has Nano Banana Pro redraw it to fit the wide shape
without squashing or cutting faces, then *shows you* how it will look under the
profile photo + name on both desktop and phone, and only then ships an optimised
file.

## Prerequisites (one-time)

- **ImageMagick** (`magick`) — `brew install imagemagick`
- **Pillow + google-genai** in the GRIP venv — `<venv>/bin/pip install Pillow google-genai`
- **Optimisers** — `brew install pngquant oxipng`
- **Gemini key** in keychain as `grip-gemini` (Nano Banana Pro = `gemini-3-pro-image`).
  The wrapper reads it in-process; the token never crosses argv/env.

## Platform presets (pass any `WxH`; default LinkedIn cover)

| Surface | Size | Aspect | Safe-zone note |
|---|---|---|---|
| **LinkedIn personal cover** (default) | 1584x396 | 4:1 | profile photo eats bottom-left; mobile hides ~15% each side |
| LinkedIn company cover | 1128x191 | ~5.9:1 | — |
| X / Twitter header | 1500x500 | 3:1 | avatar bottom-left |
| YouTube channel art | 2560x1440 (safe 1546x423) | — | only centre safe across devices |

The banner geometry is parametric (`banner_compose.py <in> <out> WxH`). The verify
mock (`banner_verify.py`) is calibrated to **LinkedIn desktop+mobile**; recalibrate
its disc constants for other surfaces (YAGNI — do it when a non-LinkedIn job appears).

## The pipeline (follow in order)

### 1. Recon
Read the source geometry + confirm tools + the `grip-gemini` key. Pick the target
preset. (`identify` / Pillow for dims; sample is automatic in step 2.)

### 2. CBC base — the guaranteed floor
```bash
<venv>/bin/python3 banner_compose.py <src> base.png 1584x396
```
Samples the source's own border colour, **pads (never crops)** to the exact target
aspect, Lanczos-downscales to exactly `WxH`. Distortion-free, content-complete,
correct-by-construction. If the source is already banner-shaped, ship this.

### 3. Generative recompose — the delight (optional, gated)
When the aspect gap is large (a near-square source into 4:1), padding leaves the art
a sparse island. Have **Nano Banana Pro** recompose instead:
```bash
<venv>/bin/python3 banner_gen.py <mode> <src-or-base> out_prefix [model] \
    [--style=..] [--subjects=..] [--names=..] [--motifs=..] [--bg=..]
```
Modes (pick by need):
| mode | use when |
|---|---|
| `outpaint` | spread subjects to the far ends of a true ultra-wide banner |
| `contained` | subjects fully inside the frame with headroom on all sides (no edge cuts) |
| `mirror` | restore a left-right mirror-symmetric composition |
| `fill` | keep the centre subjects, only paint matching detail into blank margins |
| `linkedin` | compose with the bottom-left profile-photo safe-zone in mind |
| `list` | print available image models |

The prompts are **parameterised**: the composition discipline (headroom, safe-zone,
containment, mirror-V) is fixed, while `--style` / `--subjects` / `--names` /
`--motifs` / `--bg` are placeholders. Defaults reproduce the reference banner; pass
your own for any other style (this is what makes the skill reusable, not one-shot).

The model returns ~its own wide aspect (often ~4:1, not exact) — that is expected;
step 4 normalises **any** aspect to exactly `WxH` by padding the off-aspect axis on
the sampled background (never cropping), then a single Lanczos resize. A large input
deviation just means more pad, never a crop or a distortion.

### 4. Re-compose the generated output to EXACT size
```bash
<venv>/bin/python3 banner_compose.py out_0.jpg final.png 1584x396
```
Pads the few off-aspect pixels (no crop), downscales to exactly `WxH`.

### 5. Verify in context — PARAMOUNT, never skip
The metadata will say `1584x396 ✓` even when the pixels are sparse, cramped, or the
profile photo bisects a face. **Render the mock and LOOK** (`verify-canonical`):
```bash
<venv>/bin/python3 banner_verify.py final.png mock_desktop.png "Full Name"
<venv>/bin/python3 banner_verify.py --mobile final.png mock_mobile.png "Full Name"
```
Then **Read** both PNGs. Check: faces clear of the bottom-left photo disc, headroom
at top (subjects not cut by the top edge), names legible and centred (not under the
photo), not sparse, and that faces/names survive the mobile 15%-side-crop. If any
fail → return to step 3 with a sharper mode/prompt (this is a `/converge` loop;
switch strategy if a fix stalls — e.g. deterministic padding can't add headroom when
the source figures are bottom-cut, so regenerate `contained`).

### 6. Optimise + deliver
Line art → PNG (JPG rings the black edges). Palette-reduce + lossless recompress:
```bash
pngquant --quality=90-100 --strip --force --output banner.png 256 final.png
oxipng -o max --strip safe banner.png
```
Verify exact dims + under the platform's file cap (LinkedIn 8 MB), then deliver via
`SendUserFile`. **Re-view the optimised file** to confirm the palette pass didn't
band any shading.

## Anti-patterns (logged falsifiers)

- Trusting `identify`'s `1584x396` as "done" — the pixels were sparse/cramped/occluded
  every time metadata looked fine. View + mock, always.
- JPG for line art — ringing around the black ink. Use PNG.
- Height-cropping a banner whose subjects fill the frame — clips faces/names. Pad width
  or regenerate `contained` instead.
- Real internal data as decoration — fine for the operator's OWN banner (own name/photo);
  forbidden for shared/public templates (`rules/visual-artifact-leak-prevention.md`).

## Provenance + falsification

Crystallised 2026-06-23 (operator-directed) from the LAUR/SILJA LinkedIn banner: a
near-square Nirvana cartoon driven through CBC base → Nano Banana Pro
outpaint/contained/mirror → desktop+mobile verify → palette-optimised delivery.
**N=1 instance-class** (one banner job, many iterations) — created early by explicit
operator request, not by the functor default (then N=3, N=2 since 2026-10-03); the falsifier below makes it a
maturing artifact, not a monument.

Wrong / prune if, over the next ~5 banner tasks: nobody invokes it (skill-spam — the
`/create` Q1 risk); OR the verify-mock never once catches a layout problem the raw
banner hid (then step 5 is overhead — but this session it caught sparse-island,
cramped-top, and photo-occlusion, so it earns its place today); OR the generative step
proves unreliable enough that the deterministic base is always shipped (then demote to
a `banner_compose` one-liner in `optimizing-images` and delete the rest). Track:
banner tasks using this skill vs done ad-hoc; verify-mock catches per task.
