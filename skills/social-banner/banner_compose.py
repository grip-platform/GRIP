#!/usr/bin/env python3
"""banner_compose — deterministic CBC compose to an exact banner size.

Samples the image's own border background, pads to the TARGET aspect on that
background (no distortion, no content crop), then Lanczos-downscales to exactly
WxH. This is the correct-by-construction floor extracted from the social-banner
pipeline (sampled bg keeps the pad seamless; pad-not-crop keeps every figure).

Usage:
  banner_compose.py <in> <out> [WxH]      # default 1584x396 (LinkedIn cover)
"""
import sys
from collections import Counter
from PIL import Image

DEFAULT = (1584, 396)


def sample_bg(im):
    w, h = im.size
    border = ([im.getpixel((x, 0)) for x in range(0, w, 4)] +
              [im.getpixel((x, h - 1)) for x in range(0, w, 4)])
    return Counter(border).most_common(1)[0][0]


def pad_to_aspect(im, target_aspect, bg):
    """Pad (never crop) so im matches target_aspect, centred on bg."""
    w, h = im.size
    cur = w / h
    if abs(cur - target_aspect) < 1e-4:
        return im
    if cur < target_aspect:           # too narrow -> pad width
        nw, nh = round(h * target_aspect), h
    else:                              # too wide -> pad height
        nw, nh = w, round(w / target_aspect)
    canvas = Image.new("RGB", (nw, nh), bg)
    canvas.paste(im, ((nw - w) // 2, (nh - h) // 2))
    return canvas


def compose(in_path, out_path, size):
    im = Image.open(in_path).convert("RGB")
    bg = sample_bg(im)
    padded = pad_to_aspect(im, size[0] / size[1], bg)
    final = padded.resize(size, Image.LANCZOS)
    final.save(out_path, optimize=True)
    print(f"COMPOSE: {out_path} {final.size}  bg=#{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}"
          f"  (src {im.size} -> pad {padded.size} -> {final.size})")
    return final.size


def parse_size(tok):
    w, h = tok.lower().split("x")
    return (int(w), int(h))


if __name__ == "__main__":
    size = parse_size(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT
    compose(sys.argv[1], sys.argv[2], size)
