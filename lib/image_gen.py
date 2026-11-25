#!/usr/bin/env python3
"""Generic image-generation syscall — Gemini Nano Banana Pro + /converge + Pillow + ImageMagick.

The channel-agnostic core for `imagine` (and AI-`emoji`) on ANY channel
(WhatsApp / Slack / Discord / Web). One stable `generate_image()` API; the TS
grip-channel daemon invokes it via the CLI. Syscall-doctrine shim for image gen
(mirrors lib/slack_transport / lib/whatsapp_transport — one API, every caller).

Pipeline:
    prompt
      -> /converge prompt-refinement (lib.converge.converge, depth 11):
          generate (Gemini Nano Banana Pro) -> score -> refine prompt -> regenerate
          until criterion met OR max_depth, tracking the best image by score
      -> Pillow + ImageMagick post-processing (resize/format/transparency/strip)
      -> ImageResult (bytes + format + metadata)

Model: `gemini-3-pro-image` (Nano Banana Pro), graceful fallback to
`gemini-nano-banana-2.1` (Nano Banana) on 404 — never fail-hard. Both replace
models Google has shut down (deprecations page, read 2026-11-24): the Nano Banana
Pro preview on 25 June 2026 and Gemini 2.5 Flash Image on 15 January 2026.

Origin: 2026-05-29, W2 of the Guild-bot fold (H791). V>> directive: "create an
image using our core Gemini Nano Banana Pro /converge prompting technology +
ImageMagick + Pillow. Criterion: CBC - optimal novel delight++. Depth = 11."

CBC: correct-by-construction — every external call fail-safe; scorer/refiner are
INJECTABLE (DIP) so the heuristic floor can be swapped for an LLM-judge "delight"
layer (the llm-augmentation pattern: mechanical floor + optional LLM recall)
WITHOUT touching callers. The default heuristic is an HONEST measurable floor
(validity + colour entropy + contrast), NOT a claim of "optimal delight" — the
LLM judge is the documented delight layer, injected, not faked.

Secret handling: the Gemini key is resolved in-process via the macOS keychain
(canonical GRIP provider pattern, mirrors lib/hal/providers/anthropic_native.py)
or passed explicitly by the caller. The value never leaves this module's scope
and is never logged (syscall-doctrine; env-var-protection-safe by construction).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from converge import converge  # noqa: E402  (reuse the canonical kernel)

# --- model + API (Nano Banana Pro primary, Nano Banana fallback) --------------
NANO_BANANA_PRO = "gemini-3-pro-image"
NANO_BANANA_FALLBACK = "gemini-nano-banana-2.1"
_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_KEYCHAIN_SERVICE = "grip-gemini-api-key"
_DEPTH = 11  # V>>: depth = 11 (the optimal prime / fold-the-kernel depth)
_SCORE_TARGET = 0.80  # converge exits when best score >= this (the CBC bar)
_HTTP_TIMEOUT_S = 60

# --- per-purpose / per-channel output specs -----------------------------------
# emoji sizes per channel's native spec; image = full-res passthrough.
_EMOJI_SPEC = {
    "discord": {"size": 128, "fmt": "png", "transparent": True},
    "slack": {"size": 128, "fmt": "png", "transparent": True},
    "web": {"size": 128, "fmt": "png", "transparent": True},
    # WhatsApp has no custom emoji — the "novel technology" is a 512px webp
    # STICKER (the closest native primitive). Delivered by the WA adapter.
    "whatsapp": {"size": 512, "fmt": "webp", "transparent": True},
}
_DEFAULT_EMOJI = {"size": 128, "fmt": "png", "transparent": True}
_IMAGE_SPEC = {"size": None, "fmt": "png", "transparent": False}  # full-res


@dataclass
class ImageResult:
    ok: bool
    data: Optional[bytes] = None
    fmt: str = "png"
    model: str = ""
    depth_used: int = 0
    score: float = 0.0
    prompt_final: str = ""
    error: str = ""
    meta: dict = field(default_factory=dict)


# --- secret resolution (in-scope keychain; never logged/echoed) ---------------
def _resolve_key(explicit: Optional[str] = None) -> Optional[str]:
    """Gemini key, in-process scope only. Explicit arg wins; else macOS keychain
    (canonical provider pattern). Value never returned to logs. On non-keychain
    hosts the caller passes api_key explicitly."""
    if explicit:
        return explicit
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", _KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# --- Gemini generateContent image call ----------------------------------------
def _shield(value):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value)


def _img_mime(data: bytes) -> str:
    """Detect a supported image mime from magic bytes; default image/png."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _coerce_image_bytes(base_image) -> Optional[bytes]:
    """Accept a path (str/Path) or raw bytes; return bytes or None. Fail-safe —
    an unreadable base image degrades to text-to-image, never a crash."""
    if base_image is None:
        return None
    if isinstance(base_image, (bytes, bytearray)):
        return bytes(base_image)
    try:
        return Path(base_image).read_bytes()
    except (OSError, TypeError, ValueError):
        return None


def _image_parts(prompt: str, base_image: Optional[bytes]) -> list:
    """The request parts: the base image first when there is one, then the prompt."""
    parts: list = []
    if base_image:
        parts.append({"inline_data": {"mime_type": _img_mime(base_image),
                                      "data": base64.b64encode(base_image).decode()}})
    parts.append({"text": prompt})
    return parts


def _first_image(data: dict) -> Optional[bytes]:
    """The first inline image in a generateContent response, or None."""
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    for p in parts:
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            try:
                return base64.b64decode(inline["data"])
            except (ValueError, TypeError):
                return None
    return None


def _gemini_image(prompt: str, api_key: str, model: str,
                  base_image: Optional[bytes] = None) -> Optional[bytes]:
    """One generateContent image call. Returns PNG/JPEG bytes or None. Fail-safe.

    When base_image bytes are supplied they are sent as an inline_data part
    BEFORE the text (Nano Banana Pro img2img / reference-guided editing — the
    base yin-yang artwork becomes the visual reference the model rides on). The
    base image never leaves this process except inside the outbound API call.

    The prompt text passes the redaction shield first; image bytes are not text
    and go unchanged. When the shield cannot run, nothing is sent: None."""
    import urllib.error
    import urllib.request

    try:
        body = json.dumps(_shield({
            "contents": [{"parts": _image_parts(prompt, base_image)}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        })).encode()
    except OSError:  # a failed scrub, or no shield to load: nothing is sent
        return None
    url = f"{_API_BASE}/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # 404/400 = model unavailable / bad request -> fall back to the next model
        # silently. Other codes (401 auth, 429 rate-limit, 5xx) are NOT normal
        # fallback cases: log them to stderr so production failures are debuggable,
        # then return None (the kernel stays fail-safe — the caller renders a
        # graceful miss rather than crashing). Distinct handling, not a dead branch.
        if e.code not in (404, 400):
            print(f"[image_gen] Gemini {model} HTTP {e.code} — not a model-fallback case", file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return _first_image(data)


def generate_raw(prompt: str, api_key: str,
                 base_image: Optional[bytes] = None) -> tuple[Optional[bytes], str]:
    """Generate one image, Nano Banana Pro -> fallback. Returns (bytes|None, model)."""
    for model in (NANO_BANANA_PRO, NANO_BANANA_FALLBACK):
        img = _gemini_image(prompt, api_key, model, base_image=base_image)
        if img:
            return img, model
    return None, ""


# --- default heuristic scorer (the measurable floor; LLM judge is injectable) -
def heuristic_score(image_bytes: bytes) -> float:
    """Honest measurable delight FLOOR (NOT 'optimal delight'): validity +
    colour entropy + contrast spread, each in [0,1], averaged. A degenerate
    (blank/tiny/monochrome) image scores low; a rich one scores high. Pure +
    deterministic -> Goodhart-testable. Swap for an LLM judge via the `scorer`
    arg of converge_image() (llm-augmentation: floor here, recall in L2)."""
    try:
        import io
        from PIL import Image  # local import: graceful if Pillow absent
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return 0.0
    w, h = im.size
    if w < 16 or h < 16:
        return 0.1  # too small to be useful
    im_small = im.resize((64, 64))
    px = list(im_small.getdata())
    uniq = len(set(px))
    colour_richness = min(1.0, uniq / 1200.0)  # ~1200 distinct = rich
    lum = [0.299 * r + 0.587 * g + 0.114 * b for (r, g, b) in px]
    contrast = min(1.0, (max(lum) - min(lum)) / 200.0)
    validity = 1.0
    return round((validity + colour_richness + contrast) / 3.0, 4)


def default_refiner(prompt: str, depth: int) -> str:
    """Progressive prompt escalation per converge depth (deterministic, testable).
    Each rung adds a quality/novelty directive without discarding intent."""
    rungs = [
        "Make it bold, iconic, and instantly readable.",
        "Increase contrast and use a striking, harmonious colour palette.",
        "Add depth and a sense of motion; make it feel alive and delightful.",
        "Refine composition for balance; remove clutter; sharpen the focal point.",
        "Push novelty — an unexpected, memorable visual twist that still fits.",
    ]
    # Distinct, progressively-richer directive at EVERY depth (0..max_depth).
    # Through the rung count: CUMULATIVE (each depth adds one more directive).
    # Beyond it: all rungs + a numbered intensification pass — so a depth-11
    # converge keeps exploring a genuinely different prompt each iteration rather
    # than regenerating the identical final rung (AGORA W2: the old min(depth,..)
    # made depths 5-11 identical, hollowing the deep converge).
    if depth < len(rungs):
        add = " ".join(rungs[: depth + 1])
    else:
        add = (" ".join(rungs)
               + f" Refinement pass {depth}: intensify the single most striking"
                 " element further while keeping it coherent.")
    return f"{prompt} {add}".strip()


# --- the /converge prompt-refinement loop -------------------------------------
def converge_image(
    prompt: str,
    api_key: str,
    *,
    max_depth: int = _DEPTH,
    score_target: float = _SCORE_TARGET,
    scorer: Optional[Callable[[bytes], float]] = None,
    refiner: Optional[Callable[[str, int], str]] = None,
    base_image: Optional[bytes] = None,
) -> dict:
    """Converge on the best image: generate -> score -> refine prompt -> regenerate,
    up to max_depth, tracking the best by score. Reuses lib.converge.converge.
    When base_image is supplied every generation rides on it (img2img). Returns
    the best state dict {prompt, image, model, score, depth}."""
    score_fn = scorer or heuristic_score
    refine_fn = refiner or default_refiner

    def gen_state(p: str, depth: int) -> dict:
        img, model = generate_raw(p, api_key, base_image=base_image)
        sc = score_fn(img) if img else 0.0
        return {"prompt": p, "image": img, "model": model, "score": sc, "depth": depth}

    seed = gen_state(prompt, 0)

    def criterion(s: dict) -> bool:
        return bool(s.get("image")) and s.get("score", 0.0) >= score_target

    def improve(s: dict) -> dict:
        nxt = refine_fn(s["prompt"], s.get("depth", 0))
        return gen_state(nxt, s.get("depth", 0) + 1)

    result = converge(
        state=seed,
        criterion=criterion,
        improve=improve,
        max_depth=max_depth,
        _scorer=lambda s: s.get("score", 0.0),
        label="image_gen.converge",
    )
    return result.best_state


# --- Pillow + ImageMagick post-processing -------------------------------------
def postprocess(image_bytes: bytes, *, size: Optional[int], fmt: str, transparent: bool) -> bytes:
    """Resize/format/transparency via Pillow; optimise/strip via ImageMagick when
    available. Fail-safe: returns the input bytes if Pillow is absent."""
    try:
        import io
        from PIL import Image
    except Exception:
        return image_bytes  # graceful: deliver raw if Pillow unavailable
    im = Image.open(io.BytesIO(image_bytes))
    im = im.convert("RGBA" if transparent else "RGB")
    if size:
        im = im.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    save_fmt = "WEBP" if fmt == "webp" else "PNG"
    im.save(out, format=save_fmt)
    return _imagemagick_optimise(out.getvalue(), fmt)


def _imagemagick_optimise(data: bytes, fmt: str) -> bytes:
    """Optional ImageMagick pass (strip metadata + optimise). No-op if `magick`
    absent or it errors — never fail the pipeline on optimisation."""
    if not _has_magick():
        return data
    src = dst = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as fin:
            fin.write(data)
            src = fin.name
        dst = src + f".opt.{fmt}"
        r = subprocess.run(
            ["magick", src, "-strip", "-quality", "92", dst],
            capture_output=True, timeout=20,
        )
        if r.returncode == 0 and os.path.exists(dst):
            return Path(dst).read_bytes()
        return data
    except (subprocess.TimeoutExpired, OSError):
        return data
    finally:
        _quiet_unlink(src)
        _quiet_unlink(dst)


def _has_magick() -> bool:
    try:
        return subprocess.run(["magick", "--version"], capture_output=True, timeout=5).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _quiet_unlink(path: str) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _spec_for(purpose: str, channel: Optional[str]) -> dict:
    if purpose == "emoji":
        return dict(_EMOJI_SPEC.get((channel or "").lower(), _DEFAULT_EMOJI))
    return dict(_IMAGE_SPEC)


# --- public syscall -----------------------------------------------------------
def generate_image(
    prompt: str,
    *,
    purpose: str = "image",
    channel: Optional[str] = None,
    api_key: Optional[str] = None,
    max_depth: int = _DEPTH,
    score_target: float = _SCORE_TARGET,
    scorer: Optional[Callable[[bytes], float]] = None,
    base_image=None,
) -> ImageResult:
    """Channel-agnostic image generation. purpose in {image, emoji}; channel sizes
    the output to that channel's native spec. base_image (path or bytes), when
    given, drives Nano Banana Pro img2img — every converge iteration rides on the
    reference artwork. Returns ImageResult (fail-safe)."""
    key = _resolve_key(api_key)
    if not key:
        return ImageResult(ok=False, error=f"no Gemini key (keychain {_KEYCHAIN_SERVICE} or pass api_key)")
    if not prompt or not prompt.strip():
        return ImageResult(ok=False, error="empty prompt")

    best = converge_image(prompt, key, max_depth=max_depth, score_target=score_target,
                          scorer=scorer, base_image=_coerce_image_bytes(base_image))
    if not best.get("image"):
        return ImageResult(ok=False, error="Gemini returned no image (both models)", prompt_final=best.get("prompt", prompt))

    spec = _spec_for(purpose, channel)
    # Defensive access: all spec literals carry size/fmt/transparent today, but
    # .get with sane defaults means a future spec edit that drops a key degrades
    # to a valid image rather than a KeyError on this path (AGORA W2 hardening).
    final = postprocess(
        best["image"],
        size=spec.get("size"),
        fmt=spec.get("fmt", "png"),
        transparent=spec.get("transparent", False),
    )
    return ImageResult(
        ok=True, data=final, fmt=spec.get("fmt", "png"), model=best.get("model", ""),
        depth_used=best.get("depth", 0), score=best.get("score", 0.0),
        prompt_final=best.get("prompt", prompt),
        meta={"purpose": purpose, "channel": channel, "size": spec.get("size")},
    )


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="image_gen", description="Generate an image via Nano Banana Pro + converge.")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--purpose", default="image", choices=["image", "emoji"])
    ap.add_argument("--channel", default=None)
    ap.add_argument("--out", required=True, help="output file path")
    ap.add_argument("--depth", type=int, default=_DEPTH)
    ap.add_argument("--key-file", default=None,
                    help="path to a chmod-600 file holding the Gemini API key (for "
                         "hosts without the keychain, e.g. the VPS). Never passed on "
                         "argv, so no ps leak.")
    ap.add_argument("--base-image", default=None,
                    help="path to a reference image (img2img). Nano Banana Pro rides "
                         "on it every converge iteration — e.g. a base artwork to "
                         "restyle. Omit for text-to-image.")
    args = ap.parse_args(argv)

    api_key = None
    if args.key_file:
        try:
            api_key = Path(args.key_file).read_text().strip() or None
        except OSError:
            api_key = None
    res = generate_image(args.prompt, purpose=args.purpose, channel=args.channel,
                         max_depth=args.depth, api_key=api_key,
                         base_image=args.base_image)
    if not res.ok:
        print(json.dumps({"ok": False, "error": res.error}))
        return 1
    Path(args.out).write_bytes(res.data)
    print(json.dumps({
        "ok": True, "out": args.out, "fmt": res.fmt, "model": res.model,
        "depth_used": res.depth_used, "score": res.score, "bytes": len(res.data),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
